# -*- coding: utf-8 -*-
"""启动**单个**演示服务（供 启动服务.ps1 以子进程方式调用）。

为什么不把启动逻辑直接写在 启动服务.ps1 里：
PowerShell 的 `Start-Job` 子进程**不继承**父进程的 `$env:` 修改（实测），
所以模型/线程等环境变量必须在子进程里重新设置一遍。这里把"设环境变量 →
跑服务"固定成一个可单独运行的脚本，父子两边口径就不会漂移。

用法：
    python scripts/serve_one.py --service ui          # Gradio  → 7860
    python scripts/serve_one.py --service api         # FastAPI → 8000

环境变量：
    LAWGATE_THREADS   torch 线程数（默认 8；CPU-only 下 8 线程 ≈ 单条 10–15 s 上限）
    LAWGATE_UI_MAXTOK UI 生成的 max_new_tokens（默认 192，与 RouterOptions 默认一致）
                      —— 这是**左右两栏共用**的上限：左栏直答显式传它，右栏律核经
                      RouterOptions.max_new_tokens 拿同一个数，保证双栏对照公平。
                      想缩短演示等待可临时调小（如 80），两栏会一起变短。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

# OpenMP 双运行时：不设会直接崩（libiomp5md.dll already initialized）
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
# 本环境 huggingface_hub(httpx) 证书校验失败（D0），强制离线走本地权重
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
# 本机网络被中间层劫持，禁止 Gradio 联网统计/检查更新
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")

# .env（DEEPSEEK_* / 门控开关等）必须在 import torch / transformers **之前**读进来：
# 各库首次 import 时就会把环境变量读成模块级常量，之后再设就晚了（见 lawgate/env_setup.py）。
from lawgate.env_setup import apply as _apply_lawgate_env  # noqa: E402

_apply_lawgate_env()


def _redirect_stdio_to_log(log_path: str) -> None:
    """把本进程的 stdout/stderr **直接接到日志文件**（而不是交给父 shell 管道）。

    为什么必须这样做（2026-09-11 实测）：
      用 PowerShell `& python ... | ForEach-Object {…} | Out-File x.log` 起 Gradio 时，
      子进程的 stdout 是一条**管道**；权重加载进度条会往里灌大量输出，管道缓冲区一满就
      阻塞写入 —— 结果 Gradio 的 `launch()` 卡在建服前，端口 300 s 都不监听，
      日志里只留下几行之前的 print。手工直接跑（stdout 是控制台）则 6 s 就监听。
      父 shell 又按行拉取、还要逐行做正则过滤，只会让这个背压更严重。

      故这里在服务自身进程内用 os.dup2 把 fd 1/2 换成文件句柄：管道里再无数据，
      既不会被背压卡住，日志也由本进程以 UTF-8 写出（父 shell 的编码不再参与）。
    """
    p = Path(log_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    if fd > 2:
        os.close(fd)
    sys.stdout = os.fdopen(1, "w", encoding="utf-8", errors="replace", buffering=1)
    sys.stderr = os.fdopen(2, "w", encoding="utf-8", errors="replace", buffering=1)


def _model_banner() -> str:
    """启动时说明"今天最终回答用的是哪个模型"（不加载权重，秒级）。

    为什么值得单独打印：本项目历史上出现过"配置写着 1.5B、实际生效 0.5B"的口径漂移
    （docs/deviations.md D21、D29），后来又整体换到 DeepSeek API（D30）。这里把
    **实际生效路径 + 代价**印在服务日志开头，现场答辩/复现时一眼可查，不必再翻配置文件。
    """
    try:
        from lawgate.config import get_settings

        s = get_settings()
        if getattr(s, "llm_provider", "local") == "deepseek":
            key_state = "已读到" if s.deepseek_api_key else "**缺失**（调用会 401）"
            return (f"[serve_one] 最终回答模型 = {s.deepseek_model}"
                    f"（DeepSeek 官方 API：{s.deepseek_base_url}）"
                    f"　思考模式={'开' if s.deepseek_thinking else '关'}"
                    f"　API Key {key_state}"
                    f"　门控草稿来源={s.draft_source}（{s.model_label('draft')}）"
                    "　（关思考下单条 192 token 约 1–3 s）")
        name = s.model_label("causal")
        note = ""
        big = "fuzi" in name.lower() or "chatglm" in name.lower()
        if big:
            note = ("　（6.7B 司法模型；fp16 常驻约 12.5 GB，CPU 生成约 1 token/s —— "
                    "192 token 单栏约 2–4 分钟；演示可用 -UiMaxTokens 80 缩短）")
        return (f"[serve_one] 最终回答模型 = {name}（{s.causal_model}）"
                f"　精度={s.dtype}　来源={s.model_source}{note}")
    except Exception as exc:  # noqa: BLE001
        return f"[serve_one] 模型信息获取失败：{type(exc).__name__}: {exc}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--service", choices=["ui", "api"], required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--log-file", default=None,
                    help="把本进程 stdout/stderr 直接写到该文件（避免父 shell 管道背压）")
    args = ap.parse_args()

    if args.log_file:
        _redirect_stdio_to_log(args.log_file)

    # 控制台/管道是 GBK，中文模型输出会 UnicodeEncodeError：显式 reconfigure
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass

    threads = int(os.environ.get("LAWGATE_THREADS") or 8)
    import torch

    torch.set_num_threads(threads)
    print(f"[serve_one] service={args.service} torch_threads={threads} "
          f"cwd={ROOT} pid={os.getpid()}", flush=True)
    print(_model_banner(), flush=True)

    if args.service == "ui":
        max_tok = int(os.environ.get("LAWGATE_UI_MAXTOK") or 192)
        # 通过 configs/base.yaml 覆盖不了运行期参数，这里显式改 Settings：
        # 默认 192 = RouterOptions 的默认上限（两栏口径一致）。
        # 左栏直答读它；右栏律核由 ui.ensure_loaded() 把同一个数写进
        # RouterOptions.max_new_tokens（见 lawgate/api/ui.py），故两栏必然同长。
        from lawgate.config import get_settings

        get_settings().max_new_tokens = max_tok
        print(f"[serve_one] ui max_new_tokens={max_tok}"
              f"（左栏直答与右栏律核共用同一上限）", flush=True)
        import gradio as gr

        from lawgate.api.ui import build_demo

        build_demo().launch(server_name=args.host,
                            server_port=args.port or 7860,
                            share=False, show_error=True,
                            inbrowser=False)
        return 0

    import uvicorn

    from lawgate.api.app import app

    uvicorn.run(app, host=args.host, port=args.port or 8000,
                log_level="info", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
