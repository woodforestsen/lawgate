# -*- coding: utf-8 -*-
"""意图检测 + 槽位抽取（手册 S3.1），全确定性、无模型依赖。

对《手册》原实现的两处必要修正：

D10. 手册的 ``slots_complete`` 判据 ``(topic and not article_no)`` 会把纯概念题
     误送进通道 B。例：手册自己的冒烟用例 6「什么是离婚冷静期」——
     "离婚冷静期" 命中 TOPIC_KEYWORDS['离婚纠纷']，于是 hit_provision=True、
     slots_complete=True，直接走通道 B 主题 FTS，与手册期望的"通道 A 或 C"
     冲突。本实现要求主题路线额外出现**法条索取标记**（哪条/依据/法条/…），
     否则不视为通道 B 意图。

D12. 手册缺"法律名 + 时效问句"这条路径，而 S3.2 的验收用例
     「担保法现在还有用吗」正需要它（应走 check_law 返回"已废止 + 沿革链"）。
     本实现新增 ``law_validity_query`` 标志与该路径。
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from lawgate.config import get_settings
from lawgate.knowledge.flk_parser import cn2int
# 案号正则 / 法院·案件类型映射 / 异常代字的**唯一出处**（docs/deviations.md D31）：
# 原先 gate/intent、knowledge/judgment_parser、scripts/import_judgments、eval/metrics
# 各复制一份，改一处漏两处；现统一收敛到 knowledge.judgment_parser，本文件只
# re-export，对下游（如 scripts/import_judgments 从 gate.intent 导入映射表）的
# 导入路径保持不变。
from lawgate.knowledge.judgment_parser import (  # noqa: E402
    CODE_REGION,
    CASE_TYPE_NAME,
    INVALID_CASE_CHARS,
    RE_CASE_NO,
    RE_CASE_NO_LIKE,
    parse_case_no,
)

CN = r"[零〇一二三四五六七八九十百千0-9]+"

# ---- 内置兜底词典（DB law_alias / topic_keyword 表优先）--------------------
LAW_ALIAS_FALLBACK: dict[str, str] = {
    "民法典": "民法典", "民法": "民法典", "中华人民共和国民法典": "民法典",
    "合同法": "合同法", "公司法": "公司法", "新公司法": "公司法",
    "劳动合同法": "劳动合同法", "劳动法": "劳动合同法",
    "消费者权益保护法": "消保法", "消法": "消保法",
    "刑法": "刑法", "治安管理处罚法": "治安管理处罚法",
    "物权法": "物权法", "担保法": "担保法", "婚姻法": "婚姻法",
    "侵权责任法": "侵权责任法", "继承法": "继承法", "收养法": "收养法",
    "民事诉讼法": "民事诉讼法", "民诉法": "民事诉讼法",
    "民法典时间效力规定": "民法典时间效力规定",
    "时间效力规定": "民法典时间效力规定",
}

TOPIC_KEYWORDS_FALLBACK: dict[str, list[str]] = {
    "民间借贷": ["借钱", "借款", "欠钱", "还钱", "借条", "欠条", "利息", "民间借贷",
                 "高利贷", "逾期利息", "本金"],
    "劳动争议": ["加班费", "辞退", "裁员", "工伤", "社保", "劳动合同", "竞业限制",
                 "经济补偿", "赔偿金", "试用期"],
    "离婚纠纷": ["离婚", "抚养权", "抚养费", "夫妻共同财产", "离婚冷静期", "彩礼",
                 "感情破裂", "共同债务", "婚前财产"],
    "房屋租赁": ["押金", "退租", "房东", "租客", "租赁合同", "涨房租", "转租",
                 "不定期租赁", "租金"],
    "违约金": ["违约金", "定金", "订金", "赔偿金", "过高", "调整", "双倍返还"],
    "合同效力": ["合同无效", "无效合同", "格式条款", "欺诈", "胁迫", "公序良俗",
                 "强制性规定", "恶意串通"],
    "侵权责任": ["侵权", "过错责任", "人身损害", "精神损害", "用人单位责任"],
    "公司治理": ["注册资本", "认缴", "实缴", "抽逃出资", "股东出资", "股权转让",
                 "失权"],
}

TEMPORAL_WORDS = ["现在", "目前", "最新", "现行", "有效", "废止", "失效", "修订", "修改",
                  "旧法", "新法", "以前", "过去", "2021年前", "民法典施行前", "还有用吗",
                  "还有效吗", "是否有效", "是否废止", "还行吗", "还能用吗"]

# 法条索取标记：出现这些才算"要条文"，用于区分"概念问"与"查条问"（D10）
# D26-3：补 "有哪些" —— 「借款合同的法律规定有哪些？」是典型的查条问法，
# 但原表只有"怎么规定/如何规定/规定是"，这句一个标记都不命中，
# 于是 slots_complete=False，P4 根本进不去（修好检索也白搭）。
# 刻意**不**收"有什么"：G5-09 记的正是「…对消费者有什么影响？」这类误触。
PROVISION_SEEKING = ["哪条", "哪一条", "哪部", "哪一章", "哪一节", "依据", "法条",
                     "条文", "条款", "第", "适用哪", "怎么规定", "如何规定", "规定是",
                     "几条规定", "明文", "原文", "内容是什么", "写了什么", "有哪些"]

RE_ARTICLE_REF = re.compile(
    rf"第\s*({CN})\s*条(?:\s*第\s*({CN})\s*款)?(?:\s*[（(]\s*({CN})\s*[)）])?")
# D26-1：口语里「婚姻法32」「公司法47」不写「第…条」，只靠 RE_ARTICLE_REF 抽不到条号，
# 于是不进通道 B、静默降级到通道 A —— 模型会凭记忆原文背诵已废止条文且不给警示。
# 这里补一条"法名后紧跟数字"的形态。两道守卫缺一不可：
#   (?!\d)            —— 数字必须**整个**匹配，否则 `(\d{1,4})` 会回溯成
#                        「2021年」→ 取 "202"，把年份啃成条号（实测踩到过）；
#   (?!\s*[年月日])   —— 挡住「民法典2021年施行」里的年份；
#   _plausible_article_no —— 挡住「合同法1999」这种四位数年份
#                            （民法典最长 1260 条，1900–2100 不可能是条号）。
RE_ARTICLE_AFTER_LAW = re.compile(
    r"^\s*第?\s*(\d{1,4})(?!\d)\s*条?(?!\s*[年月日])")


def _plausible_article_no(n: int | None) -> bool:
    """条号合理性：1–2000，且排除疑似年份的 1900–2100（D26-1）。"""
    if not n or not (1 <= n <= 2000):
        return False
    return not (1900 <= n <= 2100)


# ---- 案号映射（D31 单一出处，re-export 保持下游导入路径不变）----
# 文件头从 judgment_parser 导入的符号：
#   RE_CASE_NO / INVALID_CASE_CHARS / RE_CASE_NO_LIKE —— 正则与异常代字判定（直接复用）；
#   CODE_REGION / CASE_TYPE_NAME —— 本文件"案号知识"唯一数据源；
#   parse_case_no —— 库口径解析（intent.extract_case_no 在其上做槽位字段映射）。
# 下面是历史导入名（scripts/import_judgments 等仍从 gate.intent 取这两张表），
# 直接指向同一份数据，避免"门控层/知识库/导入脚本"三处各存一份。
COURT_CODE_MAP = CODE_REGION
CASE_TYPE_MAP = CASE_TYPE_NAME


@dataclass
class Slots:
    law_short: str | None = None
    article_no: int | None = None
    paragraph_no: int | None = None
    item_no: str | None = None
    case_no_raw: str | None = None
    case_no_parsed: dict = field(default_factory=dict)
    topic: str | None = None
    temporal_query: bool = False
    law_validity_query: bool = False
    provision_seeking: bool = False
    inherited: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "law_short": self.law_short, "article_no": self.article_no,
            "paragraph_no": self.paragraph_no, "item_no": self.item_no,
            "case_no_raw": self.case_no_raw, "topic": self.topic,
            "temporal_query": self.temporal_query,
            "law_validity_query": self.law_validity_query,
            "provision_seeking": self.provision_seeking,
            "inherited": list(self.inherited),
        }


@dataclass
class Intent:
    hit_provision: bool = False
    hit_case_no: bool = False
    slots: Slots = field(default_factory=Slots)
    slots_complete: bool = False
    route_b_reason: str = ""

    def to_dict(self) -> dict:
        return {"hit_provision": self.hit_provision, "hit_case_no": self.hit_case_no,
                "slots_complete": self.slots_complete,
                "route_b_reason": self.route_b_reason,
                "slots": self.slots.to_dict()}


class Dictionaries:
    """别名/主题词典：优先读 DB，读不到用内置兜底。"""

    def __init__(self, db_path: str | None = None):
        self.law_alias = dict(LAW_ALIAS_FALLBACK)
        self.topic_keywords = {k: list(v) for k, v in TOPIC_KEYWORDS_FALLBACK.items()}
        self.source = "builtin"
        try:
            db = db_path or get_settings().db_path
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT alias, law_short FROM law_alias").fetchall()
            if rows:
                self.law_alias = {r["alias"]: r["law_short"] for r in rows}
            trows = conn.execute(
                "SELECT topic, keyword FROM topic_keyword").fetchall()
            if trows:
                d: dict[str, list[str]] = {}
                for r in trows:
                    d.setdefault(r["topic"], []).append(r["keyword"])
                self.topic_keywords = d
            conn.close()
            self.source = "db"
        except Exception:  # noqa: BLE001
            pass
        # 长别名优先匹配（"民法典时间效力规定" 不应被 "民法典" 截断）
        self._alias_sorted = sorted(self.law_alias.items(),
                                    key=lambda kv: -len(kv[0]))


_DICTS: Dictionaries | None = None


def get_dicts(db_path: str | None = None) -> Dictionaries:
    global _DICTS
    if _DICTS is None or db_path is not None:
        _DICTS = Dictionaries(db_path)
    return _DICTS


def extract_case_no(text: str) -> dict | None:
    """解析案号，返回**门控槽位契约**字段。格式不合法返回 None。

    底层解析（正则 + 法院/类型映射）统一用 knowledge.judgment_parser.parse_case_no
    （docs/deviations.md D31 唯一出处）；本函数只做"库口径 → 槽位口径"的字段映射
    （court_region / case_type_name / abnormal_marker），下游（如 b_case_verify.verify）
    依赖的字段名保持不变。
    """
    p = parse_case_no(text or "")
    if not p:
        return None
    norm = p.get("normalized") or ""
    return {
        "year": p.get("year"), "court_code": p.get("court_code"),
        "court_region": p.get("region"), "case_type": p.get("case_type_short"),
        "case_type_name": p.get("case_type"), "seq_no": p.get("seq_no"),
        "normalized": norm,
        # "异常代字"判定：看规范化案号本身是否含编造词（如"测"），口径与旧实现一致
        "abnormal_marker": bool(INVALID_CASE_CHARS.search(norm)),
    }


def detect_intent(query: str, history: list[dict] | None = None,
                  db_path: str | None = None) -> Intent:
    """全确定性意图检测 + 槽位抽取 + 槽位继承。"""
    intent = Intent()
    slots = Slots()
    dicts = get_dicts(db_path)
    q = query or ""

    # 1. 案号
    if cn := extract_case_no(q):
        intent.hit_case_no = True
        slots.case_no_raw = cn["normalized"]
        slots.case_no_parsed = cn
    elif INVALID_CASE_CHARS.search(q) and RE_CASE_NO_LIKE.search(q):
        # 形式像案号（含 (年份) 前缀）且类型代字为编造词（如"测字"），
        # 才判为格式非法；避免"合同无效""3号楼"这类普通文本误触发。
        intent.hit_case_no = True
        slots.case_no_raw = q.strip()[:60]
        slots.case_no_parsed = {}

    # 2. 法律名（长别名优先）
    alias_end = -1
    for alias, canon in dicts._alias_sorted:
        if alias in q:
            slots.law_short = canon
            alias_end = q.index(alias) + len(alias)   # D26-1：记下法名结束位置
            break

    # 3. 条号 / 款项
    if m := RE_ARTICLE_REF.search(q):
        slots.article_no = cn2int(m.group(1))
        if m.group(2):
            slots.paragraph_no = cn2int(m.group(2))
        if m.group(3):
            slots.item_no = f"({m.group(3)})"
        intent.hit_provision = True
    elif alias_end >= 0 and (m2 := RE_ARTICLE_AFTER_LAW.match(q[alias_end:])):
        # 「婚姻法32」「公司法47」：法名后紧跟数字（D26-1）
        n = int(m2.group(1))
        if _plausible_article_no(n):
            slots.article_no = n
            intent.hit_provision = True

    # 4. 主题
    for topic, kws in dicts.topic_keywords.items():
        if any(k in q for k in kws):
            slots.topic = topic
            break

    # 5. 法条索取标记 / 时效问句
    slots.provision_seeking = any(w in q for w in PROVISION_SEEKING)
    slots.temporal_query = any(w in q for w in TEMPORAL_WORDS)
    slots.law_validity_query = bool(
        slots.law_short and (slots.temporal_query
                             or re.search(r"(有效|废止|失效|修订|还有用|还能用|还行)", q)))

    if slots.topic and slots.provision_seeking:
        intent.hit_provision = True
    if slots.temporal_query and slots.law_short:
        intent.hit_provision = True

    # 6. 槽位继承：只继承**前一轮经通道 B 事实确认**的槽位，不从未确认轮次抓词
    if history:
        confirmed: dict = {}
        for turn in reversed(history):
            tr = (turn.get("trace") or {}) if isinstance(turn, dict) else {}
            if tr.get("channel") == "B" and isinstance(tr.get("slots"), dict):
                for k, v in tr["slots"].items():
                    if v and k not in confirmed:
                        confirmed[k] = v
        for k in ("topic", "law_short"):
            if getattr(slots, k) is None and k in confirmed:
                setattr(slots, k, confirmed[k])
                slots.inherited.append(k)

    # 7. 完整性（D10/D12 修正后的判据）
    reason = ""
    if intent.hit_case_no:
        intent.slots_complete, reason = True, "case_no"
    elif slots.law_short and slots.article_no:
        intent.slots_complete, reason = True, "law+article"
    elif slots.law_validity_query:
        intent.slots_complete, reason = True, "law_validity"
    elif slots.topic and slots.provision_seeking and not slots.article_no:
        intent.slots_complete, reason = True, "topic+provision_seeking"

    intent.slots = slots
    intent.route_b_reason = reason
    return intent
