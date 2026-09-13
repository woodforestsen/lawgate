# -*- coding: utf-8 -*-
"""评测子包（manual S4）。

包含：

* :mod:`lawgate.eval.benchmark` —— 确定性评测集构建器与校验器（本模块交付物）；
* 其余模块（io/metrics/stats/baselines/plotting/run_exp 等）由同目录其他任务提供，
  这里用惰性 ``__getattr__`` 转发，避免子包内相互 import 造成循环依赖。

用法::

    python -m lawgate.eval.benchmark --seed 42
"""
from __future__ import annotations

import importlib
from typing import Any

__all__ = ["benchmark"]

_CANDIDATE_SUBMODULES = (
    "benchmark", "io", "metrics", "stats", "baselines", "plotting", "run_exp",
)


def __getattr__(name: str) -> Any:
    """惰性转发子模块（``lawgate.eval.metrics`` 等按需导入）。"""
    if name in _CANDIDATE_SUBMODULES:
        module = importlib.import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
