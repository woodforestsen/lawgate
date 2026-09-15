# -*- coding: utf-8 -*-
"""端到端自检：**现行默认模型 Qwen3-4B（本机权重）这条路径是否可用**（真机，非流式 + 流式）。

为什么要有这个脚本（D38）
------------------------
2026-09-13 起，`configs/base.yaml` 把**回答模型与门控草稿都钉在本机 `models/Qwen3-4B`**
（`llm_backend: hf`），这是现行默认形态。本脚本做三件事：

  1. 确认**真的加载了 Qwen3-4B**（而不是静默退到 fuzi 兜底或 HF 缓存里的旧小模型）；
  2. 确认**门控草稿确实复用回答模型本身**（`trace.draft_source == "answer_model"`，
     手册 S3.5 原设计：共享模型与前缀、prefill 不翻倍）——这是 D38 的核心机制，
     它错了门控信号 u 的来源就悄悄换了；
  3. 跑通非流式 + 流式两条路，验证缓存与 delta/done 一致性仍成立。

与其它自检的分工：
  * scripts/check_backend.py   —— 秒级，回答"配置指向哪个模型"（不加载权重）；
  * scripts/check_draft_u.py   —— 只测门控信号 u 的分布（D38 阈值体检）；
  * scripts/check_deepseek.py  —— 云端路径（可选加速，离线段 + `--live`）；
  * scripts/check_fuzi_e2e.py  —— 历史 6.7B 兜底路径；
  * 本脚本                      —— **现行默认路径**，真机加载约 8 GB 权重。

用法（工作目录 = 仓库根）：
    set PYTHONIOENCODING=utf-8 && E:\\Anaconda\\python.exe scripts\\check_qwen3_e2e.py
参数：
    --max-tokens N   单次生成长度（默认 48；CPU 上 192 会到分钟级，越大越慢）
    --question  ...  自定义提问
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lawgate.env_setup import apply  # noqa: E402

apply()

# 本轮自检专测**本机权重**路径：把服务形态钉到 hf。
# （D38 起 base.yaml 默认已是 hf，这里显式再钉一次，防环境里残留 LAWGATE_LLM_PROVIDER=deepseek）
os.environ["LAWGATE_LLM_PROVIDER"] = "hf"

RESULTS: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((bool(ok), name, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f"  {detail}" if detail else ""), flush=True)


def _mem_gb() -> tuple[float, float]:
    """(可用, 总) GB；psutil 缺失时返回 (nan, nan) 而不是崩掉。"""
    try:
        import psutil  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return float("nan"), float("nan")
    vm = psutil.virtual_memory()
    return vm.available / 2**30, vm.total / 2**30


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-tokens", type=int, default=48)
    ap.add_argument("--question", default="什么是离婚冷静期？")
    args = ap.parse_args()

    from lawgate.config import get_settings
    from lawgate.eval.run_exp import build_context
    from lawgate.router import RouterOptions

    s = get_settings()
    print(f"回答模型   : {s.causal_model}")
    print(f"草稿模型   : {s.draft_model}")
    print(f"精度/设备  : {s.dtype} / {s.device}")
    print(f"生成上限   : {args.max_tokens} token（本次实验值）")
    avail0, total0 = _mem_gb()
    print(f"加载前内存 : 可用 {avail0:.1f} GB / 总 {total0:.1f} GB", flush=True)

    t0 = time.time()
    ctx = build_context(options=RouterOptions(max_new_tokens=args.max_tokens))
    llm = ctx.llm
    load_s = time.time() - t0
    print(f"\n加载耗时   : {load_s:.1f} s", flush=True)
    print(f"后端自述   : {llm.describe()}", flush=True)
    avail1, _ = _mem_gb()
    used_gb = avail0 - avail1
    print(f"加载后内存 : 可用 {avail1:.1f} GB（占 {used_gb:.1f} GB）", flush=True)

    print("\n== 1. 后端确实是本机 Qwen3-4B ==", flush=True)
    desc = llm.describe()
    name = str(desc.get("llm_name", ""))
    check("qwen3" in name.lower(), "回答模型是 Qwen3-4B（不是 fuzi / 0.5B 快照）",
          f"llm_name={name}")
    check("4b" in name.lower().replace("-", ""), "规模确实是 4B 档", f"llm_name={name}")
    check(desc.get("llm_backend") == "hf", "走 HF（transformers）后端",
          f"llm_backend={desc.get('llm_backend')}")
    check(str(desc.get("llm_dtype", "")).lower().endswith("float16"),
          "精度为 float16（CPU 内存可行，fp32 会翻倍到 ~16 GB）",
          f"llm_dtype={desc.get('llm_dtype')}")
    # 思考模式必须显式关：Qwen3 模板不传 enable_thinking 就等于开思考，
    # 会把 max_new_tokens 吃在 <think> 前缀上（D38 的关键修复之一）。
    check(desc.get("llm_thinking") is False,
          "思考模式已显式关闭（否则 192 token 会被 <think> 推理吃光）",
          f"llm_thinking={desc.get('llm_thinking')} "
          f"模板支持={desc.get('llm_thinking_supported')}")
    # 4B fp16 ≈ 8 GB；给一个宽松上限，只想抓"落成 fp32（~16 GB）"这种翻倍事故
    if used_gb == used_gb:  # not NaN
        check(used_gb < 13.0, "加载占用 < 13 GB（未落成 fp32）", f"{used_gb:.1f} GB")

    print("\n== 2. 门控草稿复用回答模型本身（D38 核心机制）==", flush=True)
    t1 = time.time()
    out = ctx.router.answer(args.question)
    dt1 = time.time() - t1
    ans = out["answer"]
    tr = out["trace"]
    print(f"  提问：{args.question}", flush=True)
    print(f"  通道={tr.get('channel')} 桶={tr.get('bucket')} 延迟={dt1:.1f}s", flush=True)
    print(f"  草稿源={tr.get('draft_source')} 耗时={tr.get('draft_seconds')}", flush=True)
    print(f"  回答：{ans[:200]}", flush=True)
    ds = str(tr.get("draft_source") or "")
    if tr.get("channel") == "B":
        # 通道 B 是确定性查库，不取草稿——这时 draft_source 记的是 channel_b（未取草稿）
        print("  [SKIP] 草稿源断言（本题走了通道 B，确定性命中，不取草稿）", flush=True)
    else:
        check(ds == "answer_model",
              "草稿源 = answer_model（复用回答模型，未额外加载第二份权重）",
              f"draft_source={ds}")

    print("\n== 3. 非流式回答（/chat 口径）==", flush=True)
    check(bool(ans.strip()), "非流式回答非空", f"{len(ans)} 字")
    check(any("\u4e00" <= c <= "\u9fff" for c in ans), "回答含中文（编码正确）")
    check(tr.get("llm_backend") == "hf", "trace 记录了后端", str(tr.get("llm_backend")))

    print("\n== 4. 同问再问一次（缓存命中应秒回且文本一致）==", flush=True)
    t2 = time.time()
    out2 = ctx.router.answer(args.question)
    dt2 = time.time() - t2
    print(f"  第二轮延迟={dt2:.1f}s（首轮 {dt1:.1f}s）", flush=True)
    check(out2["answer"] == ans, "两轮文本完全一致（同键同参）")
    if dt1 < 0.5:
        print("  [SKIP] 第二轮明显更快　（首轮已命中缓存，缓存断言无意义；"
              "想测真实生成请换 --question）", flush=True)
    else:
        check(dt2 < dt1 / 2, "第二轮明显更快（缓存生效）",
              f"{dt2:.1f}s vs {dt1:.1f}s")

    print("\n== 5. 流式（/chat/stream 与 UI 口径）==", flush=True)
    q2 = args.question + "（请再简短说明一次）"
    t3 = time.time()
    pieces: list[str] = []
    stages: list[str] = []
    done_answer = ""
    n_delta = 0
    first_tok = 0.0
    for ev in ctx.router.answer_stream(q2):
        if ev.get("event") == "stage":
            stages.append(ev.get("stage", ""))
        elif ev.get("event") == "delta":
            pieces.append(ev.get("text", ""))
            n_delta += 1
            if n_delta == 1:
                first_tok = time.time() - t3
                print(f"  首字延迟：{first_tok:.1f}s", flush=True)
        elif ev.get("event") == "done":
            done_answer = ev.get("answer", "")
    dt3 = time.time() - t3
    print(f"  事件顺序：{stages} → delta×{n_delta} → done，总耗时 {dt3:.1f}s", flush=True)
    print(f"  流式回答：{done_answer[:160]}", flush=True)
    check("generate" in stages or "channel_b" in stages, "有 stage 事件", str(stages))
    check(n_delta >= 1, "有 delta 增量", f"{n_delta} 片")
    check(bool(done_answer.strip()), "done.answer 非空")
    check("".join(pieces).strip() == done_answer.strip(),
          "delta 拼接 == done.answer（权威全文一致）")
    check(any("\u4e00" <= c <= "\u9fff" for c in done_answer), "流式回答含中文")

    # 单条吞吐（用于估算 192 token 的演示耗时）
    n_tok = n_delta or max(args.max_tokens, 1)
    per_tok = dt3 / max(n_tok, 1)
    print(f"  参考吞吐：约 {1/per_tok:.2f} token/s（按实际 {n_tok} 片估算）", flush=True)
    print(f"  换算 192 token 约需 {per_tok*192:.0f} s/栏", flush=True)

    ok = sum(1 for r in RESULTS if r[0])
    print("\n" + "=" * 70, flush=True)
    print(f"结果：{ok}/{len(RESULTS)} 通过", flush=True)
    for good, nm, detail in RESULTS:
        if not good:
            print(f"  未通过：{nm} {detail}", flush=True)

    report = "\n".join([f"[{'PASS' if g else 'FAIL'}] {n}  {d}" for g, n, d in RESULTS])
    report += (
        f"\n\n回答模型：{s.causal_model}\n草稿模型：{s.draft_model}\n"
        f"精度：{s.dtype}  设备：{s.device}  思考模式：{'开' if getattr(s, 'local_thinking', False) else '关'}\n"
        f"生成上限：{args.max_tokens} token\n"
        f"加载 {load_s:.1f}s（占 {used_gb:.1f} GB）/ 首轮 {dt1:.1f}s / "
        f"缓存轮 {dt2:.1f}s / 流式 {dt3:.1f}s（首字 {first_tok:.1f}s）\n"
        f"草稿源：{ds or '(通道 B 未取草稿)'}\n"
        f"\n⚠ 阈值提醒（D38）：configs/thresholds.json 的 τ_b 是 0.5B 草稿口径下校准的，\n"
        f"换 Qwen3-4B 后须先用 scripts/check_draft_u.py 看 u 分布再决定是否重校准。\n"
    )
    (ROOT / "docs" / "check_qwen3_4b.txt").write_text(report, encoding="utf-8")
    print("\n报告 → docs/check_qwen3_4b.txt", flush=True)
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
