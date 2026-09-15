# -*- coding: utf-8 -*-
"""演示系统·Gradio 双栏对照界面（手册 S7.2 / S7.3）。

左栏：无门控直答（对照组，模拟"通用模型直接回答法律问题"）
右栏：CA-LegalGate 三通道路由结果
下方：Trace 面板（通道 / u / τ_b / 桶 / 槽位继承 / 时效状态 / 案号核验 / 来源 / 延迟）

启动：
    python -m lawgate.api.ui --port 7860

演示话术（手册 S7.3 的 4 步，界面里做成了 4 个预设按钮）：
  1. 《合同法》第52条 → 左栏照引废止法；右栏出废止警示 + 民法典替代条 + 沿革链
  2. 编造案号 → 右栏"不存在，疑似编造"
  3. 真实案号 + 错误案由 → 右栏"存在但案由不符"
  4. 看 Trace 面板字段逐项亮起

**流式输出**：两栏都是边生成边显示（见 ``respond_stream``），且**两栏生成长度上限相同**
（`gen_max_tokens()`，默认 192 token，见 `docs/deviations.md` D28-3）。
延迟取决于后端：走 DeepSeek API 单条 1–3 s；走本机权重（**现行默认
models/Qwen3-4B**，CPU fp16；更早是 fuzi-mingcha 6.7B，D29）则慢得多，
那种情况下不流式就是长时间白屏。
"""
from __future__ import annotations

import argparse
import html
import json
import os
import queue
import sys
import threading
import time

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import gradio as gr  # noqa: E402

from lawgate.config import get_settings  # noqa: E402

STATE: dict = {"router": None, "ctx": None, "base_llm": None, "error": None,
               "max_tokens": None, "notes": []}


def gen_max_tokens() -> int:
    """两栏统一的**最大生成长度**（唯一来源）。

    为什么必须统一：这是"双栏对照"演示，左栏是无门控直答、右栏是律核门控路径，
    两者若上限不同，长答案看起来"更完整"，对照本身就不公平了。
    取值顺序：`LAWGATE_UI_MAXTOK`（由 scripts/serve_one.py 写进 Settings）→
    `configs/base.yaml: max_new_tokens`（默认见该文件注释）。
    """
    return int(get_settings().max_new_tokens)


def ensure_loaded():
    """惰性加载：首次交互时才加载模型与向量库。

    关键：把生成长度**在构造 router 时就定死**（`RouterOptions.max_new_tokens`）。
    右栏（通道 A/C）的 `max_tokens` 取自 router options（见 `lawgate/router.py`），
    不在这里设就会退回 dataclass 默认值 192 —— 与左栏的 `Settings.max_new_tokens`
    不一致，于是两栏"同题不同长"。评测侧也是同一套约定，见
    `lawgate/eval/run_exp.py`（"LegalGate 的生成参数走 router options，
    必须与其它基线一致，否则对比不公平"）。
    """
    if STATE["router"] is None and STATE["error"] is None:
        try:
            from lawgate.eval.run_exp import build_context
            from lawgate.router import RouterOptions

            mt = gen_max_tokens()
            ctx = build_context(options=RouterOptions(max_new_tokens=mt))
            STATE["ctx"] = ctx
            STATE["router"] = ctx.router
            STATE["base_llm"] = ctx.llm
            STATE["max_tokens"] = mt
        except Exception as exc:  # noqa: BLE001
            STATE["error"] = f"{type(exc).__name__}: {exc}"
    return STATE["router"]


def column_max_tokens(router) -> int:
    """本次提问两栏实际使用的生成上限。

    以 **router options 为准**（右栏的真实取值），左栏显式传同一个数——
    这样"两栏同长"是由构造保证的，而不是靠两处配置碰巧相等。
    """
    opt = getattr(router, "opt", None)
    mt = getattr(opt, "max_new_tokens", None)
    return int(mt or gen_max_tokens())


