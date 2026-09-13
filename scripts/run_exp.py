# -*- coding: utf-8 -*-
"""统一实验入口（手册 S6.1）。

    python scripts/run_exp.py --exp e1 --method legalgate --seed 0 --split test
    python scripts/run_exp.py --exp e1 --method alwaysrag --seed 0 --split test --limit 20
    python scripts/run_exp.py --exp e1 --method legalgate --seed 0 --split test \
        --shard 2 --nshards 6          # 并行分片（配合多个独立进程）
    python scripts/run_exp.py --exp e1 --merge --method legalgate --seed 0 --split test
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

from lawgate.cache import get_cache  # noqa: E402
from lawgate.eval.run_exp import (  # noqa: E402
    collect_summaries,
    merge_shards,
    run_experiment,
)
from lawgate.router import RouterOptions  # noqa: E402

METHODS = ["neverrag", "alwaysrag", "targ", "complexity", "legal_llm", "legalgate"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", default="e1")
    ap.add_argument("--method", default="legalgate",
                    help=f"逗号分隔或 all；可选 {METHODS}")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--split", default="test")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--nshards", type=int, default=1)
    ap.add_argument("--tag", default="")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--no-answer", action="store_true",
                    help="不保存原始答案（减小文件体积）")
    ap.add_argument("--threads", type=int, default=None,
                    help="torch 线程数（单进程内 intra-op）")
    ap.add_argument("--workers", type=int, default=1,
                    help="同进程内的并发条目数（线程池；torch 计算期释放 GIL）")
    ap.add_argument("--progress-every", type=int, default=20)
    # 消融开关（手册 S6.6）
    ap.add_argument("--signal", default="margin",
                    choices=["margin", "entropy", "variance", "neglogp"])
    ap.add_argument("--k-draft", type=int, default=20)
    ap.add_argument("--max-tokens", type=int, default=128)
    ap.add_argument("--top-k", type=int, default=8)
    ap.add_argument("--no-rerank", action="store_true")
    ap.add_argument("--disable-channel-b", action="store_true")
    ap.add_argument("--single-tau", action="store_true")
    ap.add_argument("--tau-single", type=float, default=0.10)
    ap.add_argument("--tau-scale", type=float, default=1.0)
    ap.add_argument("--router-mode", default="hybrid",
                    choices=["hybrid", "signal", "complexity"],
                    help="hybrid=b2用神经信号/其余桶用复杂度；signal=全用神经信号"
                         "（E0红灯对照）；complexity=全用复杂度（手册风险表降级方案）")
    ap.add_argument("--cache-stats", action="store_true")
    args = ap.parse_args()

    if args.cache_stats:
        print(json.dumps(get_cache().stats(), ensure_ascii=False, indent=2))
        return 0

    methods = METHODS if args.method == "all" else \
        [m.strip() for m in args.method.split(",") if m.strip()]

    if args.merge:
        for m in methods:
            p = merge_shards(args.out_dir or f"results/{args.exp}", m, args.seed,
                             args.split, args.tag)
            print(f"merged {m}: {p}")
        for r in collect_summaries(Path(args.out_dir or f"results/{args.exp}")):
            print(json.dumps(r, ensure_ascii=False))
        return 0

    options = RouterOptions(
        signal=args.signal, k_draft=args.k_draft, max_new_tokens=args.max_tokens,
        top_k=args.top_k, rerank=not args.no_rerank,
        disable_channel_b=args.disable_channel_b, single_tau=args.single_tau,
        tau_single=args.tau_single, tau_scale=args.tau_scale,
        router_mode=args.router_mode)

    for m in methods:
        print(f"[exp {args.exp}] method={m} seed={args.seed} split={args.split} "
              f"shard={args.shard}/{args.nshards} limit={args.limit}", flush=True)
        recs, path = run_experiment(
            exp=args.exp, method_name=m, seed=args.seed, split=args.split,
            out_dir=args.out_dir, limit=args.limit, shard=args.shard,
            nshards=args.nshards, overwrite=args.overwrite, tag=args.tag,
            save_answer=not args.no_answer, max_tokens=args.max_tokens,
            top_k=args.top_k, rerank=not args.no_rerank, k_draft=args.k_draft,
            signal=args.signal, tau_single=args.tau_single,
            tau_scale=args.tau_scale, options=options, threads=args.threads,
            workers=args.workers,
            progress_every=args.progress_every)
        from lawgate.eval.metrics import aggregate
        s = aggregate(recs)
        print(f"  -> {path}")
        print(f"  acc={s['acc']} rr={s['rr']} lac={s['lac']} "
              f"p50={s['p50_ms']}ms p95={s['p95_ms']}ms tok={s['mean_tokens']} "
              f"n={s['n']}", flush=True)
    print(json.dumps(get_cache().stats(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
