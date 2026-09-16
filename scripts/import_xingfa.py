# -*- coding: utf-8 -*-
"""导入《中华人民共和国刑法》（现行版，2020-12-26 修正案（十一）后文本）入库。

## 背景

LawBench 3-1（法条预测）金标全部引用刑法条文，而 CLF 语料（179 部）不含刑法，
必须补库。来源为开源结构化数据集 `13098806890/laws-data`（MIT）的
`json/法律/中华人民共和国刑法_20201226.json`（total_articles=452，与官方
公布条数一致），其 `full_text` 为标准「第X条　正文」格式，直接走
flk_parser.parse_law_text 逐行状态机。

## 校验

条号 1..452 严格连续（data/raw/刑法.txt 上游已验；本脚本入库前再验一次）。
"之一/之二" 等修正案条文由解析器作为 suffix 处理，不计入连续性主序列。

## 用法

    python scripts/import_xingfa.py            # 入库
    python scripts/import_xingfa.py --dry-run  # 只校验不写库
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import date
from pathlib import Path

os_environ_ok = True
import os  # noqa: E402

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lawgate.config import get_settings  # noqa: E402
from lawgate.knowledge.build_sqlite import build_keywords, connect  # noqa: E402
from lawgate.knowledge.flk_parser import (  # noqa: E402
    parse_law_text, validate_continuity)

TODAY = date.today().isoformat()
RAW = ROOT / "data" / "raw" / "刑法.txt"
SOURCE_URL = ("https://github.com/13098806890/laws-data  "
              "json/法律/中华人民共和国刑法_20201226.json")
EXPECT_MAX = 452  # 官方公布条数（1997 修订 + 修正案一至十一）


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    text = RAW.read_text(encoding="utf-8")
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    provs = parse_law_text(
        lines, "刑法", "中华人民共和国刑法", law_level="法律",
        version="20201226", effective_date="2021-03-01",
        source_url=SOURCE_URL, retrieval_date=TODAY,
        publish_date="2020-12-26", validity_status="现行有效",
        join_wrapped=True)

    base = sorted({p.article_no for p in provs})
    cont = validate_continuity(provs, expected_max=EXPECT_MAX)
    n_suffix = len(provs) - len(base)
    report = {
        "law_short": "刑法", "version": "20201226",
        "n_rows": len(provs), "n_articles": len(base),
        "continuity_max": cont["max"], "n_missing": len(cont["missing"]),
        "missing_sample": cont["missing"][:20],
        "n_suffix_rows": n_suffix,
        "dry_run": args.dry_run,
    }
    print(json.dumps(report, ensure_ascii=False))

    if cont["missing"]:
        print("❌ 条号存在缺口，拒绝入库")
        return 1
    if args.dry_run:
        return 0

    s = get_settings()
    s.ensure_dirs()
    db_path = Path(s.db_path)
    backup = db_path.with_suffix(db_path.suffix + ".bak_xingfa_%s" % TODAY)
    shutil.copy2(db_path, backup)
    print("备份已建:", backup)

    conn = connect(db_path)
    cur = conn.cursor()
    cur.execute("DELETE FROM provision_fts WHERE provision_id IN "
                "(SELECT provision_id FROM legal_provisions WHERE law_short=?)",
                ("刑法",))
    # provision_keywords 由 build_keywords() 全量重建，无需单独清理
    cur.execute("DELETE FROM legal_provisions WHERE law_short=?", ("刑法",))
    # schema 的 UNIQUE 键不含 suffix（现有库从未存过"之一"条文），
    # 把 suffix 编入 item_no 前缀以满足唯一性：第253条之一第1款 →
    # (253, 1, '之一')；其款项同理 '之一(一)'。article_label 保留完整条号。
    for p in provs:
        cur.execute(
            """INSERT INTO legal_provisions
               (law_name,law_short,law_level,book,chapter,section,article_no,
                article_label,paragraph_no,item_no,item_idx,text,validity_status,
                effective_date,publish_date,version,source_db,source_url,retrieval_date)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (p.law_name, p.law_short, p.law_level, p.book, p.chapter, p.section,
             p.article_no, p.article_label, p.paragraph_no,
             (p.suffix or "") + p.item_no, p.item_idx,
             p.text, p.validity_status, p.effective_date, p.publish_date,
             p.version, "LAWSDATA", p.source_url, p.retrieval_date))
        pid = cur.lastrowid
        cur.execute(
            "INSERT INTO provision_fts(text,law_short,article_no,provision_id) "
            "VALUES (?,?,?,?)", (p.text, p.law_short, p.article_no, pid))
    n_kw = build_keywords(conn)
    cur.execute(
        """INSERT OR REPLACE INTO ingest_provenance
           (law_short,version,source_kind,source_url,retrieval_date,verification,
            verified_by,verified_date,n_provisions,continuity_gaps,note)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        ("刑法", "20201226", "OPEN_SOURCE_DATASET", SOURCE_URL, TODAY,
         "VERIFIED", "条号1..452连续性校验+官方公布条数比对", TODAY,
         len(base), json.dumps(cont["missing"][:100]),
         f"laws-data 开源数据集导入；共 {len(provs)} 行（含款/项/之一条），"
         f"主序列 {len(base)} 条；关键词索引 {n_kw} 行"))
    conn.commit()
    after = conn.execute(
        "SELECT COUNT(DISTINCT article_no) FROM legal_provisions "
        "WHERE law_short='刑法'").fetchone()[0]
    conn.close()
    print(f"✅ 刑法入库完成：主序列 {after} 条，总行数 {len(provs)}")
    return 0


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)).fetchone() is not None


if __name__ == "__main__":
    raise SystemExit(main())
