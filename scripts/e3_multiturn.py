# -*- coding: utf-8 -*-
"""E3 多轮实验（手册 S6.7）。

指标：
  * 每轮 acc / RR 随 turn_id 的趋势；
  * **槽位继承错误率** = 应继承槽位中未被正确继承的比例
    （Trace 的 ``slots_inherited`` 与基准题 ``slots.inherited`` 比对）。

    python scripts/e3_multiturn.py
    python scripts/e3_multiturn.py --merge / --analyse-only
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
from lawgate.eval.metrics import aggregate  # noqa: E402
from lawgate.eval.run_exp import merge_shards, run_experiment  # noqa: E402

OUT = Path("results/e3")
METHODS = ["neverrag", "alwaysrag", "legalgate"]


def run(shard: int, nshards: int, limit: int | None, split: str,
        max_tokens: int = 80, threads: int | None = None) -> None:
    for m in METHODS:
        recs, p = run_experiment(exp="e3", method_name=m, seed=0,
                                 split=split, shard=shard,
                                 nshards=nshards, limit=limit, overwrite=True,
                                 max_tokens=max_tokens, threads=threads,
                                 progress_every=0)
        print(f"  {m}: {len(recs)} -> {p}", flush=True)


def analyse(split: str = "test_multiturn") -> dict:
    items = {it["qid"]: it for it in eio.load_split(split)}
    rows: list[dict] = []
    per_method: dict[str, dict] = {}
    for m in METHODS:
        recs = eio.load_results(OUT / f"{m}_seed0_{split}.jsonl")
        if not recs:
            continue
        d = {"method": m, "by_turn": {}}
        for t in (0, 1, 2):
            sub = [r for r in recs if int(r.get("turn_id", 0)) == t]
            if not sub:
                continue
            a = aggregate(sub)
            d["by_turn"][f"turn{t}"] = {
                "n": a["n"], "acc": a["acc"], "rr": a["rr"],
                "slot_inherit_acc": a.get("slot_inherit_acc"),
                "slot_inherit_n": a.get("slot_inherit_n"),
            }
            rows.append({"method": m, "turn_id": t, "acc": a["acc"], "rr": a["rr"],
                         "slot_inherit_acc": a.get("slot_inherit_acc"),
                         "slot_inherit_n": a.get("slot_inherit_n"),
                         "n": a["n"]})
        # 槽位继承错误率（只统计"应当继承"的条目）
        want_n = 0
        ok_n = 0
        for r in recs:
            it = items.get(r["qid"], {})
            want = set((it.get("slots") or {}).get("inherited") or [])
            if not want:
                continue
            want_n += 1
            got = set((r.get("trace") or {}).get("slots_inherited") or [])
            ok_n += int(want <= got)
        d["slot_inherit"] = {
            "n_should_inherit": want_n, "n_ok": ok_n,
            "error_rate": round(1 - ok_n / want_n, 4) if want_n else None,
        }
        d["overall"] = {k: v for k, v in aggregate(recs).items()
                        if k in ("n", "acc", "rr", "mean_tokens")}
        per_method[m] = d

    OUT.mkdir(parents=True, exist_ok=True)
    eio.write_json({"split": split, "per_method": per_method,
                    "trend_rows": rows,
                    "note": ("手册 S6.7 要求 test_multiturn 全量（240 条）。"
                             "本机 CPU-only 预算下按 docs/deviations.md D19 使用 "
                             "test_e4 子集（20 组 × 3 轮 = 60 条），"
                             "每组三整轮完整入样，故轮次趋势可比。")},
                   OUT / "e3_report.json")
    try:
        from lawgate.eval.plotting import plot_multiturn_trend

        plot_multiturn_trend(rows, out_path="figures/e4/multiturn_trend.png",
                             provenance=eio.run_meta())
    except Exception as exc:  # noqa: BLE001
        print(f"[e3] 出图失败：{type(exc).__name__}: {exc}")

    for m, d in per_method.items():
        turns = {k: (v["acc"], v["rr"]) for k, v in d["by_turn"].items()}
        print(f"  {m:11s} (acc,rr) by turn: {turns}  "
              f"inherit_err={d['slot_inherit']['error_rate']} "
              f"(n={d['slot_inherit']['n_should_inherit']})")
    return {"per_method": per_method, "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--nshards", type=int, default=1)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--split-e4", action="store_true",
                    help="用 test_e4 子集（20 组）而非 test_multiturn 全量")
    ap.add_argument("--max-tokens", type=int, default=80)
    ap.add_argument("--threads", type=int, default=None)
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--analyse-only", action="store_true")
    args = ap.parse_args()
    split = "test_e4" if args.split_e4 else "test_multiturn"
    if args.merge:
        for m in METHODS:
            print(merge_shards(OUT, m, 0, split))
    if not args.analyse_only and not args.merge:
        run(args.shard, args.nshards, args.limit, split,
            max_tokens=args.max_tokens, threads=args.threads)
        if args.nshards > 1:
            return 0
    r = analyse(split)
    print(json.dumps(r["per_method"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
