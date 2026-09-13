# -*- coding: utf-8 -*-
"""桶级阈值校准（手册 S6.4）。

流程（与手册一致）：
  1. dev 集上跑 AlwaysRAG 与 NeverRAG，得到每条 ``correct_if_retrieve`` /
     ``correct_if_direct``；
  2. 复用 E0 已算好的 dev 信号（figures/e0/e0_signals_dev.jsonl）得到 u；
  3. ``calibrate()`` 逐桶求 τ_b → configs/thresholds.json；
  4. 同时为 TARG 基线网格搜索单全局 τ（同一 dev、同一目标函数）。

三种门控模式都校准一遍并全部写入 results/calibrate_report.json：
  signal     全部桶用神经信号（E0 汇总红灯，作为消融对照）
  complexity 全部桶用确定性复杂度评分（手册风险表降级方案）
  hybrid     b2 用神经信号、其余桶用复杂度评分（**默认**，依据 E0 分桶结论）

    python scripts/calibrate.py                 # 复用已有 dev 结果
    python scripts/calibrate.py --run           # 先跑 dev 的 neverrag/alwaysrag
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lawgate.eval import io as eio  # noqa: E402
from lawgate.eval.run_exp import run_experiment  # noqa: E402
from lawgate.gate.calibrate import (  # noqa: E402
    BUCKETS,
    calibrate,
    calibrate_single_tau,
    classify_bucket,
)
from lawgate.gate.complexity import complexity_score  # noqa: E402
from lawgate.gate.intent import detect_intent  # noqa: E402

DEV_BASELINE_DIR = "results/dev_baseline"
OUT_TAUS = "configs/thresholds.json"
OUT_REPORT = "results/calibrate_report.json"


def load_dev_pairs(split: str = "dev") -> tuple[list[dict], dict[str, dict], dict[str, dict]]:
    """返回 (dev items, neverrag 记录 by qid, alwaysrag 记录 by qid)。"""
    items = eio.load_split(split)
    direct = {r["qid"]: r for r in eio.load_results(
        f"{DEV_BASELINE_DIR}/neverrag_seed0_{split}.jsonl")}
    retr = {r["qid"]: r for r in eio.load_results(
        f"{DEV_BASELINE_DIR}/alwaysrag_seed0_{split}.jsonl")}
    return items, direct, retr


def load_signal_map(split: str = "dev") -> dict[str, dict]:
    p = Path(f"figures/e0/e0_signals_{split}.jsonl")
    if not p.exists():
        raise FileNotFoundError(
            f"{p} 不存在；请先运行 python scripts/e0_diagnostic.py --split {split}")
    return {r["qid"]: r for r in eio.load_results(p)}


def build_dev_preds(items, direct, retr, signals, mode: str,
                    signal: str = "margin",
                    signal_buckets: tuple = ("b2",),
                    delta: float = 0.005) -> tuple[list[dict], dict]:
    preds: list[dict] = []
    skipped: list[str] = []
    for it in items:
        qid = it.get("qid")
        if qid not in direct or qid not in retr or qid not in signals:
            skipped.append(qid)
            continue
        bucket = it.get("bucket") or classify_bucket(it)
        sig = signals[qid]["signals"]
        u_signal = float(sig.get(signal, 0.0))
        sl = detect_intent(it.get("query", ""), it.get("history"))
        u_cplx = complexity_score(it.get("query", ""), sl.slots,
                                  it.get("history"),
                                  category=it.get("category")).score
        if mode == "signal":
            u, src = u_signal, "neural_signal"
        elif mode == "complexity":
            u, src = u_cplx, "complexity"
        else:
            u, src = ((u_signal, "neural_signal")
                      if bucket in signal_buckets else (u_cplx, "complexity"))
        preds.append({
            "qid": qid, "bucket": bucket, "u": u, "gate_source": src,
            "u_signal": u_signal, "u_complexity": u_cplx,
            "correct_if_retrieve": int(bool(retr[qid]["correct"])),
            "correct_if_direct": int(bool(direct[qid]["correct"])),
        })
    meta = {"n_items": len(items), "n_preds": len(preds),
            "n_skipped": len(skipped), "skipped_examples": skipped[:5]}
    return preds, meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true",
                    help="先跑 dev 的 neverrag/alwaysrag（缺失时）")
    ap.add_argument("--split", default="dev",
                    help="用于校准的开发集划分（默认 dev，可用 dev_calib 等）")
    ap.add_argument("--signal", default="margin")
    ap.add_argument("--delta", type=float, default=0.005)
    ap.add_argument("--rule", default="largest_feasible",
                    choices=["largest_feasible", "smallest_feasible"])
    ap.add_argument("--grid-max", type=float, default=None,
                    help="τ 网格上限（默认 0.50，即手册 S3.6 的 GRID）。"
                         "hybrid 模式下 b1/b3/b4 用复杂度评分（值域 [0,1]），"
                         "实测 b3 恒为 1.0、b4 中位数 0.5，故需 --grid-max 1.0 "
                         "才能让这些桶的 τ 落在有意义的工作点上（见 D20）。")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    split = args.split
    grid = ([round(0.01 * i, 2) for i in range(1, int(round(args.grid_max * 100)) + 1)]
            if args.grid_max else None)
    need = [f"{DEV_BASELINE_DIR}/neverrag_seed0_{split}.jsonl",
            f"{DEV_BASELINE_DIR}/alwaysrag_seed0_{split}.jsonl"]
    if args.run or not all(Path(p).exists() for p in need):
        for m in ("neverrag", "alwaysrag"):
            print(f"[calibrate] 运行 {split} 基线 {m} …", flush=True)
            run_experiment(exp="dev_baseline", method_name=m, seed=0, split=split,
                           overwrite=True, max_tokens=128, progress_every=40)

    items, direct, retr = load_dev_pairs(split)
    signals = load_signal_map(split)
    # 参照精度：AlwaysRAG 在 dev 上的 acc
    always_acc = sum(1 for r in retr.values() if r["correct"]) / max(len(retr), 1)
    never_acc = sum(1 for r in direct.values() if r["correct"]) / max(len(direct), 1)

    report: dict = {
        "date": eio.run_meta().get("date"),
        "provenance": eio.run_meta(),
        "split": split,
        "dev_items": len(items),
        "always_rag_acc_on_dev": round(always_acc, 4),
        "never_rag_acc_on_dev": round(never_acc, 4),
        "target": round(always_acc - args.delta, 4),
        "rule": args.rule,
        "signal": args.signal,
        "grid_max": (args.grid_max if args.grid_max else 0.50),
        "modes": {}, "single_tau": {},
    }

    default_taus: dict[str, float] = {}
    for mode in ("hybrid", "complexity", "signal"):
        preds, meta = build_dev_preds(items, direct, retr, signals, mode=mode,
                                      signal=args.signal, delta=args.delta)
        if not preds:
            report["modes"][mode] = {"error": "无有效 dev 预测", **meta}
            continue
        out = f"results/calibrate_{mode}_thresholds.json"
        taus, rep = calibrate(preds, always_acc, out_path=out, delta=args.delta,
                              rule=args.rule, grid=grid)
        tau_single, srep = calibrate_single_tau(preds, always_acc,
                                                rule=args.rule, delta=args.delta,
                                                grid=grid)
        # 该模式在 dev 上的实际表现（用校准出的 τ_b）
        acc = sum(p["correct_if_retrieve"] if p["u"] > taus[p["bucket"]]
                  else p["correct_if_direct"] for p in preds) / len(preds)
        rr = sum(1 for p in preds if p["u"] > taus[p["bucket"]]) / len(preds)
        report["modes"][mode] = {
            "meta": meta, "taus": taus, "per_bucket": rep,
            "dev_acc_at_taus": round(acc, 4), "dev_rr_at_taus": round(rr, 4),
        }
        report["single_tau"][mode] = {"tau": tau_single, "report": srep,
                                      "dev_acc": srep.get("acc_at_tau"),
                                      "dev_rr": srep.get("rr_at_tau")}
        if mode == "hybrid":
            default_taus = taus

    eio.write_json(report, OUT_REPORT, overwrite=True)
    if default_taus:
        eio.write_json(default_taus, OUT_TAUS, overwrite=True)
        print(f"默认（hybrid）τ_b 已写入 {OUT_TAUS}: "
              f"{json.dumps(default_taus, ensure_ascii=False)}")

    for mode, d in report["modes"].items():
        if "taus" in d:
            print(f"  {mode:11s} dev_acc={d['dev_acc_at_taus']} "
                  f"dev_rr={d['dev_rr_at_taus']} taus={d['taus']}")
    print(f"  alwaysrag dev_acc={always_acc:.4f}  neverrag dev_acc={never_acc:.4f}")
    print(f"报告：{OUT_REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