def real_case_no() -> str:
    """从案号库里取一个真实（合成库内）案号，供预设按钮使用。"""
    import sqlite3

    s = get_settings()
    conn = sqlite3.connect(s.db_path)
    try:
        r = conn.execute(
            "SELECT case_no, cause_action FROM case_registry "
            "WHERE cause_action='民间借贷纠纷' LIMIT 1").fetchone()
    finally:
        conn.close()
    return r[0] if r else "（2022）沪01民终12345号"


PRESETS = {
    "① 已废止法律（杀手用例）": "《合同法》第52条规定哪些情形合同无效？",
    "② 编造案号": "（2099）沪01民终88888号 这个案子是什么案由？",
    "③ 真实案号 + 错误案由": None,   # 运行时填入
    "④ 法律是否仍有效": "担保法现在还有用吗？",
    "⑤ 纯概念题（应走直答/检索，不进 B）": "什么是离婚冷静期？",
    "⑥ 多轮追问（槽位继承）": "那我能主张多少利息？",
}


def build_trace_html(trace: dict) -> str:
    if not trace:
        return "<i>无 trace</i>"

    def esc(x) -> str:
        return html.escape(str(x))

    temporal = trace.get("temporal") or {}
    cv = trace.get("case_verify") or {}
    rows = [
        ("通道", trace.get("channel")),
        ("门控来源", trace.get("gate_source")),
        ("决策", trace.get("decision")),
        ("u（决策分）", trace.get("u")),
        ("u_signal（神经信号）", trace.get("u_signal")),
        ("u_complexity（复杂度）", trace.get("u_complexity")),
        # 草稿来源必须显示（D30）：回答模型换成 API 后，u 可能来自 API 自己的
        # logprobs、本机小模型或确定性伪分布，三者含义不同，混在一起看就是错的
        ("草稿来源", trace.get("draft_source")),
        ("草稿耗时(ms)", (round(float(trace["draft_seconds"]) * 1000)
                          if trace.get("draft_seconds") is not None else None)),
        ("τ_b", trace.get("tau_b")),
        ("桶", trace.get("bucket")),
        ("槽位", json.dumps(trace.get("slots") or {}, ensure_ascii=False)),
        ("继承槽位", trace.get("slots_inherited")),
        ("时效状态", temporal.get("status") or trace.get("validity_status")),
        ("案号核验", cv.get("level")),
        ("来源", trace.get("source_url")),
        ("延迟(ms)", trace.get("latency_ms")),
        ("检索调用", trace.get("n_retrieval_calls")),
    ]
    body = "".join(
        f"<tr><td style='padding:2px 8px;color:#666;white-space:nowrap'>{esc(k)}</td>"
        f"<td style='padding:2px 8px'><b>{esc(v)}</b></td></tr>"
        for k, v in rows)
    warn = ""
    if trace.get("must_show_warning"):
        warn = ("<div style='background:#fff4e5;border-left:4px solid #e8a33d;"
                "padding:6px 10px;margin-bottom:8px'>⚠️ 本条命中时效/核验风险，"
                "必须向用户显式提示</div>")
    retr = trace.get("retrieval") or {}
    hits = retr.get("hits") or []
    hit_html = ""
    if hits:
        items = "".join(
            f"<li>score={h.get('score')} type={esc(h.get('type'))} "
            f"law={esc(h.get('law'))} article={esc(h.get('article'))} "
            f"case={esc(h.get('case_no'))}</li>" for h in hits[:8])
        hit_html = f"<details><summary>检索命中 {len(hits)} 条</summary>" \
                   f"<ul style='font-size:12px'>{items}</ul></details>"
    steps = trace.get("steps") or []
    return (f"{warn}<table style='font-size:13px;line-height:1.5'>{body}</table>"
            f"<div style='font-size:12px;color:#555;margin-top:6px'>"
            f"通道 B 步骤：{esc(steps)}</div>{hit_html}")


