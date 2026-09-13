# -*- coding: utf-8 -*-
"""统计检验（手册 S6.10）。

  * ``paired_bootstrap``：逐条 0/1 正确向量的配对 bootstrap（n=10000）；
  * ``wilcoxon``：配对符号秩检验；
  * ``cohens_d``：效应量；
  * ``holm_bonferroni``：多重比较校正（手册只要求报 p 值，但多个方法两两比较时
    不校正会夸大显著性，故一并提供）。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def paired_bootstrap(a, b, n: int = 10000, seed: int = 0) -> dict:
    """配对 bootstrap：a、b 为逐条 0/1 正确向量（顺序必须一一对应）。

    返回 mean_diff 及其 95% CI 与近似双侧 p 值。
    """
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    if a.shape != b.shape:
        raise ValueError(f"配对向量长度不一致：{a.shape} vs {b.shape}")
    if a.size == 0:
        return {"mean_diff": 0.0, "ci95": (0.0, 0.0), "p": 1.0, "n": 0}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, a.size, size=(n, a.size))
    d = a[idx] - b[idx]
    means = d.mean(axis=1)
    return {
        "mean_diff": float(means.mean()),
        "ci95": (float(np.percentile(means, 2.5)),
                 float(np.percentile(means, 97.5))),
        "p": float((means <= 0).mean() * 2 if means.mean() > 0
                   else (means >= 0).mean() * 2),
        "n": int(a.size),
        "n_boot": int(n),
    }


def wilcoxon(a, b) -> dict:
    from scipy import stats

    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    d = a - b
    if a.size == 0 or np.allclose(d, 0):
        return {"statistic": None, "p": 1.0,
                "note": "全零差异或空样本，Wilcoxon 不适用"}
    try:
        r = stats.wilcoxon(a, b, zero_method="wilcox")
        return {"statistic": float(r.statistic), "p": float(r.pvalue),
                "note": ""}
    except Exception as exc:  # noqa: BLE001
        return {"statistic": None, "p": 1.0, "note": f"失败：{exc}"}


def cohens_d(a, b) -> float:
    """配对 Cohen's d（用差值的标准差标准化）。"""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    d = a - b
    if d.size < 2:
        return 0.0
    sd = d.std(ddof=1)
    return float(d.mean() / sd) if sd > 0 else 0.0


def holm_bonferroni(pvals: dict[str, float], alpha: float = 0.05) -> dict[str, dict]:
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    out: dict[str, dict] = {}
    prev_reject = True
    for i, (k, p) in enumerate(items):
        thr = alpha / (m - i)
        reject = prev_reject and (p <= thr)
        if not reject:
            prev_reject = False
        out[k] = {"p": p, "threshold": round(thr, 6),
                  "reject_h0": bool(reject)}
    return out


@dataclass
class CompareReport:
    method_a: str
    method_b: str
    bootstrap: dict
    wilcoxon: dict
    d: float
    n: int

    def to_dict(self) -> dict:
        return {"method_a": self.method_a, "method_b": self.method_b,
                "n": self.n,
                "mean_diff": round(self.bootstrap.get("mean_diff", 0.0), 4),
                "ci95": [round(x, 4) for x in self.bootstrap.get("ci95", (0, 0))],
                "p_bootstrap": round(self.bootstrap.get("p", 1.0), 5),
                "p_wilcoxon": round(self.wilcoxon.get("p", 1.0), 5),
                "cohens_d": round(self.d, 4),
                "wilcoxon_note": self.wilcoxon.get("note", "")}


def compare(name_a: str, vec_a, name_b: str, vec_b,
            n_boot: int = 10000, seed: int = 0) -> CompareReport:
    bs = paired_bootstrap(vec_a, vec_b, n=n_boot, seed=seed)
    wx = wilcoxon(vec_a, vec_b)
    return CompareReport(name_a, name_b, bs, wx, cohens_d(vec_a, vec_b),
                         n=int(min(len(vec_a), len(vec_b))))


def mean_std(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    arr = np.asarray(values, dtype=np.float64)
    return float(arr.mean()), float(arr.std(ddof=1)) if arr.size > 1 else 0.0
