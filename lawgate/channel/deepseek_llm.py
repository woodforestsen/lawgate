# -*- coding: utf-8 -*-
"""DeepSeek 官方 API 后端（OpenAI 兼容）—— D30，D31 起改用官方 openai SDK。

为什么单独一个模块而不是塞进 ``llm_base.HFLLM``
------------------------------------------------
本地权重后端与 HTTP 后端在**三件事**上根本不同，混在一起会让两边都难读：

  1. **没有 tokenizer**：HFLLM 靠 ``apply_chat_template`` 把 messages 拼成一段
     prompt 再喂模型；API 直接吃 ``messages`` 数组。所以这里缓存键里的"prompt"
     是 **messages 的 JSON 序列化**（确定性、可审计），而不是模板文本。
  2. **没有权重**：``describe()`` 里没有精度/设备，取而代之的是 base_url、模型名、
     累计 token 用量——这几项才是"这份结果到底是谁算的"的答案。
  3. **网络会失败**：401/402/429/超时/连接被重置都必须有明确行为（重试 + 人话报错），
     绝不能让上层看到"答案莫名其妙是空的"。

== D31：HTTP 传输层换成官方 openai SDK ==
原先手写 ``requests`` + 手动 SSE 解析 + 手动重试退避 + 手动修中文编码
（SSE 无 charset 坑），这些**全部**是 openai SDK 自带的成熟能力。现改为
``openai.OpenAI(base_url=..., api_key=...)`` + ``client.chat.completions.create``：

  * 整段：``create(stream=False)``；流式：``create(stream=True)`` 逐块迭代。
  * 非标准参数（``thinking`` / ``reasoning_effort`` / ``stream_options`` /
    DeepSeek 扩展的 ``logprobs`` 字段）用 SDK 的 ``extra_body`` 透传，语义不变。
  * 401/402/429/超时 → SDK 抛 ``openai.AuthenticationError`` /
    ``InsufficientQuotaError`` / ``RateLimitError`` / ``APIConnectionError`` /
    ``APITimeoutError``，本模块统一归一成 ``DeepSeekError`` 家族（人话报错）。
  * 重试：SDK 默认 ``max_retries`` 已含指数退避；这里显式传 0，改由本模块的
    ``_request`` 层做**可控**重试（与旧行为一致：只对 429/5xx/连接/超时重试，
    且把重试次数写进 ``usage.retries``，供自检断言 ``retries >= 2``）。

**保留不变的独门逻辑（SDK 不替我们判断，留在应用层）：**
  * 思考模式参数自动降级（``thinking`` ↔ ``reasoning_effort``）：同一网关不同
    模型/版本对新参数支持不一致，"参数不被接受"应当是**降级**而不是**失败**。
  * 退化 logprobs 检测（``degenerate_reason``）：关思考时 DeepSeek 返回的
    logprobs 是占位符（全 0.0 / -9999），不能拿来算门控信号，必须识别并抛
    ``DegenerateLogprobs``，由 ``DraftSourceChain`` 换到本地源。
  * 内容寻址缓存（``GenCache``）与逐次调用台账（``call_log``，含线程名）：
    双栏 UI 并发时靠它把额度分摊回各自那一栏（原 scripts/smoke_stream.py F 段断言；
    该脚本 2026-09-16 已删除，见 docs/deviations.md D37，结论保留为历史记录）。

关键实测结论（2026-09-13 真机探测，见 docs/deviations.md D30）
--------------------------------------------------------------
* ``model="deepseek-v4-flash"`` 被接受，服务端回报 ``model="deepseek-flash"``。
* **默认是"思考模式"**：返回体里先给 ``reasoning_content``，再给 ``content``；
  关思考的开关是 ``{"thinking": {"type": "disabled"}}``（``reasoning_effort``
  亦可；字符串 "off" 会被 400 拒绝）。
* **logprobs 只在思考模式下是真实分布**；因此门控草稿（``draft_logprobs``）
  自动改用思考模式取 reasoning 前缀分布，并做退化检测。
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from lawgate.cache import GenCache, get_cache, make_key

try:  # openai 是官方 SDK；缺失时给出人话报错（本机已装 openai==2.44.0）
    import openai  # type: ignore
    from openai import OpenAI  # type: ignore

    _HAS_OPENAI = True
except Exception:  # pragma: no cover - 仅在极简环境出现
    _HAS_OPENAI = False
    OpenAI = None  # type: ignore[assignment]

# openai SDK 的异常类（D31）。注意：**逐个**导入而非整块 try——
# 不同 openai 版本的异常命名不同（如 402 可能没有专门的 QuotaError），
# 任何一个缺失都不该让其它异常归一化失效。
try:
    from openai import APIConnectionError as _SDKConnError  # type: ignore
except Exception:  # pragma: no cover
    _SDKConnError = None  # type: ignore
try:
    from openai import APITimeoutError as _SDKTimeoutError  # type: ignore
except Exception:  # pragma: no cover
    _SDKTimeoutError = None  # type: ignore
try:
    from openai import AuthenticationError as _SDKAuthError  # type: ignore
except Exception:  # pragma: no cover
    _SDKAuthError = None  # type: ignore
try:
    from openai import InternalServerError as _SDKServerError  # type: ignore
except Exception:  # pragma: no cover
    _SDKServerError = None  # type: ignore
try:
    from openai import RateLimitError as _SDKRateLimitError  # type: ignore
except Exception:  # pragma: no cover
    _SDKRateLimitError = None  # type: ignore
# 402 在多数 openai 版本里没有专属类，落到 APIStatusError（status_code==402）
try:
    from openai import APIStatusError as _SDKStatusError  # type: ignore
except Exception:  # pragma: no cover
    _SDKStatusError = None  # type: ignore

from lawgate.channel.llm_base import BaseLLM, DraftStats, PromptMixin

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
# 退化分布的判据：候选 logprob ≤ 该值即视为"占位符"（实测占位值恰为 -9999.0）
PLACEHOLDER_LOGPROB = -9998.0

# 异常类逐次解析（见上）；下面按"非 None"过滤出可用类，
# 保证个别版本缺失某类时不影响其余归一化（D31 的健壮性要点）。
def _usable(*cls) -> tuple:
    return tuple(c for c in cls if c is not None)

_RETRYABLE = _usable(_SDKRateLimitError, _SDKServerError,
                     _SDKConnError, _SDKTimeoutError)
_AUTH = _usable(_SDKAuthError)
_QUOTA = _usable(_SDKStatusError)   # 402 在多数 openai 版本落到 APIStatusError


class DeepSeekError(RuntimeError):
    """API 调用失败（网络/鉴权/配额/服务端）。消息是**给用户看的中文**。"""


class DeepSeekAuthError(DeepSeekError):
    """401/403：密钥缺失或无效。"""


class DegenerateLogprobs(DeepSeekError):
    """logprobs 是退化分布（全 0.0 / 全 -9999），不能拿来算门控信号。"""


@dataclass
class ApiUsage:
    """累计用量（成本透明：谁都能一眼看出这些答案花了多少 token）。"""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cached_prompt_tokens: int = 0
    seconds: float = 0.0
    errors: int = 0
    retries: int = 0

    def add(self, usage: dict | None, seconds: float) -> None:
        self.calls += 1
        self.seconds += float(seconds or 0.0)
        u = usage or {}
        self.prompt_tokens += int(u.get("prompt_tokens") or 0)
        self.completion_tokens += int(u.get("completion_tokens") or 0)
        self.reasoning_tokens += int(
            (u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
        self.cached_prompt_tokens += int(u.get("prompt_cache_hit_tokens") or 0)

    def to_dict(self) -> dict:
        return {
            "api_calls": self.calls,
            "api_prompt_tokens": self.prompt_tokens,
            "api_completion_tokens": self.completion_tokens,
            "api_reasoning_tokens": self.reasoning_tokens,
            "api_cached_prompt_tokens": self.cached_prompt_tokens,
            "api_seconds": round(self.seconds, 1),
            "api_errors": self.errors,
            "api_retries": self.retries,
        }


def messages_prompt(messages: list[dict]) -> str:
    """把 messages 序列化成**缓存键里的 prompt**（确定性、含角色与顺序）。"""
    return json.dumps(messages, ensure_ascii=False, sort_keys=True)


class DeepSeekLLM(PromptMixin, BaseLLM):
    """DeepSeek 官方 API（OpenAI 兼容）后端，底层用官方 openai SDK（D31）。

    参数
    ----
    model          模型名，默认 ``deepseek-v4-flash``
    api_key        密钥；缺省读 ``DEEPSEEK_API_KEY``
    base_url       服务地址，默认 ``https://api.deepseek.com``
    max_new_tokens 单次最大生成 token
    topk_logprobs  草稿 logprobs 的 top-k
    cache          内容寻址缓存
    thinking       True 打开思考模式（慢、耗 token，但 logprobs 才是真的）
    timeout        单次请求超时（秒）
    max_retries    429/5xx/超时/连接失败的重试次数（本模块可控层）
    """

    backend = "deepseek"

    def __init__(self, model: str = DEFAULT_MODEL,
                 api_key: str | None = None,
                 base_url: str | None = None,
                 max_new_tokens: int = 192, topk_logprobs: int = 20,
                 cache: GenCache | None = None, timeout: float = 120.0,
                 thinking: bool = False, max_retries: int = 3,
                 threads: int | None = None, **kw) -> None:
        if not _HAS_OPENAI:  # pragma: no cover
            raise DeepSeekError("缺少 openai 库（官方 SDK），无法调用 DeepSeek API")
        self.model = str(model or DEFAULT_MODEL)
        self.base_url = str(base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = (api_key if api_key is not None
                        else os.environ.get("DEEPSEEK_API_KEY") or "sk-unset")
        self.name = self.model
        self.max_new_tokens = int(max_new_tokens)
        self.topk = int(topk_logprobs)
        self.thinking = bool(thinking)
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.cache = cache or get_cache()
        self.usage = ApiUsage()
        # 逐次调用台账（含缓存命中的还原值）。为什么要有它：
        #   1. 成本透明——每一次调用花了多少 token、多少秒，可逐条审计；
        #   2. 双栏 UI 的两栏是**并发**的（左栏后台线程），只有"每次调用自己记账 +
        #      记线程名"才能在事后把额度分摊回各自那一栏（原 scripts/smoke_stream.py F 段
        #      断言；该脚本 2026-09-16 删除，见 docs/deviations.md D37）。
        self.call_log: list[dict] = []
        # 服务端是否认 ``thinking`` 参数：不认时自动改发 reasoning_effort（降级不失败）
        self._thinking_param = "thinking"
        # 服务端是否认 ``stream_options``：不认时去掉（流式语义不变，只是拿不到尾部 usage）
        self._use_stream_options = True
        # 线程局部 SDK 客户端（openai 的 httpx 连接池非线程安全；按线程各建一份）
        self._local = threading.local()

    # ------------------------------------------------------------ 会话/请求
    @property
    def client(self) -> "OpenAI":
        """当前线程的 openai 客户端（首次访问时惰性建立，复用连接池）。"""
        c = getattr(self._local, "client", None)
        if c is None:
            c = OpenAI(base_url=self.base_url, api_key=self.api_key,
                       timeout=self.timeout, max_retries=0)
            self._local.client = c
        return c

    # SDK 异常 → 归一化的 (error_kind, 人话消息)
    @staticmethod
    def _classify(exc: Exception) -> tuple[str, str]:
        # 401/403 鉴权（AuthenticationError 是 APIStatusError 子类，先判它）
        if _AUTH and isinstance(exc, _AUTH):
            return "auth", (f"DeepSeek API 鉴权失败：{getattr(exc, 'message', exc)}\n"
                            "→ 检查 .env 的 DEEPSEEK_API_KEY 是否为空/失效"
                            "（key 一旦出现在截图或聊天记录里就该去控制台换新）")
        # 402 余额不足：多数 openai 版本没有专属 QuotaError，靠 status_code==402 判
        if (_QUOTA and isinstance(exc, _QUOTA)
                and getattr(exc, "status_code", None) == 402):
            return "quota", f"DeepSeek 账户余额不足：{getattr(exc, 'message', exc)}"
        # 429/5xx/连接/超时 —— 值得重试
        if _RETRYABLE and isinstance(exc, _RETRYABLE):
            return "retry", f"{type(exc).__name__}: {getattr(exc, 'message', exc)}"
        return "other", f"{type(exc).__name__}: {getattr(exc, 'message', exc)}"

    def _create(self, messages: list[dict], mt: int, stream: bool,
                thinking: bool | None = None, logprobs: bool = False):
        """发一次 create（**不重试**）。返回 (response|stream, 秒, raw_usage)。

        思考/流式参数用 ``extra_body`` 透传；``stream`` 为 True 时返回的是
        SDK 的流式迭代器（调用方负责消费完并 close）。
        """
        c = self.client
        extra: dict[str, Any] = {}
        # 思考模式：同一开关的两种写法，服务端只认其一；不认的由 _request 降级
        eff = self.thinking if thinking is None else thinking
        if self._thinking_param == "reasoning_effort":
            extra["reasoning_effort"] = "high" if eff else "none"
        else:
            extra["thinking"] = {"type": "enabled" if eff else "disabled"}
        if logprobs:
            # DeepSeek 扩展：logprobs=True + top_logprobs（非 OpenAI 标准，走 extra_body）
            extra["logprobs"] = True
            extra["top_logprobs"] = int(self.topk)
        stream_options = ({"include_usage": True}
                          if (stream and self._use_stream_options) else None)
        if stream_options is not None:
            extra["stream_options"] = stream_options

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            # temperature=0：与本地后端的贪心解码口径一致（可复现、可比对）
            "temperature": 0,
            "max_tokens": mt,
            "stream": bool(stream),
        }
        if extra:
            kwargs["extra_body"] = extra

        t0 = time.time()
        return c.chat.completions.create(**kwargs), time.time() - t0, None

    def _request(self, messages: list[dict], max_tokens: int, stream: bool,
                 thinking: bool | None = None, logprobs: bool = False):
        """带重试 + 两处**只降级参数、不改语义**的兜底。

        为什么值得写这两条：同一网关上的模型/版本对新参数的支持并不一致，
        而"参数不被接受"应当是**降级**而不是**失败**：
          1. 服务端不认 ``thinking`` → 改发 ``reasoning_effort``（同一个开关的另一种写法）；
          2. 服务端不认 ``stream_options`` → 去掉它再发（流式语义不变）。
        降级是**立即重试**（不消耗重试槽位、不记 retries）——它是"换个参数写法"，
        不是"网络抖动"。网络重试口径（与旧行为一致）：只对 429/5xx/连接/超时重试，
        每次记进 ``usage.retries``，便于自检断言（flaky 场景期望 retries>=2）。
        """
        mt = int(max_tokens)
        last: str = ""
        for attempt in range(max(1, self.max_retries)):
            try:
                return self._create(messages, mt, stream=stream,
                                    thinking=thinking, logprobs=logprobs)
            except Exception as exc:  # noqa: BLE001 — 统一归一化
                kind, human = self._classify(exc)
                if kind == "auth":
                    raise DeepSeekAuthError(human) from exc
                if kind == "quota":
                    raise DeepSeekError(human) from exc
                # 参数不被接受 → **立即降级重试**（不消耗网络重试槽位）
                if kind == "other":
                    msg = str(getattr(exc, "message", "") or "")
                    if self._thinking_param == "thinking" and "thinking" in msg:
                        print("[deepseek] 服务端不接受 thinking 参数，改用 reasoning_effort 重试")
                        self._thinking_param = "reasoning_effort"
                        continue
                    if stream and "stream_options" in msg:
                        print("[deepseek] 服务端不接受 stream_options，去掉后重试（流式语义不变）")
                        self._use_stream_options = False
                        continue
                    # 其余 4xx（如 400）：直接放弃，不猜、不静默改语义
                    self.usage.errors += 1
                    raise DeepSeekError(f"DeepSeek API 错误 {human}") from exc
                # kind == "retry"（429/5xx/连接/超时）
                last = human
                self.usage.retries += 1
                if attempt < self.max_retries - 1:
                    time.sleep(min(8.0, 0.8 * (2 ** attempt)))
                continue
        self.usage.errors += 1
        raise DeepSeekError(
            f"DeepSeek API 调用失败（已重试 {self.max_retries} 次）：{last}")

    def build_prompt(self, query: str, history: list | None = None,
                     context: str | None = None) -> str:
        """缓存键里的 "prompt" = messages 的规范 JSON（API 不吃模板文本）。"""
        return messages_prompt(self.build_messages(query, history, context))

    def messages(self, query: str, history: list | None = None,
                 context: str | None = None) -> list[dict]:
        return self.build_messages(query, history, context)

    # ---------------------------------------------------------------- 生成
    def describe(self) -> dict:
        d = super().describe()
        d.update({"llm_model": self.model,
                  "llm_api_base": self.base_url,
                  "llm_thinking": self.thinking,
                  "llm_key_present": bool(self.api_key),
                  "llm_http_client": "openai-sdk"})
        d.update(self.usage.to_dict())
        return d

    def recent_calls(self, thread_prefix: str | None = None) -> list[dict]:
        """按线程名筛选调用台账（双栏 UI 里区分"左栏/右栏各花了多少"）。"""
        if thread_prefix is None:
            return list(self.call_log)
        return [c for c in self.call_log if str(c.get("thread", "")).startswith(thread_prefix)]

    def _cache_model(self) -> str:
        """缓存键里的模型标识：把思考模式也编进去（它直接改变输出）。"""
        return f"{self.model}|thinking={int(bool(self.thinking))}"

    # SDK 响应对象 → 纯 dict（content / reasoning_content / finish_reason / usage / model）
    @staticmethod
    def _from_completion(resp: Any) -> dict:
        ch = (resp.choices or [None])[0]
        msg = getattr(ch, "message", None) if ch is not None else None
        content = ""
        reasoning = ""
        finish = ""
        if msg is not None:
            content = getattr(msg, "content", "") or ""
            # reasoning_content 是 DeepSeek 扩展字段；SDK 可能放进 model_extra
            extra = getattr(msg, "model_extra", None) or {}
            reasoning = extra.get("reasoning_content", "") or ""
            finish = getattr(ch, "finish_reason", "") or ""
        return {"content": content.strip(), "reasoning": reasoning,
                "finish_reason": finish, "usage": getattr(resp, "usage", None),
                "model_raw": getattr(resp, "model", None)}

    def _log_call(self, kind: str, max_tokens: int, text: str, usage: dict | None,
                  seconds: float, cached: bool) -> None:
        """记一次调用（线程安全：list.append 在 GIL 下是原子的）。"""
        u = usage or {}
        self.call_log.append({
            "kind": kind, "model": self.model, "thinking": bool(self.thinking),
            "max_tokens": int(max_tokens),
            "thread": threading.current_thread().name,
            "cached": bool(cached),
            "prompt_tokens": int(u.get("prompt_tokens") or 0),
            "completion_tokens": int(u.get("completion_tokens") or 0),
            "seconds": round(float(seconds or 0.0), 3),
            "chars": len(text or ""),
        })

    def generate_messages(self, messages: list[dict], max_tokens: int | None = None,
                          thinking: bool | None = None) -> str:
        """按外部给定的 messages 生成（``LegalLLMPersona`` 这类"换人设"基线要用）。"""
        mt = int(max_tokens or self.max_new_tokens)
        prompt = messages_prompt(messages)
        key = make_key(self._cache_model(), "generate", mt, prompt)
        hit = self.cache.get(key)
        if hit is not None:
            self.cache.hit_miss("generate", True)
            self._log_call("generate", mt, hit.get("text", ""), hit.get("usage"),
                           0.0, True)
            return hit["text"]

        resp, sec, _ = self._request(messages, mt, stream=False, thinking=thinking)
        body = self._from_completion(resp)
        text = body["content"]
        usage = body["usage"]
        usage_dict = (usage.model_dump() if hasattr(usage, "model_dump")
                      else (usage if isinstance(usage, dict) else None))
        self.usage.add(usage_dict, sec)
        self._log_call("generate", mt, text, usage_dict, sec, False)
        self.cache.put(key, "generate", self._cache_model(), mt,
                       {"text": text, "reasoning": body["reasoning"],
                        "finish_reason": body["finish_reason"],
                        "model_raw": body["model_raw"],
                        "usage": usage_dict},
                       n_tokens=len(text), seconds=sec)
        self.cache.hit_miss("generate", False)
        return text

    def generate(self, query: str, history: list | None = None,
                 context: str | None = None, max_tokens: int | None = None) -> str:
        return self.generate_messages(self.build_messages(query, history, context),
                                      max_tokens=max_tokens)

    def generate_stream(self, query: str, history: list | None = None,
                        context: str | None = None, max_tokens: int | None = None):
        """真·逐 token 流式：SDK 逐块迭代，每块 delta.content 就是一个增量。

        与 ``generate()`` 的一致性（与本地后端的约定完全一样）：
          * 同一把缓存键（``_cache_model`` 里含 thinking，故关/开思考不会互相污染）；
          * 同一份 messages、同一 ``max_tokens``、同一 temperature=0；
          * 缓存命中时按固定大小切片产出——不伪造打字节奏，但让前端走同一条渲染路径。

        编码：openai SDK 内部按 UTF-8 解码 SSE（旧手写 requests 曾踩过
        "SSE 无 charset → 默认 ISO-8859-1 → 中文乱码"的坑，SDK 已解决）。
        """
        mt = int(max_tokens or self.max_new_tokens)
        messages = self.build_messages(query, history, context)
        prompt = messages_prompt(messages)
        key = make_key(self._cache_model(), "generate", mt, prompt)
        hit = self.cache.get(key)
        if hit is not None:
            self.cache.hit_miss("generate", True)
            text = hit["text"]
            self._log_call("generate_stream", mt, text, hit.get("usage"), 0.0, True)
            for i in range(0, len(text), 8):
                yield text[i:i + 8]
            return

        resp, t0, _ = self._request(messages, mt, stream=True)
        pieces: list[str] = []
        usage: dict | None = None
        try:
            for chunk in resp:
                cu = getattr(chunk, "usage", None)
                if cu is not None:
                    usage = cu
                for ch in (chunk.choices or []):
                    delta = getattr(ch, "delta", None)
                    piece = (getattr(delta, "content", "") or "") if delta else ""
                    if piece:
                        pieces.append(piece)
                        yield piece
        finally:
            close = getattr(resp, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:  # noqa: BLE001
                    pass
        sec = time.time() - t0
        text = "".join(pieces).strip()
        usage_dict = (usage.model_dump() if hasattr(usage, "model_dump")
                      else (usage if isinstance(usage, dict) else None))
        self.usage.add(usage_dict, sec)
        self._log_call("generate_stream", mt, text, usage_dict, sec, False)
        self.cache.put(key, "generate", self._cache_model(), mt,
                       {"text": text, "streamed": True, "usage": usage_dict},
                       n_tokens=len(text), seconds=sec)
        self.cache.hit_miss("generate", False)

    # ---------------------------------------------------------- 草稿 logprobs
    def draft_logprobs(self, query: str, history: list | None = None,
                       k: int = 20) -> DraftStats:
        """取前 k 个 token 的 top-k 分布，供门控信号使用。

        **强制思考模式**：只有思考模式下的 logprobs 才是真实分布（见模块文档）。
        这里不悄悄改语义——拿到的若是退化分布，直接抛 ``DegenerateLogprobs``，
        由 ``DraftSourceChain`` 决定换源，并把"换过源"写进 trace。
        """
        messages = self.build_messages(query, history, None)
        prompt = messages_prompt(messages)
        key = make_key(f"{self._cache_model()}|draft", "draft", int(k), prompt)
        hit = self.cache.get(key)
        if hit is not None:
            self.cache.hit_miss(f"draft{k}", True)
            return DraftStats(
                logprobs=[[(float(lp), t) for lp, t in pos] for pos in hit["logprobs"]],
                tokens=hit["tokens"], text=hit["text"],
                seconds=hit.get("seconds", 0.0), cached=True, source="api")

        resp, sec, _ = self._request(messages, int(k), stream=False,
                                     thinking=True, logprobs=True)
        body = self._from_completion(resp)
        self.usage.add(body["usage"] if isinstance(body["usage"], dict)
                       else (body["usage"].model_dump() if body["usage"] else None), sec)
        ch = (getattr(resp, "choices", [None]) or [None])[0]
        lp = getattr(ch, "logprobs", None) if ch is not None else None
        # DeepSeek 扩展：logprobs.reasoning_content / .content 两列之一有 top 分布
        raw: list[dict] = []
        if lp is not None:
            for attr in ("reasoning_content", "content"):
                seq = getattr(lp, attr, None)
                if seq:
                    raw = [s.model_dump() if hasattr(s, "model_dump") else dict(s)
                           for s in seq]
                    break
        if not raw:
            raise DegenerateLogprobs(
                "DeepSeek 未返回任何 logprobs（该模型/该参数组合不支持），"
                "门控信号需改用本地草稿模型")

        per_pos: list[list[tuple[float, str]]] = []
        for item in raw:
            alts = item.get("top_logprobs") or []
            row = sorted([(float(a.get("logprob") or 0.0), str(a.get("token") or ""))
                         for a in alts], key=lambda x: -x[0])
            if not row:      # 没有候选表时至少保留被选中的 token
                row = [(float(item.get("logprob") or 0.0), str(item.get("token") or ""))]
            per_pos.append(row)
        text = "".join(str(item.get("token") or "") for item in raw)

        reason = degenerate_reason(per_pos)
        if reason:
            raise DegenerateLogprobs(
                f"DeepSeek 返回的 logprobs 是退化分布（{reason}），"
                "拿它算 margin/entropy 会得到无意义的信号值")

        payload_out = {"logprobs": [[[lp_, t] for lp_, t in pos] for pos in per_pos],
                       "tokens": [str(item.get("token") or "") for item in raw],
                       "text": text}
        self.cache.put(key, f"draft{k}", self._cache_model(), int(k), payload_out,
                       n_tokens=len(per_pos), seconds=sec)
        self.cache.hit_miss(f"draft{k}", False)
        return DraftStats(logprobs=per_pos,
                          tokens=payload_out["tokens"], text=text,
                          seconds=sec, source="api")


def degenerate_reason(positions: list[list[tuple[float, str]]]) -> str | None:
    """判断一组分布是不是"占位符分布"；是则返回中文原因，否则 None。

    判据（对应真机实测的两种退化形态）：
      * 被选中 token 的 logprob **全部**恰好为 0.0（概率为 1 的序列不可能处处为 0.0）；
      * 候选表里超过一半的 logprob ≤ -9998（实测占位值恰为 -9999.0）。
    """
    if not positions:
        return "没有任何位置"
    chosen = [p[0][0] for p in positions if p]
    if not chosen:
        return "没有任何位置"
    if all(c == 0.0 for c in chosen):
        return "被选中 token 的 logprob 全为 0.0"
    alts = [lp for p in positions for lp, _ in p[1:]]
    if alts:
        n_ph = sum(1 for lp in alts if lp <= PLACEHOLDER_LOGPROB)
        if n_ph > len(alts) / 2:
            return f"候选分布中 {n_ph}/{len(alts)} 个是 -9999 占位符"
    return None


__all__ = ["DeepSeekLLM", "DeepSeekError", "DeepSeekAuthError",
           "DegenerateLogprobs", "degenerate_reason", "messages_prompt",
           "ApiUsage", "DEFAULT_MODEL", "DEFAULT_BASE_URL"]
