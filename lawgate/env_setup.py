# -*- coding: utf-8 -*-
"""本仓库的环境自修复入口。

为什么必须有这个模块：
  * Anaconda 的 MKL 与 torch 会同时链接 libiomp5md.dll，不修正会直接崩；
  * huggingface_hub 在本机证书校验失败，需要强制离线走本地权重；
  * 仓库根 `.env`（DEEPSEEK_API_KEY / 门控开关等）必须在 torch / transformers
    **首次 import 之前**读进环境变量，否则各库把缺省值固化成模块级常量。

约定：
  * 幂等 —— 任何模块（lawgate/__init__、serve_one.py、eval/run_exp.py）首次 import
    时都会调用 :func:`apply`，重复调用不会报错；
  * 密钥只从环境变量 / `.env` 读，绝不写进 configs/ 或代码；
  * 不联网 —— 全部走本地。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_applied = False


def _fix_openmp() -> None:
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def _fix_hf_offline() -> None:
    # 本机 huggingface_hub(httpx) 证书校验失败（D0），强制离线。
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    # 远程代码模型的动态模块缓存指到仓库内，避免写全局缓存不可复现。
    mods = _ROOT / "data" / "kb" / "hf_modules"
    os.environ.setdefault("HF_MODULES_CACHE", str(mods))


def _fix_encoding() -> None:
    # 控制台是 GBK，中文输出会 UnicodeEncodeError
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")


def _fix_gradio() -> None:
    # 本机网络被中间层劫持，禁止 Gradio 联网统计/检查更新
    os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")


def _load_dotenv() -> None:
    """从仓库根 `.env` 读 KEY=VALUE 进 os.environ（已存在的变量优先，不覆盖）。

    只读、不写；解析失败/文件不存在都安静放过（离线演示可以没有密钥）。
    """
    env_file = _ROOT / ".env"
    if not env_file.is_file():
        return
    try:
        lines = env_file.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def apply() -> None:
    """幂等地修正一次环境。重复调用直接返回。"""
    global _applied
    if _applied:
        return
    _fix_openmp()
    _fix_hf_offline()
    _fix_encoding()
    _fix_gradio()
    _load_dotenv()
    # 仓库根进 sys.path，保证 `import lawgate` 永远解析到本地源码
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    _applied = True
