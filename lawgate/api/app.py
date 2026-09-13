# -*- coding: utf-8 -*-
"""演示系统·FastAPI 服务（手册 S7.1）。

启动：
    uvicorn lawgate.api.app:app --host 127.0.0.1 --port 8000
或：
    python -m lawgate.api.app --port 8000

接口：
    GET  /health        健康检查 + 后端溯源
    POST /chat          单轮/多轮问答，返回 answer + trace
    POST /chat/stream   同上，但 **SSE 流式**返回（见下）
    GET  /trace/schema  返回 trace 字段说明（答辩演示用）
    POST /verify_case   单独调用案号核验
    POST /temporal      单独调用时效状态机

``/chat/stream`` 的帧协议（每帧都是 ``event: <名>`` + ``data: <JSON>``）：

    event: stage    {"stage":"channel_b|retrieval|generate", "channel":…, "u":…, …}
    event: delta    {"text": "增量文本"}
    event: done     {"answer": 权威全文, "trace": {...}, "provision": …}
    event: error    {"message": "异常类型: 异常信息"}

约定：``delta`` 只是**增量**，首尾空白可能与全文略有出入；客户端必须用
``done.answer`` 收口（不能只靠拼接 delta）。答案主体与 ``/chat`` 完全同源——
两条路径共用 router 的同一份决策逻辑，trace 字段一致。

为什么端点是**同步 def** 而不是 ``async def``：路由 + 生成是 CPU 密集的阻塞调用
（1.5B CPU 推理十几秒），放进事件循环会卡死整个服务。Starlette 对同步生成器会
自动用 ``iterate_in_threadpool`` 逐帧取数，事件循环始终空闲（本项目 D-系列教训）。

设计说明：``ROUTER`` 为进程内单例、惰性初始化，避免导入即加载 1GB 权重；
模型与向量库加载失败时退回 ExtractiveLLM（响应中 ``trace.llm_backend`` 会写明）。
"""
from __future__ import annotations

import json as _json
import os
import threading
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from lawgate.config import get_settings  # noqa: E402

app = FastAPI(title="CA-LegalGate 律核",
              description="三通道异构法律问答路由系统（免训练）",
              version="0.1.0")

_ROUTER: Any = None
_LOAD_ERROR: str | None = None
_STARTED = time.time()
_ROUTER_LOCK = threading.Lock()


def get_router():
    """惰性单例：首次请求时才加载模型、向量库与通道 B。

    加锁防止并发首请求重复执行 build_context()（会加载 1GB+ 的模型权重）。
    """
    global _ROUTER, _LOAD_ERROR
    if _ROUTER is None:
        with _ROUTER_LOCK:
            if _ROUTER is None:
                try:
                    from lawgate.eval.run_exp import build_context

                    ctx = build_context()
                    _ROUTER = ctx.router
                    _LOAD_ERROR = None
                except Exception as exc:  # noqa: BLE001
                    _LOAD_ERROR = f"{type(exc).__name__}: {exc}"
                    raise
    return _ROUTER


class Turn(BaseModel):
    query: str
    answer: str | None = None
    # D26-4：**必须**保留 trace。detect_intent 的槽位继承只认"上一轮 trace.channel=='B'"
    # 的轮次（D10/D12 的设计：只继承经通道 B 事实确认的槽位）。
    # 原先这里只声明 query/answer，pydantic 默认丢弃未声明字段 → UI 明明把 trace
    # 塞进了 history，却在 API 边界被丢掉 → 多轮继承在 /chat 上恒不触发。
    trace: dict | None = None


class ChatReq(BaseModel):
    query: str = Field(..., description="用户法律咨询问题")
    history: list[Turn] = Field(default_factory=list,
                                description="历史轮次（含 trace 时会用于槽位继承）")
    as_of: str | None = Field(None, description="可选：时效判定的评估时点 YYYY-MM-DD")


class CaseReq(BaseModel):
    case_no: str
    claimed_cause: str | None = None


class TemporalReq(BaseModel):
    law_short: str
    article_no: int | None = None
    as_of: str | None = None


@app.get("/health")
def health() -> dict:
    s = get_settings()
    d = {
        "status": "ok", "uptime_s": round(time.time() - _STARTED, 1),
        "provenance": s.provenance(), "router_loaded": _ROUTER is not None,
        "load_error": _LOAD_ERROR,
    }
    # 回答模型与草稿来源的**当前生效值**（不联网、秒级）。
    # 为什么单列一块：换模型/换草稿源之后，"这次服务到底是谁在回答"必须一眼可查，
    # 而不是靠翻配置推（历史上出现过口径漂移，见 docs/deviations.md D21/D29/D30）。
    d["llm"] = {
        "provider": s.llm_provider,
        "backend": s.llm_backend,
        "model": s.model_label("causal"),
        "api_base": s.deepseek_base_url if s.llm_provider == "deepseek" else None,
        "thinking": bool(s.deepseek_thinking) if s.llm_provider == "deepseek" else None,
        "key_present": bool(s.deepseek_api_key),
        "draft_source": s.draft_source,
        "draft_model": s.model_label("draft"),
        "note": ("DeepSeek API 调用需要网络；本字段只反映配置，不发起请求。"
                 "连通性用 scripts/check_deepseek.py 验。"),
    }
    try:
        from lawgate.cache import get_cache

        d["cache"] = get_cache().stats()
    except Exception:  # noqa: BLE001
        pass
    return d


