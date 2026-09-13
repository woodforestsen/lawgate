# -*- coding: utf-8 -*-
"""E0 门控信号诊断（手册 S6.3）——决定后续路由方案的关键前置实验。

做三件事：
  1. dev 集逐条生成 k=20 草稿，算 4 个 [0,1] 信号 + 4 个原始量（全部落盘复用）；
  2. 用各信号预测 ``need_retrieval``，报整体 AUC 与分桶 AUC；
  3. 按手册三档判定给出结论：≥0.75 绿灯 / 0.60–0.75 换信号或细分桶 /
     <0.60 关闭信号门控，改"意图分类 + 复杂度路由"（通道 B 与 E5/E6 不受影响）。

同时报出"置信度方向"的 AUC（1 − AUC）作为对照，避免因方向约定造成误判——
手册未固定方向时这是必要的自证（见 signal.py 的 D11/D18 说明）。

    python scripts/e0_diagnostic.py --split dev                 # 全量
    python scripts/e0_diagnostic.py --split dev --shard 1 --nshards 6
    python scripts/e0_diagnostic.py --merge                     # 合并分片并出图
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lawgate.cache import get_cache  # noqa: E402
from lawgate.config import get_settings  # noqa: E402
from lawgate.eval import io as eio  # noqa: E402
from lawgate.gate.calibrate import BUCKETS, classify_bucket  # noqa: E402
from lawgate.gate.signal import compute_all  # noqa: E402

OUT = Path("figures/e0")   # 可用 --tag 改到 figures/e0_<tag>/（D21：1.5B 复测不覆盖 0.5B 产物）
SIGNAL_NAMES = ["margin", "entropy", "variance", "neglogp"]
RAW_NAMES = ["raw_margin", "raw_entropy", "raw_variance", "raw_neglogp"]
ALL_NAMES = SIGNAL_NAMES + RAW_NAMES + ["complexity"]


def auc(labels: list[int], scores: list[float]) -> float | None:
    try:
        from sklearn.metrics import roc_auc_score

        if len(set(labels)) < 2:
            return None
        return float(roc_auc_score(labels, scores))
    except Exception:  # noqa: BLE001
        return None


def run_shard(split: str, shard: int, nshards: int, k: int,
              limit: int | None) -> Path:
    from lawgate.channel.draft_source import build_draft_source
    from lawgate.channel.llm_base import get_llm

    items = eio.load_split(split)
    if nshards > 1:
        items = [it for i, it in enumerate(items) if i % nshards == shard]
    if limit:
        items = items[:limit]
    llm = get_llm()
    # D33：E0 必须测**路由器实际使用的那条草稿链**（build_draft_source，与
    # run_exp.build_context / TARG / LegalGate 同源）。直接调 llm.draft_logprobs
    # 在 deepseek 后端下会拿到 D30 已证实"塌缩到 ≈0"的 API logprobs，
    # 静默产出没有区分度的垃圾信号（比报错更隐蔽）。
    draft = build_draft_source(llm, get_settings())
    print(f"[e0] answer_model={llm.name} backend={llm.backend} "
          f"draft_chain={draft.describe()['draft_chain']} "
          f"items={len(items)} shard={shard}/{nshards}", flush=True)

    rows = []
    import time

    from lawgate.gate.complexity import complexity_score
    from lawgate.gate.intent import detect_intent

    t0 = time.time()
    for i, it in enumerate(items, start=1):
        st = draft.draft_logprobs(it.get("query", ""), it.get("history"), k=k)
        sig = compute_all(st)
        # 同时算确定性复杂度评分：作为信号门控失效时的候选替代（手册风险表）
        sl = detect_intent(it.get("query", ""), it.get("history"))
        cb = complexity_score(it.get("query", ""), sl.slots, it.get("history"),
                              category=it.get("category"))
        sig["complexity"] = cb.score
        sig["complexity_features"] = cb.features
        rows.append({
            "qid": it.get("qid"), "category": it.get("category"),
            "bucket": it.get("bucket") or classify_bucket(it),
            "need_retrieval": int(bool(it.get("need_retrieval"))),
            "u_true_bucket": it.get("bucket"),
            "signals": sig, "draft_text": st.text[:120],
            "cached": st.cached,
            "draft_source": getattr(st, "source", None),
        })
        if i % 25 == 0:
            print(f"    {i}/{len(items)} {time.time() - t0:.0f}s", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / (f"e0_signals_{split}_part{shard}.jsonl" if nshards > 1
               else f"e0_signals_{split}.jsonl")
    eio.write_jsonl(rows, p, overwrite=True)
    print(f"  -> {p}")
    print(json.dumps(get_cache().stats(), ensure_ascii=False))
    return p


def merge(split: str) -> Path:
    parts = sorted(glob.glob(str(OUT / f"e0_signals_{split}_part*.jsonl")))
    if not parts:
        single = OUT / f"e0_signals_{split}.jsonl"
        if not single.exists():
            raise FileNotFoundError("没有 E0 信号文件")
        return single
    rows: list[dict] = []
    for p in parts:
        rows += eio.load_results(p)
    rows.sort(key=lambda r: str(r.get("qid")))
    out = OUT / f"e0_signals_{split}.jsonl"
    eio.write_jsonl(rows, out, overwrite=True)
    print(f"merged {len(parts)} shards -> {out} ({len(rows)} rows)")
    return out


def analyse(split: str, model_ctx: dict) -> dict:
    from lawgate.eval.plotting import plot_e0_distributions

    path = OUT / f"e0_signals_{split}.jsonl"
    rows = eio.load_results(path)
    if not rows:
        raise FileNotFoundError(path)

    labels = [r["need_retrieval"] for r in rows]
    res: dict = {"split": split, "n": len(rows),
                 "need_retrieval_true": sum(labels),
                 "need_retrieval_false": len(labels) - sum(labels),
                 "signals": {}, "raw": {}, "by_bucket": {}, "by_category": {},
                 "verdict": {}}

    for name in ALL_NAMES:
        vals = [float(r["signals"].get(name, 0.0)) for r in rows]
        a = auc(labels, vals)
        if a is None:
            continue
        holder = res["signals"] if name in SIGNAL_NAMES + ["complexity"] \
            else res["raw"]
        holder[name] = {
            "auc_uncertainty": round(a, 4),
            "auc_confidence_orientation": round(1.0 - a, 4),
            "n": len(rows),
            "mean_pos": round(sum(v for v, l in zip(vals, labels) if l == 1)
                              / max(sum(labels), 1), 4),
            "mean_neg": round(sum(v for v, l in zip(vals, labels) if l == 0)
                              / max(len(labels) - sum(labels), 1), 4),
        }

    # 分桶 AUC
    buckets: dict[str, list[int]] = {}
    for name in SIGNAL_NAMES + ["complexity"]:
        per: dict[str, dict] = {}
        for b in list(BUCKETS) + ["all"]:
            sub = [r for r in rows if b == "all" or r.get("bucket") == b]
            if len(sub) < 10:
                per[b] = {"auc": None, "n": len(sub),
                          "note": "样本不足"}
                continue
            lb = [r["need_retrieval"] for r in sub]
            sc = [float(r["signals"].get(name, 0.0)) for r in sub]
            a = auc(lb, sc)
            per[b] = {"auc": (round(a, 4) if a is not None else None),
                      "n": len(sub),
                      "label_balance": f"{sum(lb)}/{len(lb)}",
                      "note": ("标签在该桶内恒定，AUC 无定义" if a is None else "")}
        res["by_bucket"][name] = per

    # 类别内 AUC：用于识别"辛普森悖论"——汇总 AUC 被桶间差异拉平
    for name in SIGNAL_NAMES + ["complexity"]:
        per: dict[str, dict] = {}
        for cat in sorted({r.get("category") for r in rows}):
            sub = [r for r in rows if r.get("category") == cat]
            lb = [r["need_retrieval"] for r in sub]
            if len(sub) < 10 or len(set(lb)) < 2:
                per[cat] = {"auc": None, "n": len(sub),
                            "note": "类别内标签恒定或样本不足"}
                continue
            sc = [float(r["signals"].get(name, 0.0)) for r in sub]
            a = auc(lb, sc)
            per[cat] = {"auc": (round(a, 4) if a is not None else None),
                        "n": len(sub), "label_balance": f"{sum(lb)}/{len(lb)}"}
        res["by_category"][name] = per

    for b in list(BUCKETS) + ["all"]:
        buckets[b] = [i for i, r in enumerate(rows)
                      if b == "all" or r.get("bucket") == b]

    # 三档判定：以"手册口径的汇总 AUC"为准（保持与手册一致），
    # 同时把分桶结论作为"如何处置"的依据。
    ns = res["signals"]
    best_name = max(ns, key=lambda k: ns[k]["auc_uncertainty"]) if ns else None
    best_auc = ns[best_name]["auc_uncertainty"] if best_name else None

    # 信号有效性分桶判定
    informative, uninformative = [], []
    for b in BUCKETS:
        a = (res["by_bucket"].get("variance") or {}).get(b, {}).get("auc")
        if a is None:
            continue
        (informative if a >= 0.60 else uninformative).append((b, a))
    cplx_all = (res["signals"].get("complexity") or {}).get("auc_uncertainty")

    if best_auc is None:
        tier, action = "无数据", "检查 dev 信号文件"
    elif best_auc >= 0.75:
        tier = "绿灯"
        action = "直接使用该信号做默认门控"
    elif best_auc >= 0.60:
        tier = "黄灯"
        action = "换信号或加细分桶后重测"
    else:
        tier = "红灯"
        action = ("按手册风险表：关闭**全桶统一**的信号门控，路由改为"
                  "『意图分类 + 复杂度路由』；通道 B 与 E5/E6 结论不受影响。"
                  "若分桶显示部分桶仍有效，采用 hybrid：有效桶用神经信号、"
                  "其余桶用确定性复杂度评分，τ_b 仍逐桶在 dev 上校准。")
    res["verdict"] = {"best_signal": best_name, "best_auc": best_auc,
                      "tier": tier, "action": action,
                      "thresholds": {"green": 0.75, "amber": 0.60},
                      "buckets_signal_informative": informative,
                      "buckets_signal_uninformative": uninformative,
                      "complexity_auc": cplx_all,
                      "simpson_note": (
                          "汇总 AUC 与分桶 AUC 方向相反时即为辛普森悖论："
                          "need_retrieval 标签由类别规则决定，桶间标签分布差异"
                          "主导了汇总 AUC，掩蔽了桶内的真实判别力。"
                          "故本项目的门控按桶取用信号（见 gate/complexity.py）。")}
    res["dataset_caveat"] = (
        "need_retrieval 标签由规则化构造（非人工标注），且 case 类为合成文书、"
        "民法典仅覆盖 62 条关键条文；因此本 AUC 只反映「信号与构造标签的一致性」，"
        "不能等同于人工标注下的真实判别力。")
    res["construct_overlap_caveat"] = (
        "**复杂度评分的高 AUC 有构造重叠成分，必须打折解读**："
        "need_retrieval 标签本身由类别规则生成（case 恒为需检索、provision 按问法、"
        "concept 40% 随机），而复杂度评分显式含 category_prior 与 topic 特征，"
        "两者共享同一套规则来源。因此 0.861 中相当一部分是"
        "『用类别规则预测类别规则』，并非对真实检索需求的判别力。"
        "判定这一点的证据是 by_category 表：若复杂度评分在**类别内**（标签仍混合）"
        "的 AUC 也显著 >0.5，才说明它捕捉到了类别之外的实例级信息。"
        "正式结论以上述类别内 AUC 为准；本项目的默认路由因此只在 b2 依赖神经信号、"
        "其余桶用复杂度评分，并在 E1/E2 中同时报告 signal / complexity / hybrid "
        "三种门控模式的实际 acc–RR 表现，让结论由端到端实验而非 AUC 决定。")

    eio.write_json(res, OUT / "e0_auc.json")

    # 出图
    prov = dict(get_settings().provenance())
    prov.update(model_ctx)
    plot_e0_distributions(
        signals={n: [float(r["signals"][n]) for r in rows] for n in SIGNAL_NAMES
                 if n in res["signals"]},
        labels=labels,
        aucs={n: {"auc": v["auc_uncertainty"], "n": v["n"]}
              for n, v in res["signals"].items()},
        out_path=OUT / "e0_distributions.png",
        provenance=prov,
        buckets=buckets,
    )
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--nshards", type=int, default=1)
    ap.add_argument("--k", type=int, default=20)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--analyse-only", action="store_true")
    ap.add_argument("--tag", default="",
                    help="输出目录后缀：figures/e0_<tag>/。用于换模型复测时"
                         "**不覆盖**原有产物（D21：1.5B 复测 vs 0.5B 原始信号）")
    args = ap.parse_args()

    if args.tag:
        global OUT
        OUT = Path(f"figures/e0_{args.tag}")
        print(f"[e0] 输出目录改为 {OUT}（不影响 figures/e0/ 里的原有产物）")

    if args.merge:
        merge(args.split)
        return 0
    if not args.analyse_only:
        run_shard(args.split, args.shard, args.nshards, args.k, args.limit)
        if args.nshards > 1:
            return 0
    r = analyse(args.split, {"k_draft": args.k})
    print(json.dumps(r["verdict"], ensure_ascii=False, indent=2))
    print(json.dumps({k: v["auc_uncertainty"] for k, v in r["signals"].items()},
                     ensure_ascii=False))
    print(json.dumps({k: v["auc_uncertainty"] for k, v in r["raw"].items()},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
