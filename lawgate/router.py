# -*- coding: utf-8 -*-
"""三通道路由（手册 S3.7）+ 消融开关（手册 S6.6）。

路由顺序：
  1. 意图命中且槽位齐 → 通道 B（0 次检索；查无则降级）
  2. 否则分桶 b → 确定性复杂度评分 u → τ_b
  3. u > τ_b → 通道 C（1 次检索）；否则通道 A（0 次检索）

默认门控信号是**确定性复杂度评分**（E2-A3 实测：全桶复杂度门控与混合门控
在 test_e1 300 条上 acc/RR 逐位一致，signals 不带来增益），因此**默认不取
草稿、不跑神经信号**——草稿是最贵的门控步骤（本机 Qwen3-4B 每条 18-35s）。
只有显式设 ``router_mode="hybrid"/"signal"`` 时才对需要的桶取草稿。

消融开关（E2）全部集中在 ``RouterOptions``，避免在多处复制路由代码：
  A1 disable_channel_b  关闭通道 B（法条/案号题全部走门控）
  A2 single_tau         用单全局阈值替代桶级阈值
  A3 signal / router_mode  切换信号与门控来源（margin / entropy / …）
  A4 k_draft            草稿长度
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from lawgate.channel.b_structured import ChannelB
from lawgate.channel.c_semantic import Retriever
from lawgate.channel.llm_base import BaseLLM
from lawgate.gate.calibrate import BUCKETS, classify_bucket, load_taus
from lawgate.gate.complexity import complexity_score
from lawgate.gate.intent import detect_intent
from lawgate.gate.signal import SIGNALS, normalize_signal


@dataclass
class RouterOptions:
    signal: str = "margin"
    k_draft: int = 20
    max_new_tokens: int = 192
    top_k: int = 8
    rerank: bool = True
    disable_channel_b: bool = False
    single_tau: bool = False
    tau_single: float = 0.10
    tau_scale: float = 1.0
    # 门控模式（E2-A3 后默认 complexity；E0 分桶结论见 gate/complexity.py 模块注释）
    #   complexity : 全部桶用确定性复杂度评分（默认：与混合门控同精度、零草稿开销）
    #   hybrid     : signal_buckets 里的桶用神经信号，其余用复杂度评分（取草稿）
    #   signal     : 全部桶用神经信号（E0 红灯，仅作消融对照）
    router_mode: str = "complexity"
    signal_buckets: tuple = ()

    def to_dict(self) -> dict:
        return asdict(self)


class LegalGateRouter:
    def __init__(self, llm: BaseLLM, retriever: Retriever | None = None,
                 channel_b: ChannelB | None = None,
                 taus: str | Path | dict | None = None,
                 options: RouterOptions | None = None, db_path: str | None = None,
                 draft_llm: BaseLLM | None = None):
        self.llm = llm
        # 草稿（门控信号）来源：默认与回答模型同一个后端；回答换成 API 后
        # 由 ``draft_source.build_draft_source`` 传入来源链（见 docs/deviations.md D30）。
        # 为什么单独一个字段而不是"让 llm 自己想办法"：换源是**决策**，
        # 必须能被 trace 记录、被开关切换（--draft-source），不能藏在后端内部。
        self.draft = draft_llm or llm
        self.retriever = retriever
        self.B = channel_b or ChannelB(db_path)
        self.opt = options or RouterOptions()
        if taus is None:
            self.taus = load_taus()
        elif isinstance(taus, (str, Path)):
            self.taus = load_taus(taus)
        else:
            self.taus = dict(taus)
        self.opt.signal = normalize_signal(self.opt.signal)
        if self.opt.signal not in SIGNALS:
            raise ValueError(f"未知信号 {self.opt.signal}，可选 {list(SIGNALS)}")

    # ------------------------------------------------------------------ 工具
    def _tau_for(self, bucket: str) -> float:
        if self.opt.single_tau:
            base = self.opt.tau_single
        else:
            base = float(self.taus.get(bucket, 0.10))
        return round(base * self.opt.tau_scale, 4)

    def description(self) -> dict:
        d = {"router": "LegalGate", "options": self.opt.to_dict(),
             "taus": self.taus, "n_buckets": len(BUCKETS)}
        d.update(self.llm.describe())
        if self.draft is not self.llm:
            desc = getattr(self.draft, "describe", None)
            d["draft"] = desc() if callable(desc) else {"draft_backend": type(self.draft).__name__}
        return d

    # ------------------------------------------------------------------ 主入口
    def answer(self, query: str, history: list | None = None,
               meta: dict | None = None) -> dict:
        """非流式入口（实验/评测一律走这里；与流式版共享同一份决策逻辑）。"""
        out: dict = {}
        for ev in self._answer_events(query, history, meta, stream=False):
            if ev.get("event") == "done":
                out = {"answer": ev["answer"], "trace": ev["trace"]}
                if "provision" in ev:
                    out["provision"] = ev["provision"]
        if not out:
            raise RuntimeError("router 未产出 done 事件（不应发生）")
        return out

    def answer_stream(self, query: str, history: list | None = None,
                      meta: dict | None = None):
        """流式入口：产出事件字典，供 SSE / UI 增量渲染。

        事件序列：``stage``* → ``delta``+ → ``done``。``done.answer`` 是**权威全文**
        （逐个 delta 的首尾空白可能与它略有出入，前端应以它收口）。

        ``stage`` 事件的 ``stage`` 字段：``channel_b``（确定性命中）/ ``retrieval``
        （走通道 C 前的检索）/ ``generate``（开始生成，带 channel/u/tau_b/bucket）。
        """
        yield from self._answer_events(query, history, meta, stream=True)

    def _answer_events(self, query: str, history: list | None, meta: dict | None,
                       stream: bool):
        """``answer`` 与 ``answer_stream`` 的唯一实现（决策逻辑只此一份）。

        ``stream=False`` 时不做逐 token 生成，只把整段答案当成一个 delta 产出——
        这样两条路径的意图判定、路由、trace 字段、通道 B 的早退条件永远一致，
        不会出现"界面上走 B、接口里却走 C"这种口径漂移。
        """
        t0 = time.time()
        history = history or []
        meta = meta or {}

        # 评测时点（时效题需要）：只取"评估日期"这一无害字段
        as_of = None
        if isinstance(meta.get("temporal"), dict) and meta["temporal"].get("as_of"):
            try:
                as_of = date.fromisoformat(meta["temporal"]["as_of"])
            except ValueError:
                as_of = None

        intent = detect_intent(query, history, db_path=self.B.db_path)
        bucket_hint = meta.get("bucket")

        # ---------------------------------------------------------- 通道 B
        b_eligible = (intent.hit_provision or intent.hit_case_no
                      or intent.slots_complete)
        if not self.opt.disable_channel_b and b_eligible:
            if intent.slots_complete:
                res = self.B.run(intent.slots, query, as_of=as_of)
                if res:
                    tr = res["trace"]
                    bucket = bucket_hint or ("b2" if intent.hit_provision else "b3")
                    tr.update({
                        "u": None, "tau_b": None, "bucket": bucket,
                        "slots_inherited": list(intent.slots.inherited),
                        "decision": f"route=B reason={intent.route_b_reason}",
                        "latency_ms": round((time.time() - t0) * 1000, 1),
                        "n_retrieval_calls": 0,
                        "must_show_warning": bool(res.get("must_show_warning")),
                        "intent": intent.to_dict(),
                        # 通道 B 是查库拼装，根本没取草稿：字段留着并写明原因，
                        # 免得看 trace 的人以为"门控被跳过了却不知道为什么"
                        "draft_source": "channel_b（未取草稿）",
                        "draft_attempts": [],
                    })
                    answer = res["answer"]
                    yield {"event": "stage", "stage": "channel_b", "channel": "B",
                           "bucket": bucket, "decision": tr["decision"]}
                    # 通道 B 是查库拼装，没有 token 流：整段产出一次（诚实，不故弄玄虚）
                    yield {"event": "delta", "text": answer}
                    done_ev: dict = {"event": "done", "answer": answer, "trace": tr}
                    if res.get("provision"):
                        done_ev["provision"] = res["provision"]
                    yield done_ev
                    return

        # ---------------------------------------------------------- 门控
        bucket = bucket_hint if bucket_hint in BUCKETS else classify_bucket({
            "category": "case" if intent.hit_case_no else (
                "provision" if intent.slots.article_no else "concept"),
            "history": history,
            "slots": intent.slots.to_dict(),
            "turn_id": len(history),
        })

        cplx = complexity_score(query, intent.slots, history,
                                category=meta.get("category"))
        u_cplx = cplx.score

        # 草稿（神经信号）是最贵的门控步骤，只在模式真的要用它时才取：
        # complexity 模式永不取草稿；hybrid 只对 signal_buckets 里的桶取。
        needs_signal = (self.opt.router_mode == "signal"
                        or (self.opt.router_mode == "hybrid"
                            and bucket in tuple(self.opt.signal_buckets)))
        stats = None
        if needs_signal:
            stats = self.draft.draft_logprobs(query, history, k=self.opt.k_draft)
        u_signal = float(SIGNALS[self.opt.signal](stats)) if stats is not None else None

        if self.opt.router_mode == "signal":
            u, gate_source = u_signal, "neural_signal"
        elif self.opt.router_mode == "complexity":
            u, gate_source = u_cplx, "complexity"
        else:  # hybrid
            if bucket in tuple(self.opt.signal_buckets):
                u, gate_source = u_signal, "neural_signal"
            else:
                u, gate_source = u_cplx, "complexity"

        tau_b = self._tau_for(bucket)

        retr_info = None
        context = None
        if u > tau_b:
            channel, n_calls = "C", 1
            # 先播报"要去检索了"：检索 + 生成在 CPU 上合计十几秒，静默等待就是黑屏
            yield {"event": "stage", "stage": "retrieval", "channel": "C",
                   "bucket": bucket, "u": round(u, 6), "tau_b": tau_b,
                   "decision": f"[{gate_source}] u={u:.4f} > tau_{bucket}={tau_b} → C"}
            rr = self.retriever.retrieve(query, top_k=self.opt.top_k,
                                         rerank=self.opt.rerank) if self.retriever else None
            context = rr.context if rr else None
            retr_info = rr.to_dict() if rr else None
        else:
            channel, n_calls = "A", 0

        yield {"event": "stage", "stage": "generate", "channel": channel,
               "bucket": bucket, "u": round(u, 6),
               **({"u_signal": round(u_signal, 6)} if u_signal is not None else {}),
               "u_complexity": round(u_cplx, 6), "tau_b": tau_b,
               "gate_source": gate_source,
               "decision": (f"[{gate_source}] u={u:.4f} "
                            f"{'>' if u > tau_b else '<='} "
                            f"tau_{bucket}={tau_b} → {channel}")}

        if stream:
            pieces: list[str] = []
            for piece in self.llm.generate_stream(query, history, context=context,
                                                  max_tokens=self.opt.max_new_tokens):
                if not piece:
                    continue
                pieces.append(piece)
                yield {"event": "delta", "text": piece}
            answer = "".join(pieces).strip()
        else:
            answer = self.llm.generate(query, history, context=context,
                                       max_tokens=self.opt.max_new_tokens)
            yield {"event": "delta", "text": answer}

        trace = {
            "channel": channel,
            "u": round(u, 6),
            "u_signal": round(u_signal, 6) if u_signal is not None else None,
            "u_complexity": round(u_cplx, 6),
            "gate_source": gate_source,
            "router_mode": self.opt.router_mode,
            "complexity_features": cplx.to_dict()["features"],
            "tau_b": tau_b,
            "bucket": bucket,
            "slots": intent.slots.to_dict(),
            "slots_inherited": list(intent.slots.inherited),
            "validity_status": None,
            "case_verify": None,
            "source_url": None,
            "decision": (f"[{gate_source}] u={u:.4f} {'>' if u > tau_b else '<='} "
                         f"tau_{bucket}={tau_b} → {channel}"),
            "latency_ms": round((time.time() - t0) * 1000, 1),
            "n_retrieval_calls": n_calls,
            "signal": self.opt.signal,
            "k_draft": self.opt.k_draft,
            "intent": intent.to_dict(),
            "retrieval": retr_info,
            "llm_backend": self.llm.describe().get("llm_backend"),
            # 门控草稿是谁给的（api / local / rule）+ 换源过程（D30）：
            # 没有这两项，事后无法判断"这条 u 是不是换了模型之后口径变了"。
            # complexity 模式不取草稿：写明跳过原因，避免被当成漏记。
            "draft_source": (getattr(stats, "source", "") or "answer_model")
                            if stats is not None else "skipped（complexity 门控无需草稿）",
            "draft_attempts": list(getattr(stats, "attempts", []) or []) if stats is not None else [],
            "draft_seconds": round(float(getattr(stats, "seconds", 0.0) or 0.0), 3)
                             if stats is not None else 0.0,
            "draft_text": stats.text[:200] if stats is not None else "",
        }
        yield {"event": "done", "answer": answer, "trace": trace}

    # ------------------------------------------------------------------ 便利
    def answer_many(self, items: list[dict], as_of_key: str = "temporal") -> list[dict]:
        out = []
        for it in items:
            out.append(self.answer(it.get("query", ""), it.get("history"),
                                   meta=it))
        return out

    def close(self) -> None:
        self.B.close()
