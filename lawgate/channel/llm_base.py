# -*- coding: utf-8 -*-
"""LLM 后端（手册 S3.8）：通道 A 直答 / 通道 C 检索增强 / 门控草稿 logprobs。

后端选择顺序：
  1. ``HFLLM``——transformers + 本地权重（本机可用，CPU fp32；有 CUDA 时走 4bit）；
  2. ``VLLMLLM``——部署路径，接口与 HFLLM 完全一致（手册 S3.5 尾注）；
  3. ``ExtractiveLLM``——无任何权重时的确定性兜底：从参考资料抽取式回答。
     它**不是**语言模型，仅用于保证极受限环境下仍能跑通全链路，
     其产出必须标注 ``llm_backend=ExtractiveLLM``，不得与神经模型结果混为一谈。

所有生成走 lawgate.cache.GenCache，命中即 0 计算。

流式输出（手册 S7 演示口径）：每个后端都提供 ``generate_stream``，产出**增量文本**：
  * ``HFLLM`` 走 transformers 的 ``TextIteratorStreamer`` + 后台线程，真·逐 token；
  * ``VLLMLLM`` 走离线引擎的 ``add_request`` + ``step()`` 循环，真·逐 token；
  * ``ExtractiveLLM`` 与基类默认实现整段产出一次（它不是语言模型，没有 token 概念）。
流式与整段共用**同一把缓存键**（model|generate|max_tokens|prompt），解码参数也一致
（贪心、无采样），故"先流式问一次、再整段问一次"必然得到同一段文本
（一致性原由 scripts/smoke_stream.py 实测断言；该脚本 2026-09-16 删除，见
docs/deviations.md D37，结论保留为历史记录；现行可复跑佐证见 check_stream.py）。
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from lawgate.cache import GenCache, get_cache, make_key

SYSTEM_BASE = ("你是法律咨询助手。回答必须准确、简洁，并在引用法律条文时注明法律"
               "名称与条号。若参考资料不足，应明确说明不确定，不要编造法条或案号。")

SYSTEM_WITH_CONTEXT = SYSTEM_BASE + "\n【参考资料】\n{context}\n\n" \
    "请优先依据上述参考资料回答；引用时给出法律名称与条号。"


def default_local_thinking() -> bool:
    """本机权重的"思考模式"默认值——**默认关**（D38）。

    为什么必须显式关：Qwen3 系的 chat template 写法是
    ``{%- if enable_thinking is defined and enable_thinking is false %}<think></think>``，
    也就是说**不传这个变量 = 思考模式开启**。开着思考时模型会先写一大段
    ``<think>…</think>`` 推理，``max_new_tokens=192`` 很可能被 reasoning 吃光，
    正文变成空串或截断——这与 D30 在 DeepSeek API 上踩到的是**同一款陷阱**
    （当时靠 ``thinking=False`` 解决）。因此本机权重也统一默认关思考。

    读取优先级：``LAWGATE_LOCAL_THINKING`` 环境变量 > Settings.local_thinking > False。
    """
    v = os.environ.get("LAWGATE_LOCAL_THINKING")
    if v is not None:
        return str(v).strip().lower() in ("1", "true", "yes", "on")
    try:
        from lawgate.config import get_settings
        return bool(getattr(get_settings(), "local_thinking", False))
    except Exception:  # noqa: BLE001 — 取配置失败不该影响能否加载模型
        return False


@dataclass
class DraftStats:
    """一次草稿的前 k 个 token 及其 top-k logprob 分布。"""

    logprobs: list[list[tuple[float, str]]] = field(default_factory=list)
    tokens: list[str] = field(default_factory=list)
    text: str = ""
    seconds: float = 0.0
    cached: bool = False
    # 这份草稿**实际**由谁产生（D30）：``api`` = 回答模型的 API logprobs，
    # ``local`` = 本机小模型，``rule`` = 无权重时的确定性伪分布，``answer_model`` = 与回答同一个后端。
    # 门控换源必须留痕：不写这一项，事后没人能判断"这条 u 到底是哪个模型给的"。
    source: str = ""
    # 换源过程中每一次尝试的结果（成功/失败原因），一并写进 trace
    attempts: list[dict] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.logprobs)


class BaseLLM:
    name = "base"
    backend = "base"

    def generate(self, query: str, history: list | None = None,
                 context: str | None = None, max_tokens: int | None = None) -> str:
        raise NotImplementedError

    def generate_stream(self, query: str, history: list | None = None,
                        context: str | None = None, max_tokens: int | None = None):
        """生成增量文本（生成器）。

        默认实现：整段算完后一次性产出——**语义上是流式，视觉上不是**。
        子类应覆盖为真流；调用方不得依赖"一定有多个分片"，
        且应以 done 事件的全文（而非分片拼接）为权威答案。
        """
        yield self.generate(query, history, context=context, max_tokens=max_tokens)

    def draft_logprobs(self, query: str, history: list | None = None,
                       k: int = 20) -> DraftStats:
        raise NotImplementedError

    def build_prompt(self, query: str, history: list | None = None,
                     context: str | None = None) -> str:
        raise NotImplementedError

    # 结果溯源（写进 trace 与图注）
    def describe(self) -> dict:
        return {"llm_backend": self.backend, "llm_name": self.name}

    def gen_dtype(self) -> str:
        """实际生效的权重精度（写进溯源；大模型在 CPU 上这条尤其重要）。"""
        try:
            return str(next(self.mdl.parameters()).dtype).replace("torch.", "")
        except Exception:  # noqa: BLE001
            return "n/a"


class PromptMixin:
    def build_messages(self, query: str, history: list | None,
                       context: str | None) -> list[dict]:
        sys = (SYSTEM_WITH_CONTEXT.format(context=context) if context
               else SYSTEM_BASE)
        msgs = [{"role": "system", "content": sys}]
        for t in (history or []):
            q = t.get("query") or t.get("q") or ""
            a = t.get("answer") or t.get("a") or ""
            if q:
                msgs.append({"role": "user", "content": q})
            if a:
                msgs.append({"role": "assistant", "content": a})
        msgs.append({"role": "user", "content": query})
        return msgs


class HFLLM(PromptMixin, BaseLLM):
    """transformers 后端。CPU fp32 可用；CUDA 时按手册走 4bit。

    两条装载路径（``dtype`` 决定精度，见 configs/base.yaml 的 ``dtype`` 注释）：
      * 普通模型（Qwen 等）：``AutoModelForCausalLM`` + ``apply_chat_template``；
      * 旧版远程代码模型（ChatGLM 系，如 fuzi-mingcha-v1_0）：交给
        ``lawgate.compat_chatglm`` 处理中文路径 / 旧 tokenizer 接口 /
        ``AutoModel`` 装载三件事（细节见该模块文档字符串 D-fuzi-1..4）。
    """

    backend = "hf"

    def __init__(self, model_path: str, device: str = "cpu",
                 max_new_tokens: int = 192, topk_logprobs: int = 20,
                 cache: GenCache | None = None, threads: int | None = None,
                 dtype: str | None = None, thinking: bool | None = None):
        import torch

        if threads:
            torch.set_num_threads(int(threads))
        self.torch = torch
        self.model_path = model_path
        self.name = Path(model_path).name
        self.max_new_tokens = max_new_tokens
        self.topk = topk_logprobs
        self.cache = cache or get_cache()
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.dtype = dtype or "auto"
        # 思考模式（D38）：Qwen3 模板**不传 enable_thinking 就等于开启思考**，
        # 会在 max_new_tokens 里先写一大段 reasoning（详见 default_local_thinking 的注释）。
        self.thinking = bool(thinking) if thinking is not None else default_local_thinking()
        self.is_chatglm = False

        from lawgate.compat_chatglm import is_chatglm_dir

        if is_chatglm_dir(model_path):
            # ChatGLM 系：由兼容层统一处理（含 sentencepiece 中文路径补丁）
            from lawgate.compat_chatglm import load_chatglm

            bundle = load_chatglm(model_path, dtype=self.dtype, device=self.device)
            self.tok = bundle.tokenizer
            self.mdl = bundle.model
            self.is_chatglm = True
        else:
            from transformers import AutoModelForCausalLM, AutoTokenizer

            from lawgate.compat_chatglm import _resolve_dtype

            self.tok = AutoTokenizer.from_pretrained(model_path,
                                                     trust_remote_code=True)
            kw: dict = {}
            if self.device == "cuda":
                try:
                    from transformers import BitsAndBytesConfig

                    kw["quantization_config"] = BitsAndBytesConfig(
                        load_in_4bit=True, bnb_4bit_compute_dtype=torch.bfloat16)
                    kw["device_map"] = "cuda:0"
                except Exception:  # noqa: BLE001
                    kw["dtype"] = torch.float16
                    kw["device_map"] = "cuda:0"
            else:
                kw["dtype"] = _resolve_dtype(self.dtype, torch)
            self.mdl = AutoModelForCausalLM.from_pretrained(model_path, **kw)
        self.mdl.eval()
        self.device = str(next(self.mdl.parameters()).device)
        # 模板级开关探测（D38）：只有模板里真的引用了 enable_thinking 才传这个 kwarg，
        # 免得给别的模型塞无用参数（部分 tokenizer 会对多余 kwarg 报错）。
        tpl = str(getattr(self.tok, "chat_template", "") or "")
        self._tpl_supports_thinking = "enable_thinking" in tpl
        self._tpl_kwargs: dict = (
            {"enable_thinking": bool(self.thinking)} if self._tpl_supports_thinking else {})

    def build_prompt(self, query: str, history: list | None = None,
                     context: str | None = None) -> str:
        msgs = self.build_messages(query, history, context)
        if getattr(self.tok, "chat_template", None):
            extra = getattr(self, "_tpl_kwargs", {}) or {}
            try:
                return self.tok.apply_chat_template(
                    msgs, tokenize=False, add_generation_prompt=True, **extra)
            except TypeError:
                # 该 tokenizer/模板版本不接受这个 kwarg（或签名不匹配）：
                # 退回不带参调用——宁可保留模板默认，也不要拼不出 prompt。
                try:
                    return self.tok.apply_chat_template(
                        msgs, tokenize=False, add_generation_prompt=True)
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001
                pass
        if type(self.tok).__name__ == "ChatGLMTokenizer":
            # ChatGLM-6B 底座（如 fuzi-mingcha）的原生对话格式，且不支持
            # system 角色：把系统提示并入首条提问。用错模板会显著劣化输出。
            sys_txt = ""
            if msgs and msgs[0]["role"] == "system":
                sys_txt = msgs[0]["content"].strip()
                msgs = msgs[1:]
            parts: list[str] = []
            rnd = 0
            for m in msgs:
                if m["role"] == "user":
                    rnd += 1
                    content = m["content"]
                    if rnd == 1 and sys_txt:
                        content = f"{sys_txt}\n\n{content}"
                    parts.append(f"[Round {rnd}]\n问：{content}\n答：")
                elif m["role"] == "assistant":
                    parts.append(f"{m['content']}\n")
            return "".join(parts)
        parts = [f"{m['role']}: {m['content']}" for m in msgs]
        return "\n".join(parts) + "\nassistant:"

    # ------------------------------------------------------------------ 生成
    def describe(self) -> dict:
        """溯源信息：模型名 + 精度 + 是否 ChatGLM 兼容路径。

        为什么精度必须写进溯源：fuzi-mingcha 这类 6.7B 模型在 CPU 上 fp16 / fp32
        的内存占用差一倍（13.4 GB vs 27 GB），只看模型名无法复现同一份结果。
        """
        d = super().describe()
        d.update({"llm_dtype": self.gen_dtype(),
                  "llm_backend_impl": "chatglm-compat" if self.is_chatglm else "hf",
                  # 思考模式（D38）：模板支持时才真正生效，见 default_local_thinking()
                  "llm_thinking": bool(self.thinking),
                  "llm_thinking_supported": bool(
                      getattr(self, "_tpl_supports_thinking", False))})
        return d

    def generate(self, query: str, history: list | None = None,
                 context: str | None = None, max_tokens: int | None = None) -> str:
        mt = int(max_tokens or self.max_new_tokens)
        prompt = self.build_prompt(query, history, context)
        key = make_key(self.name, "generate", mt, prompt)
        hit = self.cache.get(key)
        if hit is not None:
            self.cache.hit_miss("generate", True)
            return hit["text"]

        t0 = time.time()
        inp = self.tok(prompt, return_tensors="pt").to(self.mdl.device)
        with self.torch.no_grad():
            out = self.mdl.generate(**inp, max_new_tokens=mt, do_sample=False,
                                    temperature=None, top_p=None, top_k=None,
                                    pad_token_id=self.tok.eos_token_id)
        new_ids = out[0][inp["input_ids"].shape[1]:]
        text = self.tok.decode(new_ids, skip_special_tokens=True).strip()
        sec = time.time() - t0
        self.cache.put(key, "generate", self.name, mt,
                       {"text": text}, n_tokens=int(len(new_ids)), seconds=sec)
        self.cache.hit_miss("generate", False)
        return text

    def generate_stream(self, query: str, history: list | None = None,
                        context: str | None = None, max_tokens: int | None = None):
        """真·逐 token 流式生成（TextIteratorStreamer + 后台线程）。

        与 ``generate()`` 的三处对齐（改了任意一处都会破坏"流式/整段同结果"）：
          1. 同一把缓存键：``make_key(name, "generate", mt, prompt)``；
          2. 同一套解码参数：贪心（do_sample=False，temperature/top_p/top_k 全禁）；
          3. 同一段 prompt：``build_prompt``（含 system/参考资料/多轮历史）。

        为什么要线程：``model.generate(streamer=...)`` 是**阻塞**调用，它会自己把结果
        put 进 streamer 的队列；只有把它放到别的线程，主线程才能边算边把队列里的
        分片 yield 出去（CPU 上单条 80 token 要 10–15 s，不流式就是干等黑屏）。

        线程里抛的异常**必须**搬回主线程重抛：否则 UI/接口只会看到"流莫名其妙断了"。
        """
        mt = int(max_tokens or self.max_new_tokens)
        prompt = self.build_prompt(query, history, context)
        key = make_key(self.name, "generate", mt, prompt)
        hit = self.cache.get(key)
        if hit is not None:
            self.cache.hit_miss("generate", True)
            # 缓存命中：算力是 0，没必要伪造"逐字打字"的节奏，但也切成小片产出，
            # 让前端的渲染路径与真实流式完全一致（同一套代码、同一种刷新频率）。
            text = hit["text"]
            for i in range(0, len(text), 8):
                yield text[i:i + 8]
            return

        from transformers import TextIteratorStreamer

        inp = self.tok(prompt, return_tensors="pt").to(self.mdl.device)
        streamer = TextIteratorStreamer(self.tok, skip_prompt=True,
                                        skip_special_tokens=True)
        box: dict = {}

        def _run() -> None:
            try:
                with self.torch.no_grad():
                    self.mdl.generate(**inp, max_new_tokens=mt, do_sample=False,
                                      temperature=None, top_p=None, top_k=None,
                                      pad_token_id=self.tok.eos_token_id,
                                      streamer=streamer)
            except BaseException as exc:  # noqa: BLE001 — 搬回主线程再抛
                box["error"] = exc

        t0 = time.time()
        th = threading.Thread(target=_run, daemon=True,
                              name="lawgate-hf-stream")
        th.start()
        pieces: list[str] = []
        for piece in streamer:          # 队列空了会阻塞等待，生成结束自然退出
            if piece:
                pieces.append(piece)
                yield piece
        th.join()
        if "error" in box:
            raise box["error"]

        text = "".join(pieces).strip()
        sec = time.time() - t0
        # n_tokens 用分片数近似（真的是"每步一片"，跳过的特殊 token 不计数）
        self.cache.put(key, "generate", self.name, mt, {"text": text},
                       n_tokens=len(pieces), seconds=sec)
        self.cache.hit_miss("generate", False)

    # ------------------------------------------------------- 草稿 logprobs
    def draft_logprobs(self, query: str, history: list | None = None,
                       k: int = 20) -> DraftStats:
        """取草稿前 k 个 token 的 top-``self.topk`` logprob 分布。

        门控只需要**前缀**信号（手册 S3.5 亦指出取前 min(k,8) 更稳），
        因此这里逐位置保存完整 top-k 分布，供 signal.py 计算多种信号。
        """
        prompt = self.build_prompt(query, history, None)
        key = make_key(self.name, f"draft{k}", k, prompt)
        hit = self.cache.get(key)
        if hit is not None:
            self.cache.hit_miss(f"draft{k}", True)
            return DraftStats(
                logprobs=[[(float(lp), t) for lp, t in pos]
                          for pos in hit["logprobs"]],
                tokens=hit["tokens"], text=hit["text"],
                seconds=hit.get("seconds", 0.0), cached=True)

        t0 = time.time()
        inp = self.tok(prompt, return_tensors="pt").to(self.mdl.device)
        with self.torch.no_grad():
            out = self.mdl.generate(**inp, max_new_tokens=k, do_sample=False,
                                    output_scores=True,
                                    return_dict_in_generate=True,
                                    temperature=None, top_p=None, top_k=None,
                                    pad_token_id=self.tok.eos_token_id)
        per_pos: list[list[tuple[float, str]]] = []
        tok_strs: list[str] = []
        for step, scores in enumerate(out.scores):
            logp = self.torch.log_softmax(scores[0].float(), dim=-1)
            tv, ti = self.torch.topk(logp, min(self.topk, logp.shape[-1]))
            per_pos.append([(float(v), self.tok.decode([int(i)]))
                            for v, i in zip(tv, ti)])
        new_ids = out.sequences[0][inp["input_ids"].shape[1]:]
        tok_strs = [self.tok.decode([int(i)]) for i in new_ids]
        text = self.tok.decode(new_ids, skip_special_tokens=True).strip()
        sec = time.time() - t0
        payload = {
            "logprobs": [[[lp, t] for lp, t in pos] for pos in per_pos],
            "tokens": tok_strs, "text": text, "seconds": sec,
        }
        self.cache.put(key, f"draft{k}", self.name, k, payload,
                       n_tokens=int(len(new_ids)), seconds=sec)
        self.cache.hit_miss(f"draft{k}", False)
        return DraftStats(logprobs=per_pos, tokens=tok_strs, text=text, seconds=sec)


class VLLMLLM(PromptMixin, BaseLLM):
    """部署后端（手册 S3.5）：与正式生成共享前缀 KV，免重复 prefill。

    本机无 vllm（未安装 / 无 CUDA），故此类仅在部署环境启用；
    接口与 HFLLM 一致，router/baseline 无需改动。
    """

    backend = "vllm"

    def __init__(self, model_path: str, max_new_tokens: int = 192,
                 topk_logprobs: int = 20, cache: GenCache | None = None):
        from vllm import LLM  # 延迟导入，避免无 vllm 环境导入失败

        self.llm = LLM(model=model_path, enable_prefix_caching=True)
        self.name = Path(model_path).name
        self.max_new_tokens = max_new_tokens
        self.topk = topk_logprobs
        self.cache = cache or get_cache()

    def build_prompt(self, query, history=None, context=None) -> str:
        from transformers import AutoTokenizer

        tok = AutoTokenizer.from_pretrained(self.llm.llm_engine.model_config.model)
        return tok.apply_chat_template(self.build_messages(query, history, context),
                                       tokenize=False, add_generation_prompt=True)

    def generate(self, query, history=None, context=None, max_tokens=None) -> str:
        from vllm import SamplingParams

        mt = int(max_tokens or self.max_new_tokens)
        prompt = self.build_prompt(query, history, context)
        key = make_key(self.name, "generate", mt, prompt)
        hit = self.cache.get(key)
        if hit is not None:
            self.cache.hit_miss("generate", True)
            return hit["text"]
        sp = SamplingParams(max_tokens=mt, logprobs=0, temperature=0)
        res = self.llm.generate([prompt], sp)[0]
        text = res.outputs[0].text.strip()
        self.cache.put(key, "generate", self.name, mt, {"text": text},
                       n_tokens=len(res.outputs[0].token_ids))
        self.cache.hit_miss("generate", False)
        return text

    def generate_stream(self, query, history=None, context=None, max_tokens=None):
        """真·逐 token 流式（离线引擎的 add_request + step 循环）。

        与 ``generate()`` 同键、同参数（temperature=0，即贪心），故缓存互通。
        vllm 不在本机（本机无 CUDA），此路径**未经本机实测**，仅按官方离线引擎
        接口实现；取不到 ``llm_engine`` 时退回整段产出，绝不静默丢答案。
        """
        from vllm import SamplingParams

        mt = int(max_tokens or self.max_new_tokens)
        prompt = self.build_prompt(query, history, context)
        key = make_key(self.name, "generate", mt, prompt)
        hit = self.cache.get(key)
        if hit is not None:
            self.cache.hit_miss("generate", True)
            yield hit["text"]
            return

        engine = getattr(self.llm, "llm_engine", None)
        if engine is None:  # 接口变动时的兜底：整段产出，不假装流式
            yield self.generate(query, history, context=context, max_tokens=mt)
            return

        sp = SamplingParams(max_tokens=mt, logprobs=0, temperature=0)
        req_id = f"lawgate-stream-{time.time_ns()}"
        engine.add_request(req_id, prompt, sp)
        pieces: list[str] = []
        prev_len = 0
        n_tok = 0
        t0 = time.time()
        while engine.has_unfinished_requests():
            for out in engine.step():
                o = out.outputs[0]
                n_tok = len(o.token_ids)
                if len(o.text) > prev_len:
                    piece = o.text[prev_len:]
                    prev_len = len(o.text)
                    pieces.append(piece)
                    yield piece
        text = "".join(pieces).strip()
        self.cache.put(key, "generate", self.name, mt, {"text": text},
                       n_tokens=n_tok, seconds=time.time() - t0)
        self.cache.hit_miss("generate", False)

    def draft_logprobs(self, query, history=None, k: int = 20) -> DraftStats:
        from vllm import SamplingParams

        prompt = self.build_prompt(query, history, None)
        key = make_key(self.name, f"draft{k}", k, prompt)
        hit = self.cache.get(key)
        if hit is not None:
            self.cache.hit_miss(f"draft{k}", True)
            return DraftStats(logprobs=[[(float(lp), t) for lp, t in pos]
                                        for pos in hit["logprobs"]],
                              tokens=hit["tokens"], text=hit["text"], cached=True)
        sp = SamplingParams(max_tokens=k, logprobs=self.topk, temperature=0)
        res = self.llm.generate([prompt], sp)[0]
        per_pos: list[list[tuple[float, str]]] = []
        for step_lps in (res.outputs[0].logprobs or []):
            items = sorted(step_lps.items(), key=lambda kv: -kv[1].logprob)
            per_pos.append([(float(v.logprob), kk) for kk, v in items])
        toks = [self.llm.get_tokenizer().decode([t])
                for t in res.outputs[0].token_ids]
        payload = {"logprobs": [[[lp, t] for lp, t in p] for p in per_pos],
                   "tokens": toks, "text": res.outputs[0].text}
        self.cache.put(key, f"draft{k}", self.name, k, payload)
        self.cache.hit_miss(f"draft{k}", False)
        return DraftStats(logprobs=per_pos, tokens=toks,
                          text=res.outputs[0].text)


class ExtractiveLLM(PromptMixin, BaseLLM):
    """确定性抽取式兜底后端（无权重时）。

    有 context：按 BM25 选出与 query 最相关的句子，拼成带出处的答案。
    无 context：直接从 SQLite 事实库按意图检索条文/案号后作答。
    明确标记为 rule_based，绝不冒充神经模型输出。

    流式口径：继承 ``BaseLLM.generate_stream``（整段一次产出）。它是确定性抽取，
    本来就没有 token 概念，逐字"打字"只是伪装；接口统一是为了让上层不必分支。
    """

    backend = "rule_based"
    name = "ExtractiveLLM"

    def __init__(self, db_path: str | None = None, cache: GenCache | None = None):
        from lawgate.config import get_settings

        self.db_path = db_path or get_settings().db_path
        self.cache = cache or get_cache(enabled=False)

    def build_prompt(self, query, history=None, context=None) -> str:
        return json.dumps({"query": query, "context": context,
                           "history": history or []}, ensure_ascii=False)

    def generate(self, query, history=None, context=None, max_tokens=None) -> str:
        from lawgate.knowledge.rerank import tokenize

        if context:
            sents = [s.strip() for s in
                     context.replace("\n", "。").split("。") if len(s.strip()) > 6]
            if not sents:
                return "根据现有参考资料无法确定答案。"
            q = set(tokenize(query))
            sents.sort(key=lambda s: -len(q & set(tokenize(s))))
            picked = sents[:3]
            return "根据参考资料：" + "。".join(picked) + "。"
        return (f"【规则兜底后端】未提供参考资料，无法就「{query[:40]}」给出可靠结论。"
                "请结合法条检索通道或补充事实。")

    def draft_logprobs(self, query, history=None, k: int = 20) -> DraftStats:
        """用词面"可判定性"构造伪 logprob：确定性、可审计，但不是语言模型信号。

        仅供无权重环境跑通链路；E0 会显式标注 backend=rule_based，
        并说明该信号不可外推到神经模型。
        """
        import math

        from lawgate.knowledge.rerank import tokenize

        toks = tokenize(query)[:k] or list(query)[:k]
        per_pos = []
        for i, t in enumerate(toks):
            # 越靠后越"平"，模拟长序列信心衰减
            m = 2.0 / (1.0 + 0.35 * i)
            per_pos.append([(-0.2 - i * 0.02, t), (-0.2 - i * 0.02 - m, "的")])
        return DraftStats(logprobs=per_pos, tokens=toks, text="".join(toks))


def get_llm(prefer: str = "auto", **kw) -> BaseLLM:
    """按可用性选择后端。prefer: auto|deepseek|hf|vllm|rule。

    选择顺序（按代码实际行为写，别再照旧的"自动降级"说法）：
      * 解析结果为 **deepseek**（``prefer="deepseek"``，或 ``prefer="auto"`` 且
        ``llm_backend=deepseek`` 读到了 API Key）：**只返回 ``DeepSeekLLM``，不降级到本地**。
        ``DEEPSEEK_API_KEY`` 缺失只 print 一条警告，问题在调用时以 401 暴露。
        这是**刻意**的：静默换成本地模型会产出"改了配置却跑出另一套结果"（D30 的教训）。
        想离线就显式 ``prefer="hf"``（或 ``启动服务.ps1 -LlmBackend hf``）。
      * 解析结果为 **本地**（``llm_backend`` 为 ``hf``/``auto``，**D38 起的默认**）：
        按 ``HFLLM`` → ``VLLMLLM`` → ``ExtractiveLLM`` 逐级尝试，每一级的失败原因
        都 print 出来（谁降级、为什么降级，必须留在日志里）。
    """
    from lawgate.config import get_settings

    s = get_settings()
    want = (prefer or "auto").strip().lower()
    if want == "auto":
        want = (getattr(s, "llm_provider", None) or s.llm_backend or "auto").lower()
    if want in ("local", "hf"):
        want = "hf" if s.llm_backend in ("auto", "hf") else s.llm_backend

    if want == "deepseek":
        from lawgate.channel.deepseek_llm import DeepSeekLLM

        llm = DeepSeekLLM(
            model=getattr(s, "deepseek_model", "") or "deepseek-v4-flash",
            api_key=getattr(s, "deepseek_api_key", "") or None,
            base_url=getattr(s, "deepseek_base_url", None),
            max_new_tokens=s.max_new_tokens,
            timeout=float(getattr(s, "deepseek_timeout", 120.0) or 120.0),
            thinking=bool(getattr(s, "deepseek_thinking", False)),
            max_retries=int(getattr(s, "deepseek_max_retries", 3) or 3),
            **{k: v for k, v in kw.items() if k != "threads"})
        if not llm.api_key:
            print("[llm] ⚠ 走 DeepSeek API 但没有读到 DEEPSEEK_API_KEY"
                  "（.env 或环境变量），调用会以 401 失败")
        return llm

    if want in ("auto", "hf"):
        try:
            return HFLLM(s.causal_model, device=s.device,
                         max_new_tokens=s.max_new_tokens,
                         dtype=getattr(s, "dtype", None) or "auto",
                         thinking=bool(getattr(s, "local_thinking", False)),
                         threads=kw.pop("threads", None), **kw)
        except Exception as exc:  # noqa: BLE001
            print(f"[llm] HF 后端加载失败（{type(exc).__name__}: {exc}）")
    if want in ("auto", "vllm"):
        try:
            return VLLMLLM(s.causal_model, **kw)
        except Exception as exc:  # noqa: BLE001
            print(f"[llm] vLLM 后端不可用（{type(exc).__name__}: {exc}）")
    print("[llm] 退回 ExtractiveLLM（规则兜底，结果需标注 backend=rule_based）")
    return ExtractiveLLM()
