# -*- coding: utf-8 -*-
"""门控信号 u 的**分布体检**：换草稿模型后必须先跑这个（D38）。

为什么需要它
------------
门控信号 u 的定义是"模型自己对这段回答有多不确定"，它的**绝对尺度由草稿模型决定**
（前 k 个 token 的 top1−top2 裕度 → u = exp(−平均裕度)）。所以：

* `configs/thresholds.json` 的桶级阈值 τ_b 与 TARG 基线的单阈值 τ=0.10，
  **只在它们被校准的那份草稿模型上有效**；
* 一旦换掉草稿模型（例如 D30 的 0.5B → D38 的 `models/Qwen3-4B`），
  u 的分布会整体平移/缩放，**"阈值照旧"是没有依据的**。

本脚本就是为了"换完先看一眼"：它走**产品里那条真实的草稿链**
（`build_draft_source`，与 router / TARG / `e0_diagnostic` 同源——不是另搭一条），
对一组固定问题打印 u 与原始裕度，并输出 Markdown 表便于贴进文档。

内置问题集刻意与 `docs/deviations.md` D30 的那张对照表**逐字一致**，
这样新旧两组 u 可以直接上下对比（D30 记录的是 0.5B 草稿的实测值）。

用法::

    # 默认：走 base.yaml 现行配置（回答模型走本地时，草稿复用回答模型本身）
    E:\\Anaconda\\python.exe scripts\\check_draft_u.py

    # 写进文档（Markdown 表 + 机器可读 JSON）
    E:\\Anaconda\\python.exe scripts\\check_draft_u.py --out docs\\check_draft_u.txt

    # 自定义问题
    E:\\Anaconda\\python.exe scripts\\check_draft_u.py --query "什么是离婚冷静期？" --query "担保法还有用吗？"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# 与 docs/deviations.md D30 的对照表逐字一致（便于新旧并排比较），末尾两条为补充样本
DEFAULT_QUERIES = [
    "什么是离婚冷静期？",
    "《合同法》第52条规定哪些情形合同无效？",
    "（2022）沪01民终12345号 这个案子是怎么回事？",
    "担保法现在还有用吗？",
    "用人单位未签书面劳动合同超过一个月，劳动者可以主张什么？",
    "《民法典》第1077条与已废止的《合同法》第52条有什么不同？",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", action="append", default=[],
                    help="自定义问题（可重复）；不给就用内置的 6 条")
    ap.add_argument("--signal", default="margin",
                    help="要打印的主导信号（margin|entropy|variance|neglogp），默认 margin")
    ap.add_argument("--out", default="", help="把 Markdown 表写到该文件（如 docs\\check_draft_u.txt）")
    ap.add_argument("--json-out", default="", help="同时把原始数字写成 JSON")
    args = ap.parse_args()

    from lawgate.channel.draft_source import build_draft_source
    from lawgate.channel.llm_base import get_llm
    from lawgate.config import get_settings
    from lawgate.gate.signal import SIGNALS, compute_all

    s = get_settings()
    queries = args.query or DEFAULT_QUERIES

    print("=" * 72)
    print("  门控信号 u 分布体检（换草稿模型后必跑，D38）")
    print("=" * 72)
    print(f"  回答模型     : {s.model_label('causal')}  <{s.causal_model}>")
    print(f"  后端 / 精度  : {s.llm_backend} / {s.dtype}（provider={s.llm_provider}）")
    print(f"  草稿配置     : draft_source={s.draft_source}  draft_model={s.draft_model or '(未配置)'}")
    print(f"  k_draft      : {s.k_draft}（信号聚合只用前 8 位）")
    print(f"  现行 τ_b     : {_taus_text(REPO_ROOT / 'configs' / 'thresholds.json')}")
    print("  加载模型（首次提问才加载草稿模型；本机 Qwen3-4B fp16 约 8 GB 内存）…")
    sys.stdout.flush()

    llm = get_llm()
    draft = build_draft_source(llm, s, k_default=int(s.k_draft))
    chain = [src.source for src in getattr(draft, "sources", [])]
    print(f"  草稿链顺序   : {chain or ['(无来源)']}")
    print(f"  回答模型自述 : {json.dumps(llm.describe(), ensure_ascii=False)}")
    sys.stdout.flush()

    rows: list[dict] = []
    for q in queries:
        st = draft.draft_logprobs(q, None, k=int(s.k_draft))
        sig = compute_all(st)
        rows.append({
            "query": q,
            "u_margin": round(sig["margin"], 4),
            "u_entropy": round(sig["entropy"], 4),
            "u_variance": round(sig["variance"], 4),
            "u_neglogp": round(sig["neglogp"], 4),
            "raw_margin": round(sig["raw_margin"], 4),
            "source": getattr(st, "source", "") or "",
            "seconds": round(float(getattr(st, "seconds", 0.0) or 0.0), 2),
            "cached": bool(getattr(st, "cached", False)),
            "n_positions": int(getattr(st, "n", 0) or 0),
            "draft_head": (getattr(st, "text", "") or "")[:40].replace("\n", " "),
        })
        print(f"  ✓ {q[:36]:38s} u({args.signal})={sig[args.signal]:.4f} "
              f"裕度={sig['raw_margin']:.3f} 源={rows[-1]['source']} "
              f"{rows[-1]['seconds']}s", flush=True)

    us = [r["u_margin"] for r in rows]
    spread = (max(us) - min(us)) if us else 0.0
    print("\n  ---- 汇总 ----")
    print(f"  u(margin) 区间 : [{min(us):.4f}, {max(us):.4f}]，极差 {spread:.4f}")
    print(f"  跨 0.10 的条数 : {sum(1 for u in us if u > 0.10)}/{len(us)}"
          "（TARG 单阈值 τ=0.10；若恒为 0 或恒为全部，则该阈值在新草稿上失去区分度）")
    print("  ⚠ 结论：上面只是**分布体检**。是否重校准 τ_b / TARG τ，"
          "取决于你还要不要在**新草稿**上跑 E1/E2（见 docs/deviations.md D38）。")

    if args.out:
        md = _markdown(rows, s, chain or [], args.signal, spread)
        out = Path(args.out)
        if not out.is_absolute():
            out = REPO_ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        print(f"\n  Markdown 表已写入 {out}")
    if args.json_out:
        jp = Path(args.json_out)
        if not jp.is_absolute():
            jp = REPO_ROOT / jp
        jp.parent.mkdir(parents=True, exist_ok=True)
        jp.write_text(json.dumps({"settings": s.provenance(), "rows": rows},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  原始数字已写入 {jp}")
    return 0


def _taus_text(p: Path) -> str:
    try:
        return json.loads(p.read_text(encoding="utf-8")).__repr__()
    except Exception:  # noqa: BLE001
        return "(读不到 configs/thresholds.json)"


def _markdown(rows: list[dict], s, chain: list[str], signal: str, spread: float) -> str:
    lines = [
        "# 门控信号 u 分布体检（换草稿模型后必跑）",
        "",
        f"- 生成时间：{s.run_date}",
        f"- 硬件：{s.hardware_note}",
        f"- 回答模型：`{s.model_label('causal')}`（路径 `{s.causal_model}`）",
        f"- 后端 / 精度：`{s.llm_backend}` / `{s.dtype}`",
        f"- 草稿配置：`draft_source={s.draft_source}`、`draft_model={s.draft_model or '(未配置)'}`",
        f"- 草稿链实际顺序：`{chain or ['(无来源)']}`（`answer_model` = 与回答模型同一份权重）",
        f"- k_draft：{s.k_draft}（信号聚合只用前 8 位）",
        f"- 现行桶级阈值 τ_b：`{_taus_text(REPO_ROOT / 'configs' / 'thresholds.json')}`",
        "",
        "| 查询 | u(margin) | u(entropy) | u(variance) | u(neglogp) | 平均裕度 | 草稿源 | 耗时(s) | 草稿开头 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['query']} | **{r['u_margin']:.4f}** | {r['u_entropy']:.4f} | "
            f"{r['u_variance']:.4f} | {r['u_neglogp']:.4f} | {r['raw_margin']:.3f} | "
            f"`{r['source']}` | {r['seconds']} | `{r['draft_head']}` |")
    lines += [
        "",
        f"u(margin) 极差 **{spread:.4f}**；"
        f"超过 TARG 单阈值 τ=0.10 的样本 {sum(1 for r in rows if r['u_margin'] > 0.10)}/{len(rows)}。",
        "",
        "> **怎么用这张表**：`configs/thresholds.json` 的 τ_b 与 TARG 的单阈值 τ=0.10",
        "> 都是在**另一份草稿模型**上校准的。若上表的 u 全部贴 0（或全部超阈），",
        "> 说明该阈值在新草稿上没有区分度，**重跑 E1/E2 前必须重新校准**；",
        "> 若量级与旧表接近，也要在结论里注明\"草稿模型已变更\"（见 `docs/deviations.md` D38）。",
        "",
        "> ⚠ 与 `docs/deviations.md` D30 的对照：D30 那张表是 **Qwen2.5-0.5B 草稿**的实测",
        "> （u = 0.0161 / 0.0263 / 0.3632 / 0.0790），本表是**现行草稿**的实测，两表不可混用。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
