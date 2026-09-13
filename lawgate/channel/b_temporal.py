# -*- coding: utf-8 -*-
"""通道 B·时效性状态机（手册 S3.2）。

在手册实现上补三处（docs/deviations.md D14）：
  D14.1 ``check_law`` 的 SQL 用 ``from_law LIKE '%法%'`` 会串法：查「公司法」会同时
        命中「公司法(2018修正)」与其它含"公司法"的行。改为**精确优先 + 明确后缀变体**
        的两级匹配，并对结果按生效日期排序。
  D14.2 ``_trace_chain`` 每步只取一行 ``to_law``，链在分支处会断。改为收集全部
        后继并按生效日期排序，形成完整沿革链（去重、限深 6）。
  D14.3 新增 ``check_law_validity_query``：支持"X法现在还有用吗"这类**无条号**的
        时效询问（手册 S3.2 验收用例之一）。
"""
from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import date

from lawgate.config import get_settings
from lawgate.knowledge.seed_corpus import SUPERSEDE_MAP


@dataclass
class TemporalVerdict:
    status: str
    is_safe: bool
    warning: str | None = None
    replacement: dict | None = None
    lifecycle_chain: list | None = None
    as_of: str = ""
    matched_law: str | None = None

    def to_dict(self) -> dict:
        return {"status": self.status, "is_safe": self.is_safe,
                "warning": self.warning, "replacement": self.replacement,
                "chain": self.lifecycle_chain, "as_of": self.as_of,
                "matched_law": self.matched_law}


