# -*- coding: utf-8 -*-
"""E2 消融实验（手册 S6.6）：4 组，每组只改一处。

| 组 | 开关 | 观测指标 |
|---|---|---|
| A1 关通道 B | ``--disable-channel-b``（法条/案号题全部走门控） | LAC/arc、TVC 下降幅度 |
| A2 单全局 τ | ``--single-tau``（用 TARG 的单 τ 替代 τ_b） | RR 上升幅度 |
| A3 信号切换 | margin / entropy / variance / neglogp | acc、RR 变化 |
| A4 草稿长度 | k = 8/16/20/32/64 | acc–延迟–token 权衡曲线 |

输出 results/e2/ablation_rows.json（含 group/variant/metrics，供 plot_ablation 使用）。

    python scripts/e2_ablation.py --group A3
    python scripts/e2_ablation.py --all
    python scripts/e2_ablation.py --shard 0 --nshards 6 --group A1
    python scripts/e2_ablation.py --merge
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
from lawgate.eval.run_exp import run_experiment  # noqa: E402

OUT = Path("results/e2")

# 每组：(variant, tag, 额外参数)
GROUPS: dict[str, list[tuple[str, str, dict]]] = {
    "A1": [("full", "full", {}),
           ("no_channel_b", "nochB", {"disable_channel_b": True})],
    "A2": [("per_bucket_tau", "perb", {}),
           ("single_tau", "single", {"single_tau": True, "tau_single": 0.30})],
    "A3": [("margin", "sig_margin", {"signal": "margin"}),
           ("entropy", "sig_entropy", {"signal": "entropy"}),
           ("variance", "sig_variance", {"signal": "variance"}),
           ("neglogp", "sig_neglogp", {"signal": "neglogp"}),
           ("complexity_only", "sig_cplx", {"router_mode": "complexity"}),
           ("signal_only", "sig_only", {"router_mode": "signal"})],
    "A4": [("k8", "k8", {"k_draft": 8}),
           ("k16", "k16", {"k_draft": 16}),
           ("k20", "k20", {"k_draft": 20}),
           ("k32", "k32", {"k_draft": 32}),
           ("k64", "k64", {"k_draft": 64})],
}

ABL_DIR = {
    "A1": "results/e2_a1", "A2": "results/e2_a2",
    "A3": "results/e2_a3", "A4": "results/e2_a4",
}

# A2 单 τ 的取值说明（main() 里按校准报告解析后覆盖；analyse 写进 payload note）
A2_TAU_NOTE = "A2 single_tau 使用脚本默认 0.30（未读到校准报告）"


def calibrated_single_tau(default: float = 0.30) -> tuple[float, str]:
    """A2 的单全局 τ 取**与 legalgate τ_b 同一份 dev 校准**出来的值。

    本文件 payload note 的原意就是"A2 的 single_tau 使用校准出的单 τ（TARG
    口径）"，但历史上代码写死 0.30；D30 换草稿源后 u 分布已变，写死值没有任何
    依据（D33 修正）。运行时读 ``results/calibrate_report.json`` 的
    ``single_tau.hybrid.tau``——即在与 A2 对照臂（per_bucket_tau）完全相同的
    hybrid 门控信号上、同一 dev_calib 校准出的单全局 τ，两臂唯一差异就是
    "单 τ vs 逐桶 τ_b"。读不到时回退 default，并在 note 里如实披露。
    """
    rep = eio.read_json(Path("results/calibrate_report.json")) or {}
    try:
        tau = float(rep["single_tau"]["hybrid"]["tau"])
    except Exception:  # noqa: BLE001
        return default, f"无法读取校准报告，回退脚本默认 {default}"
    return tau, (f"calibrate_report.single_tau.hybrid.tau"
                 f"（split={rep.get('split')}, date={rep.get('date')}）")


def run_group(g: str, shard: int, nshards: int, limit: int | None,
              split: str = "test", **rk) -> None:
    """跑一组消融。

    ``split`` 默认 ``test``（手册 S6.6 原设定，944 条）。CPU-only 预算下
    E2 改用 ``test_e1``（300 条），见 docs/deviations.md D23。

    D34 修复：``disable_channel_b`` / ``single_tau`` / ``router_mode`` 是
    ``RouterOptions`` 字段，不是 ``run_experiment`` 的形参——旧代码把它们当
    关键字参数直接透传，A1/A2/A3 的消融臂全部 ``TypeError``（且从未被测出，
    因为 E2 此前从未跑通过）。现在把变体参数装进 ``RouterOptions`` 显式传入；
    ``signal``/``k_draft``/``tau_single`` 本就是 ``run_experiment`` 形参，
    照常透传（保证 ``common``/缓存键与路由选项同源）。
    """
    from lawgate.router import RouterOptions

    ROUTER_ONLY = ("disable_channel_b", "single_tau", "router_mode")
    mt = int(rk.get("max_tokens") or 128)
    for variant, tag, kwargs in GROUPS[g]:
        kwargs = dict(kwargs)
        options = RouterOptions(
            max_new_tokens=mt,
            signal=kwargs.get("signal", "margin"),
            k_draft=int(kwargs.get("k_draft", 20)),
            tau_single=float(kwargs.get("tau_single", 0.10)),
            single_tau=bool(kwargs.get("single_tau", False)),
            disable_channel_b=bool(kwargs.get("disable_channel_b", False)),
            router_mode=str(kwargs.get("router_mode", "hybrid")),
        )
        pass_on = {k: v for k, v in kwargs.items() if k not in ROUTER_ONLY}
        recs, p = run_experiment(exp=f"e2_{g.lower()}", method_name="legalgate",
                                 seed=0, split=split, shard=shard,
                                 nshards=nshards, limit=limit, tag=tag,
                                 overwrite=True, progress_every=0,
                                 options=options,
                                 **{**rk, **pass_on})  # type: ignore[arg-type]
        print(f"  [{g}] {variant}: {len(recs)} -> {p}", flush=True)


def merge_all(split: str = "test") -> None:
    from lawgate.eval.run_exp import merge_shards

    for g in GROUPS:
        for variant, tag, _ in GROUPS[g]:
            p = merge_shards(ABL_DIR[g], "legalgate", 0, split, tag)
            print(f"[{g}] merged {variant}: {p}")


def analyse(groups: list[str] | None = None, split: str = "test") -> dict:
    groups = groups or list(GROUPS)
    rows: list[dict] = []
    detail: dict[str, list[dict]] = {}
    for g in groups:
        for variant, tag, kwargs in GROUPS[g]:
            from lawgate.eval.io import result_path

            p = result_path(ABL_DIR[g], "legalgate", 0, split, tag)
            recs = eio.load_results(p)
            if not recs:
                print(f"  [{g}] {variant}: 无结果，跳过")
                continue
            agg = aggregate(recs)
            row = {"group": g, "variant": variant, "arm": variant,
                   "n": agg["n"], "acc": agg["acc"], "rr": agg["rr"],
                   "lac": agg["lac"], "arc": agg["arc"], "tvc": agg["tvc"],
                   "p50_ms": agg["p50_ms"], "p95_ms": agg["p95_ms"],
                   "mean_tokens": agg["mean_tokens"],
                   "invalid_law_citation_rate": agg["invalid_law_citation_rate"]}
            rows.append(row)
            detail.setdefault(g, []).append({"variant": variant, "metrics": agg,
                                             "kwargs": kwargs})
    OUT.mkdir(parents=True, exist_ok=True)
    payload = {"rows": rows, "detail": detail, "split": split,
               "note": (f"{A2_TAU_NOTE}；"
                        "A3 的 signal_only 即 E0 红灯对应的纯神经信号门控，"
                        "complexity_only 即手册风险表的降级方案，"
                        "hybrid（默认）在 A1/A2/A4 的 full/per_bucket_tau 中出现。"
                        f"本表的 split={split}（D23：非手册原设定 test 全量）。")}
    eio.write_json(payload, OUT / "ablation_rows.json")
    try:
        from lawgate.eval.plotting import plot_ablation

        plot_ablation(rows, out_path="figures/e3/ablation.png",
                      provenance=eio.run_meta(), metric="lac")
    except Exception as exc:  # noqa: BLE001
        print(f"[e2] 出图失败：{type(exc).__name__}: {exc}")
    for r in rows:
        print(f"  {r['group']:3s} {r['variant']:16s} acc={r['acc']:<7} "
              f"rr={r['rr']:<7} arc={r['arc']:<6} p95={r['p95_ms']}")
    return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default=None, choices=list(GROUPS))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--nshards", type=int, default=1)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--split", default="test",
                    help="手册 S6.6 原设定为 test（944 条）；CPU-only 预算下用 "
                         "test_e1（300 条），见 docs/deviations.md D23")
    ap.add_argument("--max-tokens", type=int, default=80,
                    help="必须与 E1 一致（D19），否则消融与主对比口径不可比")
    ap.add_argument("--threads", type=int, default=None)
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--analyse-only", action="store_true")
    args = ap.parse_args()
    # D33：A2 的单全局 τ 一律解析自当前校准报告（含 --merge/--analyse-only 路径，
    # 保证跑批与事后分析用的是同一个值，且 ablation_rows.json 的 detail/note 如实留痕）
    tau_a2, tau_src = calibrated_single_tau()
    global A2_TAU_NOTE
    A2_TAU_NOTE = f"A2 的 single_tau 使用单全局 τ={tau_a2}，来源：{tau_src}"
    _a2 = list(GROUPS["A2"])
    _a2[1] = ("single_tau", "single", {"single_tau": True, "tau_single": tau_a2})
    GROUPS["A2"] = _a2
    print(f"[e2] {A2_TAU_NOTE}", flush=True)
    groups = list(GROUPS) if (args.all or not args.group) else [args.group]
    rk = {"max_tokens": args.max_tokens, "threads": args.threads}

    if args.merge:
        merge_all(args.split)
    if not args.analyse_only and not args.merge:
        for g in groups:
            run_group(g, args.shard, args.nshards, args.limit, args.split, **rk)
        if args.nshards > 1:
            return 0
    analyse(groups if not args.merge else None, args.split)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