def _stage_html(ev: dict) -> str:
    """stage 事件的"正在做什么"提示（流式期间 trace 还没出来，先给进展）。"""
    stage = ev.get("stage")
    if stage == "channel_b":
        return "<i>⚖️ 通道 B（确定性结构化）：查法条/案号库中…</i>"
    if stage == "retrieval":
        return ("<i>🔎 通道 C：检索法条与案例中…"
                f"（u={ev.get('u')} &gt; τ={ev.get('tau_b')}）</i>")
    if stage == "generate":
        return (f"<i>✍️ 通道 {html.escape(str(ev.get('channel')))}：生成中…"
                f"（桶 {html.escape(str(ev.get('bucket')))}，"
                f"u={ev.get('u')} / τ={ev.get('tau_b')}）</i>")
    return "<i>路由中…</i>"


def respond_stream(query: str, history: list, as_of: str):
    """流式版应答（**生成器**）：yield ``(左栏, 右栏, trace_html, state, 备注)``。

    Gradio 的流式输出 = 处理函数是生成器，每次 yield 刷新一次组件。因此旧版
    "算完一次性 return" 的 ``respond`` 已由本函数取代（同签名、同输出位序）。

    两栏为什么必须并行：
      左栏（无门控直答）与右栏（律核）各要十几秒 CPU 生成。若串行，"先左后右"
      或"先右后左"都会让另一栏一直空白。故左栏丢后台线程、分片写进 ``queue``，
      本线程在推进右栏事件的间隙顺手取走——两栏同时长出来。
    """
    history = history or []
    router = ensure_loaded()
    if router is None:
        err = f"**加载失败**：{STATE['error']}"
        yield err, err, "<i>trace 不可用</i>", history, ""
        return
    if not query.strip():
        yield "", "", "<i>请输入问题</i>", history, ""
        return

    meta = {}
    if as_of.strip():
        meta["temporal"] = {"as_of": as_of.strip()}

    # 两栏共用的生成上限（右栏取 router options，左栏显式传同一个数）
    mt = column_max_tokens(router)

    # ---------------------------------------------------------- 左栏：后台线程
    q: queue.Queue = queue.Queue()

    def _left_worker() -> None:
        try:
            # max_tokens 必须显式给：不传就会用 HFLLM 自己的默认值（Settings 值），
            # 与右栏的 router options 是两个来源，容易再次漂移。
            for piece in STATE["base_llm"].generate_stream(query, history,
                                                           context=None,
                                                           max_tokens=mt):
                if piece:
                    q.put(("delta", piece))
            q.put(("end", ""))
        except Exception as exc:  # noqa: BLE001
            q.put(("error", f"{type(exc).__name__}: {exc}"))

    left_buf = ""
    left_err = ""
    left_done = False

    def _take_left(kind: str, payload: str) -> None:
        nonlocal left_buf, left_err, left_done
        if kind == "delta":
            left_buf += payload
        elif kind == "error":
            left_err = f"[左栏生成失败] {payload}"
            left_done = True
        else:
            left_done = True

    def _drain_left() -> None:
        while True:
            try:
                _take_left(*q.get_nowait())
            except queue.Empty:
                return

    threading.Thread(target=_left_worker, daemon=True,
                     name="lawgate-ui-left").start()

    # ---------------------------------------------------------- 右栏：本线程
    right_buf = ""
    right_err = ""
    provision_tail = ""
    trace: dict = {}
    status_html = "<i>路由中…</i>"
    t0 = time.time()
    last_flush = 0.0

    def _snapshot(final: bool = False) -> tuple:
        left = (left_err or left_buf).strip()
        right = (right_err or (right_buf + provision_tail)).strip()
        if trace:
            trace_html = build_trace_html(trace)
        elif right_err:
            trace_html = f"<i>{html.escape(right_err)}</i>"
        else:
            trace_html = status_html
        ch = trace.get("channel")
        note = (f"左栏=无门控直答　右栏=律核（{ch or '路由中'}）　"
                f"两栏生成长度上限均 {mt} token　"
                f"{'已完成' if final else '流式输出中…'}")
        if final:
            note += f"　右栏耗时 {(time.time() - t0) * 1000:.0f} ms"
        return left, right, trace_html, note

    try:
        for ev in router.answer_stream(query, history, meta=meta):
            _drain_left()
            kind = ev.get("event")
            if kind == "stage":
                status_html = _stage_html(ev)
            elif kind == "delta":
                right_buf += ev.get("text", "")
            elif kind == "done":
                # done.answer 是权威全文：用它覆盖分片拼接，避免首尾空白/特殊 token
                # 造成的细微差异（分片只是过程，最终口径以它为准）。
                right_buf = ev.get("answer") or right_buf
                trace = ev.get("trace") or {}
                if ev.get("provision"):
                    provision_tail = "\n\n【结构化命中】" + "；".join(
                        f"《{p.get('law_short')}》{p.get('article_label')}"
                        for p in ev["provision"][:5])
            now = time.time()
            # 节流：token 级事件在 CPU 上约 3–8 个/秒，但缓存命中时会一次涌出很多分片，
            # 不节流会把 WebSocket 打爆（每次 yield 都是一次 Markdown 重渲染）。
            if now - last_flush >= 0.06 or kind in ("done", "stage"):
                last_flush = now
                left, right, trace_html, note = _snapshot()
                yield left, right, trace_html, history, note
    except Exception as exc:  # noqa: BLE001
        right_err = f"[右栏失败] {type(exc).__name__}: {exc}"

    # 右栏收工了，但左栏可能还在生成（两栏各十几秒，谁先完不一定）：
    # 继续把左栏的尾巴取干净，否则界面会停在半句话上。
    while not left_done:
        try:
            _take_left(*q.get(timeout=0.2))
        except queue.Empty:
            continue
        now = time.time()
        if now - last_flush >= 0.06:
            last_flush = now
            left, right, trace_html, note = _snapshot()
            yield left, right, trace_html, history, note

    _drain_left()
    left, right, trace_html, note = _snapshot(final=True)
    history = history + [{"query": query, "answer": right, "trace": trace}]
    yield left, right, trace_html, history, note


