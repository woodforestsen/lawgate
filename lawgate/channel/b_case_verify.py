# -*- coding: utf-8 -*-
"""通道 B·案号三级核验（手册 S3.3）。

在手册实现上的补强（docs/deviations.md D15）：
  D15.1 手册的顺序是"格式→存在→类型→案由"，但"格式非法"要靠 ``parsed`` 为空来
        判定；而 intent 层对"看起来像案号但代字非法"的输入会给出 ``parsed={}``，
        两者需区分。本实现显式区分 **格式非法** 与 **格式合法但不存在**，
        并把解析细节（年份/法院代字/类型代字/序号）写进 detail 便于审计。
  D15.2 增加``year_range`` 合理性检查（1990—当前年+1），拦截"（1899）京01民终1号"
        这类格式合法但不可能的案号。
  D15.3 增加 ``claimed_cause`` 的模糊匹配：真实案由常写作"民间借贷纠纷"，
        提问可能写"借贷"，故用包含关系双向匹配而非严格相等。
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import date

from lawgate.config import get_settings

LEVELS = ["格式非法", "不存在", "存在但类型不符", "存在但案由不符", "核验通过"]


@dataclass
class VerifyResult:
    level: str
    passed: bool
    detail: str
    real_case: dict | None = None
    parsed_detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"level": self.level, "passed": self.passed, "detail": self.detail,
                "real_case": self.real_case, "parsed": self.parsed_detail}


def _cause_match(claimed: str, real: str) -> bool:
    """D15.3：双向包含匹配（'借贷' ⊂ '民间借贷纠纷' 视为一致）。"""
    if not claimed or not real:
        return True
    c = claimed.replace("纠纷", "").strip()
    r = real.replace("纠纷", "").strip()
    if not c or not r:
        return True
    return c in r or r in c


class CaseNoVerifier:
    def __init__(self, db_path: str | None = None,
                 year_range: tuple[int, int] = (1990, date.today().year + 1)):
        self.db_path = db_path or get_settings().db_path
        # 连接按线程持有（D28）：本对象被多线程共享（FastAPI 线程池 / 评测线程池），
        # 而 sqlite3 连接默认禁止跨线程使用。
        self._local = threading.local()
        self.year_range = year_range

    @property
    def conn(self) -> sqlite3.Connection:
        """当前线程的连接（首次访问时建立）。"""
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.db_path)
            c.row_factory = sqlite3.Row
            self._local.conn = c
        return c

    def verify(self, parsed: dict | None, claimed_cause: str | None = None) -> VerifyResult:
        # ---- 一级：格式 ----
        if not parsed:
            return VerifyResult(
                "格式非法", False,
                "❌ 未能解析出合法案号格式。合法案号形如「（2022）沪01民终12345号」："
                "年份+法院代字+案件类型代字+序号，四要素缺一不可。",
                None, {})

        norm = parsed.get("normalized", "")
        detail = {k: parsed.get(k) for k in
                  ("year", "court_code", "case_type", "case_type_name", "seq_no")}
        if parsed.get("abnormal_marker"):
            return VerifyResult(
                "格式非法", False,
                f"❌ 案号「{norm}」使用了非法案件类型代字（如「测」），"
                f"不属于最高人民法院《案号标准》认可的代字。", None, detail)

        y = parsed.get("year")
        if not y or not (self.year_range[0] <= y <= self.year_range[1]):
            return VerifyResult(
                "格式非法", False,
                f"❌ 案号「{norm}」的年份 {y} 不在合理范围 "
                f"{self.year_range[0]}—{self.year_range[1]} 内。", None, detail)

        code = str(parsed.get("court_code") or "")
        if not code or not code[0].isalpha() and not ("\u4e00" <= code[0] <= "\u9fa5"):
            return VerifyResult(
                "格式非法", False,
                f"❌ 案号「{norm}」的法院代字「{code}」非法：法院代字须以"
                f"省级行政区简称开头。", None, detail)

        # ---- 二级：存在性 ----
        row = self.conn.execute(
            "SELECT * FROM case_registry WHERE case_no=?", (norm,)).fetchone()
        if not row or not row["exists_in_db"]:
            return VerifyResult(
                "不存在", False,
                f"❌ 案号 {norm} 在裁判文书库中不存在，疑似编造。请勿在正式文书中引用。",
                None, detail)

        real = {"case_no": row["case_no"], "court_name": row["court_name"],
                "cause_action": row["cause_action"], "case_type": row["case_type"],
                "judgment_date": row["judgment_date"],
                "data_source": row["data_source"] if "data_source" in row.keys() else None}

        # ---- 三级：类型一致性 ----
        claimed_type = parsed.get("case_type_name") or ""
        if claimed_type and row["case_type"] and claimed_type != row["case_type"]:
            return VerifyResult(
                "存在但类型不符", False,
                f"⚠️ 案号 {norm} 真实存在，但类型为「{row['case_type']}」，"
                f"而非所称「{claimed_type}」。", real, detail)

        # ---- 四级：案由一致性 ----
        if claimed_cause and row["cause_action"] and \
                not _cause_match(claimed_cause, row["cause_action"]):
            return VerifyResult(
                "存在但案由不符", False,
                f"⚠️ 案号 {norm} 真实存在，但真实案由为「{row['cause_action']}」，"
                f"与所述「{claimed_cause}」不符。引用将导致法律文书失实。",
                real, detail)

        return VerifyResult(
            "核验通过", True,
            f"✅ 案号 {norm} 核验通过：{row['court_name']}，"
            f"案由「{row['cause_action']}」，{row['case_type']}。",
            real, detail)

    def verify_raw(self, raw: str, claimed_cause: str | None = None) -> VerifyResult:
        """从原始文本解析并核验。"""
        from lawgate.gate.intent import extract_case_no

        parsed = extract_case_no(raw or "")
        if not parsed:
            return VerifyResult("格式非法", False,
                                f"❌ 未能解析出合法案号格式：{raw[:60]}", None, {})
        return self.verify(parsed, claimed_cause)

    def log(self, result: VerifyResult, expected: str | None = None) -> None:
        try:
            self.conn.execute(
                """INSERT INTO case_verify_log
                   (case_no, claimed_cause, expected, got_level, got_passed, run_date)
                   VALUES (?,?,?,?,?,date('now'))""",
                (result.real_case["case_no"] if result.real_case else "",
                 None, expected, result.level, int(result.passed)))
            self.conn.commit()
        except sqlite3.Error:
            pass  # 日志表缺失不应影响核验主流程
