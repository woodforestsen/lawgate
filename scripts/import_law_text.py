# -*- coding: utf-8 -*-
"""导入 FLK 官方原文，覆盖人工录入的种子语料（**正式申报前的必做步骤**）。

背景（见 docs/deviations.md D0）：本机网络存在中间层劫持，法律原文一律未从网络
抓取，`data/raw/*.txt` 目前是人工录入的关键条文。本脚本用于在**干净网络环境**
下载官方文件后，把库升级为官方原文。

用法：
    # 1) 把 FLK 下载的文件放进 data/raw/，文件名与被替换的法名对应
    #    （例：下载《民法典》→ data/raw/民法典.txt 或 民法典.docx）
    # 2) 校验连续性、逐条入库、升级溯源状态
    python scripts/import_law_text.py --law-short 民法典 --expect-max 1260 \
        --file data/raw/民法典.docx --verified-by 张三 --dry-run
    python scripts/import_law_text.py --all --verified-by 张三

行为：
  * 解析 → 连续性检查（`--expect-max` 给定期望总条数）
  * 与库内现有条文比对：新增/删除/条号变化逐条列出
  * `--dry-run` 只报告不写库
  * 写库时**只替换该 `law_short` + `version` 的行**，并重建其 FTS 与关键词
  * `ingest_provenance` 升级为 VERIFIED（记录 verified_by / verified_date）
  * 产出 data/kb/import_report_<law>.md
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lawgate.config import get_settings  # noqa: E402
from lawgate.knowledge.audit import audit_law  # noqa: E402
from lawgate.knowledge.build_sqlite import connect  # noqa: E402
from lawgate.knowledge.flk_parser import parse_law_file, validate_continuity  # noqa: E402
from lawgate.knowledge.seed_corpus import LAW_SPECS  # noqa: E402

TODAY = date.today().isoformat()


def find_file(raw_dir: Path, law_short: str) -> Path | None:
    for ext in (".docx", ".txt", ".md"):
        p = raw_dir / f"{law_short}{ext}"
        if p.exists():
            return p
    return None


def existing_articles(conn: sqlite3.Connection, law_short: str,
                      version: str | None = None) -> set[int]:
    sql = "SELECT DISTINCT article_no FROM legal_provisions WHERE law_short=?"
    args: list = [law_short]
    if version:
        sql += " AND version=?"
        args.append(version)
    return {r[0] for r in conn.execute(sql, args)}


def import_one(conn: sqlite3.Connection, raw_dir: Path, law_short: str,
               file_override: str | None, expect_max: int | None,
               verified_by: str, dry_run: bool) -> dict:
    spec = LAW_SPECS.get(law_short)
    if spec is None:
        return {"law_short": law_short, "ok": False,
                "error": f"LAW_SPECS 中没有 {law_short}；请先补充元信息"}

    path = Path(file_override) if file_override else find_file(raw_dir, law_short)
    if not path or not Path(path).exists():
        return {"law_short": law_short, "ok": False,
                "error": f"data/raw/ 下找不到 {law_short}.docx/.txt"}

    provs = parse_law_file(
        Path(path), law_short, spec["law_name"], law_level=spec["law_level"],
        version=spec["version"], effective_date=spec["effective_date"],
        source_url=spec["source_url"], retrieval_date=TODAY,
        publish_date=spec.get("publish_date", ""),
        validity_status=spec["validity_status"])
    cont = validate_continuity(provs, expected_max=expect_max)
    src_text = Path(path).read_text(encoding="utf-8", errors="replace") \
        if Path(path).suffix.lower() != ".docx" else ""
    audit = audit_law(src_text, provs, expected_max=expect_max) if src_text else None

    before = existing_articles(conn, law_short, spec["version"])
    after = {p.article_no for p in provs}
    report = {
        "law_short": law_short, "ok": True, "file": str(path),
        "n_rows": len(provs), "n_articles": len(after),
        "continuity_max": cont["max"], "continuity_missing": cont["missing"][:50],
        "n_missing": len(cont["missing"]),
        "roundtrip_rate": (round(audit.roundtrip_rate, 4) if audit else None),
        "articles_added": sorted(after - before)[:50],
        "articles_removed": sorted(before - after)[:50],
        "dry_run": dry_run,
    }

    if not dry_run:
        cur = conn.cursor()
        cur.execute("DELETE FROM provision_fts WHERE provision_id IN "
                    "(SELECT provision_id FROM legal_provisions "
                    " WHERE law_short=? AND version=?)",
                    (law_short, spec["version"]))
        cur.execute("DELETE FROM legal_provisions WHERE law_short=? AND version=?",
                    (law_short, spec["version"]))
        for p in provs:
            cur.execute(
                """INSERT INTO legal_provisions
                   (law_name,law_short,law_level,book,chapter,section,article_no,
                    article_label,paragraph_no,item_no,item_idx,text,validity_status,
                    effective_date,publish_date,version,source_db,source_url,retrieval_date)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (p.law_name, p.law_short, p.law_level, p.book, p.chapter, p.section,
                 p.article_no, p.article_label, p.paragraph_no, p.item_no, p.item_idx,
                 p.text, p.validity_status, p.effective_date, p.publish_date,
                 p.version, "FLK", p.source_url, p.retrieval_date))
            pid = cur.lastrowid
            cur.execute("INSERT INTO provision_fts(text, law_short, article_no, "
                        "provision_id) VALUES (?,?,?,?)",
                        (p.text, p.law_short, p.article_no, pid))
        conn.execute(
            """INSERT OR REPLACE INTO ingest_provenance
               (law_short,version,source_kind,source_url,retrieval_date,verification,
                verified_by,verified_date,n_provisions,continuity_gaps,note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (law_short, spec["version"], "FLK_OFFICIAL", spec["source_url"], TODAY,
             "VERIFIED", verified_by, TODAY, len(provs),
             json.dumps(cont["missing"][:100]),
             f"官方原文导入，往返一致率 {report['roundtrip_rate']}"))
        conn.commit()
        report["db_rows_after"] = conn.execute(
            "SELECT COUNT(*) FROM legal_provisions WHERE law_short=?",
            (law_short,)).fetchone()[0]
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--law-short", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--file", default=None)
    ap.add_argument("--expect-max", type=int, default=None,
                    help="期望条数上限（民法典为 1260）；不给则不检查缺口")
    ap.add_argument("--verified-by", default="")
    ap.add_argument("--verified-date", default=TODAY)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    s = get_settings()
    raw_dir = Path(s.raw_dir)
    conn = connect(s.db_path)

    laws = list(LAW_SPECS) if args.all else ([args.law_short] if args.law_short else [])
    if not laws:
        ap.print_help()
        return 2

    results = []
    for law in laws:
        em = args.expect_max if len(laws) == 1 else (
            1260 if law == "民法典" else None)
        r = import_one(conn, raw_dir, law, args.file if len(laws) == 1 else None,
                       em, args.verified_by, args.dry_run)
        print(json.dumps(r, ensure_ascii=False))
        results.append(r)

    out = Path(s.kb_dir) / ("import_report_dryrun.md" if args.dry_run
                            else "import_report.md")
    lines = [f"# FLK 官方原文导入报告（{TODAY}）\n",
             f"- dry_run：{args.dry_run}",
             f"- verified_by：{args.verified_by or '（未填写）'}\n",
             "| 法 | 文件 | 行数 | 条数 | continuity_max | 缺口 | 往返一致率 | 状态 |",
             "|---|---|---|---|---|---|---|---|"]
    for r in results:
        if not r.get("ok"):
            lines.append(f"| {r['law_short']} | - | - | - | - | - | - | ❌ {r['error']} |")
            continue
        lines.append(
            f"| {r['law_short']} | {Path(r['file']).name} | {r['n_rows']} | "
            f"{r['n_articles']} | {r['continuity_max']} | {r['n_missing']} | "
            f"{r['roundtrip_rate']} | {'DRY-RUN' if r['dry_run'] else '已入库'} |")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告：{out}")
    conn.close()
    return 0 if all(r.get("ok") for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
