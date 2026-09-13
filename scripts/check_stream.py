# -*- coding: utf-8 -*-
"""流式输出自检（**离线**，不加载任何模型权重，秒级完成）。

跑什么：
  1. ``router.answer_stream`` 的事件序列与内容（stage* → delta+ → done），通道 B 与门控两条路；
  2. ``answer()`` 与 ``answer_stream()`` **同源一致性**：路由判定与 trace 字段必须完全一致
     （历史上最容易漂移的就是"界面走一条路、接口走另一条路"）；
  3. FastAPI ``POST /chat/stream`` 的 SSE 帧协议（真实 ASGI 往返，含中文编码断言）；
  4. 生成期异常也必须成帧（``event: error``），不能只让连接静默断掉。

用 ``ExtractiveLLM``（规则兜底后端）跑，是为了让本脚本**不依赖权重也能秒级回归**；
真正的神经后端流式一致性由真机自检打（原 ``scripts/smoke_stream.py`` 已于 2026-09-16 删除，
见 ``docs/deviations.md`` D37；现替代为 ``scripts/check_deepseek.py --live`` 与
``scripts/check_fuzi_e2e.py``）。

用法：
    python scripts/check_stream.py
    python scripts/check_stream.py --out docs/check_stream.txt

退出码：0 = 全部通过；1 = 有失败项。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
# 自检会打印含 ⚠️ 等 emoji 的中文答案样本；CP936/GBK 控制台编不了它们，
# 强制 stdout 按 UTF-8（errors=replace）输出，避免在非 UTF-8 终端上崩掉整个自检。
if sys.platform.startswith("win"):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)

from lawgate.channel.llm_base import ExtractiveLLM  # noqa: E402
from lawgate.eval.run_exp import build_context  # noqa: E402

LINES: list[str] = []
FAILS: list[str] = []


def log(*parts) -> None:
    line = " ".join(str(p) for p in parts)
    LINES.append(line)
    print(line, flush=True)


def check(name: str, cond: bool, detail: str = "") -> bool:
    mark = "PASS" if cond else "FAIL"
    log(f"  [{mark}] {name}" + (f"　{detail}" if detail else ""))
    if not cond:
        FAILS.append(f"{name}　{detail}")
    return cond


def offline_router():
    """不加载权重、不加载向量库的 router（ExtractiveLLM 兜底）。"""
    return build_context(llm=ExtractiveLLM(), with_retriever=False).router


# ===========================================================================
# 1 / 2 —— 事件序列 + 与非流式入口的一致性
# ===========================================================================
def collect(router, query: str) -> list[dict]:
    return list(router.answer_stream(query, [], meta={}))


def test_events(router) -> None:
    log("\n## 1. 事件序列（stage* → delta+ → done）")

    for query, want_channel in (
            ("《合同法》第52条规定哪些情形合同无效？", "B"),
            ("什么是离婚冷静期", None)):      # None = 门控 A/C 均可
        log(f"\n### 提问：{query}")
        events = collect(router, query)
        kinds = [e.get("event") for e in events]
        log(f"  事件序列：{kinds}")

        check("至少 1 个 stage", kinds.count("stage") >= 1)
        check("至少 1 个 delta", kinds.count("delta") >= 1)
        check("最后一个是 done", bool(kinds) and kinds[-1] == "done")
        check("done 只出现一次", kinds.count("done") == 1)
        check("done 后无其它事件", kinds.index("done") == len(kinds) - 1)
        if "delta" in kinds:
            check("stage 全在第一个 delta 之前",
                  all(k == "stage" for k in kinds[:kinds.index("delta")]))

        done = events[-1]
        trace = done.get("trace") or {}
        answer = done.get("answer") or ""
        check("done.answer 非空", bool(answer.strip()))
        check("done.trace 有必填字段",
              all(k in trace for k in ("channel", "latency_ms", "decision",
                                       "n_retrieval_calls", "slots", "intent")))
        if want_channel:
            check(f"通道 = {want_channel}", trace.get("channel") == want_channel,
                  f"实际 {trace.get('channel')}")
        else:
            check("通道 ∈ {A, C}", trace.get("channel") in ("A", "C"),
                  f"实际 {trace.get('channel')}")
            check("门控字段齐全",
                  all(trace.get(k) is not None
                      for k in ("u", "tau_b", "bucket", "gate_source", "router_mode")))

        # 拼接 delta 必须能拼出全文（允许首尾空白差异）
        joined = "".join(e.get("text", "") for e in events
                         if e.get("event") == "delta")
        check("delta 拼接 ≈ done.answer", joined.strip() == answer.strip(),
              f"拼接 {len(joined)} 字 / 全文 {len(answer)} 字")
        check("答案含中文（编码自检）",
              any("\u4e00" <= c <= "\u9fff" for c in answer), answer[:30])


def test_parity(router) -> None:
    log("\n## 2. answer() 与 answer_stream() 同源一致性")
    for query in ("《合同法》第52条规定哪些情形合同无效？", "什么是离婚冷静期",
                  "（2099）沪01民终88888号 这个案子什么案由"):
        one = router.answer(query, [], meta={})
        done = collect(router, query)[-1]
        t1, t2 = one.get("trace") or {}, done.get("trace") or {}
        same = (t1.get("channel") == t2.get("channel")
                and t1.get("bucket") == t2.get("bucket")
                and t1.get("u") == t2.get("u")
                and t1.get("tau_b") == t2.get("tau_b")
                and t1.get("slots") == t2.get("slots"))
        log(f"\n### 提问：{query}")
        check("路由判定与 trace 完全一致", same,
              f"answer→{t1.get('channel')}/{t1.get('bucket')} "
              f"stream→{t2.get('channel')}/{t2.get('bucket')}")
        check("答案文本一致", (one.get("answer") or "").strip()
              == (done.get("answer") or "").strip())


# ===========================================================================
# 3 / 4 —— SSE 端点（真实 ASGI 往返）
# ===========================================================================
def parse_sse(text: str) -> list[tuple[str, dict]]:
    """把 SSE 报文解析成 [(event, data), …]。

    只认 ``event:`` / ``data:`` 两行 + 空行分帧；``data`` 必须是单行 JSON，
    带裸换行的 JSON 会破坏帧边界，所以这里刻意不做"续行拼接"的宽容处理——
    宽容会把这类 bug 藏起来。
    """
    frames: list[tuple[str, dict]] = []
    name, data = None, None
    for raw in text.split("\n"):
        line = raw.rstrip("\r")
        if line.startswith("event:"):
            name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            payload = line[len("data:"):].strip()
            data = json.loads(payload) if payload else None
        elif line == "" and name is not None:
            frames.append((name, data or {}))
            name, data = None, None
    return frames


def test_sse(router) -> None:
    log("\n## 3. POST /chat/stream（SSE 帧协议，TestClient 真实往返）")
    from fastapi.testclient import TestClient

    from lawgate.api import app as app_mod

    app_mod._ROUTER = router          # 注入离线 router，避免惰性加载真实后端
    app_mod._LOAD_ERROR = None
    client = TestClient(app_mod.app)

    query = "《合同法》第52条规定哪些情形合同无效？"
    with client.stream("POST", "/chat/stream",
                       json={"query": query}) as resp:
        check("HTTP 200", resp.status_code == 200, f"实际 {resp.status_code}")
        ctype = resp.headers.get("content-type", "")
        check("Content-Type 为 text/event-stream 且带 charset",
              "text/event-stream" in ctype and "charset=utf-8" in ctype.lower(), ctype)
        check("禁缓存头存在",
              resp.headers.get("cache-control") == "no-cache",
              str(resp.headers.get("cache-control")))
        raw = b"".join(resp.iter_bytes()).decode("utf-8")

    frames = parse_sse(raw)
    names = [n for n, _ in frames]
    log(f"  帧序列：{names}")
    check("帧序列符合 stage* → delta+ → done",
          names[-1] == "done" and "delta" in names and "stage" in names)
    deltas = "".join(d.get("text", "") for n, d in frames if n == "delta")
    done = [d for n, d in frames if n == "done"][-1]
    check("delta 累积 ≈ done.answer", deltas.strip() == (done.get("answer") or "").strip())
    check("done 带 trace 且通道 = B",
          (done.get("trace") or {}).get("channel") == "B")
    check("中文原样传输（不是 \\uXXXX、不是乱码）",
          "民法典" in (done.get("answer") or "")
          or "废止" in (done.get("answer") or ""),
          (done.get("answer") or "")[:40])
    check("原始报文里没有转义中文",
          "\\u" not in raw, f"含 {raw.count(chr(92) + 'u')} 处 \\u")


def test_sse_error_frame(router) -> None:
    log("\n## 4. 生成期异常也必须成帧（event: error）")
    from fastapi.testclient import TestClient

    from lawgate.api import app as app_mod

    class Boom:
        def answer_stream(self, query, history=None, meta=None):
            yield {"event": "stage", "stage": "generate", "channel": "A"}
            yield {"event": "delta", "text": "开始"}
            raise RuntimeError("模拟生成中途炸掉")

    app_mod._ROUTER = Boom()
    client = TestClient(app_mod.app, raise_server_exceptions=False)
    with client.stream("POST", "/chat/stream", json={"query": "x"}) as resp:
        raw = b"".join(resp.iter_bytes()).decode("utf-8")
    frames = parse_sse(raw)
    names = [n for n, _ in frames]
    log(f"  帧序列：{names}")
    check("异常后仍发出 error 帧", "error" in names)
    err = [d for n, d in frames if n == "error"]
    check("error 帧带可读信息",
          bool(err) and "模拟生成中途炸掉" in err[0].get("message", ""),
          err[0].get("message", "") if err else "无")
    app_mod._ROUTER = router          # 还原，避免影响后续


# ===========================================================================
# 5 —— UI 两栏生成长度上限一致（用户要求：左栏与右栏"同长"）
# ===========================================================================
class _RecordingLLM:
    """只记录"被要求生成多少 token"的假后端（不真生成，故本用例仍是离线秒级）。

    靠**线程名**区分两栏：左栏跑在 ``lawgate-ui-left``，右栏（router）跑在调用线程。
    """

    backend = "stub"
    name = "recording-stub"

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def _log(self, max_tokens) -> None:
        self.calls.append((threading.current_thread().name, max_tokens))

    def generate(self, query, history=None, context=None, max_tokens=None) -> str:
        self._log(max_tokens)
        return "整段答案（stub）"

    def generate_stream(self, query, history=None, context=None, max_tokens=None):
        self._log(max_tokens)
        yield "流式"
        yield "答案（stub）"

    def draft_logprobs(self, query, history=None, k: int = 20):
        from lawgate.channel.llm_base import DraftStats

        return DraftStats(logprobs=[[(-0.1, "甲"), (-0.3, "乙")], [(-0.2, "丙")]],
                          tokens=["甲"], text="甲")

    def build_prompt(self, query, history=None, context=None) -> str:
        return "prompt"

    def describe(self) -> dict:
        return {"llm_backend": self.backend, "llm_name": self.name}


def test_ui_max_tokens_parity() -> None:
    """左栏（无门控直答）与右栏（律核）必须用**同一个** max_new_tokens。

    两处来源不同，历史上正是它们会漂移：
      * 左栏 → ``HFLLM.max_new_tokens``（= ``Settings.max_new_tokens``）
      * 右栏 → ``RouterOptions.max_new_tokens``（通道 A/C 生成，默认 192）
    故这里连**装配链路**一起测：``ui.ensure_loaded()`` 是否把 Settings 的数写进
    RouterOptions，以及 ``respond_stream`` 是否把同一个数传给左栏。
    """
    log("\n## 5. UI 两栏生成长度上限一致")
    import lawgate.eval.run_exp as run_exp_mod
    from lawgate.api import ui as ui_mod
    from lawgate.channel.b_structured import ChannelB
    from lawgate.config import get_settings
    from lawgate.router import LegalGateRouter, RouterOptions

    s = get_settings()
    orig = s.max_new_tokens
    stub = _RecordingLLM()
    captured: dict = {}

    def fake_build_context(**kw):
        """替代真实 build_context：只按传入的 options 造一个假 router（不加载权重）。"""
        captured.update(kw)
        r = LegalGateRouter(stub, None, ChannelB(),
                            taus="configs/thresholds.json",
                            options=kw.get("options"))

        class _Ctx:
            pass

        c = _Ctx()
        c.router = r
        c.llm = stub
        c.retriever = None
        return c

    want = 77                      # 取一个与 192/128/80 都不撞的数，便于区分来源
    s.max_new_tokens = want
    real_build = run_exp_mod.build_context
    run_exp_mod.build_context = fake_build_context
    saved_state = dict(ui_mod.STATE)
    try:
        ui_mod.STATE.update({"router": None, "ctx": None, "base_llm": None,
                             "error": None, "max_tokens": None})
        router = ui_mod.ensure_loaded()
        for _ in ui_mod.respond_stream("什么是离婚冷静期", [], ""):
            pass
    finally:
        run_exp_mod.build_context = real_build
        ui_mod.STATE.update(saved_state)
        s.max_new_tokens = orig

    opts = captured.get("options")
    log(f"  Settings.max_new_tokens = {want}（本次实验值，原值 {orig}）")
    check("ensure_loaded 把 Settings 的数传进 RouterOptions",
          opts is not None and int(getattr(opts, "max_new_tokens", -1)) == want,
          f"实际 {getattr(opts, 'max_new_tokens', None)}")
    check("router options 生效（右栏真实取值）",
          int(router.opt.max_new_tokens) == want,
          f"实际 {router.opt.max_new_tokens}")

    left_calls = [mt for name, mt in stub.calls if name.startswith("lawgate-ui-left")]
    right_calls = [mt for name, mt in stub.calls if not name.startswith("lawgate-ui-left")]
    log(f"  两栏实际请求的 max_tokens：左栏 {left_calls}、右栏 {right_calls}")
    check("左栏确实发起了生成", bool(left_calls))
    check("右栏确实发起了生成", bool(right_calls))
    check("左栏 max_tokens = 统一值", set(left_calls) == {want}, str(set(left_calls)))
    check("右栏 max_tokens = 统一值", set(right_calls) == {want}, str(set(right_calls)))
    check("两栏上限完全一致", set(left_calls) == set(right_calls) == {want})

    # 记录（不作断言）：两个默认值现已统一为 192，但**只要 -UiMaxTokens 一改**
    # 两处就会重新分岔（Settings 变、RouterOptions 默认不变）——所以必须靠显式传递，
    # 而不能依赖"默认值碰巧相等"。本用例用 77 正是为了验证这一点。
    log(f"  参考：RouterOptions().max_new_tokens={RouterOptions().max_new_tokens}"
        f"　base.yaml max_new_tokens={orig}　（本次仍强行用 {want} 验证显式传递生效）")


# ===========================================================================
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/check_stream.txt")
    args = ap.parse_args()

    log("=" * 78)
    log("CA-LegalGate 流式输出自检（离线，ExtractiveLLM）")
    log(time.strftime("%Y-%m-%d %H:%M:%S"))
    log("=" * 78)

    t0 = time.time()
    router = offline_router()
    log(f"router 就绪（llm_backend={router.llm.describe().get('llm_backend')}）")

    test_events(router)
    test_parity(router)
    test_sse(router)
    test_sse_error_frame(router)
    test_ui_max_tokens_parity()

    log("\n" + "=" * 78)
    if FAILS:
        log(f"结果：{len(FAILS)} 项失败")
        for f in FAILS:
            log(f"  - {f}")
    else:
        log("结果：全部通过")
    log(f"耗时 {time.time() - t0:.1f} s")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(LINES) + "\n", encoding="utf-8")
    print(f"\n报告 → {out}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
