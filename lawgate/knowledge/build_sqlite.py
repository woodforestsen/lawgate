# -*- coding: utf-8 -*-
"""知识库构建（手册 S2.1 + S2.3 + S2.5）：法条库 / 案号真值库 / 沿革表 / 词典。

用法：
    python -m lawgate.knowledge.build_sqlite                # 全量重建（种子语料）
    python -m lawgate.knowledge.build_sqlite --from-raw     # 以 data/raw/*.txt 为准
    python -m lawgate.knowledge.build_sqlite --report       # 只重算 qa_report

产物：
    data/kb/legal_facts.db
    data/raw/*.txt              （种子语料落盘，供 --from-raw 与人工校对）
    data/raw/sources.json       （每源 source_url / retrieval_date / 状态）
    data/kb/qa_report.md        （S2.4 验收证据）
    data/kb/manual_audit.csv    （120 条人工抽验清单）
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lawgate.config import get_settings  # noqa: E402
from lawgate.knowledge.audit import audit_law, sample_for_manual_audit  # noqa: E402
from lawgate.knowledge.flk_parser import (  # noqa: E402
    cn2int,
    parse_law_file,
    parse_law_text,
    validate_continuity,
)
from lawgate.knowledge.seed_corpus import (  # noqa: E402
    LAW_ALIAS_SEED,
    LAW_SPECS,
    LIFECYCLE_SEED,
    SEED_VERSION,
    SUPERSEDE_MAP,
    TOPIC_KEYWORDS_SEED,
)
from lawgate.knowledge.seed_cases import generate_cases, render_full_text  # noqa: E402

TODAY = date.today().isoformat()

# 官方原文的条数上限（用于连续性检查的期望值）。
# 民法典 1260 条是手册的硬验收指标；其余法律全文尚未导入，不设期望上限。
EXPECTED_MAX = {"民法典": 1260}

# 未导入全文的法律：种子语料只覆盖关键条文，需在报告中标记为"部分"
PARTIAL_LAWS = {"合同法", "公司法", "公司法(2018修正)", "劳动合同法",
                "民事诉讼法", "物权法", "担保法", "婚姻法", "侵权责任法",
                "继承法", "收养法", "民法典时间效力规定"}


def connect(db_path: str | Path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    here = Path(__file__).parent
    for f in ("schema.sql", "schema_ext.sql"):
        conn.executescript((here / f).read_text(encoding="utf-8"))
    # 幂等增量列
    for stmt in (
        # 项序整数索引：item_no（'(一)'）无法按数字序排序，见 D7
        "ALTER TABLE legal_provisions ADD COLUMN item_idx INTEGER DEFAULT 0",
        "ALTER TABLE case_registry ADD COLUMN data_source TEXT DEFAULT 'unknown'",
        "ALTER TABLE judgments ADD COLUMN data_source TEXT DEFAULT 'unknown'",
    ):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass
    conn.commit()


def write_raw_files(raw_dir: Path) -> dict:
    """把种子语料落盘为 data/raw/*.txt + sources.json（保留真实导入通道）。"""
    raw_dir.mkdir(parents=True, exist_ok=True)
    sources: dict = {}
    for short, spec in LAW_SPECS.items():
        (raw_dir / f"{short}.txt").write_text(
            spec["text"].strip() + "\n", encoding="utf-8")
        sources[short] = {
            "law_name": spec["law_name"],
            "version": spec["version"],
            "source_kind": "MANUAL_TRANSCRIPT",
            "source_url": spec["source_url"],
            "retrieval_date": TODAY,
            "verification": "PENDING_FLK_VERIFICATION",
            "seed_version": SEED_VERSION,
            "note": ("项目组人工录入的关键条文，尚未与 FLK 官方文本逐字比对；"
                     "import_law_text.py 可覆盖为官方原文并升级为 VERIFIED"),
        }
    (raw_dir / "sources.json").write_text(
        json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")
    return sources


def ingest_laws(conn: sqlite3.Connection, raw_dir: Path, from_raw: bool,
                sources: dict) -> tuple[list, dict]:
    """入库法条。返回 (全部 Provision, 每法 audit 结果)。"""
    all_provs: list = []
    audits: dict[str, dict] = {}

    conn.execute("DELETE FROM legal_provisions")
    conn.execute("DELETE FROM provision_fts")
    conn.execute("DELETE FROM provision_keywords")

    for short, spec in LAW_SPECS.items():
        txt_path = raw_dir / f"{short}.txt"
        if from_raw and txt_path.exists():
            provs = parse_law_file(
                txt_path, short, spec["law_name"],
                law_level=spec["law_level"], version=spec["version"],
                effective_date=spec["effective_date"], source_url=spec["source_url"],
                retrieval_date=sources.get(short, {}).get("retrieval_date", TODAY),
                publish_date=spec.get("publish_date", ""),
                validity_status=spec["validity_status"],
            )
            source_text = txt_path.read_text(encoding="utf-8")
        else:
            source_text = spec["text"]
            provs = parse_law_text(
                source_text.splitlines(), short, spec["law_name"],
                law_level=spec["law_level"], version=spec["version"],
                effective_date=spec["effective_date"], source_url=spec["source_url"],
                retrieval_date=TODAY, publish_date=spec.get("publish_date", ""),
                validity_status=spec["validity_status"],
            )

        cur = conn.cursor()
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
            cur.execute(
                "INSERT INTO provision_fts(text, law_short, article_no, provision_id) "
                "VALUES (?,?,?,?)", (p.text, p.law_short, p.article_no, pid))

        expected_max = EXPECTED_MAX.get(short) if not (short in PARTIAL_LAWS) else None
        a = audit_law(source_text, provs, expected_max=expected_max,
                      is_truncated=short in PARTIAL_LAWS)
        audits[short] = a.to_dict()
        all_provs.extend(provs)

        conn.execute(
            """INSERT OR REPLACE INTO ingest_provenance
               (law_short,version,source_kind,source_url,retrieval_date,verification,
                n_provisions,continuity_gaps,note)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (short, spec["version"], "MANUAL_TRANSCRIPT", spec["source_url"], TODAY,
             "PENDING_FLK_VERIFICATION", len(provs),
             json.dumps(a.continuity["missing"]),
             "部分条文语料（关键条文），continuity_max=%d" % a.continuity["max"]))

    conn.commit()
    return all_provs, audits


def apply_supersede(conn: sqlite3.Connection) -> int:
    """按 SUPERSEDE_MAP 回填 superseded_by / amendment_note（通道 B 的替代指引）。"""
    n = 0
    for old_law, items in SUPERSEDE_MAP.items():
        for it in items:
            m = cn2int("".join(ch for ch in it["old"] if ch.isdigit()
                               or ch in "零一二三四五六七八九十百千"))
            if not m:
                continue
            cur = conn.execute(
                """UPDATE legal_provisions
                   SET superseded_by=?, amendment_note=COALESCE(amendment_note,'')||?
                   WHERE law_short=? AND article_no=?""",
                (f"{it['new_law']}#{it['new_article']}", f"[{it['old']}→]{it['note']}",
                 old_law, m))
            n += cur.rowcount
    conn.commit()
    return n


def seed_lifecycle(conn: sqlite3.Connection) -> int:
    conn.execute("DELETE FROM law_lifecycle")
    conn.executemany("INSERT INTO law_lifecycle VALUES (?,?,?,?,?,?)", LIFECYCLE_SEED)
    conn.commit()
    return len(LIFECYCLE_SEED)


def seed_dictionaries(conn: sqlite3.Connection) -> tuple[int, int]:
    conn.execute("DELETE FROM law_alias")
    conn.executemany("INSERT OR REPLACE INTO law_alias VALUES (?,?)",
                     list(LAW_ALIAS_SEED.items()))
    conn.execute("DELETE FROM topic_keyword")
    rows = [(t, k) for t, kws in TOPIC_KEYWORDS_SEED.items() for k in kws]
    conn.executemany("INSERT OR REPLACE INTO topic_keyword VALUES (?,?)", rows)
    conn.commit()
    return len(LAW_ALIAS_SEED), len(rows)


def build_keywords(conn: sqlite3.Connection) -> int:
    """关键词 → (法, 条) 反向索引：用于主题 FTS 与基准题场景构造。"""
    n = 0
    for topic, kws in TOPIC_KEYWORDS_SEED.items():
        for kw in kws:
            rows = conn.execute(
                """SELECT law_short, article_no FROM legal_provisions
                   WHERE text LIKE ? AND validity_status='现行有效'""",
                (f"%{kw}%",)).fetchall()
            for r in rows:
                conn.execute(
                    "INSERT INTO provision_keywords(keyword,law_short,article_no,weight) "
                    "VALUES (?,?,?,?)", (kw, r["law_short"], r["article_no"], 1.0))
                n += 1
    conn.commit()
    return n


def build_registry(conn: sqlite3.Connection, n_per_cause: int = 150,
                   with_judgments: bool = True) -> dict:
    """案号真值库 + 极简合成文书（合成数据，见 seed_cases 说明）。"""
    import random

    conn.execute("DELETE FROM judgments")
    conn.execute("DELETE FROM case_registry")
    cases = generate_cases(n_per_cause=n_per_cause, seed=42)
    for c in cases:
        conn.execute(
            """INSERT OR REPLACE INTO case_registry
               (case_no,year,court_code,court_name,case_type,seq_no,cause_action,
                judgment_date,exists_in_db,source_url,doc_hash,data_source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (c["case_no"], c["year"], c["court_code"], c["court_name"], c["case_type"],
             c["seq_no"], c["cause_action"], c["judgment_date"], 1, None, None,
             "SYNTHETIC"))
    if with_judgments:
        rng = random.Random(7)
        for c in cases:
            full = render_full_text(c, rng)
            # 分块（手册：300 字 / 50 字重叠）
            size, overlap = 300, 50
            chunks = [full[i:i + size] for i in range(0, len(full), max(size - overlap, 1))]
            conn.execute(
                """INSERT INTO judgments(case_no,court_name,cause_action,full_text,
                                         chunks,data_source)
                   VALUES (?,?,?,?,?,?)""",
                (c["case_no"], c["court_name"], c["cause_action"], full,
                 json.dumps(chunks, ensure_ascii=False), "SYNTHETIC"))
    conn.commit()
    return {"n_cases": len(cases), "n_judgments": len(cases) if with_judgments else 0,
            "causes": sorted({c["cause_action"] for c in cases})}


def write_qa_report(conn: sqlite3.Connection, audits: dict, all_provs: list,
                    registry: dict, out: Path, extra: dict | None = None) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    total_rows = conn.execute("SELECT COUNT(*) c FROM legal_provisions").fetchone()["c"]
    total_arts = conn.execute(
        "SELECT COUNT(DISTINCT law_short||'#'||article_no) c FROM legal_provisions"
    ).fetchone()["c"]
    lines: list[str] = []
    lines.append("# 知识库质量报告（S2.4 验收证据）\n")
    lines.append(f"- 生成日期：{TODAY}")
    lines.append(f"- 语料版本：{SEED_VERSION}")
    lines.append(f"- 法条表行数（款/项级）：**{total_rows}**")
    lines.append(f"- 条文条数（条级）：**{total_arts}**")
    lines.append(f"- 案号库：**{registry['n_cases']}** 条（合成）；"
                 f"文书：**{registry['n_judgments']}** 篇（合成）")
    lines.append("")
    lines.append("## 1. 逐法审计\n")
    lines.append("| 法 | 条数 | 行数 | continuity max | 缺号 | 往返一致率 | 全文状态 |")
    lines.append("|---|---|---|---|---|---|---|")
    for short, a in audits.items():
        gaps = a["continuity_missing"]
        gap_s = "无" if not gaps else (f"{len(gaps)} 个：{gaps[:6]}{'…' if len(gaps) > 6 else ''}")
        status = "**仅关键条文（未导入全文）**" if a["source_truncated"] else "全文"
        lines.append(
            f"| {short} | {a['n_articles']} | {a['n_rows']} | {a['continuity_max']} | "
            f"{gap_s} | {a['roundtrip_rate'] * 100:.1f}% | {status} |")
    lines.append("")
    worst = min((a["roundtrip_rate"] for a in audits.values()), default=1.0)
    lines.append(f"**往返一致率最低值：{worst * 100:.2f}%**")
    lines.append("")
    lines.append("## 2. 验收判定\n")
    mv = audits.get("民法典", {})
    lines.append(f"- 民法典条数连续性期望 1–{EXPECTED_MAX['民法典']}："
                 f"当前 **{mv.get('continuity_max', 0)}** 条，"
                 f"缺号 **{len(mv.get('continuity_missing', []))}** 个")
    if mv.get("continuity_max", 0) < EXPECTED_MAX["民法典"]:
        lines.append("  - ⚠️ **未达手册验收**：种子语料仅收录关键条文。"
                     "须执行 `python scripts/import_law_text.py` 导入 FLK 官方《民法典》全文。")
    lines.append(f"- 解析往返一致率 ≥95%：**"
                 f"{'PASS' if worst >= 0.95 else 'FAIL'}**（自动等价性检查，覆盖 100% 条文）")
    lines.append("- 人工抽验 126 条（10%）：**待办** —— 清单已生成于 "
                 "`data/kb/manual_audit.csv`，需法学生逐条核对官方原文后签名。")
    lines.append("")
    lines.append("## 3. 溯源与残余风险\n")
    lines.append("- 全部法条 `source_kind=MANUAL_TRANSCRIPT`、"
                 "`verification=PENDING_FLK_VERIFICATION`。")
    lines.append("- 案号库与文书均为**合成数据**（`data_source=SYNTHETIC`），"
                 "不对应真实案件；E6 结论仅限核验器判别能力。")
    lines.append("- 执行环境网络存在中间层劫持（详见 `docs/deviations.md` D0），"
                 "故未从网络抓取法律原文。")
    lines.append("")
    if extra:
        lines.append("## 4. 其他统计\n")
        for k, v in extra.items():
            lines.append(f"- {k}: {v}")
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")


def write_manual_audit_csv(all_provs: list, out: Path, per_law: int = 0) -> int:
    by_law: dict[str, list] = {}
    for p in all_provs:
        by_law.setdefault(p.law_short, []).append(p)
    rows: list[dict] = []
    for short, provs in by_law.items():
        n_arts = len({p.article_no for p in provs})
        want = per_law if per_law else max(12, int(n_arts * 0.10))
        for r in sample_for_manual_audit(provs, want, seed=42):
            r["law_short"] = short
            rows.append(r)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["law_short", "article_no", "article_label",
                                          "n_rows", "rendered", "verdict", "auditor",
                                          "date"])
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def build(db_path: str | None = None, from_raw: bool = False,
          with_judgments: bool = True, n_per_cause: int = 150) -> dict:
    s = get_settings()
    s.ensure_dirs()
    db_path = db_path or s.db_path
    raw_dir = Path(s.raw_dir)

    sources = write_raw_files(raw_dir)
    conn = connect(db_path)
    create_schema(conn)
    all_provs, audits = ingest_laws(conn, raw_dir, from_raw, sources)
    n_sup = apply_supersede(conn)
    n_life = seed_lifecycle(conn)
    n_alias, n_topic = seed_dictionaries(conn)
    n_kw = build_keywords(conn)
    registry = build_registry(conn, n_per_cause=n_per_cause,
                              with_judgments=with_judgments)
    n_audit = write_manual_audit_csv(all_provs, Path(s.kb_dir) / "manual_audit.csv")
    write_qa_report(conn, audits, all_provs, registry, Path(s.kb_dir) / "qa_report.md",
                    extra={"法条行数": len(all_provs), "superseded_by 回填行数": n_sup,
                           "沿革条目": n_life, "法律别名": n_alias,
                           "主题词条": n_topic, "关键词反向索引": n_kw,
                           "人工抽验清单条数": n_audit})
    # 供向量库与基准构造复用
    (Path(s.kb_dir) / "build_summary.json").write_text(
        json.dumps({"sources": sources, "audits": audits, "registry": registry,
                    "n_provisions_rows": len(all_provs), "supersede_rows": n_sup,
                    "keywords": n_kw, "manual_audit_rows": n_audit,
                    "from_raw": from_raw, "date": TODAY},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    conn.close()
    return {"rows": len(all_provs), "registry": registry, "audits": audits}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-raw", action="store_true",
                    help="以 data/raw/*.txt 为源（导入官方原文后使用）")
    ap.add_argument("--db", default=None)
    ap.add_argument("--no-judgments", action="store_true")
    ap.add_argument("--n-per-cause", type=int, default=150)
    args = ap.parse_args()
    r = build(db_path=args.db, from_raw=args.from_raw,
              with_judgments=not args.no_judgments, n_per_cause=args.n_per_cause)
    print(json.dumps({k: v for k, v in r.items() if k != "audits"},
                     ensure_ascii=False, indent=2))
    print("\n逐法审计：")
    for short, a in r["audits"].items():
        print(f"  {short:16s} 条={a['n_articles']:4d} 行={a['n_rows']:4d} "
              f"max={a['continuity_max']:4d} 缺号={len(a['continuity_missing']):3d} "
              f"往返={a['roundtrip_rate'] * 100:6.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
