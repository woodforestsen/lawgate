# -*- coding: utf-8 -*-
"""门控草稿的**来源链**（D30）——回答模型换成 API 后，u 从哪来。

为什么需要这一层
----------------
门控信号 u 的定义是"模型自己对这段回答有多不确定"，靠的是**token 级 logprob 分布**
（``lawgate/gate/signal.py``）。历史上 u 与回答来自同一个本地模型，所以 router 里
直接 ``self.llm.draft_logprobs(...)`` 就够了。

但把回答换成 DeepSeek API 之后，这条直路不成立了：API 的 logprobs 只在**思考模式**
下是真实分布（关思考时是 0.0 / -9999 占位符，见 ``deepseek_llm`` 模块文档）。
于是 u 的来源变成一个**可选项**，必须显式记录、可复现、可解释：

    local → 本机草稿模型（现行 = base.yaml 的 draft_model: models/Qwen3-4B，D38；
            历史上是 HF 缓存里的 Qwen2.5-0.5B）取 logprobs
    api   → 回答模型自己的 logprobs（思考模式），语义最贴"模型的不确定性"
    rule  → 无任何模型时的确定性伪分布（ExtractiveLLM），**只是让链路能跑通**

**为什么默认顺序是 local → api（实测证据，2026-09-13）**
------------------------------------------------------
真机测了同一组问题在两个草稿源上的 u（``scripts/check_deepseek.py --live
--with-local-draft``，原始输出在 docs/check_deepseek_gate.txt）：

    查询                                  u(api 草稿)   u(本机 0.5B 草稿)
    什么是离婚冷静期？                        0.0000          0.0161
    《合同法》第52条规定哪些情形合同无效？       0.0001          0.0263
    （2022）沪01民终12345号 这个案子是…        0.0012          0.3632
    担保法现在还有用吗？                      0.0000          0.0790

API 的 logprobs **格式上完全合法**（是真实分布，不是占位符），但它给的是
**思考（reasoning）前缀**的分布，而那段前缀几乎逐字固定（"我们需要回答用户…"），
模型在上面极度自信：平均裕度 top1−top2 ≈ 9–11 nats，而 u = exp(−裕度) 只有 1e-4 量级。
后果是 u 在所有问题上都贴着 0，门控信号**没有区分度**——这不是"取不到信号"，
而是"取到了但塌缩了"，比报错更隐蔽，因此必须写清楚而不是悄悄照用。

本机 0.5B 草稿的 u 有量级差异（0.016 → 0.363，案号题最高），与"案号/时效类问题
更需要检索"的直觉一致，因此作为默认。

注意（重要偏差，见 docs/deviations.md D30 / D38）：**阈值 τ_b 是在更早的本地草稿模型上
校准的**（configs/thresholds.json：b1=0.29 / b2=1.0 / b3=1.0 / b4=1.0）。
换草稿模型会改变 u 的分布尺度，换源之后必须重新看一遍 u 的分布
（``scripts/check_draft_u.py`` 会打印现行草稿源上的 u 分布表），
不能默认"阈值照旧仍然有效"。

另外记清 **D38 现行配置下"来源链"并不生效**：base.yaml 把 causal/draft 都钉在
``models/Qwen3-4B``，于是 ``build_draft_source`` 走第一条分支，直接返回
``answer_model`` 单来源（草稿 = 回答模型本身，手册 S3.5 原设计）。上面这张
local → api 的对照表只在"回答模型切成 DeepSeek API"时才用得上。
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from lawgate.channel.llm_base import BaseLLM, DraftStats

# 允许的来源名（也用于 --draft-source 之类的显式覆盖）
SOURCES = ("api", "local", "rule")


class LazySource:
    """惰性构造的草稿来源：第一次真正用到时才加载（0.5B 也要 ~2 GB 内存，
    现行的 Qwen3-4B fp16 则要 ~8 GB —— 所以"何时加载"这件事必须显式可见）。"""

    def __init__(self, source: str, factory: Callable[[], BaseLLM],
                 note: str = "") -> None:
        self.source = source
        self._factory = factory
        self.note = note
        self._llm: BaseLLM | None = None
        self._error: str | None = None
        self._lock = threading.Lock()

    def get(self) -> BaseLLM:
        if self._llm is not None:
            return self._llm
        if self._error is not None:
            raise RuntimeError(self._error)
        with self._lock:
            if self._llm is None and self._error is None:
                try:
                    self._llm = self._factory()
                except Exception as exc:  # noqa: BLE001 — 加载失败要变成"换源"而不是崩溃
                    self._error = f"{type(exc).__name__}: {exc}"
                    raise
        return self._llm

    @property
    def loaded(self) -> bool:
        return self._llm is not None

    @property
    def error(self) -> str | None:
        return self._error


class DraftSourceChain:
    """按顺序尝试的草稿来源链（接口与 ``BaseLLM`` 的草稿部分兼容）。"""

    backend = "draft-chain"

    def __init__(self, sources: list[LazySource], k_default: int = 20,
                 name: str = "draft-chain") -> None:
        self.sources = list(sources)
        self.k_default = int(k_default)
        self.name = name
        self.backend = "draft-chain"
        # 统计：每条来源成功/失败次数（跑完一轮评测能一眼看出到底用了谁）
        self.stats: dict[str, dict] = {}

    # ------------------------------------------------------------------ 主入口
    def draft_logprobs(self, query: str, history: list | None = None,
                       k: int | None = None) -> DraftStats:
        kk = int(k or self.k_default)
        attempts: list[dict] = []
        for src in self.sources:
            t0 = time.time()
            try:
                llm = src.get()
                stats = llm.draft_logprobs(query, history, k=kk)
                stats.source = stats.source or src.source
                stats.attempts = attempts + [{
                    "source": src.source, "ok": True,
                    "seconds": round(time.time() - t0, 3), "note": src.note}]
                self._bump(src.source, True)
                return stats
            except Exception as exc:  # noqa: BLE001 — 换源是**预期路径**，不是异常崩溃
                msg = f"{type(exc).__name__}: {exc}"
                attempts.append({"source": src.source, "ok": False,
                                 "seconds": round(time.time() - t0, 3),
                                 "error": msg, "note": src.note})
                self._bump(src.source, False)
                print(f"[draft] 草稿来源 {src.source} 不可用，换下一条：{msg}")
        # 所有来源都不可用：用确定性伪分布兜底（**不是**神经信号，trace 会写明 source=rule）
        from lawgate.channel.llm_base import ExtractiveLLM

        stats = ExtractiveLLM().draft_logprobs(query, history, k=kk)
        stats.source = "rule"
        stats.attempts = attempts + [{"source": "rule", "ok": True,
                                      "note": "无可用模型，退回确定性伪分布"}]
        self._bump("rule", True)
        return stats

    def _bump(self, source: str, ok: bool) -> None:
        d = self.stats.setdefault(source, {"ok": 0, "fail": 0})
        d["ok" if ok else "fail"] += 1

    # ------------------------------------------------------------------ 溯源
    def describe(self) -> dict:
        used = [s.source for s in self.sources]
        return {
            "draft_backend": self.backend,
            "draft_chain": used,
            "draft_stats": {k: dict(v) for k, v in self.stats.items()},
            "draft_loaded": {s.source: s.loaded for s in self.sources},
            "draft_load_errors": {s.source: s.error for s in self.sources if s.error},
        }

    def generate(self, *a, **kw):        # pragma: no cover - 门控链不负责生成
        raise NotImplementedError("DraftSourceChain 只提供 draft_logprobs()")

    def build_prompt(self, *a, **kw):    # pragma: no cover
        raise NotImplementedError

    def gen_dtype(self) -> str:
        return "n/a"


def build_draft_source(llm: BaseLLM, settings=None, threads: int | None = None,
                       k_default: int = 20) -> DraftSourceChain:
    """按配置构造草稿来源链。

    ``settings.draft_source``：
      ``local`` 只用本机小模型（D30 起的默认，理由见本模块文档的实测对照表）
      ``api``   只用回答模型的 API logprobs（要求回答模型本身是 API 后端）
      ``none``  不用任何模型（只剩确定性复杂度评分；u_signal 退化为伪分布）
      ``auto``  local → api →（兜底 rule）：本机模型不可用（换台机器没有缓存）时，
                宁可退到 API 草稿，也不要用规则伪分布冒充神经信号

    ⚠ 但下面第一条分支优先于以上四项（D38 现行配置就走这条）：只要**回答模型不是
    API 后端**（llm_backend=hf，即 base.yaml 的 causal_model: models/Qwen3-4B），
    草稿就直接复用回答模型本身，draft_source 这个开关根本不参与决策。
    """
    from lawgate.config import get_settings

    s = settings or get_settings()
    mode = (getattr(s, "draft_source", "auto") or "auto").strip().lower()
    is_api = getattr(llm, "backend", "") == "deepseek"
    model_path = getattr(s, "draft_model", "") or ""

    # 回答模型本身就是本地模型（hf / rule）：草稿直接用它——这正是手册 S3.5 的原设计
    # （草稿与正式生成共享同一模型与前缀，prefill 成本不翻倍），也保证了
    # "离线自检脚本不加载任何额外权重"这条既有承诺继续成立。
    # 只有换成 API 回答模型之后，才需要另找草稿来源。
    if not is_api and mode != "none":
        return DraftSourceChain(
            [LazySource("answer_model", lambda: llm, note="本地回答模型自取草稿")],
            k_default=k_default)

    srcs: list[LazySource] = []

    def _api_factory() -> BaseLLM:
        # 同一后端、同一条连接（草稿用的是"思考模式 + logprobs"，语义词条见模块文档）
        if not is_api:
            raise RuntimeError("回答模型不是 API 后端，无法用它取 API logprobs")
        return llm

    def _local_factory() -> BaseLLM:
        if not model_path:
            raise RuntimeError("未配置本地草稿模型（LAWGATE_DRAFT_MODEL / base.yaml）")
        from lawgate.channel.llm_base import HFLLM

        # dtype 必须跟 base.yaml 走，**不能写死 auto**：auto 在 CPU 上落成 fp32，
        # 对 model_path 指向的 4B 级模型就是 ~16 GB 内存（D38 起草稿默认就是
        # models/Qwen3-4B），float16 只要 ~8 GB。写死 auto 会在"回答切成 API"这条
        # 路径上悄悄把内存翻倍。
        # thinking 同理必须显式传 False：Qwen3 模板不传 enable_thinking = 开思考，
        # 草稿只取前 k 个 token 的分布，若落在 reasoning 前缀上信号会整体失真（D38）。
        return HFLLM(model_path, device=s.device, max_new_tokens=8,
                     dtype=getattr(s, "dtype", None) or "auto",
                     thinking=bool(getattr(s, "local_thinking", False)),
                     threads=threads)

    def _add_local() -> None:
        if model_path:
            srcs.append(LazySource("local", _local_factory,
                                   note=f"本机草稿模型 {model_path}"))
        else:
            print("[draft] 没有可用的本地草稿模型（LAWGATE_DRAFT_MODEL / "
                  "models/ / HF 缓存都没找到）")

    def _add_api() -> None:
        if is_api:
            srcs.append(LazySource("api", _api_factory,
                                   note="回答模型自身的 logprobs（思考模式）"))

    if mode == "local":
        _add_local()
    elif mode == "api":
        _add_api()
    elif mode == "none":
        srcs = []
    else:
        if mode not in SOURCES + ("auto",):
            print(f"[draft] 未知 draft_source={mode!r}（可选 {SOURCES + ('auto',)}），"
                  "按 auto 处理")
        _add_local()      # 先本机：u 有区分度，实测对照见模块文档
        _add_api()        # 本机模型缺失时的兜底：API 草稿总比伪分布诚实
    return DraftSourceChain(srcs, k_default=k_default)


__all__ = ["DraftSourceChain", "LazySource", "build_draft_source", "SOURCES"]
