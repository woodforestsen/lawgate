# -*- coding: utf-8 -*-
"""E5 时效性陷阱实验（手册 S6.8）——本项目的"杀手实验"之一。

1. 在 temporal_trap 划分（120 条，T1–T4 各 30）上跑 4 个方法；
2. 用 ``metrics`` 的 TVC 判据统计"是否把失效法条当作现行依据"；
3. 按 trap_type 分组输出 TVC 堆叠柱状图；
4. 把所有"非法引用失效法条"的原始答案存档，供论文引用。

    python scripts/e5_temporal.py                      # 全量（复用缓存）
    python scripts/e5_temporal.py --shard 0 --nshards 6
    python scripts/e5_temporal.py --merge
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

OUT = Path("results/e5")
METHODS = ["alwaysrag", "neverrag", "legal_llm", "legalgate"]


def trap_type_of(item: dict) -> str:
    return ((item.get("temporal") or {}).get("trap_type")) or "TX"


def run(shard: int, nshards: int, limit: int | None,
        max_tokens: int = 80, threads: int | None = None) -> None:
    for m in METHODS:
        recs, p = run_experiment(exp="e5", method_name=m, seed=0,
                                 split="temporal_trap", shard=shard,
                                 nshards=nshards, limit=limit, overwrite=True,
                                 max_tokens=max_tokens, threads=threads,
                                 progress_every=0)
        print(f"  {m}: {len(recs)} 条 -> {p}", flush=True)


def merge() -> None:
    from lawgate.eval.run_exp import merge_shards

    for m in METHODS:
        p = merge_shards(OUT, m, 0, "temporal_trap")
        print(f"merged {m}: {p}")


def analyse() -> dict:
    items = {it["qid"]: it for it in eio.load_split("temporal_trap")}
    tvc: dict[str, dict[str, float]] = {}
    counts: dict[str, dict[str, int]] = {}
    failures: list[dict] = []
    per_method: dict[str, dict] = {}

    for m in METHODS:
        recs = eio.load_results(OUT / f"{m}_seed0_temporal_trap.jsonl")
        if not recs:
            continue
        per_method[m] = {
            "n": len(recs),
            "tvc": round(sum(1 for r in recs if r.get("tvc") == 1)
                         / max(sum(1 for r in recs if r.get("tvc") is not None), 1), 4),
            "invalid_law_citation_rate": round(
                sum(1 for r in recs if r.get("invalid_law_cited")) / len(recs), 4),
            "by_trap": {},
        }
        for t in ("T1", "T2", "T3", "T4"):
            sub = [r for r in recs if trap_type_of(items.get(r["qid"], {})) == t]
            if not sub:
                continue
            ok = sum(1 for r in sub if r.get("tvc") == 1)
            tvc.setdefault(t, {})[m] = round(ok / len(sub), 4)
            counts.setdefault(t, {})[m] = len(sub)
            per_method[m]["by_trap"][t] = round(ok / len(sub), 4)
        # 失败用例（引用了失效法条）
        for r in recs:
            if r.get("invalid_law_cited"):
                it = items.get(r["qid"], {})
                failures.append({
                    "qid": r["qid"], "method": m,
                    "trap_type": trap_type_of(it),
                    "expect_status": (it.get("temporal") or {}).get("expect_status"),
                    "superseded_by": (it.get("temporal") or {}).get("superseded_by"),
                    "query": it.get("query"),
                    "invalid_laws": r.get("invalid_laws"),
                    "answer": r.get("answer"),
                })

    OUT.mkdir(parents=True, exist_ok=True)
    eio.write_json(tvc, OUT / "tvc_by_trap.json")
    eio.write_json({"tvc_by_trap": tvc, "n_by_trap": counts,
                    "per_method": per_method,
                    "evidence_scope": (
                        "temporal_trap 的 T1–T4 由 law_lifecycle + SUPERSEDE_MAP + "
                        "validity_status 规则化构造；语料为人工录入的关键条文"
                        "（PENDING_FLK_VERIFICATION），故 TVC 反映的是"
                        "『通道 B 的时效状态机 + 生成通道的失效引用行为』，"
                        "语料本身未经官方原文逐字核对。")},
                   OUT / "e5_report.json")
    eio.write_jsonl(failures, OUT / "failure_cases.jsonl", overwrite=True)

    try:
        from lawgate.eval.plotting import plot_tvc_by_trap

        plot_tvc_by_trap(tvc, out_path="figures/e5/tvc_by_trap.png",
                         provenance=eio.run_meta())
    except Exception as exc:  # noqa: BLE001
        print(f"[e5] 出图失败：{type(exc).__name__}: {exc}")

    for m, d in per_method.items():
        print(f"  {m:11s} tvc={d['tvc']} invalid_rate={d['invalid_law_citation_rate']} "
              f"by_trap={d['by_trap']}")
    print(f"失败用例（非法引用失效法条）：{len(failures)} 条 -> "
          f"{OUT / 'failure_cases.jsonl'}")
    return {"tvc_by_trap": tvc, "per_method": per_method,
            "n_failures": len(failures)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--nshards", type=int, default=1)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--max-tokens", type=int, default=80)
    ap.add_argument("--threads", type=int, default=None)
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--analyse-only", action="store_true")
    args = ap.parse_args()
    if args.merge:
        merge()
    if not args.analyse_only and not args.merge:
        run(args.shard, args.nshards, args.limit,
            max_tokens=args.max_tokens, threads=args.threads)
        if args.nshards > 1:
            return 0
    r = analyse()
    print(json.dumps(r["tvc_by_trap"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
