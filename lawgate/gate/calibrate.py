# -*- coding: utf-8 -*-
"""桶级阈值校准（手册 S3.6 / S6.4）。

== 一处必须修正的目标函数方向（docs/deviations.md D13）==
手册写"取满足 acc(τ_b) ≥ acc_AlwaysRAG − δ 的**最小** τ_b"。
但路由判据是 ``u > τ_b → 检索``：

  * τ 越小 → 超过阈值的条数越多 → **检索越多**；
  * acc(τ) 关于 τ 单调不增（假设检索有用），故可行集是 τ ≤ τ_max；
  * 该集合里的最小 τ 就是网格下界 → 检索率 100%，
    **与项目核心指标"检索调用下降 ≥40%"直接矛盾**。

本实现默认 ``rule='largest_feasible'``：在满足精度约束的前提下取**最大** τ，
即"满足精度约束的最小检索代价"工作点——这才是 Pareto 框架下的正确选择。
同时保留 ``rule='smallest_feasible'``（手册原式）供 E2/A2 消融对照，
并把两种规则的结果都写进报告，不做隐藏。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

BUCKETS: dict[str, str] = {"b1": "概念咨询", "b2": "法条查询",
                           "b3": "案例检索", "b4": "多轮追问"}

GRID: list[float] = [round(0.01 * i, 2) for i in range(1, 51)]
DELTA = 0.005


def classify_bucket(sample: dict) -> str:
    """分桶规则。优先采用基准题自带 bucket；否则按槽位/类别推导。"""
    if sample.get("bucket") in BUCKETS:
        return sample["bucket"]
    if sample.get("turn_id", 0) > 0 or sample.get("history"):
        return "b4"
    cat = sample.get("category", "")
    if cat in ("temporal_trap",) or (sample.get("slots") or {}).get("article_no"):
        return "b2"
    if cat == "provision":
        return "b2"
    if cat == "case":
        return "b3"
    return "b1"


def _acc_curve(sub: list[dict], grid: list[float]) -> dict[float, dict]:
    """对每个 τ 计算 (acc, retrieval_rate)。"""
    out: dict[float, dict] = {}
    n = max(len(sub), 1)
    for tau in grid:
        ret = [p for p in sub if p["u"] > tau]
        corr = sum(p["correct_if_retrieve"] for p in ret) + \
            sum(p["correct_if_direct"] for p in sub if p["u"] <= tau)
        out[tau] = {"acc": corr / n, "rr": len(ret) / n}
    return out


def calibrate(dev_preds: list[dict], always_rag_acc: float,
              out_path: str | Path = "configs/thresholds.json",
              delta: float = DELTA, grid: list[float] | None = None,
              rule: str = "largest_feasible") -> tuple[dict, dict]:
    """按桶求 τ_b。

    dev_preds 每条需含::
        {"bucket": "b2", "u": 0.42,
         "correct_if_retrieve": 1|0, "correct_if_direct": 1|0}
    """
    grid = grid or GRID
    target = always_rag_acc - delta
    taus: dict[str, float] = {}
    report: dict[str, dict] = {}

    for b in BUCKETS:
        sub = [p for p in dev_preds if p.get("bucket") == b]
        if not sub:
            taus[b] = 0.50
            report[b] = {"n": 0, "tau": 0.50, "feasible": False,
                         "reason": "no dev samples in bucket", "target": round(target, 4)}
            continue

        curve = _acc_curve(sub, grid)
        feasible = [t for t in grid if curve[t]["acc"] >= target]
        if feasible:
            tau = max(feasible) if rule == "largest_feasible" else min(feasible)
            chosen, is_feasible = tau, True
        else:
            # 无可行解：取最接近目标精度的工作点（同精度则取检索更少者）
            best = min(grid, key=lambda t: (abs(curve[t]["acc"] - target),
                                            -t if rule == "largest_feasible" else t))
            chosen, is_feasible = best, False

        n = max(len(sub), 1)
        acc_if_always_retrieve = sum(p["correct_if_retrieve"] for p in sub) / n
        acc_if_never_retrieve = sum(p["correct_if_direct"] for p in sub) / n

        taus[b] = round(float(chosen), 4)
        report[b] = {
            "n": len(sub),
            "tau": taus[b],
            "feasible": is_feasible,
            "rule": rule,
            "target": round(target, 4),
            "acc_at_tau": round(curve[chosen]["acc"], 4),
            "rr_at_tau": round(curve[chosen]["rr"], 4),
            "acc_if_always_retrieve": round(acc_if_always_retrieve, 4),
            "acc_if_never_retrieve": round(acc_if_never_retrieve, 4),
            "tau_min_feasible": (min(feasible) if feasible else None),
            "tau_max_feasible": (max(feasible) if feasible else None),
        }

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(taus, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return taus, report


def calibrate_single_tau(dev_preds: list[dict], always_rag_acc: float,
                         rule: str = "largest_feasible",
                         delta: float = DELTA,
                         grid: list[float] | None = None) -> tuple[float, dict]:
    """TARG 基线用的**单全局阈值**：同样目标函数，但不分桶。"""
    grid = grid or GRID
    target = always_rag_acc - delta
    if not dev_preds:
        return 0.5, {"n": 0, "feasible": False}
    curve = _acc_curve(dev_preds, grid)
    feasible = [t for t in grid if curve[t]["acc"] >= target]
    if feasible:
        tau = max(feasible) if rule == "largest_feasible" else min(feasible)
        ok = True
    else:
        tau = min(grid, key=lambda t: abs(curve[t]["acc"] - target))
        ok = False
    return round(float(tau), 4), {"n": len(dev_preds), "tau": round(float(tau), 4),
                                  "feasible": ok, "target": round(target, 4),
                                  "acc_at_tau": round(curve[tau]["acc"], 4),
                                  "rr_at_tau": round(curve[tau]["rr"], 4)}


def tau_scales(base: dict[str, float],
               scales: tuple[float, ...] = (0.5, 0.75, 1.0, 1.25),
               grid_max: float = 0.5) -> list[dict[str, float]]:
    """Pareto 头图用：对 4 个 τ_b 统一缩放，得到同一系统在 4 个工作点上的表现。"""
    out = []
    for a in scales:
        out.append({k: round(min(max(v * a, 0.0), grid_max), 4)
                    for k, v in base.items()})
    return out


def load_taus(path: str | Path = "configs/thresholds.json") -> dict[str, float]:
    p = Path(path)
    if not p.exists():
        return {b: 0.10 for b in BUCKETS}
    return json.loads(p.read_text(encoding="utf-8"))


def bucket_descriptions() -> dict[str, str]:
    return dict(BUCKETS)
