# -*- coding: utf-8 -*-
"""检查最终回答用的到底是哪个模型（不需要加载权重，秒级）。

用法（工作目录 = 仓库根）：
    E:\\Anaconda\\python.exe scripts\\check_backend.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lawgate.env_setup import apply  # noqa: E402

apply()

from lawgate.config import (CAUSAL_MODEL_CANDIDATES, get_settings)  # noqa: E402
from pathlib import Path as _P  # noqa: E402


def _has_weights(p: _P) -> bool:
    return (p / "config.json").is_file() and any(
        (p / w).exists() for w in ("model.safetensors", "pytorch_model.bin",
                                   "model.safetensors.index.json",
                                   "pytorch_model.bin.index.json"))


s = get_settings()
api_mode = getattr(s, "llm_provider", "local") == "deepseek"
print("== 生效配置 ==")
print(f"  服务形态     = {s.llm_provider}（llm_backend={s.llm_backend}）")
if api_mode:
    print(f"  API 模型     = {s.deepseek_model}  ← **这就是最终回答模型**")
    print(f"  API 地址     = {s.deepseek_base_url}")
    print(f"  API Key      = {'已读到（不显示明文）' if s.deepseek_api_key else '⚠ 缺失，调用会 401'}")
    print(f"  思考模式     = {'开（慢、耗 token，但 logprobs 才是真实分布）' if s.deepseek_thinking else '关（默认，1–3 s 出答案）'}")
    print(f"  门控草稿来源 = {s.draft_source}")
    print(f"  草稿模型     = {s.model_label('draft')}（{s.draft_model_source}）")
    print(f"  本地兜底模型 = {s.causal_model if hasattr(s, 'causal_model') else '-'}"
          f"（切回本地用 $env:LAWGATE_LLM_PROVIDER=\"hf\"）")
print(f"  causal_model = {s.causal_model}")
print(f"  dtype        = {s.dtype}")
print(f"  device       = {s.device}")
print(f"  max_new_tokens = {s.max_new_tokens}")
print(f"  来源         = {s.model_source}")
print(f"  模型标签     = {s.model_label('causal')}")
print(f"  向量模型     = {s.embed_model}")
print(f"  硬件         = {s.hardware_note}")
print()
print("== 候选顺序与可用性（本地权重，走 API 时只是兜底）==")
for i, cand in enumerate(CAUSAL_MODEL_CANDIDATES, 1):
    p = _P(cand)
    mark = "目录存在" if p.is_dir() else "-"
    ok = "权重完整" if (p.is_dir() and _has_weights(p)) else "无权重"
    hit = " ← 本地兜底" if cand == s.causal_model else ""
    print(f"  {i}. {cand:38s} {mark:8s} {ok}{hit}")

print()
print("== 结论 ==")
if api_mode:
    print(f"  最终回答将由 DeepSeek API 的 {s.deepseek_model} 生成"
          f"（{s.deepseek_base_url}，思考模式 {'开' if s.deepseek_thinking else '关'}）")
    if not s.deepseek_api_key:
        print("  ⚠ 没读到 DEEPSEEK_API_KEY：请在 .env 填好，或用 "
              "$env:DEEPSEEK_API_KEY=\"sk-...\" 临时设")
    print("  连通性/流式/门控真机验证：E:\\Anaconda\\python.exe scripts\\check_deepseek.py")
    print("  切回本机权重：$env:LAWGATE_LLM_PROVIDER=\"hf\"（再用本脚本确认）")
else:
    cand = _P(s.causal_model)
    if cand.is_dir() and _has_weights(cand):
        n_bin = len(list(cand.glob("pytorch_model-*.bin")))
        print(f"  最终回答将由本地模型 {s.model_label('causal')} 生成"
              f"（{n_bin} 个权重分片，精度 {s.dtype}）")
    else:
        print(f"  ⚠ {s.causal_model} 不可用，将退回 HF 缓存 / 规则后端，请检查 models/ 目录")
