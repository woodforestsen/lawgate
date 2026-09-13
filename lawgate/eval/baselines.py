# -*- coding: utf-8 -*-
"""基线与本方法的统一接口（手册 S6.2）。

统一协议：
    m = Method(...)
    out = m.query(item)   # item 为基准集一条；返回 {"answer":..., "trace":{...}}
    m.n_retrieval_calls   # 本次查询的检索调用数（0/1）
    m.name                # 方法名

关于两个必须披露的替代（docs/deviations.md D16/D17）：
  D16 ``LegalLLMPersona``：手册要求加载 LawGPT_zh / ChatLaw 开源权重，但本机
      **无法从被劫持的网络获取第三方权重**，也没有相应本地缓存。故用"同一底座
      模型 + 法律专家系统提示 + 不检索"作为**代理基线**，并明确命名 Persona 以示
      区别。它**不是**法律微调模型，不能用来支持"通用模型 vs 法律专用模型"的结论。
  D17 ``ComplexityRouter`` 的启发式阈值（长度 30 字）沿用手册原文；未调参。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from lawgate.channel.llm_base import BaseLLM, SYSTEM_BASE
from lawgate.channel.c_semantic import Retriever
from lawgate.gate.signal import SIGNALS, normalize_signal
from lawgate.router import LegalGateRouter, RouterOptions


def count_tokens(llm: BaseLLM, text: str) -> int:
    """估算答案 token 数：优先用分词器，退化到字符启发式。"""
    tok = getattr(llm, "tok", None)
    if tok is not None:
        try:
            return int(len(tok(text or "", add_special_tokens=False)["input_ids"]))
        except Exception:  # noqa: BLE001
            pass
    return max(int(len(text or "") / 1.5), 0)


class Method:
    name = "base"

    def __init__(self, llm: BaseLLM, retriever: Retriever | None = None,
                 **kw):
        self.llm = llm
        self.ret = retriever
        self.n_retrieval_calls = 0
        self.kw = kw

    def query(self, item: dict) -> dict:  # pragma: no cover
        raise NotImplementedError

    def _gen(self, item: dict, context: str | None,
             max_tokens: int = 128) -> dict:
        ans = self.llm.generate(item.get("query", ""), item.get("history"),
                                context=context, max_tokens=max_tokens)
        return {"answer": ans, "n_tokens": count_tokens(self.llm, ans)}

    def description(self) -> dict:
        d = {"method": self.name, "n_retrieval_calls": self.n_retrieval_calls}
        d.update(self.llm.describe())
        return d


class NeverRAG(Method):
    """不检索，直接生成（下界基线）。"""
    name = "neverrag"

    def query(self, item: dict) -> dict:
        self.n_retrieval_calls = 0
        r = self._gen(item, None, self.kw.get("max_tokens", 128))
        r["trace"] = {"channel": "none", "n_retrieval_calls": 0}
        return r


class AlwaysRAG(Method):
    """恒检索 top-k + 重排 + 生成（上界基线，也是精度参照）。"""
    name = "alwaysrag"

    def query(self, item: dict) -> dict:
        top_k = self.kw.get("top_k", 8)
        rerank = self.kw.get("rerank", True)
        rr = self.ret.retrieve(item.get("query", ""), top_k=top_k, rerank=rerank)
        self.n_retrieval_calls = 1
        r = self._gen(item, rr.context, self.kw.get("max_tokens", 128))
        r["trace"] = {"channel": "always", "n_retrieval_calls": 1,
                      "retrieval": rr.to_dict()}
        return r


class TARG(Method):
    """单全局阈值 τ（手册 S6.2）：与 LegalGate 同信号，但只用一个 τ。"""
    name = "targ"

    def __init__(self, llm, retriever=None, tau: float = 0.10, **kw):
        super().__init__(llm, retriever, **kw)
        self.tau = float(tau)
        self.signal = normalize_signal(kw.get("signal", "margin"))
        # 草稿来源与 LegalGate 保持一致（D30）：两者若用不同草稿模型，
        # "单阈值 vs 桶级阈值"的对比里就混进了"信号本身不同"的干扰项。
        self.draft = kw.get("draft") or llm

    def query(self, item: dict) -> dict:
        stats = self.draft.draft_logprobs(item.get("query", ""),
                                          item.get("history"),
                                          k=self.kw.get("k_draft", 20))
        u = float(SIGNALS[self.signal](stats))
        if u > self.tau:
            rr = self.ret.retrieve(item.get("query", ""),
                                   top_k=self.kw.get("top_k", 8),
                                   rerank=self.kw.get("rerank", True))
            self.n_retrieval_calls = 1
            r = self._gen(item, rr.context, self.kw.get("max_tokens", 128))
            r["trace"] = {"channel": "C", "u": u, "tau": self.tau,
                          "n_retrieval_calls": 1, "retrieval": rr.to_dict()}
        else:
            self.n_retrieval_calls = 0
            r = self._gen(item, None, self.kw.get("max_tokens", 128))
            r["trace"] = {"channel": "A", "u": u, "tau": self.tau,
                          "n_retrieval_calls": 0}
        return r


class ComplexityRouter(Method):
    """启发式复杂度路由（手册 S6.2）：长度>30 或含"案例/判决/判例/类似"即检索。"""
    name = "complexity"

    KEYWORDS = ("案例", "判决", "判例", "类似")

    def query(self, item: dict) -> dict:
        q = item.get("query", "")
        complex_q = len(q) > 30 or any(w in q for w in self.KEYWORDS)
        if complex_q:
            rr = self.ret.retrieve(q, top_k=self.kw.get("top_k", 8),
                                   rerank=self.kw.get("rerank", True))
            self.n_retrieval_calls = 1
            r = self._gen(item, rr.context, self.kw.get("max_tokens", 128))
            r["trace"] = {"channel": "cx-C", "complex": True,
                          "n_retrieval_calls": 1, "retrieval": rr.to_dict()}
        else:
            self.n_retrieval_calls = 0
            r = self._gen(item, None, self.kw.get("max_tokens", 128))
            r["trace"] = {"channel": "cx-A", "complex": False,
                          "n_retrieval_calls": 0}
        return r


LEGAL_PERSONA = (
    "你是一名资深中国执业律师，精通《民法典》及其配套司法解释。"
    "回答时必须援引具体的法律名称与条号，并说明裁判规则。若不确定，应明确说明，"
    "不得编造法条或案号。"
)


class LegalLLMPersona(Method):
    """D16 代理基线：法务人设 + 不检索（**非**法律微调模型）。"""
    name = "legal_llm"

    def query(self, item: dict) -> dict:
        self.n_retrieval_calls = 0
        msgs = [{"role": "system", "content": LEGAL_PERSONA}]
        for t in (item.get("history") or []):
            if t.get("query"):
                msgs.append({"role": "user", "content": t["query"]})
            if t.get("answer"):
                msgs.append({"role": "assistant", "content": t["answer"]})
        msgs.append({"role": "user", "content": item.get("query", "")})
        tok = getattr(self.llm, "tok", None)
        if tok is not None and hasattr(tok, "apply_chat_template"):
            prompt = tok.apply_chat_template(msgs, tokenize=False,
                                            add_generation_prompt=True)
            ans = self._generate_prompt(prompt, self.kw.get("max_tokens", 128))
        elif hasattr(self.llm, "generate_messages"):
            # API 后端没有 tokenizer，但可以直接吃 messages（人设才不会被丢掉）
            ans = self.llm.generate_messages(msgs, max_tokens=self.kw.get("max_tokens", 128))
        else:
            ans = self.llm.generate(item.get("query", ""), item.get("history"),
                                    context=None,
                                    max_tokens=self.kw.get("max_tokens", 128))
        self._last_prompt = None
        return {"answer": ans, "n_tokens": count_tokens(self.llm, ans),
                "trace": {"channel": "legal_persona", "n_retrieval_calls": 0}}

    def _generate_prompt(self, prompt: str, max_tokens: int) -> str:
        """直接用已构造好的 prompt 生成（复用 llm 的模型与缓存）。"""
        llm = self.llm
        if not hasattr(llm, "mdl") or not hasattr(llm, "tok"):
            return llm.generate(prompt, None, None, max_tokens=max_tokens)
        from lawgate.cache import make_key

        key = make_key(llm.name, "generate", int(max_tokens), prompt)
        hit = llm.cache.get(key)
        if hit is not None:
            llm.cache.hit_miss("generate", True)
            return hit["text"]
        import time as _t

        t0 = _t.time()
        inp = llm.tok(prompt, return_tensors="pt").to(llm.mdl.device)
        with llm.torch.no_grad():
            out = llm.mdl.generate(**inp, max_new_tokens=int(max_tokens),
                                   do_sample=False, temperature=None, top_p=None,
                                   top_k=None, pad_token_id=llm.tok.eos_token_id)
        new_ids = out[0][inp["input_ids"].shape[1]:]
        text = llm.tok.decode(new_ids, skip_special_tokens=True).strip()
        llm.cache.put(key, "generate", llm.name, int(max_tokens),
                      {"text": text}, n_tokens=int(len(new_ids)),
                      seconds=_t.time() - t0)
        llm.cache.hit_miss("generate", False)
        return text


class LegalGate(Method):
    """本项目：三通道 + 桶级校准。"""
    name = "legalgate"

    def __init__(self, llm, retriever=None, router: LegalGateRouter | None = None,
                 **kw):
        super().__init__(llm, retriever, **kw)
        self.router = router

    def query(self, item: dict) -> dict:
        r = self.router.answer(item.get("query", ""), item.get("history"), meta=item)
        tr = r.get("trace", {})
        self.n_retrieval_calls = int(tr.get("n_retrieval_calls", 0))
        out = {"answer": r.get("answer", ""),
               "n_tokens": count_tokens(self.llm, r.get("answer", "")),
               "trace": tr}
        if r.get("provision"):
            out["provision"] = r["provision"]
        return out

    def description(self) -> dict:
        d = super().description()
        d.update(self.router.description())
        return d


def build_methods(llm, retriever, router: LegalGateRouter,
                  tau_single: float = 0.10, **common) -> dict[str, Method]:
    """构造全部 6 个方法。公共 kwargs（top_k/rerank/max_tokens/k_draft/signal）
    对所有方法一致，确保对比公平。

    草稿来源（``draft``）默认取路由器的 ```router.draft``（D30）：TARG 与 LegalGate
    必须用同一条草稿通道，否则"单阈值 vs 桶级阈值"就掺进了"信号来源不同"。
    """
    common.setdefault("draft", getattr(router, "draft", None) or llm)
    return {
        "neverrag": NeverRAG(llm, retriever, **common),
        "alwaysrag": AlwaysRAG(llm, retriever, **common),
        "targ": TARG(llm, retriever, tau=tau_single, **common),
        "complexity": ComplexityRouter(llm, retriever, **common),
        "legal_llm": LegalLLMPersona(llm, retriever, **common),
        "legalgate": LegalGate(llm, retriever, router=router, **common),
    }
