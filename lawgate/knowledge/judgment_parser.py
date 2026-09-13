# -*- coding: utf-8 -*-
"""案号抽取与真值库构建（手册 S2.5）。

== 本模块是全仓库"案号知识"的唯一出处（docs/deviations.md D31）==
案号四要素正则（RE_CASE_NO）、法院代字→地区映射（CODE_REGION）、案件类型代字
→名称映射（CASE_TYPE_NAME）、异常代字判定（INVALID_CASE_CHARS / RE_CASE_NO_LIKE）
以及解析函数（extract_case_no / parse_case_no / case_no_fields）都在这里维护。

下游一律 import，不再自留副本（历史上 gate/intent、scripts/import_judgments、
eval/metrics 各复制过一份，改一处漏两处）：
  * ``lawgate/gate/intent.py``——门控层槽位抽取（re-export 同名符号，导入路径不变）；
  * ``scripts/import_judgments.py``——导入真实文书时抽案号；
  * ``lawgate/eval/metrics.py``——判分时的引用抽取。

案号标准（最高人民法院《人民法院案件案号标准》）：
    （年份）法院代字 + 案件类型代字（+ 审级代字）+ 序号 + 号
    例：（2022）沪01民终12345号
其中合法案件类型代字限定为 民/刑/行/知/执 五类，审级代字限定为
初/终/再/申/执/督。**代字写错（如"测"）即格式非法**——这正是 E6 的 V4 子类。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

# ---- 案号四要素正则（本模块为全仓库唯一出处，见 docs/deviations.md D31）----
# 门控层（gate/intent）与导入脚本（scripts/import_judgments）原先各自复制了一份，
# 三处内容一致但改一处漏两处；现统一收敛到这里，其余模块一律 import。
RE_CASE_NO = re.compile(
    r"[（(](\d{4})[)）]\s*([\u4e00-\u9fa5]{1,3}\d{0,4})"
    r"([民刑行知执])((?:初|终|再|申|执|督)?)\s*(\d+)\s*号")

# 明显编造/异常的案件类型代字（如"测字"）。只匹配代字组合词、不匹配单字：
# "无/效/假/试"在正常法律问题里太常见（如"合同无效"），单字匹配会把普通问题
# 误判成案号查询（D26 误报）。
INVALID_CASE_CHARS = re.compile(r"测字|假编|试验")
# "看起来像案号"的弱形态（年份+括号即可），供意图层兜底判断。
RE_CASE_NO_LIKE = re.compile(r"[（(]\d{4}[)）]?")

RE_CASE_NO_SEQ = re.compile(r"(\d+)\s*号")
RE_YEAR = re.compile(r"(\d{4})")

CODE_REGION = {
    "京": "北京", "沪": "上海", "粤": "广东", "浙": "浙江", "苏": "江苏",
    "鲁": "山东", "川": "四川", "渝": "重庆", "津": "天津", "闽": "福建",
    "皖": "安徽", "湘": "湖南", "鄂": "湖北", "豫": "河南", "冀": "河北",
    "辽": "辽宁", "黑": "黑龙江", "吉": "吉林", "赣": "江西", "桂": "广西",
    "云": "云南", "贵": "贵州", "陕": "陕西", "甘": "甘肃", "晋": "山西",
}

CASE_TYPE_NAME = {
    "民初": "民事一审", "民终": "民事二审", "民再": "民事再审",
    "民申": "民事再审审查", "民督": "督促程序",
    "刑初": "刑事一审", "刑终": "刑事二审", "刑再": "刑事再审",
    "行初": "行政一审", "行终": "行政二审", "行再": "行政再审",
    "执": "执行", "执异": "执行异议", "执复": "执行复议",
    "知初": "知识产权一审", "知终": "知识产权二审",
    "民": "民事", "刑": "刑事", "行": "行政", "知": "知识产权",
}


# 说明：本文件的 extract_case_no / parse_case_no 是"库口径"（字段名 region、
# case_type/case_type_short），供建库与导入使用；门控层（gate/intent）在
# import 它们之后另映射为自己的槽位契约（court_region / case_type_name /
# abnormal_marker），两条链在"解析"这一点上是同一套正则与映射，互不重复。
# 说明：本文件的 extract_case_no / parse_case_no 是"库口径"（字段名 region、
# case_type/case_type_short），供建库与导入使用；门控层（gate/intent）在
# import 它们之后另映射为自己的槽位契约（court_region / case_type_name /
# abnormal_marker），两条链在"解析"这一点上是同一套正则与映射，互不重复。
def extract_case_no(text: str) -> str | None:
    """从任意文本中抽取并规范化第一个案号；抽不到返回 None。"""
    m = RE_CASE_NO.search(text or "")
    if not m:
        return None
    year, court, cat, subcat, seq = m.groups()
    return f"（{year}）{court}{cat}{subcat or ''}{int(seq)}号"


def parse_case_no(case_no: str) -> dict | None:
    """解析案号为结构化字段；格式非法返回 None。

    字段口径说明（本文件是全仓库唯一出处，见模块文档 D31）：
      * 本函数是"**库口径**"解析——供建库/导入（case_registry）使用，
        字段名 region / case_type / case_type_short；
      * 门控层（gate/intent.extract_case_no）在 import 本函数之后，再做一层
        "槽位口径"字段映射（court_region / case_type_name / abnormal_marker）。
        两条链在**解析**这一点上是同一套正则与映射，互不重复。
    """
    m = RE_CASE_NO.search(case_no or "")
    if not m:
        return None
    year, court, cat, subcat, seq = m.groups()
    ctype = cat + (subcat or "")
    return {
        "year": int(year), "court_code": court, "case_type_short": ctype,
        "case_type": CASE_TYPE_NAME.get(ctype, ctype), "seq_no": int(seq),
        "region": CODE_REGION.get(court[0], court[0]),
        "normalized": f"（{year}）{court}{ctype}{int(seq)}号",
    }


def case_no_fields(case_no: str) -> dict:
    """供 SQLite 入库的扁平字段（含缺失字段的兜底）。"""
    p = parse_case_no(case_no) or {}
    y = p.get("year")
    if y is None:
        m = RE_YEAR.search(case_no or "")
        y = int(m.group(1)) if m else None
    seq = p.get("seq_no")
    if seq is None:
        m = RE_CASE_NO_SEQ.search(case_no or "")
        seq = int(m.group(1)) if m else None
    return {"year": y, "court_code": p.get("court_code"),
            "court_name": None, "case_type": p.get("case_type"),
            "seq_no": seq, "normalized": p.get("normalized") or (case_no or "")}


def build_registry(db_path: str, judgments_dir: str | Path = "data/judgments",
                   data_source: str = "CJWS") -> dict:
    """从文书目录构建案号真值库（手册 S2.5）。返回统计。"""
    root = Path(judgments_dir)
    conn = sqlite3.connect(db_path)
    n_ok = n_skip = 0
    causes: dict[str, int] = {}
    for f in sorted(root.rglob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            n_skip += 1
            continue
        if isinstance(d, list):
            n_skip += 1
            continue
        full = d.get("full_text") or ""
        case_no = d.get("case_no") or extract_case_no(full)
        if not case_no:
            n_skip += 1
            continue
        fld = case_no_fields(case_no)
        cause = d.get("cause_action") or ""
        causes[cause] = causes.get(cause, 0) + 1
        conn.execute(
            """INSERT OR REPLACE INTO case_registry
               (case_no,year,court_code,court_name,case_type,seq_no,cause_action,
                judgment_date,exists_in_db,source_url,doc_hash,data_source)
               VALUES (?,?,?,?,?,?,?,?,1,?,?,?)""",
            (fld["normalized"], fld["year"], fld["court_code"],
             d.get("court_name"), fld["case_type"], fld["seq_no"], cause,
             d.get("judgment_date"), d.get("source_url"),
             hashlib.md5(full.encode("utf-8")).hexdigest(), data_source))
        n_ok += 1
    conn.commit()
    conn.close()
    return {"n_imported": n_ok, "n_skipped": n_skip, "causes": causes}


def manual_import(db_path: str, rows: list[dict],
                  data_source: str = "MANUAL") -> int:
    """降级路径（手册 S2.5）：人工逐条录入 [case_no, court_name, cause_action]。"""
    conn = sqlite3.connect(db_path)
    n = 0
    for r in rows:
        fld = case_no_fields(r.get("case_no", ""))
        if not fld["year"]:
            continue
        conn.execute(
            """INSERT OR IGNORE INTO case_registry
               (case_no,year,court_code,court_name,case_type,seq_no,cause_action,
                judgment_date,exists_in_db,source_url,doc_hash,data_source)
               VALUES (?,?,?,?,?,?,?,?,1,?,?,?)""",
            (fld["normalized"], fld["year"], fld["court_code"],
             r.get("court_name"), fld["case_type"], fld["seq_no"],
             r.get("cause_action"), r.get("judgment_date"), None, None,
             data_source))
        n += conn.total_changes and 1 or 0
    conn.commit()
    conn.close()
    return n
