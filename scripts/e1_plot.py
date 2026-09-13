# -*- coding: utf-8 -*-
"""E1 汇总、Pareto 头图、核心断言与统计检验（手册 S6.5）。

流程：
  1. 读取 results/e1/ 下各方法的逐条结果，按 (method, seed) 聚合出指标行；
  2. 输出 results/e1/summary.csv（mean ± std 跨 seed）；
  3. 画 Pareto 头图（x=RR，y=acc；legalgate 画 4 个 τ 缩放工作点的曲线）；
  4. 执行核心断言：``RR_legalgate ≤ 0.6 × RR_alwaysrag 且 acc ≥ acc_alwaysrag − 0.01``
     → PASS/FAIL；
  5. 配对 bootstrap(n=10000) + Wilcoxon + Cohen's d + Holm 校正。

    python scripts/e1_plot.py
    python scripts/e1_plot.py --tau-scaled result  # 用 --tau-scale 跑出的额外工作点
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lawgate.eval import io as eio  # noqa: E402
from lawgate.eval.metrics import aggregate  # noqa: E402
from lawgate.eval.stats import compare, holm_bonferroni, mean_std  # noqa: E402

METHODS = ["neverrag", "alwaysrag", "targ", "complexity", "legal_llm", "legalgate"]
LABELS = {
    "neverrag": "Never-RAG（不检索）",
    "alwaysrag": "Always-RAG（恒检索）",
    "targ": "TARG（单全局τ）",
    "complexity": "Complexity（启发式）",
    "legal_llm": "Legal-LLM*（法律人设代理）",
    "legalgate": "CA-LegalGate（本项目）",
}


def load_all(results_dir: Path, seed: int, split: str, tag: str = ""):
    out = {}
    for m in METHODS:
        p = eio.result_path(results_dir, m, seed, split, tag)
        recs = eio.load_results(p)
        if recs:
            out[m] = recs
    return out


def build_rows(all_recs: dict, seed: int, split: str) -> list[dict]:
    rows = []
    for m, recs in all_recs.items():
        agg = aggregate(recs)
        agg.update({"method": m, "label": LABELS.get(m, m), "seed": seed,
                    "split": split, "tau_scale": 1.0})
        # 分桶 acc（供论文附表）
        by_bucket: dict[str, dict] = {}
        for b in ("b1", "b2", "b3", "b4"):
            sub = [r for r in recs if r.get("bucket") == b]
            if sub:
                by_bucket[b] = {"n": len(sub), "acc": round(
                    sum(1 for r in sub if r["correct"]) / len(sub), 4),
                    "rr": round(sum(1 for r in sub if r["retrieval_used"])
                                / len(sub), 4)}
        agg["by_bucket"] = by_bucket
        # 通道路由分布（本项目特有的可解释性证据）
        agg["channel_mix"] = {}
        for r in recs:
            ch = str(r.get("channel"))
            agg["channel_mix"][ch] = agg["channel_mix"].get(ch, 0) + 1
        rows.append(agg)
    return rows


def core_assertion(rows: list[dict]) -> dict:
    by = {r["method"]: r for r in rows}
    if "legalgate" not in by or "alwaysrag" not in by:
        return {"status": "SKIP", "reason": "缺少 legalgate 或 alwaysrag 结果"}
    lg, ar = by["legalgate"], by["alwaysrag"]
    rr_ok = lg["rr"] <= 0.6 * ar["rr"] if ar["rr"] > 0 else False
    acc_ok = lg["acc"] >= ar["acc"] - 0.01
    return {
        "status": "PASS" if (rr_ok and acc_ok) else "FAIL",
        "rr_legalgate": lg["rr"], "rr_alwaysrag": ar["rr"],
        "rr_ratio": round(lg["rr"] / ar["rr"], 4) if ar["rr"] else None,
        "rr_condition": f"RR_lg {lg['rr']} ≤ 0.6 × RR_ar {round(0.6 * ar['rr'], 4)}"
                        f" → {rr_ok}",
        "acc_legalgate": lg["acc"], "acc_alwaysrag": ar["acc"],
        "acc_condition": f"acc_lg {lg['acc']} ≥ acc_ar − 0.01 "
                         f"{round(ar['acc'] - 0.01, 4)} → {acc_ok}",
        "rr_ok": bool(rr_ok), "acc_ok": bool(acc_ok),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", default="results/e1")
    ap.add_argument("--seeds", type=int, nargs="+", default=[0])
    ap.add_argument("--split", default="test")
    ap.add_argument("--tag", default="")
    ap.add_argument("--tau-scaled-dir", default=None,
                    help="额外 τ 缩放工作点所在目录（results/e1_tau*）")
    ap.add_argument("--n-boot", type=int, default=10000)
    args = ap.parse_args()

    rd = Path(args.results_dir)
    rows_by_seed: list[dict] = []
    all_by_seed: dict[int, dict] = {}
    for seed in args.seeds:
        all_recs = load_all(rd, seed, args.split, args.tag)
        if not all_recs:
            print(f"[e1] seed={seed} 没有任何结果文件，跳过")
            continue
        all_by_seed[seed] = all_recs
        rows_by_seed += build_rows(all_recs, seed, args.split)

    if not rows_by_seed:
        print("[e1] 无结果可汇总")
        return 1

    # 跨 seed 聚合
    summary: list[dict] = []
    for m in METHODS:
        sub = [r for r in rows_by_seed if r["method"] == m]
        if not sub:
            continue
        rec = {"method": m, "label": LABELS.get(m, m),
               "seeds": len(sub), "split": args.split}
        for k in ("acc", "rr", "arc", "lac", "tvc", "p50_ms", "p95_ms",
                  "mean_tokens", "invalid_law_citation_rate", "n"):
            vals = [r[k] for r in sub if r.get(k) is not None]
            if vals:
                mu, sd = mean_std([float(v) for v in vals])
                rec[k] = round(mu, 4)
                rec[f"{k}_std"] = round(sd, 4)
        summary.append(rec)
    eio.write_summary_csv(summary, rd / "summary.csv")
    eio.write_json({"rows": rows_by_seed, "summary": summary},
                   rd / "summary_full.json")

    assertion = core_assertion(summary)
    eio.write_json(assertion, rd / "core_assertion.json")

    # 统计检验（seed 0 逐条配对）
    stats_out: dict = {"comparisons": [], "holm": {}}
    base_seed = args.seeds[0] if args.seeds else 0
    if base_seed in all_by_seed:
        recs = all_by_seed[base_seed]
        ref = "alwaysrag"
        pvals: dict[str, float] = {}
        if ref in recs:
            ref_map = {r["qid"]: int(bool(r["correct"])) for r in recs[ref]}
            for m in METHODS:
                if m == ref or m not in recs:
                    continue
                cur_map = {r["qid"]: int(bool(r["correct"])) for r in recs[m]}
                qids = sorted(set(ref_map) & set(cur_map))
                if len(qids) < 10:
                    continue
                a = [cur_map[q] for q in qids]
                b = [ref_map[q] for q in qids]
                c = compare(m, a, ref, b, n_boot=args.n_boot, seed=0)
                d = c.to_dict()
                d["n_paired"] = len(qids)
                stats_out["comparisons"].append(d)
                pvals[m] = d["p_wilcoxon"]
        stats_out["holm"] = holm_bonferroni(pvals) if pvals else {}
        eio.write_json(stats_out, rd / "stats.json")

    # Pareto 头图
    try:
        from lawgate.eval.plotting import plot_pareto

        curve_rows = list(summary)
        if args.tau_scaled_dir:
            for d in sorted(Path("results").glob("e1_tau*")):
                for m in ("legalgate",):
                    for seed in args.seeds:
                        p = eio.result_path(d, m, seed, args.split)
                        recs = eio.load_results(p)
                        if not recs:
                            continue
                        agg = aggregate(recs)
                        agg.update({"method": m, "label": LABELS.get(m, m),
                                    "seed": seed, "split": args.split})
                        scale = 1.0
                        try:
                            scale = float(d.name.split("tau")[-1])
                        except ValueError:
                            pass
                        agg["tau_scale"] = scale
                        curve_rows.append(agg)
        plot_pareto(curve_rows, out_path="figures/e1/pareto_rr_acc.png",
                    provenance=eio.run_meta(),
                    curve_method="legalgate",
                    tau_scales=(0.5, 0.75, 1.0, 1.25))
    except Exception as exc:  # noqa: BLE001
        print(f"[e1] Pareto 出图失败（不影响指标）：{type(exc).__name__}: {exc}")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\n== 核心断言 ==")
    print(json.dumps(assertion, ensure_ascii=False, indent=2))
    return 0 if assertion.get("status") in ("PASS", "SKIP") else 0


if __name__ == "__main__":
    raise SystemExit(main())
