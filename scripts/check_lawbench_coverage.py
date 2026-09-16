# -*- coding: utf-8 -*-
"""LawBench → lawgate 知识库覆盖核查（实验方案第 1 步）。

回答两个问题：

1. **3-1（法条预测）的金标条文能不能被检索到？**
   金标全部是刑法条号，逐条查 ``legal_facts.db`` 的 ``law_short='刑法'``
   是否存在该条号。缺一条就意味着该题**无论检索多准都答不对**，
   属于知识库缺口而非方法缺陷，必须在报告里单列。

2. **1-2 / 3-6（JEC-QA 单选）的题干提到了哪些法律，库里有几部？**
   题干里出现的《法名》按"去中华人民共和国前缀 + law_alias 归一"后与库中
   ``law_short`` 比对，给出缺失清单与"含缺失法的题目占比"。
   这解释了"即便检索命中也未必能答对"的上限来源，也直接回应方案里
   风险条目"JEC-QA 覆盖非刑法学科 → 缺的部门法同批补齐"。

用法：
    python scripts/check_lawbench_coverage.py            # 全量 500×3 核查
    python scripts/check_lawbench_coverage.py --sample 100
输出：
    data/lawbench/coverage_report.json / coverage_report.md
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lawgate.config import get_settings  # noqa: E402
from lawgate.knowledge.flk_parser import cn2int  # noqa: E402

SRC = ROOT / "data" / "external" / "LawBench" / "data" / "zero_shot"
OUT_DIR = ROOT / "data" / "lawbench"

RE_LAW_QUOTE = re.compile(r"《([^》]{2,40})》")
RE_ART_QUOTE = re.compile(r"第\s*([零〇一二三四五六七八九十百千0-9]+)\s*条")
RE_ANS_ARTICLE = re.compile(r"^法条:刑法第(.+?)条$")

# JEC-QA 题干里的《》**不全是法名**：实测混有书名/影视名/证书名
# （《今天》《美丽祖国》《吴忠画册》《爱火难消》《宅基地使用权证》…），
# 不过滤会把"缺失法名"从十几个吹到上百个，报告直接失真。
# 故只保留后缀像法律文件的名字（法/法典/条例/规定/办法/解释/公约/规则/
# 协定/议定书/条约/通则/惯例/宪章/章程/约法/律）。
LAW_SUFFIX = re.compile(
    r"(法|法典|条例|规定|办法|解释|公约|规则|协定|议定书|条约|通则|惯例|"
    r"宪章|章程|约法|律|法律|判例|政策)$")

PREFIX = "中华人民共和国"


def looks_like_law(name: str) -> bool:
    return bool(LAW_SUFFIX.search(name.strip()))


def norm_law(raw: str) -> str:
    s = raw.strip().strip("《》").strip()
    if s.startswith(PREFIX):
        s = s[len(PREFIX):]
    return s


def load_db():
    s = get_settings()
    conn = sqlite3.connect(s.db_path)
    laws = {r[0] for r in conn.execute(
        "SELECT DISTINCT law_short FROM legal_provisions")}
    # law_short 里存在带括号的版本（如"公司法(2018修正)"），归一成主名便于比对
    laws_plain = {re.sub(r"[（(].*?[)）]", "", x) for x in laws}
    alias = {a: b for a, b in conn.execute(
        "SELECT alias, law_short FROM law_alias")}
    arts = {r[0] for r in conn.execute(
        "SELECT DISTINCT article_no FROM legal_provisions WHERE law_short='刑法'")}
    conn.close()
    return laws, laws_plain, alias, arts


def resolve_law(raw: str, laws_plain: set[str], alias: dict) -> tuple[str, bool]:
    """返回 (归一法名, 库中是否存在)。

    ⚠ 别名表里有**指向不存在法名**的陈旧行（实测：``消费者权益保护法`` 与
    ``消法`` 都映射到 ``消保法``，而库里只有 ``消费者权益保护法``）。若直接
    采信别名值，这两条会被误报为"库中缺失"。故只在**别名目标确实存在**时
    才采用它，否则退回原名判定。
    """
    n = norm_law(raw)
    cands = []
    if n in alias:
        cands.append(alias[n])
    cands.append(n)
    cands.append(re.sub(r"[（(].*?[)）]", "", n))
    for c in cands:
        if c and c in laws_plain:
            return c, True
    return n, False


def gold_articles_31(ans: str) -> list[int]:
    """与 build_lawbench.gold_articles 同口径（官方 ljp_article 写法）。"""
    s = ans.strip()
    m = RE_ANS_ARTICLE.match(s)
    if m:
        out = []
        for chunk in m.group(1).replace("条", "").split("、"):
            chunk = chunk.strip()
            if chunk.isdigit():
                out.append(int(chunk))
            elif chunk:
                n = cn2int(chunk)
                if n:
                    out.append(n)
        return out
    return [cn2int(x) for x in RE_ART_QUOTE.findall(s)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=None,
                    help="每任务只核查前 N 条（默认全量）")
    args = ap.parse_args()

    laws, laws_plain, alias, xingfa_arts = load_db()
    report: dict = {
        "db": {"n_laws": len(laws_plain),
               "has_xingfa": "刑法" in laws_plain,
               "xingfa_n_articles": len(xingfa_arts),
               "xingfa_max_article": max(xingfa_arts) if xingfa_arts else 0},
        "tasks": {},
    }

    # ---------------------------------------------------------- 3-1 条文覆盖
    items31 = json.loads((SRC / "3-1.json").read_text(encoding="utf-8"))
    if args.sample:
        items31 = items31[:args.sample]
    miss31: Counter = Counter()
    n_multi = 0
    n_missing_items = 0
    parsed_ok = 0
    for i, it in enumerate(items31):
        arts = gold_articles_31(it["answer"])
        if not arts:
            miss31["<解析失败>"] += 1
            n_missing_items += 1
            continue
        parsed_ok += 1
        if len(arts) > 1:
            n_multi += 1
        absent = [a for a in arts if a not in xingfa_arts]
        if absent:
            n_missing_items += 1
            for a in absent:
                miss31[a] += 1
    report["tasks"]["3-1"] = {
        "n_items": len(items31),
        "n_gold_parsed": parsed_ok,
        "n_items_with_multi_articles": n_multi,
        "n_items_with_missing_article": n_missing_items,
        "coverage": round(1 - n_missing_items / max(len(items31), 1), 4),
        "missing_articles": dict(sorted(miss31.items(),
                                        key=lambda kv: -kv[1])[:50]),
    }

    # ------------------------------------------- 1-2 / 3-6 法律名覆盖
    for task in ("1-2", "3-6"):
        items = json.loads((SRC / f"{task}.json").read_text(encoding="utf-8"))
        if args.sample:
            items = items[:args.sample]
        cited: Counter = Counter()
        absent: Counter = Counter()
        n_item_with_law = 0
        n_item_with_absent = 0
        for it in items:
            names = {norm_law(x) for x in RE_LAW_QUOTE.findall(it["question"])}
            names = {x for x in names if x and looks_like_law(x)}
            if not names:
                continue
            n_item_with_law += 1
            bad_here = False
            for raw in names:
                n, ok = resolve_law(raw, laws_plain, alias)
                cited[n if ok else raw] += 1
                if not ok:
                    absent[raw] += 1
                    bad_here = True
            if bad_here:
                n_item_with_absent += 1
        report["tasks"][task] = {
            "n_items": len(items),
            "n_items_citing_law": n_item_with_law,
            "n_items_citing_absent_law": n_item_with_absent,
            "n_distinct_laws_cited": len(cited),
            "n_distinct_laws_absent": len(absent),
            "top_absent_laws": absent.most_common(40),
            "top_cited_laws": cited.most_common(30),
            "note": "《》内的非法律名（书名/影视名等）已按后缀白名单过滤，"
                    "见 LAW_SUFFIX",
        }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "coverage_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------ md
    L = ["# LawBench 知识库覆盖核查报告", ""]
    L.append(f"- 知识库法名数：{report['db']['n_laws']}")
    L.append(f"- 含刑法：{report['db']['has_xingfa']}"
             f"（{report['db']['xingfa_n_articles']} 条，最大条号 "
             f"{report['db']['xingfa_max_article']}）")
    t31 = report["tasks"]["3-1"]
    L += ["", "## 3-1 法条预测（金标 = 刑法条号）", "",
          f"- 题量：{t31['n_items']}，金标解析成功 {t31['n_gold_parsed']}",
          f"- 含多条法条的题：{t31['n_items_with_multi_articles']}",
          f"- **金标条文缺失的题：{t31['n_items_with_missing_article']}"
          f"（覆盖 {t31['coverage'] * 100:.1f}%）**", ""]
    if t31["missing_articles"]:
        L.append("缺失条号（条号: 出现次数）：")
        L.append("")
        for a, c in t31["missing_articles"].items():
            L.append(f"- 第{a}条：{c}")
    else:
        L.append("✅ 全部金标条号均存在于知识库。")
    for task in ("1-2", "3-6"):
        t = report["tasks"][task]
        L += ["", f"## {task}（JEC-QA 单选）", "",
              f"- 题量：{t['n_items']}，其中题干引用《法名》的 "
              f"{t['n_items_citing_law']} 条",
              f"- 引用了**库中缺失**的法律的题：{t['n_items_citing_absent_law']}",
              f"- 出现的不同法名：{t['n_distinct_laws_cited']}"
              f"（缺 {t['n_distinct_laws_absent']}）", ""]
        if t["top_absent_laws"]:
            L.append("缺失法名 Top：")
            L.append("")
            for a, c in t["top_absent_laws"]:
                L.append(f"- {a}：{c}")
    (OUT_DIR / "coverage_report.md").write_text("\n".join(L) + "\n",
                                                encoding="utf-8")

    print(json.dumps({k: (v if k == "db" else
                          {kk: vv for kk, vv in v.items()
                           if not kk.startswith("top") and kk != "missing_articles"})
                      for k, v in report.items()}, ensure_ascii=False, indent=2))
    print(f"\n✅ 报告 → {OUT_DIR / 'coverage_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