@app.post("/chat")
def chat(req: ChatReq) -> dict:
    r = get_router()
    meta: dict = {}
    if req.as_of:
        meta["temporal"] = {"as_of": req.as_of}
    hist = [t.model_dump() for t in req.history]
    out = r.answer(req.query, hist, meta=meta)
    return {"answer": out.get("answer", ""), "trace": out.get("trace", {}),
            "provision": out.get("provision")}


def _sse_frame(name: str, data: dict) -> str:
    """一帧 SSE。

    ``ensure_ascii=False`` + 显式 charset 是硬要求：中文若被转成 ``\\uXXXX``
    倒还能看，但如果响应头不带 charset 而客户端按 ISO-8859-1 解码，
    中文就会在**浏览器侧**变成乱码（本项目在上一项目上踩过，见 D 系列教训）。
    ``json.dumps`` 本身保证不会有裸换行破坏帧边界。
    """
    return f"event: {name}\ndata: {_json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/chat/stream")
def chat_stream(req: ChatReq) -> StreamingResponse:
    """流式问答（SSE）。事件顺序：stage* → delta* → done[→ error]。"""
    r = get_router()          # 惰性加载在此发生（同步端点 → 在线程池里，不卡事件循环）
    meta: dict = {}
    if req.as_of:
        meta["temporal"] = {"as_of": req.as_of}
    hist = [t.model_dump() for t in req.history]

    def gen():
        try:
            for ev in r.answer_stream(req.query, hist, meta=meta):
                kind = ev.get("event", "delta")
                if kind == "delta":
                    yield _sse_frame("delta", {"text": ev.get("text", "")})
                elif kind == "stage":
                    yield _sse_frame("stage",
                                     {k: v for k, v in ev.items() if k != "event"})
                elif kind == "done":
                    yield _sse_frame("done", {
                        "answer": ev.get("answer", ""),
                        "trace": ev.get("trace", {}),
                        "provision": ev.get("provision"),
                    })
        except Exception as exc:  # noqa: BLE001 — 异常也要成帧，否则前端只看到"连接断了"
            yield _sse_frame("error", {"message": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )


@app.post("/verify_case")
def verify_case(req: CaseReq) -> dict:
    from lawgate.channel.b_case_verify import CaseNoVerifier
    from lawgate.gate.intent import extract_case_no

    parsed = extract_case_no(req.case_no)
    if parsed is None and req.case_no:
        parsed = {"normalized": req.case_no.strip()[:60]}
    v = CaseNoVerifier().verify(parsed, req.claimed_cause)
    return v.to_dict()


@app.post("/temporal")
def temporal(req: TemporalReq) -> dict:
    from datetime import date

    from lawgate.channel.b_temporal import TemporalChecker

    as_of = None
    if req.as_of:
        as_of = date.fromisoformat(req.as_of)
    tc = TemporalChecker()
    if req.article_no:
        v = tc.check_provision(req.law_short, req.article_no, as_of)
    else:
        v = tc.check_law(req.law_short, as_of)
    d = v.to_dict()
    d["replacement_map"] = tc.replacement_map(req.law_short, req.article_no)
    return d


@app.get("/trace/schema")
def trace_schema() -> dict:
    return {
        "channel": "B=确定性结构化 / A=直接生成 / C=语义检索生成",
        "u": "门控决策分（[0,1]，越大越倾向检索）",
        "u_signal": "草稿 logprob 的不确定性信号原始值",
        "u_complexity": "确定性复杂度评分",
        "draft_source": "本次 u 的草稿来自谁：api（回答模型的 API logprobs）/ "
                        "local（本机小模型）/ rule（确定性伪分布）/ "
                        "channel_b（通道 B 未取草稿）",
        "draft_attempts": "换源过程：每一次尝试的来源、耗时、成功与否及失败原因",
        "draft_seconds": "取草稿耗时（秒）",
        "gate_source": "neural_signal | complexity —— 本桶实际采用的闸门",
        "router_mode": "hybrid | signal | complexity",
        "tau_b": "该桶的校准阈值（dev 集校准）",
        "bucket": "b1概念/b2法条/b3案例/b4多轮",
        "decision": "人类可读的判定串",
        "slots": "抽取到的槽位（含继承结果）",
        "slots_inherited": "实际继承的槽位名",
        "validity_status": "时效状态（现行有效/已修订/已废止/尚未生效/查无此条）",
        "case_verify": "案号核验四级判定结果",
        "source_url": "条文官方来源",
        "latency_ms": "端到端延迟",
        "n_retrieval_calls": "本次检索调用次数（0/1）",
        "retrieval": "检索命中明细（doc_id/score/type/law/article）",
    }


def main() -> int:
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    uvicorn.run("lawgate.api.app:app", host=args.host, port=args.port,
                log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
