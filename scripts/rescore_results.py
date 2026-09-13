# -*- coding: utf-8 -*-
"""离线重判分（D34）：判分器修复后，把已落盘的结果统一重算到新口径。

为什么需要它：实验记录里的 ``correct``/``tvc``/``invalid_law_cited`` 等字段是
**运行时**由 ``lawgate/eval/metrics.score_item`` 算出的。判分器本身出过缺陷
（D34：继承陈述句"【法律沿革】合同法 → 民法典"被误记为非法引用），修复后
必须把受影响的结果**统一**重算——原始答案全部保存在 jsonl 里，重判分是纯
离线确定性计算（零 API 花费、零生成），对所有方法一视同仁。

做什么：
  1. 逐个扫描结果目录下的 ``*.jsonl``（默认排除 _archive/_demo/pilot/timing/备份）；
  2. 对每条记录用**当前** ``score_item`` 重算判分字段（qid → 基准题 meta，
     ctx 按 ``run_exp.RunContext.score_ctx`` 的口径复原）；
  3. 首次重判前把原文件备份到 ``results/_prescore_backup/<相对路径>``；
  4. 同名的 ``summary_*.json`` 用 ``aggregate`` 重算指标字段（保留
     provenance/options/elapsed_s 等运行元数据），并加 ``rescored_at``；
  5. 产出 ``docs/rescore_report.md``：逐文件翻转统计 + 逐方法新旧指标对照。

不做什么：不改答案文本、不改 trace、不动路由/生成类字段（retrieval_used、
latency_ms、n_tokens 等原样保留）、不重画图表（重判后请再跑各实验的
``--analyse-only`` 与 ``e1_plot.py`` 重新生成汇总与图）。

    python scripts/rescore_results.py --dry-run     # 只报告，不写盘
    python scripts/rescore_results.py               # 实际重判 + 备份 + 报告
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lawgate.eval import io as eio  # noqa: E402
from lawgate.eval.metrics import aggregate, score_item  # noqa: E402
from lawgate.eval.run_exp import load_invalid_laws  # noqa: E402
from lawgate.knowledge.seed_cases import CAUSE_ACTIONS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "results/_prescore_backup"

DEFAULT_DIRS = [
    "results/e1", "results/e1_tau0.5", "results/e1_tau0.75", "results/e1_tau1.25",
    "results/e2", "results/e2_a1", "results/e2_a2", "results/e2_a3", "results/e2_a4",
    "results/e3", "results/e5", "results/e6",
    "results/dev_baseline", "results/dev_calib",
]

SCORE_FIELDS = ("correct", "tvc", "case_pass", "invalid_law_cited", "invalid_laws",
                "cited_golden", "key_recall", "refusal", "uncertain", "answer_len",
                "slots_inherited_ok", "score_detail")


def load_items() -> dict:
    """(split, qid) -> item；覆盖所有会被重判的划分。"""
    out: dict = {}
    for p in sorted(glob.glob(str(ROOT / "data/benchmark/*.jsonl"))):
        split = Path(p).stem
        if split in ("all",):
            continue
        for it in eio.load_results(p):
            out[(split, it.get("qid"))] = it
            out.setdefault(("*", it.get("qid")), it)
    return out


def rescore_file(fp: Path, items: dict, invalid_laws: set, dry: bool) -> dict:
    recs = eio.load_results(fp)
    if not recs or "correct" not in recs[0]:
        return {"file": fp, "n": 0, "note": "非判分记录，跳过"}
    flips = {"correct": 0, "tvc": 0, "invalid_law_cited": 0}
    missing = 0
    old_agg = aggregate(recs)
    scored: list[dict] = []          # dry-run 下也要能看到"新口径聚合"
    for r in recs:
        split = str(r.get("split") or "")
        item = items.get((split, r.get("qid"))) or items.get(("*", r.get("qid")))
        if item is None:
            missing += 1
            scored.append(r)
            continue
        tr = r.get("trace") or {}
        cv = tr.get("case_verify")
        ctx = {
            "invalid_laws": invalid_laws,
            "case_causes": list(CAUSE_ACTIONS),
            "case_passed": cv.get("passed") if isinstance(cv, dict) else None,
            "slots_inherited": tr.get("slots_inherited") or [],
        }
        sc = score_item(r.get("answer", ""), item, ctx)
        new = sc.to_dict()
        for k in flips:
            old_v = r.get(k)
            changed = (old_v != new[k]) if k == "tvc" else (bool(old_v) != bool(new[k]))
            if changed:
                flips[k] += 1
        if dry:
            scored.append({**r, **new})
        else:
            r.update(new)
            scored.append(r)
    new_agg = aggregate(scored)
    if not dry:
        rel = fp.relative_to(ROOT)
        bak = BACKUP / rel
        if not bak.exists():
            bak.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(fp, bak)
        eio.write_jsonl(recs, fp, overwrite=True)
        _update_summary(fp, recs, new_agg)
    return {"file": fp, "n": len(recs), "missing_items": missing,
            "flips": flips, "old": old_agg, "new": new_agg}


def _update_summary(fp: Path, recs: list, new_agg: dict) -> None:
    """同名 summary_*.json：只更新指标字段，保留运行元数据。"""
    stem = fp.stem                      # e.g. legalgate_seed0_test / ..._part2
    cand = fp.parent / f"summary_{stem}.json"
    if not cand.exists():
        return
    s = json.loads(cand.read_text(encoding="utf-8"))
    s.update(new_agg)
    s["rescored_at"] = datetime.now().isoformat(timespec="seconds")
    s["scorer_fix"] = "D34（继承陈述句不再计为非法引用）"
    eio.write_json(s, cand, overwrite=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dirs", nargs="*", default=None,
                    help=f"要重判的结果目录（默认 {len(DEFAULT_DIRS)} 个标准目录）")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不写盘")
    ap.add_argument("--report", default="docs/rescore_report.md")
    args = ap.parse_args()

    dirs = args.dirs or DEFAULT_DIRS
    items = load_items()
    invalid_laws = load_invalid_laws()
    print(f"[rescore] 失效法名集合（{len(invalid_laws)}）: {sorted(invalid_laws)}")

    reports = []
    for d in dirs:
        base = ROOT / d
        if not base.exists():
            continue
        for fp in sorted(base.rglob("*.jsonl")):
            reports.append(rescore_file(fp, items, invalid_laws, args.dry_run))

    # ---- 报告 ----
    lines = [
        "# 重判分报告（D34）",
        "",
        f"- 时间：{datetime.now().isoformat(timespec='seconds')}"
        f"{'（dry-run，未写盘）' if args.dry_run else ''}",
        f"- 判分器版本：lawgate/eval/metrics.py（含 D34 继承陈述句豁免）",
        f"- 失效法名集合：{sorted(invalid_laws)}",
        "",
        "| 文件 | n | correct 翻转 | tvc 翻转 | invalid_cite 翻转 | acc 旧→新 | tvc 旧→新 | invalid率 旧→新 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    tot = {"correct": 0, "tvc": 0, "invalid_law_cited": 0}
    for r in reports:
        if r.get("n", 0) == 0:
            continue
        f = r["flips"]
        for k in tot:
            tot[k] += f[k]
        rel = Path(r["file"]).relative_to(ROOT).as_posix()
        o, n = r["old"], r["new"]
        lines.append(
            f"| {rel} | {r['n']} | {f['correct']} | {f['tvc']} | "
            f"{f['invalid_law_cited']} | {o.get('acc')}→{n.get('acc')} | "
            f"{o.get('tvc')}→{n.get('tvc')} | "
            f"{o.get('invalid_law_citation_rate')}→{n.get('invalid_law_citation_rate')} |")
        if r.get("missing_items"):
            lines.append(f"| ⚠️ {rel} 有 {r['missing_items']} 条找不到基准题 meta |")
    lines += [
        "",
        f"**合计翻转**：correct {tot['correct']} 条、tvc {tot['tvc']} 条、"
        f"invalid_law_cited {tot['invalid_law_cited']} 条。",
        "",
        "重判后必须重新生成的下游产物：",
        "- `python scripts/e1_plot.py --split test --tau-scaled-dir results`",
        "- `python scripts/e5_temporal.py --analyse-only`",
        "- `python scripts/e3_multiturn.py --analyse-only --split-e4`",
        "- `python scripts/e2_ablation.py --analyse-only --split test_e1`",
        "",
        "备份：首次重判前的原文件在 `results/_prescore_backup/`（审计线索，勿删）。",
    ]
    txt = "\n".join(lines)
    print(txt)
    if not args.dry_run:
        p = ROOT / args.report
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(txt + "\n", encoding="utf-8")
        print(f"\n报告已写入 {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
