# -*- coding: utf-8 -*-
"""S3.1 意图检测验收：在独立用例集上测"该不该进通道 B"的召回 / 误触率。

手册 S3.1 的验收口径是「意图检测 50 条测试集，召回 ≥90%」。本脚本把这条验收
做成可复核的产物：

  * 用例集：``data/benchmark/intent_cases_v1.json``（66 条，人工标注期望值；
    其中 5 条标了 ``known_gap``，是本实现**已知不覆盖**的边界，计入报告但
    **不计入召回分母**——把已知边界混进分母只会得到一个好看但无意义的数字）。
  * 判定：``lawgate.gate.intent.detect_intent()`` 的 ``slots_complete``
    是否为 True，即"该问句能否被路由到通道 B（确定性结构化通道）"。
  * 输出：``docs/intent_acceptance.md`` + ``results/intent_acceptance.json``。

**为什么用"是否进 B"而不是"分类标签"作为判定**：系统里唯一的**可执行**后果就是
路由决策；一个"分类对了但路由错了"的实现没有任何价值。因此本验收直接测路由后果，
同时把槽位抽取按子集断言（``expect_slots``）单独评分，便于定位失败原因。

用法：
    python scripts/intent_accept.py
    python scripts/intent_accept.py --cases data/benchmark/intent_cases_v1.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lawgate.gate.intent import detect_intent  # noqa: E402

OUT_MD = Path("docs/intent_acceptance.md")
OUT_JSON = Path("results/intent_acceptance.json")


def slot_check(expected: dict | None, got: dict) -> tuple[bool, list[str]]:
    """子集断言：expected 里列出的键必须与 got 完全一致。"""
    if not expected:
        return True, []
    bad: list[str] = []
    for k, v in expected.items():
        if got.get(k) != v:
            bad.append(f"{k}: 期望 {v!r} 实际 {got.get(k)!r}")
    return (not bad), bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="data/benchmark/intent_cases_v1.json")
    args = ap.parse_args()

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))["cases"]
    rows: list[dict] = []
    for c in cases:
        it = detect_intent(c["query"])
        got_slots = it.slots.to_dict()
        ok_route = bool(it.slots_complete) == bool(c["expect_route_b"])
        ok_slots, bad = slot_check(c.get("expect_slots"), got_slots)
        rows.append({
            "id": c["id"], "query": c["query"], "group": c["group"],
            "src": c.get("src", ""), "known_gap": bool(c.get("known_gap")),
            "expect_route_b": bool(c["expect_route_b"]),
            "got_route_b": bool(it.slots_complete),
            "route_b_reason": it.route_b_reason,
            "route_ok": ok_route,
            "slots_expected": c.get("expect_slots"),
            "slots_actual": got_slots,
            "slots_ok": ok_slots, "slots_mismatch": bad,
            "pass": ok_route and ok_slots,
        })

    scored = [r for r in rows if not r["known_gap"]]
    # 召回口径 = 只统计"应当进 B"的样本（41 条里去掉 known_gap）
    should_b = [r for r in scored if r["expect_route_b"]]
    should_not_b = [r for r in scored if not r["expect_route_b"]]
    hit_b = [r for r in should_b if r["got_route_b"]]
    fp_b = [r for r in should_not_b if r["got_route_b"]]
    recall = len(hit_b) / max(len(should_b), 1)
    over_trigger = len(fp_b) / max(len(should_not_b), 1)
    slots_ok = sum(1 for r in scored if r["slots_ok"])
    pass_all = sum(1 for r in scored if r["pass"])

    by_group: dict[str, dict] = defaultdict(
        lambda: {"n": 0, "pass": 0, "should_b": 0, "hit_b": 0, "fp": 0, "known_gap": 0})
    for r in rows:
        g = by_group[r["group"]]
        g["n"] += 1
        if r["known_gap"]:
            g["known_gap"] += 1
            continue
        g["pass"] += int(r["pass"])
        if r["expect_route_b"]:
            g["should_b"] += 1
            g["hit_b"] += int(r["got_route_b"])
        else:
            g["fp"] += int(r["got_route_b"])

    report = {
        "cases_file": args.cases,
        "n_cases": len(rows),
        "n_scored": len(scored),
        "n_known_gap": len(rows) - len(scored),
        "recall_route_B": round(recall, 4),
        "n_should_route_B": len(should_b),
        "n_hit_route_B": len(hit_b),
        "over_trigger_rate": round(over_trigger, 4),
        "n_should_not_route_B": len(should_not_b),
        "n_false_route_B": len(fp_b),
        "slots_subset_accuracy": round(slots_ok / max(len(scored), 1), 4),
        "all_ok_rate": round(pass_all / max(len(scored), 1), 4),
        "manual_threshold": 0.90,
        "recall_pass": recall >= 0.90,
        "by_group": {k: dict(v) for k, v in by_group.items()},
        "rows": rows,
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    lines: list[str] = []
    lines.append("# S3.1 意图检测验收报告")
    lines.append("")
    lines.append(f"> 用例集：`{args.cases}`（{report['n_cases']} 条，其中 "
                 f"{report['n_known_gap']} 条为 **已知边界** `known_gap`，"
                 f"计入报告但不计入召回分母）")
    lines.append("> 脚本：`scripts/intent_accept.py`　"
                 f"机器可读结果：`{OUT_JSON.as_posix()}`")
    lines.append("")
    lines.append("## 1. 判定口径")
    lines.append("")
    lines.append("- **召回**（手册 S3.1 的「召回 ≥90%」）= 在『应当进通道 B』的样本中，"
                 "`detect_intent()` 的 `slots_complete` 为 True 的比例。"
                 "统计的是**路由后果**，不是分类标签：分类对了但路由错了的实现没有价值。")
    lines.append("- **误触率** = 在『不应进通道 B』的样本（纯概念题、主题命中但没要求条文、"
                 "寒暄、仅法律名/仅条号）中被误判为进 B 的比例。D10 修正的核心就是压这个数。")
    lines.append("- **槽位子集断言**：用例里 `expect_slots` 列出的键必须与抽取结果一致"
                 "（未列出的键不参与评分，例如主题词典归属）。")
    lines.append("")
    lines.append("## 2. 总体结果")
    lines.append("")
    verdict = "**PASS**" if report["recall_pass"] else "**FAIL**"
    lines.append("| 指标 | 值 | 门槛 | 判定 |")
    lines.append("|---|---|---|---|")
    lines.append(f"| 通道 B 路由召回 | {report['recall_route_B']:.4f} "
                 f"({report['n_hit_route_B']}/{report['n_should_route_B']}) | ≥ 0.90 | {verdict} |")
    lines.append(f"| 误触率（不该进 B 却进了） | {report['over_trigger_rate']:.4f} "
                 f"({report['n_false_route_B']}/{report['n_should_not_route_B']}) | 越低越好 | — |")
    lines.append(f"| 槽位子集断言一致率 | {report['slots_subset_accuracy']:.4f} | — | — |")
    lines.append(f"| 单条全对率（路由 + 槽位） | {report['all_ok_rate']:.4f} | — | — |")
    lines.append("")
    lines.append("## 3. 分组结果")
    lines.append("")
    lines.append("| 分组 | 条数 | 已知边界 | 应进 B | 实际进 B | 召回 | 误触 | 单条全对 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for g, v in by_group.items():
        rec = (v["hit_b"] / v["should_b"]) if v["should_b"] else float("nan")
        rec_s = f"{rec:.3f}" if v["should_b"] else "—"
        lines.append(f"| {g} | {v['n']} | {v['known_gap']} | {v['should_b']} | "
                     f"{v['hit_b']} | {rec_s} | {v['fp']} | {v['pass']} |")
    lines.append("")
    fails = [r for r in rows if not r["pass"]]
    lines.append("## 4. 明细（未通过的表在最前）")
    lines.append("")
    lines.append("| id | 问句 | 期望进 B | 实际进 B | reason | 失败原因 |")
    lines.append("|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda x: (x["pass"], x["id"])):
        why = []
        if not r["route_ok"]:
            why.append("路由")
        if not r["slots_ok"]:
            why.append("槽位: " + "; ".join(r["slots_mismatch"]))
        tag = "OK" if r["pass"] else " / ".join(why)
        if r["known_gap"]:
            tag = "（已知边界）" + tag
        q = r["query"].replace("|", "\\|")
        lines.append(f"| {r['id']} | {q} | {r['expect_route_b']} | {r['got_route_b']} | "
                     f"{r['route_b_reason'] or '—'} | {tag} |")
    lines.append("")
    lines.append(f"共 {len(fails)} 条未通过（其中已知边界 "
                 f"{sum(1 for r in fails if r['known_gap'])} 条）。")
    lines.append("")

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"written {OUT_MD} / {OUT_JSON}")
    print(f"recall={report['recall_route_B']:.4f} "
          f"({report['n_hit_route_B']}/{report['n_should_route_B']}) "
          f"over_trigger={report['over_trigger_rate']:.4f} "
          f"slots={report['slots_subset_accuracy']:.4f} "
          f"-> {'PASS' if report['recall_pass'] else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
