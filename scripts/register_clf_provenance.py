# -*- coding: utf-8 -*-
"""为 CLF（Chinese-Laws-folk）导入的法条补全 `ingest_provenance` 溯源登记。

背景：`lawgate/knowledge/ingest_clf.py` 全量导入 177 部法时**只写** `legal_provisions`
（标记 `source_db='CLF'`），**不写** `ingest_provenance`，导致溯源表只覆盖 G1 的 13 部法，
库里 189 部法却有 176 部"来源不明"——与本项目"来源可核查"的红线冲突。

本脚本把 `source_db='CLF'` 的法律逐部登记进 `ingest_provenance`：

  * `source_kind = 'CLF_DATASET'`，`source_url` 指向数据集仓库；
  * `verification = 'UNVERIFIED'`——**如实**：CLF 是社区整理的数据集，
    只抽检过个别条文（如民法典667 / 反家暴法2 / 消法55 / 宪法33-34），
    未逐条与官方法规数据库比对，**不得**标 VERIFIED；
  * 已登记过的 `law_short`（G1 的 13 部）**不动**，避免覆盖其更严格的溯源。

用法：
    python scripts/register_clf_provenance.py            # 写入
    python scripts/register_clf_provenance.py --dry-run  # 只报告
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lawgate.config import get_settings  # noqa: E402

#: 数据集来源（与 lawgate/knowledge/ingest_clf.py 的 REPO_URL 一致）
CLF_REPO_URL = "https://github.com/taburise/Chinese-Laws-folk"
TODAY = date.today().isoformat()
NOTE = ("来自开源数据集 Chinese-Laws-folk（社区整理版），经 flk_parser 解析入库；"
        "仅抽检个别条文与官方文本一致，未逐条比对，故 verification=UNVERIFIED。")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    s = get_settings()
    db = args.db or s.db_path
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "select law_short, version, count(*) as n_rows from legal_provisions "
        "where source_db='CLF' group by law_short, version order by law_short"
    ).fetchall()
    existing = {r["law_short"] for r in conn.execute(
        "select law_short from ingest_provenance")}

    to_add = [r for r in rows if r["law_short"] not in existing]
    print(f"source_db='CLF' 法律：{len(rows)} 个 (law_short, version) 组合")
    print(f"已登记（G1 的 13 部，跳过）：{len(existing)} 部")
    print(f"待登记：{len(to_add)} 条")

    if not args.dry_run:
        for r in to_add:
            conn.execute(
                """INSERT OR REPLACE INTO ingest_provenance
                   (law_short, version, source_kind, source_url, retrieval_date,
                    verification, verified_by, verified_date, n_provisions,
                    continuity_gaps, note)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (r["law_short"], r["version"] or "", "CLF_DATASET", CLF_REPO_URL,
                 TODAY, "UNVERIFIED", None, None, r["n_rows"], "[]", NOTE))
        conn.commit()
        print(f"已写入 {len(to_add)} 条溯源登记")

    total = conn.execute("select count(*) from ingest_provenance").fetchone()[0]
    laws = conn.execute("select count(distinct law_short) from legal_provisions").fetchone()[0]
    print(f"登记后：ingest_provenance {total} 行 / 库内 {laws} 部法")
    dist = conn.execute(
        "select source_kind, verification, count(*) from ingest_provenance "
        "group by 1,2 order by 3 desc").fetchall()
    for d in dist:
        print("   ", dict(d))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
