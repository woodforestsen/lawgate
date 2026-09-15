# -*- coding: utf-8 -*-
"""全量导入 Chinese-Laws-folk 真实法条到 legal_provisions，替换手写转录法条。

用法：
    python -m lawgate.knowledge.ingest_clf

保护机制：
    * 导入前自动备份 data/kb/legal_facts.db -> *.bak_YYYYMMDD
    * 复用现有 flk_parser.parse_law_file()（与 build_sqlite 同源，无需重写解析）
    * 导入后若总条数 < 3000（177 部法不可能这么少），判定解析失败并自动回滚备份
    * 仅替换 legal_provisions / provision_fts / provision_keywords；
      case_registry / judgments（合成案号）不动，留待 JuDGE 替换
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from lawgate.config import get_settings  # noqa: E402
from lawgate.knowledge.build_sqlite import build_keywords  # noqa: E402
from lawgate.knowledge.flk_parser import parse_law_text  # noqa: E402

REPO_URL = "https://github.com/taburise/Chinese-Laws-folk"
PREFIX = "中华人民共和国"

# CLF 单行格式：《法名》第X条(之一)?[规定][，,：:]<正文>
# 适配为行首「第X条 <正文>」，喂给 flk_parser 的行首状态机。
RE_CLF_LINE = re.compile(
    r"^《[^》]+》\s*(第[零〇一二三四五六七八九十百千0-9]+条"
    r"(?:之[零〇一二三四五六七八九十]+)?)\s*(?:规定)?[，,：:]?\s*(.*)$")
# 用于判定文件属于哪种格式（决定 join_wrapped）
RE_CLF_DETECT = re.compile(r"^《[^》]+》第[零〇一二三四五六七八九十百千0-9]+条")
# 行中条首切分：句号后紧跟「第X条(之一)? + 空白」才视作新条开头。
# 双重条件保证正文引用（"本法第十四条""依照第十六条"）不会被误切。
RE_MID_ARTICLE = re.compile(
    r"(?<=。)(?=第[零〇一二三四五六七八九十百千0-9]+条"
    r"(?:之[零〇一二三四五六七八九十]+)?[\s\u3000])")


def to_parser_lines(raw_lines: list[str]) -> list[str]:
    """CLF 行适配：剥《法名》前缀与「规定，」套话；行中条首拆成新行；
    标准格式行原样透传。"""
    out: list[str] = []
    for line in raw_lines:
        line = line.strip()
        m = RE_CLF_LINE.match(line)
        if m:
            line = f"{m.group(1)} {m.group(2)}".strip()
        parts = RE_MID_ARTICLE.split(line)
        out.extend(p.strip() for p in parts if p.strip())
    return out

# 高频法手工 short 映射（其余去「中华人民共和国」前缀兜底）
LAW_SHORT_MAP = {
    "中华人民共和国宪法": "宪法",
    "中华人民共和国民法典": "民法典",
    "中华人民共和国刑法": "刑法",
    "中华人民共和国刑事诉讼法": "刑诉法",
    "中华人民共和国民事诉讼法": "民诉法",
    "中华人民共和国行政诉讼法": "行政诉讼法",
    "中华人民共和国行政处罚法": "行政处罚法",
    "中华人民共和国行政强制法": "行政强制法",
    "中华人民共和国行政复议法": "行政复议法",
    "中华人民共和国行政许可法": "行政许可法",
    "中华人民共和国立法法": "立法法",
    "中华人民共和国妇女权益保障法": "妇女权益保障法",
    "中华人民共和国未成年人保护法": "未成年人保护法",
    "中华人民共和国消费者权益保护法": "消费者权益保护法",
    "中华人民共和国劳动合同法": "劳动合同法",
}


def law_short_of(name: str) -> str:
    if name in LAW_SHORT_MAP:
        return LAW_SHORT_MAP[name]
    if name.startswith(PREFIX):
        name = name[len(PREFIX):]
    return name or "未知法"


def law_level_of(name: str) -> str:
    for suf in ("法典", "法"):
        if name.endswith(suf):
            return "法律"
    for suf in ("条例", "规定", "办法", "细则", "规则"):
        if name.endswith(suf):
            return "行政法规"
    return "其他"


def main() -> int:
    s = get_settings()
    s.ensure_dirs()
    db_path = Path(s.db_path)
    clf_dir = Path(s.raw_dir) / "sources" / "chinese-laws-folk"
    if not clf_dir.exists():
        print("CLF 源目录不存在: %s" % clf_dir)
        return 2
    today = date.today().isoformat()

    # ---- 备份 ----
    backup = db_path.with_suffix(db_path.suffix + ".bak_%s" % today)
    if db_path.exists():
        shutil.copy2(db_path, backup)
        print("备份已建: %s" % backup)

    # ---- schema + 增量列 ----
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    here = Path(__file__).resolve().parent
    for f in ("schema.sql", "schema_ext.sql"):
        p = here / f
        if p.exists():
            conn.executescript(p.read_text(encoding="utf-8"))
    for stmt in (
        "ALTER TABLE legal_provisions ADD COLUMN item_idx INTEGER DEFAULT 0",
        "ALTER TABLE case_registry ADD COLUMN data_source TEXT DEFAULT 'unknown'",
        "ALTER TABLE judgments ADD COLUMN data_source TEXT DEFAULT 'unknown'",
    ):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass

    # ---- 清空法条相关表（保留案号 / 文书） ----
    conn.execute("DELETE FROM legal_provisions")
    conn.execute("DELETE FROM provision_fts")
    conn.execute("DELETE FROM provision_keywords")

    total = 0
    files = 0
    law_count = 0
    empty_laws: list[str] = []
    for txt in sorted(clf_dir.glob("*.txt")):
        name = txt.stem
        if not re.search(r"(法典|法|条例|规定|办法|细则|规则)$", name):
            continue
        short = law_short_of(name)
        level = law_level_of(name)
        raw_lines = txt.read_text(encoding="utf-8", errors="replace").splitlines()
        nonempty = [l for l in raw_lines if l.strip()]
        if not nonempty:
            empty_laws.append(name)
            continue
        clf_ratio = (sum(1 for l in nonempty if RE_CLF_DETECT.match(l.strip()))
                     / len(nonempty))
        # CLF 单行文件：每行自带条首，禁折行合并；标准格式文件沿用折行合并
        lines = to_parser_lines(nonempty)
        try:
            provs = parse_law_text(
                lines, short, name, law_level=level, version="",
                effective_date="", source_url=REPO_URL,
                retrieval_date=today, publish_date="",
                validity_status="现行有效", join_wrapped=clf_ratio < 0.9)
        except Exception as e:  # noqa: BLE001
            print("解析失败 %s: %s" % (name, e))
            empty_laws.append(name)
            continue
        if not provs:
            empty_laws.append(name)
        for p in provs:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO legal_provisions
                   (law_name,law_short,law_level,book,chapter,section,article_no,
                    article_label,paragraph_no,item_no,item_idx,text,validity_status,
                    effective_date,publish_date,version,source_db,source_url,retrieval_date)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (p.law_name, p.law_short, p.law_level, p.book, p.chapter, p.section,
                 p.article_no, p.article_label, p.paragraph_no, p.item_no, p.item_idx,
                 p.text, p.validity_status, p.effective_date, p.publish_date, p.version,
                 'CLF', p.source_url, p.retrieval_date))
            pid = cur.lastrowid
            conn.execute(
                "INSERT INTO provision_fts(text,law_short,article_no,provision_id) "
                "VALUES (?,?,?,?)", (p.text, p.law_short, p.article_no, pid))
        total += len(provs)
        files += 1
        if provs:
            law_count += 1

    conn.commit()
    n_kw = build_keywords(conn)  # 关键词反向索引重建
    conn.commit()
    conn.close()

    # ---- 保护：条数异常则回滚 ----
    if total < 3000:
        print("⚠️ 导入条数异常少(%d)，疑似解析失败，自动回滚" % total)
        if backup.exists():
            shutil.copy2(backup, db_path)
            print("已回滚到备份: %s" % backup)
        return 1

    print(json.dumps({"files": files, "laws": law_count, "provisions": total,
                      "keywords": n_kw, "empty_laws": empty_laws,
                      "backup": str(backup)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
