# -*- coding: utf-8 -*-
"""端到端自检：**本地兜底模型 fuzi-mingcha-v1_0 这条离线路径是否仍然可用**（真机，非流式 + 流式）。

⚠ 口径说明：**D38（2026-09-13）起，默认回答模型是本机 `models/Qwen3-4B`**
（`configs/base.yaml: causal_model`），fuzi 已退为**降级兜底**（候选目录第二名）。
本脚本因此**显式要求 fuzi 这条兜底路径**——它测的是"Qwen3-4B 缺失时系统还能不能跑"，
而不是默认路径。默认路径的自检是 ``scripts/check_qwen3_e2e.py``；
（D30–D38 之间默认曾是 DeepSeek API，那段历史的自检是 ``check_deepseek.py``。）

与其它自检的分工：
  * scripts/check_backend.py    —— 秒级，回答"配置指向哪个模型"（不加载权重）；
  * scripts/check_qwen3_e2e.py  —— **现行默认路径**（本机 Qwen3-4B，D38）；
  * scripts/check_draft_u.py    —— 门控信号 u 的分布体检（D38 阈值）；
  * scripts/check_deepseek.py   —— 云端可选加速路径（离线段 + `--live`）；
  * 本脚本                       —— 真机加载 13 GB fuzi 兜底权重，回答"兜底路径确实可用、
                                   流式与整段一致、缓存生效"。CPU 上约需 1–3 分钟。

用法（工作目录 = 仓库根）：
    set PYTHONIOENCODING=utf-8 && E:\\Anaconda\\python.exe scripts\\check_fuzi_e2e.py
参数：
    --max-tokens N   单次生成长度（默认 48；越大越慢，192 时 CPU 约 4 分钟/条）
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

# 本轮自检专测本地兜底：把服务形态钉到 hf（否则默认会走 DeepSeek API，本脚本全错）
os.environ["LAWGATE_LLM_PROVIDER"] = "hf"

import psutil  # noqa: E402

RESULTS: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((bool(ok), name, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f"  {detail}" if detail else ""), flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-tokens", type=int, default=48)
    ap.add_argument("--question", default="什么是离婚冷静期？")
    args = ap.parse_args()

    from lawgate.config import get_settings
    from lawgate.eval.run_exp import build_context
    from lawgate.router import RouterOptions

    s = get_settings()
    print(f"模型路径   : {s.causal_model}")
    print(f"精度/设备  : {s.dtype} / {s.device}")
    print(f"生成上限   : {args.max_tokens} token（本次实验值）")
    vm0 = psutil.virtual_memory()
    print(f"加载前内存 : 可用 {vm0.available/2**30:.1f} GB / 总 {vm0.total/2**30:.1f} GB",
          flush=True)

    t0 = time.time()
    ctx = build_context(options=RouterOptions(max_new_tokens=args.max_tokens))
    llm = ctx.llm
    print(f"\n加载耗时   : {time.time()-t0:.1f} s", flush=True)
    print(f"后端自述   : {llm.describe()}", flush=True)
    vm1 = psutil.virtual_memory()
    print(f"加载后内存 : 可用 {vm1.available/2**30:.1f} GB（占 {(vm0.available-vm1.available)/2**30:.1f} GB）",
          flush=True)

    print("\n== 1. 后端确实是本地 fuzi（离线兜底路径）==", flush=True)
    desc = llm.describe()
    check("fuzi" in str(desc.get("llm_name", "")).lower(),
          "回答模型是 fuzi-mingcha-v1_0", f"llm_name={desc.get('llm_name')}")
    check(desc.get("llm_backend") == "hf", "走 HF（transformers）后端",
          f"llm_backend={desc.get('llm_backend')}")
    check(desc.get("llm_dtype") == "float16", "精度为 float16（CPU 内存可行）",
          f"llm_dtype={desc.get('llm_dtype')}")

    print("\n== 2. 非流式回答（/chat 口径）==", flush=True)
    t1 = time.time()
    out = ctx.router.answer(args.question)
    dt1 = time.time() - t1
    ans = out["answer"]
    tr = out["trace"]
    print(f"  提问：{args.question}", flush=True)
    print(f"  通道={tr.get('channel')} 桶={tr.get('bucket')} 延迟={dt1:.1f}s", flush=True)
    print(f"  回答：{ans[:200]}", flush=True)
    check(bool(ans.strip()), "非流式回答非空", f"{len(ans)} 字")
    check(any("\u4e00" <= c <= "\u9fff" for c in ans), "回答含中文（编码正确）")
    check(tr.get("llm_backend") == "hf", "trace 记录了后端", str(tr.get("llm_backend")))

    print("\n== 3. 同问再问一次（缓存命中应秒回且文本一致）==", flush=True)
    t2 = time.time()
    out2 = ctx.router.answer(args.question)
    dt2 = time.time() - t2
    print(f"  第二轮延迟={dt2:.1f}s（首轮 {dt1:.1f}s）", flush=True)
    check(out2["answer"] == ans, "两轮文本完全一致（同键同参）")
    if dt1 < 0.5:
        # 首轮本身就命中了内容缓存（同一台机器上跑过同一个问题）：此时
        # "第二轮更快"毫无信息量，硬断言只会产出假失败（2026-09-13 复测踩到）。
        print("  [SKIP] 第二轮明显更快　（首轮已命中缓存，缓存断言无意义；"
              "想测真实生成请换 --question）", flush=True)
    else:
        check(dt2 < dt1 / 2, "第二轮明显更快（缓存生效）",
              f"{dt2:.1f}s vs {dt1:.1f}s")

    print("\n== 4. 流式（/chat/stream 与 UI 口径）==", flush=True)
    q2 = args.question + "（请再简短说明一次）"
    t3 = time.time()
    pieces: list[str] = []
    stages: list[str] = []
    done_answer = ""
    n_delta = 0
    for ev in ctx.router.answer_stream(q2):
        if ev.get("event") == "stage":
            stages.append(ev.get("stage", ""))
        elif ev.get("event") == "delta":
            pieces.append(ev.get("text", ""))
            n_delta += 1
            if n_delta == 1:
                print(f"  首字延迟：{time.time()-t3:.1f}s", flush=True)
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
    per_tok = dt3 / max(args.max_tokens, 1)
    print(f"  参考吞吐：约 {1/per_tok:.2f} token/s（按上限 {args.max_tokens} 估算）",
          flush=True)
    print(f"  换算 192 token 约需 {per_tok*192:.0f} s/栏", flush=True)

    ok = sum(1 for r in RESULTS if r[0])
    print("\n" + "=" * 70, flush=True)
    print(f"结果：{ok}/{len(RESULTS)} 通过", flush=True)
    for good, name, detail in RESULTS:
        if not good:
            print(f"  未通过：{name} {detail}", flush=True)
    (ROOT / "docs" / "fuzi_e2e.txt").write_text(
        "\n".join([f"[{'PASS' if g else 'FAIL'}] {n}  {d}" for g, n, d in RESULTS])
        + f"\n\n模型：{s.causal_model}\n精度：{s.dtype}\n上限：{args.max_tokens} token\n"
        + f"首轮 {dt1:.1f}s / 缓存轮 {dt2:.1f}s / 流式 {dt3:.1f}s\n",
        encoding="utf-8")
    print(f"报告 → docs/fuzi_e2e.txt", flush=True)
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
