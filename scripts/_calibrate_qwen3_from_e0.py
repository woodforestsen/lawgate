# -*- coding: utf-8 -*-
"""Qwen3-4B 尺度门控阈值重校准（路线 A，按产品真实默认路由 hybrid 校准）。

背景
----
D38 把草稿换成 Qwen3-4B 后，神经 margin 信号的 u 绝对尺度比 0.5B 小 100–1000 倍，
现行 ``configs/thresholds.json`` 的 τ_b（0.29/1.0/1.0/1.0，0.5B 尺度）全部失效 →
默认配置下门控几乎不触发。但 ``scripts/e0_diagnostic.py --tag qwen3`` 全量跑出
margin AUC = 0.8964（绿灯），证明 Qwen3-4B 信号在 236 条 dev 上**有强判别力**，
只是阈值尺度不对。

本脚本按 **产品默认路由** 校准（见 ``lawgate/router.py``：router_mode=hybrid，
signal_buckets=("b2",)）：
  - b2 用神经 margin 信号（u 量级 ~1e-4，需重校准到 Qwen3-4B 尺度）
  - b1/b3/b4 用确定性复杂度评分 u_cplx（值域 [0,1]，与草稿模型无关，但 b1 的
    complexity AUC=0.108 呈反判别，见下方 caveat）

逐桶用 dev 的 ``need_retrieval`` 金标（门控信号的直接目标：预测"是否该检索"）
做 Youden 点（max(TPR−FPR)）网格搜索，输出 Qwen3-4B 尺度 τ_b，并算 RR / 检索降 X%。

口径说明（必须诚实标注）
------------------------
- 这是**信号判别力校准**（用 need_retrieval 标签），不是严格端到端 acc 校准
  （用 LLM 生成 baseline 的 correct 标签）。后者需重跑 Qwen3-4B 的
  neverrag/alwaysrag（CPU 数十小时），本机不可行。
- RR / 检索降 X% 只依赖 u 分布 + τ_b，不依赖 LLM 生成基线，故本数字在
  Qwen3-4B 口径下是真实的（相对 AlwaysRAG=1.0）。
- need_retrieval 标签由规则化构造（非人工标注），且 dev 的 case 类为合成文书
  （见 e0_auc.json 的 dataset_caveat）。AUC 只反映"信号与构造标签的一致性"，
  不等同人工标注下的真实判别力。
- 阈值量级极小（~1e-4），区分带极窄，对 margin 信号的微小漂移敏感 → 脆弱，
  已如实标注，绝不当成"稳健有效门控"对外报。

用法::

    python scripts/_calibrate_qwen3_from_e0.py
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIG = ROOT / "figures/e0_qwen3/e0_signals_dev.jsonl"
OUT_TAUS = ROOT / "configs/thresholds.json"
OUT_REPORT = ROOT / "results/calibrate_report_qwen3.json"
BUCKETS = ["b1", "b2", "b3", "b4"]
# 与 lawgate/router.py 默认一致：hybrid，仅 b2 走神经信号
SIGNAL_BUCKETS = ("b2",)


def best_tau(pairs):
    """pairs: list[(u, y)]，y=1 表示 need_retrieval。返回 (tau, youden, tpr, fpr, n_trig)。"""
    best = None
    # τ ∈ [0, 0.1]，步长 0.0001。Qwen3-4B 的 margin u 量级极小（判别区~1e-4–5e-3），
    # 步长 0.001 会漏掉真正 Youden 最优点；complexity 评分在 [0,1]，同网格覆盖。
    for i in range(0, 1001):
        tau = i / 10000.0
        tp = sum(1 for u, y in pairs if u > tau and y == 1)
        fp = sum(1 for u, y in pairs if u > tau and y == 0)
        fn = sum(1 for u, y in pairs if u <= tau and y == 1)
        tn = sum(1 for u, y in pairs if u <= tau and y == 0)
        tpr = tp / (tp + fn) if (tp + fn) else 0.0
        fpr = fp / (fp + tn) if (fp + tn) else 0.0
        youden = tpr - fpr
        if best is None or youden > best[1]:
            best = (tau, youden, tpr, fpr, tp + fp)
    return best


def main() -> int:
    rows = [json.loads(l) for l in open(SIG, encoding="utf-8")]
    by_b: dict[str, list] = defaultdict(list)
    for r in rows:
        b = r.get("bucket")
        if b not in BUCKETS:
            continue
        um = float(r["signals"].get("margin", 0.0))
        uc = float(r["signals"].get("complexity", 0.0))
        y = int(bool(r.get("need_retrieval")))
        by_b[b].append((um, uc, y))

    # ---- hybrid：每桶选信号（signal_buckets 用 margin，其余用 complexity）----
    taus_h: dict[str, float] = {}
    per_h: dict[str, dict] = {}
    for b in BUCKETS:
        um, uc, _ = by_b[b][0]
        use_margin = b in SIGNAL_BUCKETS
        sig_name = "margin" if use_margin else "complexity"
        pairs = [(um, y) if use_margin else (uc, y) for um, uc, y in by_b[b]]
        tau, youden, tpr, fpr, n_trig = best_tau(pairs)
        taus_h[b] = round(tau, 4)
        per_h[b] = {
            "signal_used": sig_name,
            "n": len(pairs),
            "tau": tau,
            "youden": round(youden, 4),
            "tpr": round(tpr, 4),
            "fpr": round(fpr, 4),
            "need_retrieval_true": sum(y for _, y in pairs),
            "predicted_trigger": n_trig,
        }

    # ---- signal 模式（全桶 margin，作为对照，E0 说 margin 处处强）----
    taus_s: dict[str, float] = {}
    per_s: dict[str, dict] = {}
    for b in BUCKETS:
        pairs = [(um, y) for um, uc, y in by_b[b]]
        tau, youden, tpr, fpr, n_trig = best_tau(pairs)
        taus_s[b] = round(tau, 4)
        per_s[b] = {
            "signal_used": "margin",
            "n": len(pairs),
            "tau": tau,
            "youden": round(youden, 4),
            "tpr": round(tpr, 4),
            "fpr": round(fpr, 4),
            "need_retrieval_true": sum(y for _, y in pairs),
            "predicted_trigger": n_trig,
        }

    def rr_for(taus, use_margin_for):
        rr = n = 0
        for r in rows:
            b = r.get("bucket")
            if b not in BUCKETS:
                continue
            if use_margin_for(b):
                u = float(r["signals"].get("margin", 0.0))
            else:
                u = float(r["signals"].get("complexity", 0.0))
            if u > taus[b]:
                rr += 1
            n += 1
        return rr / n if n else 0.0

    rr_h = rr_for(taus_h, lambda b: b in SIGNAL_BUCKETS)
    rr_s = rr_for(taus_s, lambda b: True)

    print("=" * 64)
    print("  Qwen3-4B 尺度 τ_b 重校准（基于 e0_qwen3 + need_retrieval 金标）")
    print("=" * 64)
    print("  margin AUC (全量) = 0.8964  [绿灯，神经信号判别力有效]")
    print()
    print("  [hybrid 默认路由：b2→margin，b1/b3/b4→complexity]")
    for b in BUCKETS:
        p = per_h[b]
        print(f"  {b}: sig={p['signal_used']:<9} n={p['n']:>3} τ_b={taus_h[b]:.4f} "
              f"Youden={p['youden']:.3f} TPR={p['tpr']:.3f} FPR={p['fpr']:.3f}")
    print(f"  → hybrid dev 检索触发率 RR = {rr_h:.4f}，检索降 {1-rr_h:.4f}")
    print()
    print("  [signal 模式对照：全桶 margin]")
    for b in BUCKETS:
        p = per_s[b]
        print(f"  {b}: n={p['n']:>3} τ_b={taus_s[b]:.4f} "
              f"Youden={p['youden']:.3f} TPR={p['tpr']:.3f} FPR={p['fpr']:.3f}")
    print(f"  → signal dev 检索触发率 RR = {rr_s:.4f}，检索降 {1-rr_s:.4f}")
    print()

    # 写回 configs/thresholds.json —— 按产品默认 hybrid 路由填（这才是默认配置真正消费的）
    OUT_TAUS.write_text(json.dumps(taus_h, indent=2, ensure_ascii=False) + "\n")
    report = {
        "provenance": ("Qwen3-4B 草稿 / 按产品默认路由 hybrid 校准（b2→margin、b1/b3/b4→complexity），"
                       "基于 e0_qwen3 的 need_retrieval 金标逐桶 Youden 点校准（信号判别力校准，"
                       "非端到端 correct 校准）"),
        "date": "2026-09-14",
        "router_mode_default": "hybrid",
        "signal_buckets_default": list(SIGNAL_BUCKETS),
        "auc_margin_all": 0.8964,
        "replaces": "旧 τ_b=0.29/1.0/1.0/1.0（0.5B 尺度，对 Qwen3-4B 失效）",
        "thresholds_written": "configs/thresholds.json = hybrid 适用 τ_b（b2 为 margin 尺度，"
                               "b1/b3/b4 为 complexity 尺度 [0,1]）",
        "hybrid": {
            "taus": taus_h,
            "per_bucket": per_h,
            "dev_rr": round(rr_h, 4),
            "retrieval_drop_vs_alwaysrag": round(1 - rr_h, 4),
        },
        "signal_mode_reference": {
            "taus": taus_s,
            "per_bucket": per_s,
            "dev_rr": round(rr_s, 4),
            "retrieval_drop_vs_alwaysrag": round(1 - rr_s, 4),
        },
        "caveats": [
            "严格端到端 acc 校准需 Qwen3-4B 的 neverrag/alwaysrag baseline（correct 标签），"
            "CPU 数十小时不可行；本 τ_b 用 need_retrieval 构造标签校准，RR/降 X% 在 Qwen3-4B "
            "口径下真实。",
            "need_retrieval 标签由规则化构造（非人工标注），且 dev 的 case 类为合成文书；"
            "AUC 只反映信号与构造标签的一致性，不等同真实判别力。",
            "b1 的 complexity 评分 AUC=0.108（反判别），用 complexity 门控 b1 不可靠；"
            "E0 建议将信号桶扩到 b1/b4（margin 在 b1=0.936、b4=0.874 均强），可显著改善门控。",
            "阈值量级极小（~1e-4，区分带极窄），对 margin 信号漂移敏感 → 脆弱，非稳健门控。",
        ],
    }
    # 推荐配置：把信号桶扩到 b1/b2/b4（margin 在 b1=0.936、b4=0.874 均强），
    # b3 恒检索。等价于 signal_mode_reference 的 τ_b（b1=0.0001/b2=0.0002/b3=0.0/b4=0.0002），
    # 需改 lawgate/router.py 的 signal_buckets 默认值（代码改动，超出阈值重校准范围）。
    report["recommended_config"] = {
        "change": "router.py: signal_buckets=('b2',) → ('b1','b2','b4')；b3 走 always-retrieve",
        "taus_to_use": taus_s,
        "expected_dev_rr": round(rr_s, 4),
        "expected_retrieval_drop": round(1 - rr_s, 4),
        "note": "此配置下门控在 b1/b2/b4 均有强判别力，检索降 ~46%；阈值仍属 ~1e-4 脆弱量级。",
    }

    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"  已写回（hybrid 适用）{OUT_TAUS}")
    print(f"  报告   {OUT_REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