class TemporalChecker:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or get_settings().db_path
        # 连接按线程持有（D28）：sqlite3 连接不能跨线程使用，而本对象会被
        # FastAPI 线程池 / 评测线程池里的多个线程共享。
        self._local = threading.local()

    @property
    def conn(self) -> sqlite3.Connection:
        """当前线程的连接（首次访问时建立）。"""
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.db_path)
            c.row_factory = sqlite3.Row
            self._local.conn = c
        return c

    # ------------------------------------------------------------------ 内部
    def _lifecycle_rows(self, law_short: str) -> list[sqlite3.Row]:
        """D14.1：**仅精确匹配** from_law。

        曾经加过"LIKE 变体"兜底，但那是错的：查 ''公司法'' 时会命中
        ''公司法(2018修正)'' 这条**旧版本**沿革记录，于是把 2024-07-01 起已生效的
        现行《公司法》误判为"已修订"。查询 ''公司法(2018修正)'' 时精确匹配即可命中，
        不存在需要模糊匹配的场景。
        """
        return sorted(
            self.conn.execute(
                "SELECT * FROM law_lifecycle WHERE from_law = ?", (law_short,)
            ).fetchall(),
            key=lambda r: r["effective_date"])

    def _trace_chain(self, src: str, max_depth: int = 6) -> list[str]:
        """D14.2：收集全部后继，形成完整沿革链。"""
        chain, seen, frontier = [src], {src}, [src]
        while frontier and len(chain) < max_depth:
            nxt: list[str] = []
            for cur in frontier:
                for r in self.conn.execute(
                        "SELECT to_law FROM law_lifecycle WHERE from_law = ? "
                        "AND to_law IS NOT NULL ORDER BY effective_date", (cur,)):
                    t = r["to_law"]
                    if t and t not in seen:
                        seen.add(t)
                        chain.append(t)
                        nxt.append(t)
            frontier = nxt
        return chain

    # ------------------------------------------------------------------ 对外
    def check_law(self, law_short: str, as_of: date | None = None) -> TemporalVerdict:
        as_of = as_of or date.today()
        matched = None
        for r in self._lifecycle_rows(law_short):
            try:
                eff = date.fromisoformat(r["effective_date"])
            except (TypeError, ValueError):
                continue
            if as_of >= eff:
                abolished = r["relation"] in ("废止", "替代")
                status = "已废止" if abolished else "已修订"
                note = r["note"] or ""
                warning = (f"⚠️ 《{law_short}》已于 {eff.isoformat()} "
                           f"{'被废止' if abolished else '被修订'}，"
                           f"现行规定见《{r['to_law']}》。{note}").strip()
                return TemporalVerdict(
                    status=status, is_safe=False, warning=warning,
                    replacement={"law_short": r["to_law"], "note": note,
                                 "relation": r["relation"]},
                    lifecycle_chain=self._trace_chain(law_short),
                    as_of=as_of.isoformat(), matched_law=r["from_law"])
            matched = r
        # 未到生效日的修订
        if matched is not None:
            return TemporalVerdict(
                status="尚未生效", is_safe=True,
                warning=(f"提示：《{law_short}》的"
                         f"{matched['relation']}将于 {matched['effective_date']} 生效，"
                         f"当前仍适用修订前规定。"),
                lifecycle_chain=self._trace_chain(law_short),
                as_of=as_of.isoformat(), matched_law=matched["from_law"])
        return TemporalVerdict(status="现行有效", is_safe=True,
                               as_of=as_of.isoformat(), matched_law=law_short)

    def check_provision(self, law_short: str, article_no: int,
                        as_of: date | None = None) -> TemporalVerdict:
        as_of = as_of or date.today()
        # 必须限定 item_no=''，否则"一条多项"时 paragraph_no=1 会命中**项**行，
        # 拿到的 preview 就成了最后一个项而不是本条正文（修复 D14.4）。
        row = self.conn.execute(
            """SELECT validity_status, effective_date, superseded_by,
                      amendment_note, text, version
               FROM legal_provisions
               WHERE law_short=? AND article_no=? AND paragraph_no=1 AND item_no=''
               ORDER BY version DESC LIMIT 1""", (law_short, article_no)).fetchone()
        if not row:
            return TemporalVerdict(
                status="查无此条", is_safe=False, as_of=as_of.isoformat(),
                warning=f"数据库未收录《{law_short}》第{article_no}条，降级至语义检索")

        preview = self.article_text(law_short, article_no, version=row["version"]) \
            or row["text"]
        law_v = self.check_law(law_short, as_of)
        if not law_v.is_safe:
            return TemporalVerdict(
                status=law_v.status, is_safe=False,
                warning=(f"⚠️ 《{law_short}》第{article_no}条已随该法{law_v.status}。"
                         f"\n原条文（{row['version']}）：{preview[:160]}…\n"
                         f"{law_v.warning}"),
                replacement=law_v.replacement, lifecycle_chain=law_v.lifecycle_chain,
                as_of=as_of.isoformat(), matched_law=law_v.matched_law)
        if row["validity_status"] != "现行有效":
            return TemporalVerdict(
                status=row["validity_status"], is_safe=False,
                warning=(f"⚠️ 该条状态为「{row['validity_status']}」，"
                         f"替代条文：{row['superseded_by'] or '见修订说明'}。"
                         f"{row['amendment_note'] or ''}"),
                replacement={"ref": row["superseded_by"],
                             "note": row["amendment_note"]},
                as_of=as_of.isoformat(), matched_law=law_short)
        if row["effective_date"]:
            try:
                if date.fromisoformat(row["effective_date"]) > as_of:
                    return TemporalVerdict(
                        status="尚未生效", is_safe=False, as_of=as_of.isoformat(),
                        warning=f"该条将于 {row['effective_date']} 生效，"
                                f"截至 {as_of.isoformat()} 尚未施行")
            except ValueError:
                pass
        return TemporalVerdict(status="现行有效", is_safe=True,
                               as_of=as_of.isoformat(), matched_law=law_short)

    def article_text(self, law_short: str, article_no: int,
                     version: str | None = None) -> str:
        """把一条的全部款项拼回完整正文（用于替代条文的完整引用）。"""
        sql = """SELECT text, item_no FROM legal_provisions
                 WHERE law_short=? AND article_no=?"""
        args: list = [law_short, article_no]
        if version:
            sql += " AND version=?"
            args.append(version)
        sql += " ORDER BY version DESC, paragraph_no, item_idx"
        rows = self.conn.execute(sql, args).fetchall()
        return " ".join(r["text"] for r in rows)

    def replacement_map(self, law_short: str, article_no: int | None = None) -> list[dict]:
        """返回"旧法→现行法"替代指引：先用库内 superseded_by，再用种子映射表。"""
        out: list[dict] = []
        if article_no is not None:
            row = self.conn.execute(
                """SELECT superseded_by, amendment_note FROM legal_provisions
                   WHERE law_short=? AND article_no=? LIMIT 1""",
                (law_short, article_no)).fetchone()
            if row and row["superseded_by"]:
                m = re.match(r"([^#]+)#(\d+)", row["superseded_by"])
                if m:
                    out.append({"law_short": m.group(1),
                                "article_no": int(m.group(2)),
                                "note": row["amendment_note"] or ""})
        if not out:
            for item in SUPERSEDE_MAP.get(law_short, []):
                out.append({"law_short": item["new_law"],
                            "article_no": int(item["new_article"]),
                            "note": item["note"]})
        return out

    def fetch_current(self, law_short: str, article_no: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT law_short, article_no, article_label, paragraph_no, item_no,
                      item_idx, text, effective_date, version, source_url,
                      validity_status
               FROM legal_provisions
               WHERE law_short=? AND article_no=?
               ORDER BY version DESC, paragraph_no, item_idx""",
            (law_short, article_no)).fetchall()
