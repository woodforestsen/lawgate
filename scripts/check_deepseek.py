# -*- coding: utf-8 -*-
"""DeepSeek API 后端自检（D30）。

两段，可分开跑：

**离线段（默认，秒级、不花钱、不需要网络）**
  在 127.0.0.1 上起一个 **假的 OpenAI 兼容服务**（``http.server``），把
  ``DeepSeekLLM`` 指过去，逐项验证：

    1. 配置解析：服务形态/模型/密钥/门控来源是否按预期生效；
    2. 整段生成：请求体口径（temperature=0、思考开关、max_tokens）与答案解析；
    3. 流式：真·逐片到达（首片远早于末片）、中文不乱码（SSE 无 charset 的坑）、
       缓存命中时切片口径一致；
    4. 草稿 logprobs：思考模式 + logprobs 参数、按 logprob 降序、信号值落在 [0,1]；
    5. **退化分布检测**：被选中 token 全 0.0 / 候选全 -9999 时必须抛
       ``DegenerateLogprobs``，并由 ``DraftSourceChain`` **自动换到本地源**（写进 attempts）；
    6. 鉴权失败 401 → 人话报错 ``DeepSeekAuthError``；429 → 重试后成功；
    7. 缓存：同题第二次调用**不再发请求**（省钱与可复现的前提）。

**真机段（``--live``，需要 .env 里的 Key 与网络，会花少量额度）**
    真调 DeepSeek：整段 + 流式一致性、真机 logprobs 到底是不是真实分布、
    门控来源链实际选了谁；``--with-local-draft`` 还会加载本机 Qwen2.5-0.5B
    做一次 u 值的**新旧对照**（换草稿模型会不会改变阈值口径，据此判断）。

用法：
    E:\\Anaconda\\python.exe scripts\\check_deepseek.py                 # 只跑离线段
    E:\\Anaconda\\python.exe scripts\\check_deepseek.py --live          # 加真机段
    E:\\Anaconda\\python.exe scripts\\check_deepseek.py --live --with-local-draft
    E:\\Anaconda\\python.exe scripts\\check_deepseek.py --out docs\\check_deepseek.txt

退出码：0 = 全部通过；1 = 有失败项。
"""
from __future__ import annotations

import argparse
import http.server
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

from lawgate.env_setup import apply as _apply_env  # noqa: E402

_apply_env()

from lawgate.cache import GenCache  # noqa: E402
from lawgate.channel.deepseek_llm import (  # noqa: E402
    DeepSeekAuthError, DeepSeekLLM, DegenerateLogprobs)
from lawgate.channel.draft_source import DraftSourceChain, LazySource  # noqa: E402
from lawgate.channel.llm_base import ExtractiveLLM  # noqa: E402
from lawgate.config import get_settings  # noqa: E402
from lawgate.gate.signal import compute_all  # noqa: E402

LINES: list[str] = []
FAILS: list[str] = []

CN = "合同无效的情形包括欺诈、胁迫以及违反法律强制性规定。"

# 离线段用**独立的临时缓存**：既不动 data/kb/gen_cache.db（真实跑分缓存），
# 又让"缓存命中"这一段可断言（每次运行先清空，结果才可比）。
CACHE_PATH = Path("docs/_check_deepseek_cache.db")


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