def build_demo() -> gr.Blocks:
    rc = real_case_no()
    PRESETS["③ 真实案号 + 错误案由"] = f"{rc} 这个案子是劳动争议，对吗？"

    s = get_settings()
    prov = s.provenance()
    api_mode = prov.get("llm_provider") == "deepseek"
    model_name = prov["causal_model"]
    if api_mode:
        wait_line = ("每次提问约 **1–3 秒**出答案（DeepSeek API，关闭思考模式）；"
                     "命中内容缓存后 ≈ 0.05 s。")
        model_line = (f"**{model_name}**（DeepSeek 官方 API：{prov['llm_api_base']}；"
                      f"思考模式 {'开' if prov.get('llm_thinking') else '关'}）")
        head_note = ("语言模型走 DeepSeek 官方 API，**不需要**在本机加载权重。"
                     f"门控草稿用本机 {prov.get('draft_model')}：**首次提问**会多花时间"
                     "把它加载进内存，之后每次提问都是 1–3 秒。")
    else:
        wait_line = ("CPU 上首字几秒到几十秒出现，整段受生成长度上限约束"
                     "（命中缓存 ≈ 0.1 s）。实测数字见 `docs/deviations.md` D38。")
        model_line = (f"**{model_name}**（{prov.get('causal_model_dtype', 'auto')} 精度，"
                      "本机权重）")
        head_note = ("语言模型在本机 CPU 上运行。门控草稿与回答模型是**同一份权重**"
                     "（手册 S3.5 原设计：草稿与正式生成共享模型与前缀，prefill 不翻倍），"
                     "因此没有\"额外再加载一个草稿模型\"这笔开销。")

    with gr.Blocks(title="CA-LegalGate 律核 · 双栏对照") as demo:
        gr.Markdown(
            "## 律核 LawGate · 双栏对照演示\n"
            "**左栏**：无门控直答（通用模型直接回答，作为对照组）　|　"
            "**右栏**：CA-LegalGate 三通道路由（B 确定性结构化 / A 直接生成 / C 语义检索）\n\n"
            "> 说明书册 S7.3 演示话术：点下面的预设按钮，重点看右栏的**废止警示**、"
            "**案号核验**与下方 **Trace 面板**。\n\n"
            f"> 两栏都是**流式输出**（边生成边显示），且**生成长度上限相同"
            f"（{gen_max_tokens()} token）**：{wait_line}\n\n"
            f"> {head_note}")

        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown("### 左栏：无门控直答（对照）")
                left = gr.Markdown()
            with gr.Column(scale=1):
                gr.Markdown("### 右栏：律核（可解释路由）")
                right = gr.Markdown()

        with gr.Row():
            inp = gr.Textbox(label="你的法律咨询", lines=3,
                             placeholder="例如：《合同法》第52条规定哪些情形合同无效？")
        with gr.Row():
            as_of = gr.Textbox(label="评估时点（可选，YYYY-MM-DD；留空=今天）",
                               value="", scale=1)
            btn = gr.Button("提问", variant="primary", scale=1)

        gr.Markdown("#### 演示预设")
        with gr.Row():
            for label, q in PRESETS.items():
                if q is None:
                    continue
                gr.Button(label, size="sm").click(
                    lambda _q=q: _q, outputs=inp, queue=False)

        gr.Markdown("### Trace 面板（每一步决策可解释、可复核、有官方来源）")
        trace_html = gr.HTML()
        note = gr.Markdown()
        state = gr.State([])

        # 流式：respond_stream 是生成器，Gradio 会把每次 yield 推给浏览器（需要队列，
        # Blocks 默认已开启；此处显式声明，避免以后有人误关）。
        demo.queue()
        btn.click(respond_stream, [inp, state, as_of],
                  [left, right, trace_html, state, note])
        inp.submit(respond_stream, [inp, state, as_of],
                   [left, right, trace_html, state, note])

        # 页脚的口径必须与**实际生效**的模型一致：早期写死的 0.5B 早已过时
        # （D21），后来换成 fuzi-mingcha（D29），再后来默认走 DeepSeek API（D30），
        # 2026-09-13 起默认又回到本机权重 models/Qwen3-4B（D38）——
        # 故这里一律从 Settings.provenance() 动态取，避免再次写错。
        draft_line = (
            f"门控草稿来源 `{prov.get('draft_source')}`"
            f"（本机 {prov.get('draft_model')} → API logprobs → 确定性评分；"
            "回答模型走本地时草稿直接复用回答模型本身，手册 S3.5；"
            "每条 trace 的 **草稿来源** 行会写明本次实际用了哪条，"
            "见 `docs/deviations.md` D30 / D38）。")
        gr.Markdown(
            "---\n"
            f"**说明**：本项目运行在 {prov['hardware']} 环境，语言模型为 "
            f"{model_line}，"
            f"单栏最多生成 {gen_max_tokens()} token，向量模型为 {prov['embed_model']}；"
            "法条语料为人工录入的关键条文"
            "（PENDING_FLK_VERIFICATION），案号库为合成数据。\n\n"
            f"{draft_line}\n\n"
            "详见 `docs/DATA_GAP.md` 与 `docs/deviations.md`。")
    return demo


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7860)
    ap.add_argument("--share", action="store_true")
    args = ap.parse_args()
    build_demo().launch(server_name=args.host, server_port=args.port,
                        share=args.share, show_error=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
