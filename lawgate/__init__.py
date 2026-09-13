# -*- coding: utf-8 -*-
"""CA-LegalGate（律核）——三通道异构法律问答路由系统。

在任何 torch / transformers 导入之前统一修正本机环境（幂等，见 ``lawgate.env_setup``）：
  * Anaconda MKL 与 torch 同时链接 libiomp5md.dll → ``KMP_DUPLICATE_LIB_OK``；
  * 远程代码模型的动态模块缓存指到仓库内（``HF_MODULES_CACHE``），
    否则会写到全局缓存目录而不可写/不可复现。
"""
from __future__ import annotations

from lawgate.env_setup import apply as _apply_env

_apply_env()

__version__ = "0.1.0"
__all__ = ["__version__"]