# ===========================================================================
# 假的 DeepSeek 服务（离线段用）
# ===========================================================================
class _MockHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"      # 不保持连接：流式靠 EOF 收尾，简单可靠

    def log_message(self, *a):         # 静音默认的访问日志
        pass

    # ---------------------------------------------------------------- 工具
    def _json(self, obj: dict, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        # 故意**不带 charset**：真机 DeepSeek 的 SSE 响应头也没有 charset，
        # 客户端若按 ISO-8859-1 解码中文就会乱码（本项目踩过，D-系列教训）。
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------------------------------------------------------------- 主体
    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(n).decode("utf-8"))
        srv = self.server
        srv.requests.append(payload)
        beh = srv.behavior
        if beh == "auth":
            self._json({"error": {"message": "Authentication Fails (governor)"}}, 401)
            return
        if beh == "flaky" and srv.flaky_left > 0:
            srv.flaky_left -= 1
            self._json({"error": {"message": "rate limited"}}, 429)
            return
        if payload.get("stream"):
            self._stream(payload, beh)
        else:
            self._json(self._completion(payload, beh))

    def _completion(self, payload: dict, beh: str) -> dict:
        thinking = (payload.get("thinking") or {}).get("type") == "enabled" or \
            payload.get("reasoning_effort") in ("high", "medium", "low")
        msg: dict = {"role": "assistant", "content": CN}
        if thinking:
            msg["reasoning_content"] = "先判断法律关系，再核对法条编号。"
        body = {
            "id": "mock-1", "object": "chat.completion",
            "model": "deepseek-flash",
            "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 42, "completion_tokens": 18,
                      "total_tokens": 60,
                      "prompt_cache_hit_tokens": 0,
                      "completion_tokens_details": {"reasoning_tokens": 12 if thinking else 0}},
        }
        if payload.get("logprobs"):
            body["choices"][0]["logprobs"] = self._logprobs(payload, beh, thinking)
        return body

    def _logprobs(self, payload: dict, beh: str, thinking: bool) -> dict:
        k = int(payload.get("max_tokens") or 4)
        topk = int(payload.get("top_logprobs") or 5)
        if beh == "degenerate":
            # 真机实测的退化形态：被选中 token 全 0.0、候选全 -9999
            rows = [{"token": "第", "logprob": 0.0, "bytes": [1],
                     "top_logprobs": [{"token": "第", "logprob": 0.0, "bytes": [1]},
                                      {"token": "1", "logprob": -9999.0, "bytes": [2]}]}
                    for _ in range(max(k, 2))]
        else:
            # 真实形态：不同位置置信度不同，且候选按 logprob 降序（服务端保证）
            rows = []
            for i in range(max(k, 2)):
                top = -0.05 * (i % 4)
                rows.append({
                    "token": f"第{i}", "logprob": top, "bytes": [1],
                    "top_logprobs": [
                        {"token": f"第{i}", "logprob": top, "bytes": [1]},
                        {"token": "1", "logprob": top - 1.5 - i * 0.2, "bytes": [2]},
                        {"token": "合同", "logprob": top - 4.2, "bytes": [3]},
                    ][:max(1, topk)],
                })
        key = "reasoning_content" if thinking else "content"
        return {key: rows}

    def _stream(self, payload: dict, beh: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")   # 同样**不带 charset**
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        pieces = [CN[i:i + 6] for i in range(0, len(CN), 6)]
        for i, p in enumerate(pieces):
            frame = {"id": "mock-1", "object": "chat.completion.chunk",
                     "model": "deepseek-flash",
                     "choices": [{"index": 0, "delta": {"content": p},
                                  "finish_reason": None}]}
            self.wfile.write(f"data: {json.dumps(frame, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()
            time.sleep(0.12)       # 让"逐片到达"可被计时验证（不是一次性吐完）
        tail = {"id": "mock-1", "object": "chat.completion.chunk",
                "model": "deepseek-flash", "choices": [],
                "usage": {"prompt_tokens": 42, "completion_tokens": 18,
                          "total_tokens": 60}}
        self.wfile.write(f"data: {json.dumps(tail, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


class MockServer:
    def __init__(self) -> None:
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _MockHandler)
        self.httpd.behavior = "ok"
        self.httpd.requests = []
        self.httpd.flaky_left = 0
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True, name="lawgate-mock-ds")
        self.thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def requests(self) -> list[dict]:
        return self.httpd.requests

    def set_behavior(self, beh: str, flaky_left: int = 0) -> None:
        self.httpd.behavior = beh
        self.httpd.flaky_left = flaky_left

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def _mock_llm(srv: MockServer, cache: GenCache | None = None, **kw) -> DeepSeekLLM:
    return DeepSeekLLM(model="deepseek-v4-flash", api_key="sk-test-not-real",
                       base_url=srv.base_url, max_new_tokens=64,
                       timeout=20, thinking=False, max_retries=3,
                       cache=cache, **kw)


# ===========================================================================
# 0 —— 配置解析
# ===========================================================================
def section_config(s) -> None:
    log("\n== 0. 配置解析（决定最终回答模型到底是谁）==")
    log(f"  llm_backend={s.llm_backend}　服务形态={s.llm_provider}")
    log(f"  API 模型={s.deepseek_model}　地址={s.deepseek_base_url}　"
        f"Key={'已读到' if s.deepseek_api_key else '缺失'}　"
        f"思考模式={'开' if s.deepseek_thinking else '关'}")
    log(f"  门控草稿来源={s.draft_source}　草稿模型={s.model_label('draft')}"
        f"（{s.draft_model_source}）")
    check("服务形态与 base.yaml 一致", s.llm_provider in ("deepseek", "local"),
          f"provider={s.llm_provider}")
    if s.llm_backend == "deepseek":
        check("走 API 时模型名已配置", bool(s.deepseek_model), s.deepseek_model)
        check("走 API 时有密钥", bool(s.deepseek_api_key),
              "没有则会 401（离线段用假服务，不受影响）")
        check("默认关闭思考模式（否则 max_tokens 会被 reasoning 吃光）",
              not s.deepseek_thinking)


# ===========================================================================
# 1 / 2 / 6 —— 整段、流式、缓存
# ===========================================================================
def section_generate(srv: MockServer, cache: GenCache) -> None:
    log("\n== 1. 整段生成（请求体口径 + 答案解析）==")
    srv.set_behavior("ok")
    llm = _mock_llm(srv, cache=cache)
    t0 = time.time()
    ans = llm.generate("《合同法》第52条规定哪些情形合同无效？")
    dt = time.time() - t0
    check("返回正文非空", bool(ans.strip()), repr(ans[:30]))
    check("中文未乱码", "合同无效" in ans, ans[:20])
    req = srv.requests[-1]
    check("temperature=0（与本地贪心口径一致）", req.get("temperature") == 0,
          str(req.get("temperature")))
    check("关思考时发 thinking.type=disabled",
          (req.get("thinking") or {}).get("type") == "disabled",
          json.dumps(req.get("thinking"), ensure_ascii=False))
    check("max_tokens 透传", req.get("max_tokens") == llm.max_new_tokens,
          str(req.get("max_tokens")))
    log(f"  一次调用 {dt:.2f}s　用量={llm.describe()['api_completion_tokens']} token")

    log("\n== 2. 流式输出 ==")
    llm2 = _mock_llm(srv, cache=cache)
    n_before = len(srv.requests)
    pieces: list[str] = []
    marks: list[float] = []
    t0 = time.time()
    for p in llm2.generate_stream("再问一次：什么是离婚冷静期？"):
        pieces.append(p)
        marks.append(time.time() - t0)
    total = time.time() - t0
    text = "".join(pieces)
    check("流式产出多片（真流式，不是一次性吐完）", len(pieces) > 1,
          f"{len(pieces)} 片")
    check("中文未乱码（SSE 头不带 charset 的坑）", "合同无效" in text, repr(text[:20]))
    check("首片远早于末片（逐片到达，非缓冲后一次交付）",
          bool(marks) and marks[0] < total * 0.6,
          f"首片 {marks[0]:.2f}s / 总 {total:.2f}s")
    check("流式请求确实是 stream=true",
          bool(srv.requests[-1].get("stream")) if len(srv.requests) > n_before else False)

    log("\n== 6. 缓存（同题不重复花钱）==")
    n1 = len(srv.requests)
    llm3 = _mock_llm(srv, cache=cache)
    again = llm3.generate("《合同法》第52条规定哪些情形合同无效？",
                          max_tokens=llm.max_new_tokens)
    check("第二次同题不再发请求（命中内容缓存）", len(srv.requests) == n1,
          f"请求数 {n1}→{len(srv.requests)}")
    check("缓存返回的文本与首次一致", again == ans, "")


# ===========================================================================
# 3 / 4 —— 草稿 logprobs 与退化检测
# ===========================================================================
def section_draft(srv: MockServer, cache: GenCache) -> None:
    log("\n== 3. 草稿 logprobs（门控信号的原料）==")
    srv.set_behavior("ok")
    llm = _mock_llm(srv, cache=cache, topk_logprobs=3)
    st = llm.draft_logprobs("什么是诉讼时效？", k=5)
    req = srv.requests[-1]
    check("草稿用思考模式 + logprobs（只有这样才能拿到真实分布）",
          (req.get("thinking") or {}).get("type") == "enabled"
          and req.get("logprobs") is True and req.get("top_logprobs") == 3,
          json.dumps({k: req.get(k) for k in ("thinking", "logprobs", "top_logprobs")},
                     ensure_ascii=False))
    check("解析出多个位置", st.n >= 5, f"n={st.n}")
    check("每个位置的候选按 logprob 降序（signal 依赖这个前提）",
          all(p[0][0] >= p[1][0] for p in st.logprobs if len(p) > 1))
    check("来源标记为 api", st.source == "api", st.source)
    sig = compute_all(st)
    check("margin 不确定性落在 [0,1]",
          0.0 <= sig["margin"] <= 1.0, f"{sig['margin']:.4f}")
    check("entropy 落在 [0,1]", 0.0 <= sig["entropy"] <= 1.0, f"{sig['entropy']:.4f}")
    log(f"  草稿文本={st.text[:40]!r}　u(margin)={sig['margin']:.4f}　"
        f"raw_margin={sig['raw_margin']:.3f}")

    log("\n== 4. 退化分布必须被识破，并且自动换源 ==")
    srv.set_behavior("degenerate")
    llm2 = _mock_llm(srv, cache=cache, topk_logprobs=3)
    raised = None
    try:
        llm2.draft_logprobs("再来一次：什么是诉讼时效？", k=4)
    except Exception as exc:  # noqa: BLE001
        raised = exc
    check("退化分布抛 DegenerateLogprobs（而不是算出个 9999 的假信号）",
          isinstance(raised, DegenerateLogprobs),
          f"{type(raised).__name__}: {str(raised)[:60]}")

    chain = DraftSourceChain([
        LazySource("api", lambda: llm2, note="假服务，返回退化分布"),
        LazySource("local", lambda: ExtractiveLLM(), note="本机兜底（此处用规则后端代替）"),
    ], k_default=4)
    st2 = chain.draft_logprobs("再来一次：什么是诉讼时效？", k=4)
    check("来源链自动换到 local", st2.source == "local", st2.source)
    check("换源过程被完整记录（api 失败原因 + local 成功）",
          len(st2.attempts) == 2 and st2.attempts[0]["ok"] is False
          and st2.attempts[1]["ok"] is True,
          json.dumps(st2.attempts, ensure_ascii=False)[:200])
    check("换源后没有换出假信号：被选中 logprob 不全是 0",
          any(p[0][0] != 0.0 for p in st2.logprobs))


# ===========================================================================
# 5 —— 报错与重试
# ===========================================================================
def section_errors(srv: MockServer, cache: GenCache) -> None:
    log("\n== 5. 报错与重试 ==")
    srv.set_behavior("auth")
    llm = _mock_llm(srv, cache=cache)
    raised = None
    try:
        llm.generate("鉴权失败会怎样？")
    except Exception as exc:  # noqa: BLE001
        raised = exc
    check("401 → DeepSeekAuthError", isinstance(raised, DeepSeekAuthError),
          type(raised).__name__)
    check("报错是人话（指出该去改 .env 的哪一项）",
          raised is not None and "DEEPSEEK_API_KEY" in str(raised),
          str(raised)[:80] if raised else "")

    srv.set_behavior("flaky", flaky_left=2)
    llm2 = _mock_llm(srv, cache=cache)
    n_before = len(srv.requests)
    ans = llm2.generate("429 之后能自己恢复吗？")
    check("429 会重试并最终成功", bool(ans.strip()) and llm2.usage.retries >= 2,
          f"重试 {llm2.usage.retries} 次，请求数 +{len(srv.requests) - n_before}")
    check("重试次数与连续 429 次数相符",
          len(srv.requests) - n_before >= 3, f"+{len(srv.requests) - n_before}")


# ===========================================================================
# 7 —— 真机
# ===========================================================================
def section_live(s, with_local_draft: bool) -> None:
    log("\n== 7. 真机（DeepSeek 官方 API）==")
    if not s.deepseek_api_key:
        check("真机段需要 DEEPSEEK_API_KEY", False, "未读到密钥，跳过")
        return
    llm = DeepSeekLLM(model=s.deepseek_model, api_key=s.deepseek_api_key,
                      base_url=s.deepseek_base_url, max_new_tokens=64,
                      timeout=float(s.deepseek_timeout), thinking=False,
                      max_retries=int(s.deepseek_max_retries))
    log(f"  目标：{s.deepseek_base_url}　模型：{s.deepseek_model}")
    t0 = time.time()
    try:
        ans = llm.generate("用一句话说明《民法典》第153条与《合同法》第52条的关系。",
                           max_tokens=96)
    except Exception as exc:  # noqa: BLE001
        check("真机整段生成", False, f"{type(exc).__name__}: {exc}")
        return
    dt = time.time() - t0
    check("真机整段生成成功且非空", bool(ans.strip()), f"{dt:.2f}s，{len(ans)} 字")
    check("真机返回中文正文", any("\u4e00" <= c <= "\u9fff" for c in ans), ans[:40])
    log(f"  答案前 60 字：{ans[:60]}")
    log(f"  用量：{llm.describe()}")

    # 流式：与整段同键（命中缓存即证明"流式/整段口径一致"，这是本项目的既有约定）
    t0 = time.time()
    pieces = [p for p in llm.generate_stream(
        "用一句话说明《民法典》第153条与《合同法》第52条的关系。", max_tokens=96) if p]
    joined = "".join(pieces)
    check("真机流式有分片", len(pieces) >= 1, f"{len(pieces)} 片，{time.time() - t0:.2f}s")
    check("流式结果与整段结果一致", joined.strip() == ans.strip(),
          f"流式 {len(joined)} 字 / 整段 {len(ans)} 字")
    check("流式中文未乱码", "合同" in joined or "法" in joined, joined[:30])

    # 真机 logprobs：是不是真实分布？（这是门控能不能用 API 信号的关键）
    log("\n  -- 真机 logprobs（门控信号原料）--")
    try:
        st = llm.draft_logprobs("什么是诉讼时效？", k=8)
        sig = compute_all(st)
        check("真机草稿拿到非退化分布", True,
              f"n={st.n} u(margin)={sig['margin']:.4f} raw_margin={sig['raw_margin']:.3f}")
        log(f"  首位置候选：{st.logprobs[0][:3] if st.logprobs else '（空）'}")
        log(f"  草稿文本前 40 字：{st.text[:40]!r}")
    except DegenerateLogprobs as exc:
        check("真机草稿非退化", False, f"退化：{exc}")
        log("  → 说明该模型/参数组合不再返回真实分布，"
            "门控应走 local：把 LAWGATE_DRAFT_SOURCE 设为 local")

    # 来源链实际选了谁：**用产品里那条链**（build_draft_source），而不是在这里另搭一条，
    # 否则"自检通过"只能证明自检脚本自己写对了，证明不了线上顺序。
    log("\n  -- 门控来源链实际选择（产品默认顺序）--")
    from lawgate.channel.draft_source import build_draft_source as _bds

    chain = _bds(llm, s, k_default=int(s.k_draft))
    log(f"  链路顺序 = {[x.source for x in chain.sources]}"
        f"（draft_source={s.draft_source}）")
    st = chain.draft_logprobs("什么是诉讼时效？", k=int(s.k_draft))
    log(f"  本次 u 的草稿来源 = {st.source}　尝试记录 = "
        f"{json.dumps(st.attempts, ensure_ascii=False)}")
    check("来源链给出了明确的来源标记", st.source in ("api", "local", "rule"), st.source)
    check("默认顺序里本机草稿排在 API 之前（D30 实测结论）",
          s.draft_source != "auto" or not chain.sources
          or chain.sources[0].source == "local",
          str([x.source for x in chain.sources]))

    if with_local_draft:
        log("\n  -- 新旧草稿对照（换草稿模型会不会改变阈值口径）--")
        from lawgate.channel.llm_base import HFLLM

        t0 = time.time()
        try:
            local = HFLLM(s.draft_model, device=s.device, max_new_tokens=8, dtype="auto")
        except Exception as exc:  # noqa: BLE001
            check("本机草稿模型可加载", False, f"{type(exc).__name__}: {exc}")
            return
        log(f"  本机草稿模型加载完成 {time.time() - t0:.1f}s：{s.model_label('draft')}")
        qs = ["什么是离婚冷静期？", "《合同法》第52条规定哪些情形合同无效？",
              "（2022）沪01民终12345号 这个案子是劳动争议，对吗？",
              "担保法现在还有用吗？"]
        log("  查询".ljust(40) + "u(api)".ljust(12) + "u(local)".ljust(12) + "差")
        for q in qs:
            try:
                ua = compute_all(llm.draft_logprobs(q, k=int(s.k_draft)))["margin"]
            except DegenerateLogprobs:
                ua = float("nan")
            ul = compute_all(local.draft_logprobs(q, k=int(s.k_draft)))["margin"]
            log(f"  {q[:22].ljust(40)}{ua:<12.4f}{ul:<12.4f}{ua - ul:+.4f}")
        log("  判据：u 的**量级/排序**若明显不同，configs/thresholds.json 的 τ_b"
            "（在旧草稿上校准）需要重新校准；仅当使用默认 hybrid 模式时，"
            "只有 b2 桶吃神经信号，其余桶用确定性复杂度评分（不受影响）。")


# ===========================================================================
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="加跑真机段（需要 Key 与网络）")
    ap.add_argument("--with-local-draft", action="store_true",
                    help="真机段里额外加载本机小模型做 u 值对照（~2 GB 内存）")
    ap.add_argument("--skip-offline", action="store_true", help="跳过离线段")
    ap.add_argument("--out", default=None, help="把全部输出写到该文件（如 docs/check_deepseek.txt）")
    args = ap.parse_args()

    s = get_settings()
    log("=" * 74)
    log("CA-LegalGate · DeepSeek API 后端自检（D30）")
    log("=" * 74)

    srv: MockServer | None = None
    cache = GenCache(path=CACHE_PATH, enabled=True)
    cache.clear()          # 每次从空缓存开始，"命中/未命中"的断言才成立
    try:
        if not args.skip_offline:
            srv = MockServer()
            log(f"\n[离线段] 假 DeepSeek 服务已起于 {srv.base_url}"
                f"（行为可在 ok/degenerate/auth/flaky 间切换）")
            log(f"  离线段使用独立缓存 {CACHE_PATH}（不动 data/kb/gen_cache.db）")
            section_config(s)
            section_generate(srv, cache)
            section_draft(srv, cache)
            section_errors(srv, cache)
    finally:
        if srv is not None:
            srv.stop()
        for suffix in ("", "-wal", "-shm"):
            f = Path(str(CACHE_PATH) + suffix)
            if f.exists():
                try:
                    f.unlink()
                except OSError:
                    pass

    if args.live:
        section_live(s, args.with_local_draft)
    else:
        log("\n（未加 --live：跳过真机段。真机验证请加 --live）")

    log("\n" + "=" * 74)
    if FAILS:
        log(f"结果：{len(FAILS)} 项失败")
        for f in FAILS:
            log(f"  - {f}")
    else:
        log("结果：全部通过")
    log("=" * 74)

    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(LINES) + "\n", encoding="utf-8")
        print(f"[写出] {p}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
