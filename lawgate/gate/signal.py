# -*- coding: utf-8 -*-
"""门控不确定性信号（手册 S3.6）。

== 两处必须修正的定义（docs/deviations.md D11 / D18）==

D11（方向）：手册写 ``margin_from_logprobs`` 取"前两个 token 的 top-1 logprob 之差"，
路由判据却是 ``u > τ_b → 检索``，E0 又期望 u 能预测 ``need_retrieval``：
只有当 u 是"**不确定性**"（越大越该检索）时，``u > τ`` 与 AUC > 0.5 才同时成立。
故本模块把 u 统一为不确定性分数，并额外提供 ``*_confidence`` 版本供 E0 对照。

D18（量纲）：手册的阈值网格是 ``GRID = 0.01 … 0.50``，隐含假设 u ∈ [0,1]。
但最朴素的 margin 信号 ``u = −mean(top1−top2)`` 实测取值在 **−4.0 … −1.8**
（模型对 top-1 通常远自信于 top-2，故 top1−top2 ∈ [1.8, 4.0]）。
沿用固定网格的后果已实测复现：``u > τ`` 恒为假，**通道 C 永不触发，rr = 0.0**。
因此本模块把四个信号都映射到 **[0,1] 有界区间**，且**不引入可调常数**：

    margin   : u = exp(−mean(top1 − top2))            ∈ (0, 1]
               自信(margin=3)→0.05；犹豫(margin=0.2)→0.82
    entropy  : u = mean(H(top-k)) / log(k)            ∈ [0, 1]
    variance : u = V/(1+V)，V = Var(top1 logprob)      ∈ [0, 1)
    neglogp  : u = N/(1+N)，N = −mean(top1 logprob)    ∈ [0, 1)   （N ≥ 0）

聚合位置默认前 m=8 个草稿 token（手册 S3.5 亦建议取前段）。
``compute_raw`` 输出未映射的原始量，供 E0 诊断与论文附录取用。
"""
from __future__ import annotations

import math
from typing import Callable

import numpy as np

DEFAULT_M = 8


def _positions(stats, m: int) -> list[list[tuple[float, str]]]:
    lps = getattr(stats, "logprobs", stats)
    return list(lps[:m]) if m else list(lps)


# ------------------------------------------------------------ 原始量（无映射）
def raw_mean_margin(stats, m: int = DEFAULT_M) -> float:
    """mean(top1 − top2)，≥0 表示 top-1 更自信；越大越自信。"""
    vals = [p[0][0] - p[1][0] for p in _positions(stats, m) if len(p) >= 2]
    return float(np.mean(vals)) if vals else 0.0


def raw_mean_entropy(stats, m: int = DEFAULT_M) -> float:
    ents = []
    for p in _positions(stats, m):
        lp = np.asarray([x[0] for x in p], dtype=np.float64)
        if lp.size == 0:
            continue
        w = np.exp(lp - lp.max())
        w = w / w.sum()
        ents.append(float(-(w * np.log(w + 1e-12)).sum()))
    return float(np.mean(ents)) if ents else 0.0


def raw_variance(stats, m: int = DEFAULT_M) -> float:
    top1 = [p[0][0] for p in _positions(stats, m) if p]
    return float(np.var(np.asarray(top1, dtype=np.float64))) if len(top1) >= 2 else 0.0


def raw_neglogp(stats, m: int = DEFAULT_M) -> float:
    top1 = [p[0][0] for p in _positions(stats, m) if p]
    return float(-np.mean(top1)) if top1 else 0.0


# ------------------------------------------------------------ 有界不确定性 u
def margin_uncertainty(stats, m: int = DEFAULT_M) -> float:
    return float(math.exp(-raw_mean_margin(stats, m)))


def entropy_uncertainty(stats, m: int = DEFAULT_M) -> float:
    pos = _positions(stats, m)
    k = max((len(p) for p in pos), default=0)
    if k <= 1:
        return 0.0
    return float(raw_mean_entropy(stats, m) / math.log(k))


def variance_uncertainty(stats, m: int = DEFAULT_M) -> float:
    v = max(raw_variance(stats, m), 0.0)
    return float(v / (1.0 + v))


def neglogp_uncertainty(stats, m: int = DEFAULT_M) -> float:
    n = max(raw_neglogp(stats, m), 0.0)
    return float(n / (1.0 + n))


def margin_confidence(stats, m: int = DEFAULT_M) -> float:
    """对照用：置信度 = 1 − u。"""
    return 1.0 - margin_uncertainty(stats, m)


def neglogp_confidence(stats, m: int = DEFAULT_M) -> float:
    return 1.0 - neglogp_uncertainty(stats, m)


# ---- 官方信号表（u = 不确定性 ∈ [0,1]，越大越该检索）--------------------
SIGNALS: dict[str, Callable[..., float]] = {
    "margin": margin_uncertainty,
    "entropy": entropy_uncertainty,
    "variance": variance_uncertainty,
    "neglogp": neglogp_uncertainty,
}

# ---- 反向（置信度）版本，仅供 E0 诊断对照 -------------------------------
SIGNALS_CONFIDENCE: dict[str, Callable[..., float]] = {
    "margin_conf": margin_confidence,
    "neglogp_conf": neglogp_confidence,
    "raw_margin": raw_mean_margin,
}

RAW_SIGNALS: dict[str, Callable[..., float]] = {
    "raw_margin": raw_mean_margin,
    "raw_entropy": raw_mean_entropy,
    "raw_variance": raw_variance,
    "raw_neglogp": raw_neglogp,
}


def compute_all(stats, m: int = DEFAULT_M) -> dict[str, float]:
    """返回全部 [0,1] 信号 + 全部原始量（E0 一次遍历即得）。"""
    out = {k: float(fn(stats, m)) for k, fn in SIGNALS.items()}
    out.update({k: float(fn(stats, m)) for k, fn in RAW_SIGNALS.items()})
    out["margin_confidence"] = float(margin_confidence(stats, m))
    return out


def normalize_signal(name: str) -> str:
    alias = {"var": "variance", "margin": "margin", "entropy": "entropy",
             "neglogp": "neglogp", "nll": "neglogp", "conf_margin": "margin_conf"}
    return alias.get(name, name)


# ---------------------------------------------------------------- 兼容旧签名
def entropy_from_logprobs(logprobs, k: int = 20) -> float:
    """兼容手册 S3.6 旧签名：logprobs 为 [(lp, tok), ...]，返回归一化熵。"""
    lps = [l for l, _ in list(logprobs)[:k]]
    if not lps:
        return 0.0
    w = np.exp(np.asarray(lps, dtype=np.float64))
    w = w / w.sum()
    return float(-(w * np.log(w + 1e-12)).sum() / math.log(max(len(lps), 2)))


def margin_from_logprobs(lp_top2, m: int = DEFAULT_M) -> float:
    """兼容手册旧签名；返回**不确定性** u = exp(−平均裕度)。"""

    class _S:
        logprobs = lp_top2

    return margin_uncertainty(_S(), m)


def token_entropy(P) -> float:
    P = np.asarray(P, dtype=np.float64)
    P = P / max(P.sum(), 1e-12)
    return float(-(P * np.log(P + 1e-12)).sum())
