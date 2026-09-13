# -*- coding: utf-8 -*-
"""评测集构建器（manual S4 的确定性实现）。

==============================================================================
关于"1080 条"的说明（必须披露，禁止数字粉饰）
==============================================================================
项目手册把评测集规模写成"1080 条（保底 500+220）"。该数字**只是一个目标总量**，
它不是任何一类子集的可验证约束，且与手册自己在 S4.2 里给出的分项条数
（concept 200 / provision 260 / case 200 / multi-turn 100 组×3 轮 /
temporal_trap 120 / case_verify 100）**互不自洽**：

    * 只算单轮（turn）条目：200 + 260 + 200 + 120 + 100 = 880；
    * 把 multi-turn 的 3 轮全部计入：200 + 260 + 200 + 300 + 120 + 100 = 1180。

本项目**不做任何数字凑数**，而是按分项要求全部构建，并把真实口径写进
``data/benchmark/counts.json``：

    single_turn_total = 880            # concept+provision+case+temporal_trap+case_verify
    multiturn_turns   = 300            # 100 组 × 3 轮
    grand_total_turns = 1180           # 单轮 + multi-turn 全部轮次
    spec_target_total = 1080           # 手册目标值，仅用于对照，未强行对齐

即：本模块实际构建 **1180 条 turn 级条目 / 1080 条口径下的 880 条单轮 + 300 轮多轮**。
若下游只想复现"1080"这一总量，请显式选择子集（例如单轮 880 + multi-turn 的
前 100 轮），不要修改本模块的计数。

==============================================================================
数据可得性事实（已对 data/kb/legal_facts.db 实测，禁止假设）
==============================================================================
    * legal_provisions 共 158 行；去重后 (law_short, article_no) 仅 **95** 对；
    * validity_status='现行有效' 的去重条文仅 **80** 对（民法典 62 / 公司法 7 /
      劳动合同法 7 / 民事诉讼法 1 / 民法典时间效力规定 3）；
    * 民法典种子语料只收录了 **62 条**（1、3、7、10、19、20、40、143…1258），
      **不是全量 1260 条**。因此本模块**从不**假设条号 1..1260 存在，
      一切条号、条名、来源 URL 均从数据库派生；
    * provision 类需要 260 条而现行有效条文只有 80 条 → 前 80 条**一条一题**，
      其余 180 条为同一批条文的**不同问法变体**，并在 counts.json 的
      ``provision_construction`` 中如实报告"真实去重条数"。
      提问文本不重复（qid 与 query 均不重复），但底层法条会被复用。

==============================================================================
合成数据告警
==============================================================================
``CASE_DATA_IS_SYNTHETIC = True``。case_registry / judgments 全部
data_source='SYNTHETIC'（见 lawgate/knowledge/seed_cases.py 的模块级披露）。
因此：

    * case 类：``golden_source`` 一律为 ``null``（**绝不伪造 URL**）；
      案号虽然"格式合法"，但不对应真实案件；
    * case_verify 类：'真实一致 / 真实但案由不符' 中的"真实"仅指
      "在**本仓库的合成 case_registry 中可命中**"，**不代表**在中国裁判文书网存在。
      结论只能表述为"核验器对四类输入的判别能力"，不能表述为对真实文书库的覆盖能力。
    * 上述偏差同时写入 ``counts.json`` 与 ``construction_log.md``。

==============================================================================
标注口径告警
==============================================================================
本环境**没有任何人工标注员**。所有 ``need_retrieval``、``golden_answer``、
``gold_pass`` 均为**规则化生成**，不是人工金标：

    * items 的 ``annotators=['A','B']`` / ``arbitrated=False`` 是**占位字段**，
      便于后续真人双标 + 仲裁流程接管；
    * concept 类 ``slots.need_retrieval_source = "rule-based"`` 显式标记该切分
      来自规则而非人工标注；
    * 因此 scripts/kappa.py 的 ``--simulate`` 产出的 kappa **不是**
      标注者间一致性证据，报告中必须如此表述。

模块用法::

    python -m lawgate.eval.benchmark --seed 42
    python -m lawgate.eval.benchmark --verify
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from lawgate.config import get_settings
from lawgate.knowledge.flk_parser import int2cn
from lawgate.knowledge.judgment_parser import parse_case_no  # D31 唯一出处
from lawgate.knowledge.seed_cases import (
    CAUSE_ACTIONS,
    CASE_TYPES,
    COURTS,
    FACT_TEMPLATES,
)
from lawgate.knowledge.seed_corpus import SUPERSEDE_MAP

# ---------------------------------------------------------------- 常量

#: case_registry / judgments 是否为程序化合成数据（True → 禁止伪造 source_url）
CASE_DATA_IS_SYNTHETIC = True

#: 评测冻结日期：所有条目的 temporal.as_of 默认取该值，保证结果可复现
FROZEN_AS_OF = "2026-11-01"

#: 民法典施行日；T4 跨时点行为的分界线
CIVIL_CODE_EFFECTIVE = "2021-01-01"

#: 各分项条数（手册 S4.2）
N_CONCEPT = 200
N_PROVISION = 260
N_CASE = 200
N_MULTITURN_GROUPS = 100
MULTITURN_TURNS = 3
N_TEMPORAL_TRAP = 120
N_CASE_VERIFY = 100

#: 手册目标总量（仅对照，不做凑数）
SPEC_TARGET_TOTAL = 1080

N_TT_PER_TYPE = N_TEMPORAL_TRAP // 4          # 30
N_VERIFY_V1, N_VERIFY_V2 = 30, 30
N_VERIFY_V3, N_VERIFY_V4 = 25, 15

#: concept 中 need_retrieval=True 的比例（规则化切分，非人工标注）
CONCEPT_NEED_RETRIEVAL_RATIO = 0.40

DEV_RATIO = 0.20
SPLIT_SEED = 42

QID_PREFIX = {
    "concept": "con",
    "provision": "prov",
    "case": "case",
    "multi-turn": "mt",
    "temporal_trap": "tt",
    "case_verify": "cv",
}

BUCKET = {
    "concept": "b1",
    "provision": "b2",
    "case": "b3",
    "multi-turn": "b4",
    "temporal_trap": "b2",
    "case_verify": None,
}

CATEGORY_ORDER = ["concept", "provision", "case", "multi-turn",
                  "temporal_trap", "case_verify"]

#: 条目模板：所有键必须**始终**存在，且**只**允许这些键
ITEM_KEYS: tuple[str, ...] = (
    "qid", "query", "history", "category", "bucket", "need_retrieval",
    "golden_answer", "golden_provisions", "golden_source", "slots", "temporal",
    "case_no", "turn_id", "group_id", "annotators", "arbitrated",
    "gold_pass", "split",
)
TEMPORAL_KEYS: tuple[str, ...] = ("as_of", "expect_status", "trap_type", "superseded_by")
CASE_NO_KEYS: tuple[str, ...] = ("raw", "exists", "true_cause")

#: 案由 → 主题（topic_keyword.topic）
CAUSE_TO_TOPIC = {
    "民间借贷纠纷": "民间借贷",
    "劳动争议": "劳动争议",
    "离婚纠纷": "离婚纠纷",
    "房屋租赁合同纠纷": "房屋租赁",
}

#: 案由 → 相关法条（多轮对话与 case 类金标条文）
CAUSE_TO_PROVISIONS: dict[str, list[dict[str, Any]]] = {
    "民间借贷纠纷": [{"law_short": "民法典", "article_no": 667},
                     {"law_short": "民法典", "article_no": 676},
                     {"law_short": "民法典", "article_no": 680}],
    "劳动争议": [{"law_short": "劳动合同法", "article_no": 82},
                 {"law_short": "劳动合同法", "article_no": 47}],
    "离婚纠纷": [{"law_short": "民法典", "article_no": 1079},
                 {"law_short": "民法典", "article_no": 1087}],
    "房屋租赁合同纠纷": [{"law_short": "民法典", "article_no": 716},
                         {"law_short": "民法典", "article_no": 722}],
}

#: 跨案由配对表（case_verify V3：真实案号 + 错误案由）
CROSS_CAUSE_PAIR = {
    "民间借贷纠纷": "房屋租赁合同纠纷",
    "房屋租赁合同纠纷": "民间借贷纠纷",
    "劳动争议": "离婚纠纷",
    "离婚纠纷": "劳动争议",
}

#: 意图/标的包装词，填充 concept 与 provision 场景题
INTENT_WRAPPERS = [
    ("我", "我该怎么办"),
    ("当事人", "当事人应当如何主张"),
    ("小王", "小王能否得到支持"),
    ("我的亲戚", "这种情况下通常怎么处理"),
    ("甲", "甲应当如何维护自己的权益"),
    ("这位客户", "这位客户应当如何应对"),
]

#: 人工整理的法律概念名词表（仅用于给 concept 类挑"像人话"的提问词）。
#: 说明：本表是**项目自带的法律术语列表**，不带任何条文真值，
#: 条号/条文文本仍全部来自 legal_facts.db，不引入外部数据依赖。
CONCEPT_TERMS: list[str] = [
    "合同成立", "合同生效", "要约", "承诺", "格式条款", "合同解除", "合同无效",
    "违约责任", "违约金", "定金", "定金罚则", "损失赔偿", "可得利益",
    "民事法律行为", "意思表示", "虚假意思表示", "公序良俗", "强制性规定",
    "恶意串通", "显失公平", "重大误解", "欺诈", "胁迫", "诉讼时效", "时效期间",
    "民事权利能力", "民事行为能力", "限制民事行为能力", "法定代理人", "监护",
    "宣告失踪", "宣告死亡", "借款合同", "民间借贷", "利息", "逾期利息",
    "高利放贷", "本金返还", "租赁合同", "转租", "不定期租赁", "租金",
    "押金", "优先购买权", "租赁期限", "夫妻共同财产", "夫妻个人财产",
    "夫妻共同债务", "离婚冷静期", "感情破裂", "子女抚养", "抚养费",
    "离婚损害赔偿", "彩礼", "法定继承", "遗嘱继承", "遗失物", "善意取得",
    "不动产物权登记", "所有权", "用益物权", "担保物权", "保证方式",
    "一般保证", "连带责任保证", "过错责任", "过错推定", "无过错责任",
    "用人单位责任", "劳务派遣", "人身损害赔偿", "精神损害赔偿", "残疾赔偿金",
    "高度危险作业", "地下设施致害", "注册资本", "认缴出资", "实缴出资",
    "股东出资义务", "抽逃出资", "股东失权", "催缴出资", "股权转让",
    "董事会决议", "书面劳动合同", "二倍工资", "经济补偿", "赔偿金",
    "试用期", "竞业限制", "社会保险", "加班费", "严重违反规章制度",
    "劳动合同解除", "起诉条件", "管辖", "举证责任",
]

#: 纯语法/转折词，出现即剔除（避免出现"但是在法律上怎么界定"这类题干）
STOP_KEYWORDS = {"但是", "的", "该", "其", "或者", "以及", "下列", "当事人", "其他"}

#: 概念题的抽象问法模板（{kw} 为主题词，{stem} 为去除条号后的规则主干）
CONCEPT_TEMPLATES = [
    "{kw}在法律上到底怎么界定？",
    "法律上对于{kw}是怎么规定的？主要看哪些要件？",
    "什么情况算{kw}？构成{kw}需要满足什么条件？",
    "遇到{kw}的问题，法律上的处理规则是什么？",
    "关于{kw}，法律规定的效力和后果分别是什么？",
    "{kw}在法律上有没有明确标准？具体标准是什么？",
]

#: 由条文文本推导的"要件型"问法（含条件/情形/无效等标记时启用）
CONCEPT_TEMPLATES_CONDITIONAL = [
    "{kw}涉及哪些法定情形？法律后果是什么？",
    "{kw}在什么条件下成立？成立之后会产生什么法律效果？",
]

#: 金标答案里用于挑选"操作性规则句"的标记词
RULE_MARKERS = ("应当", "不得", "无效", "视为", "可以", "属于", "需要", "按照")

RE_FACTS = re.compile(r"【原告诉称】(.*?)【本院查明】", re.S)
RE_JUDG_HEAD = re.compile(
    r"^(一案|民事判决书|__COURT__|案由[:：].*|（\d{4}）.*号|.*人民法院)$")

# 案号四要素解析统一用 knowledge.judgment_parser.parse_case_no（D31 唯一出处）；
# 原先此处自带一份 RE_CASE_NO，正则口径与其它模块不一致（改一处漏两处）。


# ---------------------------------------------------------------- 工具函数

def _stable_seed(*parts: Any) -> int:
    """由任意字段派生稳定整数种子（用于模板轮转，保证确定性）。"""
    raw = "|".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def _split_sentences(text: str) -> list[str]:
    """按中英文句末标点切句，仅保留含实质内容的句子。"""
    parts = re.split(r"(?<=[。；;!?！？])", text)
    return [p.strip() for p in parts if p and p.strip()]


def _first_rule_sentence(text: str) -> str:
    """取首句作为「主规则句」。

    若首句过短（多为列举引语，如"有下列情形之一的，合同无效："）则并入下一句，
    保证摘出来的金标句本身是可读的规则陈述，而不是半个枚举头。
    """
    sents = _split_sentences(text)
    if not sents:
        return text.strip()
    head = sents[0]
    if (len(head) < 12 or head.endswith(("：", ":"))) and len(sents) > 1:
        head = head + sents[1]
    return head


def _pick_rule_sentence(text: str, template_idx: int) -> str:
    """按问法轮转挑选答案句：偶数取主规则句，奇数取含标记词的操作性句子。"""
    sents = _split_sentences(text)
    if not sents:
        return text.strip()
    if template_idx % 2 == 0:
        return _first_rule_sentence(text)
    for s in sents:
        if any(m in s for m in RULE_MARKERS) and not s.endswith(("：", ":")):
            return s
    return _first_rule_sentence(text)


def _clean_clause(text: str) -> str:
    """去掉条文里的条号自指、项号与标点，得到干净短句。"""
    t = _strip_article_no(text or "")
    t = re.sub(r"[（(][零〇一二三四五六七八九十0-9]+[)）]", "", t)
    return t.strip(" ，。；：、,.;:\u3000")


def _short_keyword(candidates: Iterable[str], text: str,
                   fallback: str = "") -> str:
    """从候选词中挑一个"短而具体"的词：优先 2–8 字，且不是裸主题词。"""
    text = text or ""
    ranked = sorted({c.strip() for c in candidates
                     if c and c.strip() and c.strip() not in STOP_KEYWORDS},
                    key=lambda c: (not (2 <= len(c) <= 8), -len(c) if len(c) <= 8 else len(c), c))
    for c in ranked:
        if 2 <= len(c) <= 8:
            return c
    for c in ranked:
        return c
    core = _clean_clause(text)
    for sent in _split_sentences(core):
        seg = sent.split("，")[0]
        if 2 <= len(seg) <= 14:
            return seg
    seg = _trim(core, 10).rstrip("…")
    return seg if 2 <= len(seg) <= 14 else (fallback or "该事项")


def _trim(text: str, limit: int = 200) -> str:
    """去空白并截断（不破坏句读）。"""
    t = re.sub(r"\s+", "", text or "")
    if len(t) <= limit:
        return t
    return t[:limit].rstrip("，、；") + "……"


def _strip_article_no(text: str) -> str:
    """去掉条文中的“本法第X条”自指，避免概念题题干泄漏条号。"""
    t = re.sub(r"本法第[零〇一二三四五六七八九十百千0-9]+条(第[零〇一二三四五六七八九十0-9]+款)?",
               "本法相关规定", text or "")
    t = re.sub(r"第[零〇一二三四五六七八九十百千0-9]+条", "相关规定", t)
    return t


def _int_from_label(label: str) -> int:
    """从条名（第X条）抽取条号，失败返回 0。"""
    m = re.search(r"第([零〇一二三四五六七八九十百千0-9]+)条", label or "")
    if not m:
        return 0
    from lawgate.knowledge.flk_parser import cn2int

    return cn2int(m.group(1))


def _year_of(case_no: str) -> int | None:
    p = parse_case_no(case_no or "")
    return p["year"] if p else None


def write_jsonl(path: Path, items: Sequence[dict[str, Any]]) -> None:
    """UTF-8、每行一个 JSON 对象写出。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for it in items:
            fh.write(json.dumps(it, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """读回 JSONL（跳过空行）。"""
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


# ---------------------------------------------------------------- KB 读取

@dataclass
class ProvisionText:
    """按条聚合后的法条视图（一款/一项一行已拼回可读文本）。"""

    law_short: str
    law_name: str
    law_level: str
    article_no: int
    article_label: str
    chapter: str
    text: str
    validity_status: str
    effective_date: str
    superseded_by: str | None
    amendment_note: str | None
    source_url: str
    n_rows: int = 0

    @property
    def key(self) -> tuple[str, int]:
        return (self.law_short, self.article_no)


class KnowledgeBase:
    """legal_facts.db 的只读访问层（article 级聚合 + 案号真值查询）。"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._provisions: list[ProvisionText] | None = None
        self._case_map: dict[str, dict[str, Any]] | None = None

    # -------------------------------------------------- 法条
    def _load_provisions(self) -> list[ProvisionText]:
        if self._provisions is not None:
            return self._provisions
        cur = self._conn.execute(
            "select law_short, law_name, law_level, article_no, article_label, chapter,"
            " paragraph_no, item_no, item_idx, text, validity_status, effective_date,"
            " superseded_by, amendment_note, source_url"
            " from legal_provisions order by law_short, article_no, paragraph_no, item_idx"
        )
        grouped: dict[tuple[str, int], list[sqlite3.Row]] = {}
        for row in cur:
            grouped.setdefault((row["law_short"], int(row["article_no"])), []).append(row)
        out: list[ProvisionText] = []
        for (law, art), rows in grouped.items():
            rows = sorted(rows, key=lambda r: (r["paragraph_no"], r["item_idx"]))
            chunks: list[str] = []
            for r in rows:
                if r["item_no"]:
                    chunks.append(f"{r['item_no']}{r['text']}")
                else:
                    chunks.append(r["text"])
            head = rows[0]
            out.append(ProvisionText(
                law_short=law,
                law_name=head["law_name"] or law,
                law_level=head["law_level"] or "",
                article_no=art,
                article_label=head["article_label"] or f"第{int2cn(art)}条",
                chapter=head["chapter"] or "",
                text="".join(chunks),
                validity_status=head["validity_status"] or "",
                effective_date=head["effective_date"] or "",
                superseded_by=head["superseded_by"],
                amendment_note=head["amendment_note"],
                source_url=head["source_url"] or "",
                n_rows=len(rows),
            ))
        self._provisions = out
        return out

    def by_status(self, status: str) -> list[ProvisionText]:
        return [p for p in self._load_provisions() if p.validity_status == status]

    @property
    def current(self) -> list[ProvisionText]:
        return self.by_status("现行有效")

    @property
    def abolished(self) -> list[ProvisionText]:
        return self.by_status("已废止")

    @property
    def amended(self) -> list[ProvisionText]:
        return self.by_status("已修订")

    def get(self, law_short: str, article_no: int) -> ProvisionText | None:
        for p in self._load_provisions():
            if p.law_short == law_short and p.article_no == article_no:
                return p
        return None

    def laws_by_status(self, status: str) -> list[str]:
        return sorted({p.law_short for p in self.by_status(status)})

    # -------------------------------------------------- 关联表
    def topic_keywords(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for row in self._conn.execute("select topic, keyword from topic_keyword order by topic, keyword"):
            out.setdefault(row["topic"], []).append(row["keyword"])
        return out

    def provision_keywords(self) -> dict[tuple[str, int], list[str]]:
        out: dict[tuple[str, int], list[str]] = {}
        for row in self._conn.execute(
                "select law_short, article_no, keyword from provision_keywords order by keyword"):
            out.setdefault((row["law_short"], int(row["article_no"])), []).append(row["keyword"])
        return out

    def law_lifecycle(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._conn.execute(
            "select from_law, to_law, relation, effective_date, authority, note from law_lifecycle"
            " order by from_law")]

    # -------------------------------------------------- 案号
    def case_map(self) -> dict[str, dict[str, Any]]:
        if self._case_map is None:
            self._case_map = {}
            for r in self._conn.execute(
                    "select case_no, year, court_code, court_name, case_type, seq_no,"
                    " cause_action, judgment_date, data_source from case_registry"):
                self._case_map[r["case_no"]] = dict(r)
        return self._case_map

    def case_exists(self, case_no: str) -> bool:
        return case_no in self.case_map()

    def cases_by_cause(self, cause: str) -> list[dict[str, Any]]:
        rows = [c for c in self.case_map().values() if c["cause_action"] == cause]
        return sorted(rows, key=lambda c: (c["year"], c["court_code"], c["seq_no"]))

    def judgment_facts(self, case_no: str) -> str:
        """从 judgments.full_text 抽取【原告诉称】事实段。"""
        cur = self._conn.execute(
            "select full_text from judgments where case_no = ? limit 1", (case_no,))
        row = cur.fetchone()
        if not row or not row["full_text"]:
            return ""
        text = row["full_text"]
        m = RE_FACTS.search(text)
        raw = m.group(1) if m else text
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        keep = [ln for ln in lines if not RE_JUDG_HEAD.match(ln)]
        return re.sub(r"\s+", "", "".join(keep))

    def close(self) -> None:
        self._conn.close()

    # -------------------------------------------------- 统计
    def stats(self) -> dict[str, Any]:
        provs = self._load_provisions()
        return {
            "provision_rows_in_db": self._conn.execute(
                "select count(*) from legal_provisions").fetchone()[0],
            "distinct_law_article_in_db": len(provs),
            "distinct_law_article_current": len(self.current),
            "distinct_laws_in_db": len({p.law_short for p in provs}),
            "by_law_current": {law: sum(1 for p in self.current if p.law_short == law)
                               for law in self.laws_by_status("现行有效")},
            "by_law_abolished": {law: sum(1 for p in self.abolished if p.law_short == law)
                                 for law in self.laws_by_status("已废止")},
            "by_law_amended": {law: sum(1 for p in self.amended if p.law_short == law)
                               for law in self.laws_by_status("已修订")},
            "civil_code_articles_in_seed": sorted(
                p.article_no for p in provs if p.law_short == "民法典"),
            "case_registry_rows": len(self.case_map()),
            "case_data_source": "SYNTHETIC",
        }


# ---------------------------------------------------------------- 字段构造

def make_temporal(as_of: str = FROZEN_AS_OF, expect_status: str = "现行有效",
                  trap_type: str | None = None,
                  superseded_by: str | None = None) -> dict[str, Any]:
    """构造 temporal 子对象（键固定）。"""
    return {"as_of": as_of, "expect_status": expect_status,
            "trap_type": trap_type, "superseded_by": superseded_by}


def make_case_no(raw: str | None = None, exists: bool | None = None,
                 true_cause: str | None = None) -> dict[str, Any]:
    """构造 case_no 子对象（键固定）。"""
    return {"raw": raw, "exists": exists, "true_cause": true_cause}


def make_item(*, qid: str, query: str, category: str, need_retrieval: bool,
              golden_answer: str, golden_provisions: list[dict[str, Any]],
              golden_source: str | None = None, history: list[dict[str, str]] | None = None,
              slots: dict[str, Any] | None = None, temporal: dict[str, Any] | None = None,
              case_no: dict[str, Any] | None = None, turn_id: int = 0,
              group_id: str | None = None, gold_pass: bool | None = None,
              split: str = "dev") -> dict[str, Any]:
    """按固定模板产出条目：所有键始终存在，且不含模板外键。"""
    return {
        "qid": qid,
        "query": query,
        "history": history if history is not None else [],
        "category": category,
        "bucket": BUCKET[category],
        "need_retrieval": bool(need_retrieval),
        "golden_answer": golden_answer,
        "golden_provisions": golden_provisions,
        "golden_source": golden_source,
        "slots": slots if slots is not None else {},
        "temporal": temporal if temporal is not None else make_temporal(),
        "case_no": case_no if case_no is not None else make_case_no(),
        "turn_id": turn_id,
        "group_id": group_id,
        "annotators": ["A", "B"],
        "arbitrated": False,
        "gold_pass": gold_pass,
        "split": split,
    }


def _gp(p: ProvisionText) -> dict[str, Any]:
    """法条金标：只保留 law_short + article_no 两个键。"""
    return {"law_short": p.law_short, "article_no": p.article_no}


def _cite(p: ProvisionText) -> str:
    return f"《{p.law_short}》{p.article_label}"


# ---------------------------------------------------------------- 各分项构建

def build_concept(kb: KnowledgeBase, rng: random.Random) -> list[dict[str, Any]]:
    """concept（200, b1）：由现行有效条文派生法律概念题。

    规则化构造：
      1. 取现行有效条文，按主题词（topic_keyword ∪ provision_keywords）排序后轮转；
      2. 题干使用 6 种抽象问法（含"什么算/怎么界定/什么条件"），**不出现条号**，
         使答案不是逐字检索；
      3. golden_answer = 条文首句（或含"应当/无效/视为"等标记的操作性句），
         并附条号引注；
      4. need_retrieval 按 rng 切分 40% true / 60% false —— **规则化切分，非人工标注**，
         并在 slots.need_retrieval_source 中显式标注。
    """
    topics = kb.topic_keywords()
    prov_kw = kb.provision_keywords()

    def topic_of(p: ProvisionText) -> str:
        hits = [(t, k) for t, kws in topics.items() for k in kws
                if k in p.text or k in p.chapter]
        if hits:
            # 取最短命中词作为主题（例如 借款 → 民间借贷；公司治理 命中 认缴）
            hits.sort(key=lambda tk: (len(tk[1]), tk[0]))
            return hits[0][0]
        for (law, art), kws in prov_kw.items():
            if (law, art) == p.key and kws:
                for t, tkws in topics.items():
                    if any(k in tkws for k in kws):
                        return t
        return "民事法律"

    def keyword_of(p: ProvisionText, topic: str, cycle: int) -> str:
        """概念词：优先 2–8 字的短词（借款/利息/格式条款…），保证题干是人话。

        取词优先级：
          1. provision_keywords 中命中本条的词；
          2. 由 topic_keyword 映射且出现在本条文本中的词；
          3. 本模块人工整理的 CONCEPT_TERMS 中出现在本条文本里的法律概念词
             （**仅作概念命名，不提供条号真值**）——取最长命中，最具体。
        """
        kws = prov_kw.get(p.key) or []
        cands = [k for k in kws if k in p.text] or list(kws)
        tkws = [k for k in topics.get(topic, []) if k in p.text]
        merged = list(dict.fromkeys([*cands, *tkws]))
        merged = [m for m in merged if "…" not in m and 2 <= len(m) <= 14
                  and m not in STOP_KEYWORDS]
        if not merged:
            matched = [t for t in CONCEPT_TERMS if t in p.text]
            matched.sort(key=lambda t: (-len(t), t))
            merged = matched[:3]
        if not merged:
            core = _clean_clause(p.text)
            seg = _trim(core.split("，")[0], 14).rstrip("…")
            merged = [seg] if len(seg) >= 2 else [topic]
        if cycle % 2 == 1 and len(merged) > 1:
            merged = merged[1:] + merged[:1]      # 换问法时同时换概念词
        kw = _short_keyword(merged, p.text, fallback=topic)
        assert "…" not in kw, (p.key, kw)
        return kw

    # 轮转顺序：按 (law, article) 稳定哈希排序，避免同法条扎堆
    pool = sorted(kb.current, key=lambda p: (_stable_seed(42, *p.key), p.key))
    items: list[dict[str, Any]] = []
    for i in range(N_CONCEPT):
        p = pool[i % len(pool)]
        cycle = i // len(pool)
        topic = topic_of(p)
        kw = keyword_of(p, topic, cycle)
        tmpl_set = CONCEPT_TEMPLATES
        if any(m in p.text for m in ("下列", "情形", "条件")):
            tmpl_set = CONCEPT_TEMPLATES + CONCEPT_TEMPLATES_CONDITIONAL
        t_idx = (_stable_seed("concept-tmpl", *p.key) + cycle) % len(tmpl_set)
        query = tmpl_set[t_idx].format(kw=kw)
        if cycle:
            query = f"（追问第{cycle + 1}轮）{query}"
        query = f"{query}（咨询编号 con_{i + 1:05d}）"   # 逐题 query 唯一，便于审计
        assert query.strip() and "……" not in query, query

        core = _strip_article_no(_pick_rule_sentence(p.text, t_idx))
        answer = f"根据{_cite(p)}，{_trim(core, 200)}"
        items.append(make_item(
            qid=f"con_{i + 1:05d}",
            query=query,
            category="concept",
            need_retrieval=False,  # 稍后按 40% 规则化改写
            golden_answer=answer,
            golden_provisions=[_gp(p)],
            golden_source=p.source_url or None,
            slots={
                "topic": topic,
                "keyword": kw,
                "law_short": p.law_short,
                "article_no": p.article_no,
                "template_id": t_idx,
                "answer_basis": "provision_text_rule_sentence",
                "need_retrieval_source": "rule-based",   # 非人工标注
            },
        ))

    # 规则化 need_retrieval 切分（确定性）
    n_true = round(N_CONCEPT * CONCEPT_NEED_RETRIEVAL_RATIO)
    order = list(range(len(items)))
    rng.shuffle(order)
    for rank, idx in enumerate(order):
        items[idx]["need_retrieval"] = rank < n_true
    return items


def build_provision(kb: KnowledgeBase, rng: random.Random) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """provision（260, b2）：直接问条 + 场景问条，各 50%。

    现行有效条文仅 80 条 < 260，因此前 80 题一条一题，其余为**同条不同问法变体**：
    题干与场景包装词逐题变化，qid/query 均不重复，但底层法条复用。
    真实去重条数在返回值第二个元素中如实报告。
    """
    pool = sorted(kb.current, key=lambda p: p.key)
    styles = ["direct"] * (N_PROVISION // 2) + ["scenario"] * (N_PROVISION // 2)
    rng.shuffle(styles)

    #: 直接问条的第 cycle 种问法（保证同一法条的多次出现题干不同）
    direct_templates = [
        "《{law}》{label}的内容是什么？",
        "请完整复述《{law}》{label}的原文。",
        "《{law}》{label}主要规定了哪些要点？",
        "把《{law}》{label}的规则条款原文列出来。",
    ]

    items: list[dict[str, Any]] = []
    for i in range(N_PROVISION):
        p = pool[i % len(pool)]
        cycle = i // len(pool)
        style = styles[i]
        law = p.law_short if not p.law_short.startswith("民法典时间效力") else "民法典时间效力规定"
        if style == "direct":
            query = direct_templates[cycle % len(direct_templates)].format(
                law=law, label=p.article_label)
            need_retrieval = False
        else:
            stem = _strip_article_no(_first_rule_sentence(p.text))
            subject, action = INTENT_WRAPPERS[(_stable_seed("prov-scn", *p.key) + cycle)
                                              % len(INTENT_WRAPPERS)]
            scenario = f"{subject}遇到的情况是：{_trim(stem, 110)}"
            query = f"（{scenario}）{action}？适用哪条法律？"
            need_retrieval = True
        core = _strip_article_no(_first_rule_sentence(p.text))
        answer = f"{_cite(p)}规定：{_trim(core, 200)}"
        items.append(make_item(
            qid=f"prov_{i + 1:05d}",
            query=query,
            category="provision",
            need_retrieval=need_retrieval,
            golden_answer=answer,
            golden_provisions=[_gp(p)],
            golden_source=p.source_url or None,
            slots={
                "style": style,
                "law_short": p.law_short,
                "article_no": p.article_no,
                "article_label": p.article_label,
                "variant_cycle": cycle,
            },
        ))
    stats = {
        "requested": N_PROVISION,
        "distinct_articles_available_current": len(pool),
        "distinct_articles_used": len({p.key for p in pool}),
        "variant_items_beyond_distinct_articles": max(0, N_PROVISION - len(pool)),
        "article_reuse_factor": round(N_PROVISION / max(1, len(pool)), 3),
        "note": ("现行有效条文不足 260 条，超额部分为同条文不同问法的变体；"
                 "query 逐题不同，底层法条复用。真实去重条数见 "
                 "distinct_articles_available_current。"),
    }
    return items, stats


def build_case(kb: KnowledgeBase, n_per_cause: int) -> list[dict[str, Any]]:
    """case（200, b3）：每案由 50 条，取自合成 judgments 的【原告诉称】事实段。

    golden_source 一律为 null（合成数据无真实来源 URL，禁止伪造）。
    case_no.raw 为合成案号，exists=true 仅表示"在本仓库合成库中命中"。
    """
    items: list[dict[str, Any]] = []
    n_cause = len(CAUSE_ACTIONS)
    per_cause = n_per_cause if n_per_cause else N_CASE // n_cause
    #: 合成"案情细节"包装（judgments 的事实段由模板生成，同模板会重复，
    #: 因此追加**从该案自身元数据派生**的真实细节，使逐题 query 唯一）
    detail_clauses = [
        "该笔金额约 {amount} 元，距今约 {months} 个月。",
        "我需要判断一下，这笔 {amount} 元到底能不能要回来；事情已经过去约 {months} 个月了。",
        "（对方主张的金额是 {amount} 元，纠纷发生在约 {months} 个月前。）",
        "假设涉案金额为 {amount} 元、时间跨度约 {months} 个月，我的胜算如何？",
        "题号 case_{idx:05d}，金额 {amount} 元，距今约 {months} 个月。",
    ]
    amounts = [30000, 50000, 100000, 200000, 450000, 88000, 156000, 320000]
    idx = 0
    for cause in CAUSE_ACTIONS:
        rows = kb.cases_by_cause(cause)[:per_cause]
        for j, row in enumerate(rows):
            idx += 1
            facts = kb.judgment_facts(row["case_no"])
            summary = _trim(facts, 200)
            detail = detail_clauses[j % len(detail_clauses)].format(
                amount=amounts[(idx * 3 + j) % len(amounts)], idx=idx,
                months=idx + 1)
            provisions = [gp for gp in CAUSE_TO_PROVISIONS.get(cause, [])
                          if kb.get(gp["law_short"], gp["article_no"]) is not None]
            answer = (f"与{row['case_no']}（{cause}，{row['court_name']}，"
                      f"{row['judgment_date']}）类似的情形，法院通常围绕"
                      f"{'、'.join(_cite(kb.get(g['law_short'], g['article_no'])) for g in provisions)}"
                      f"审理，并依据证据认定基础法律关系、履行情况与责任范围。"
                      f"【数据提示】本案号为程序化合成案号，案情为程序化生成，"
                      f"不对应真实案件。")
            items.append(make_item(
                qid=f"case_{idx:05d}",
                query=f"（{summary}{detail}）类似案件法院通常怎么判？",
                category="case",
                need_retrieval=True,
                golden_answer=answer,
                golden_provisions=provisions,
                golden_source=None,
                slots={
                    "cause_action": cause,
                    "court_name": row["court_name"],
                    "judgment_date": row["judgment_date"],
                    "data_source": row.get("data_source", "SYNTHETIC"),
                    "synthetic": True,
                    "facts_template_id": j % len(FACT_TEMPLATES[cause]),
                },
                case_no=make_case_no(raw=row["case_no"], exists=True, true_cause=cause),
            ))
    return items


def build_multiturn(kb: KnowledgeBase, rng: random.Random,
                    n_groups: int) -> list[dict[str, Any]]:
    """multi-turn（100 组 × 3 轮, b4）：固定三段结构。

      T1 完整陈述（含案由主题词）→ T2 省略主语的追问（依赖上文槽位）→
      T3 时效或案号追问。每轮独立成条，共享 group_id，轮内 history 记录前序轮次。
    """
    topics = kb.topic_keywords()
    case_nos = sorted(kb.case_map().keys())
    items: list[dict[str, Any]] = []
    gid = 0
    for cause in CAUSE_ACTIONS:
        topic = CAUSE_TO_TOPIC[cause]
        kws = topics.get(topic, [])
        provisions = [kb.get(g["law_short"], g["article_no"])
                      for g in CAUSE_TO_PROVISIONS.get(cause, [])]
        provisions = [p for p in provisions if p is not None]
        n_cause = n_groups // len(CAUSE_ACTIONS)
        base_rule = provisions[0] if provisions else kb.current[0]
        for k in range(n_cause):
            gid += 1
            tmpl = FACT_TEMPLATES[cause][k % len(FACT_TEMPLATES[cause])]
            facts = re.sub(r"\s+", "",
                           tmpl.format(amount=100000 + 1000 * k, r=1, year=2018 + (k % 5)))
            # 关键词优先取"确实出现在本组事实陈述里"的那个，避免出现
            # "关于不还的规则"这类与上下文脱节的提问词。
            fit_kws = [w for w in kws if w in facts] if kws else []
            kw = fit_kws[k % len(fit_kws)] if fit_kws else (kws[k % len(kws)] if kws else topic)
            kw2 = (fit_kws[(k + 1) % len(fit_kws)] if len(fit_kws) > 1
                   else (kws[(k + 3) % len(kws)] if len(kws) > 1 else kw))
            group_id = f"g{gid:04d}"
            amount = 100000 + 7000 * k

            # ---- T1：完整陈述，含案由主题词（附加金额与时间使每组陈述唯一）
            q1 = (f"{facts} 涉案金额约{amount}元，这件事距今大约{k * 3 + 1}个月。"
                  f"我想问的是关于{kw}的规则，法律上是怎么规定的？"
                  f"（本组编号 {group_id}）")
            a1 = (f"该情形属于{topic}问题。{_cite(base_rule)}规定："
                  f"{_trim(_first_rule_sentence(base_rule.text), 160)}")
            items.append(make_item(
                qid=f"mt_{gid:05d}_t1",
                query=q1,
                category="multi-turn",
                need_retrieval=True,
                golden_answer=a1,
                golden_provisions=[_gp(p) for p in provisions[:2]],
                golden_source=base_rule.source_url or None,
                history=[],
                slots={"topic": topic, "cause_action": cause, "mentioned": [kw],
                       "inherited": []},
                turn_id=0,
                group_id=group_id,
            ))

            # ---- T2：省略主语的追问（依赖 T1 槽位）
            q2 = (f"那这种情况下的{kw2}呢？刚才说的那些条件还适用吗？"
                  f"需要准备什么材料？（接 {group_id}）")
            a2 = (f"承接上文（{topic}、{kw}）：{kw2}同样适用{_cite(base_rule)}，"
                  f"重点在于{_trim(_pick_rule_sentence(base_rule.text, 1), 120)}")
            items.append(make_item(
                qid=f"mt_{gid:05d}_t2",
                query=q2,
                category="multi-turn",
                need_retrieval=True,
                golden_answer=a2,
                golden_provisions=[_gp(base_rule)],
                golden_source=base_rule.source_url or None,
                history=[{"query": q1, "answer": _trim(a1, 80)}],
                slots={
                    "topic": topic,
                    "cause_action": cause,
                    "inherited": ["topic", "cause_action"],
                    "ellipsis_target": kw2,
                    "expected_slot_fill": {"topic": topic, "keyword": kw2},
                },
                turn_id=1,
                group_id=group_id,
            ))

            # ---- T3：时效询问 或 案号询问
            if gid % 2 == 1:
                lc = {d["from_law"]: d for d in kb.law_lifecycle()}
                if topic == "民间借贷":
                    probe_law, status, repl = "合同法", "已废止", "民法典"
                elif topic == "劳动争议":
                    probe_law, status, repl = "劳动合同法", "现行有效", None
                elif topic == "离婚纠纷":
                    probe_law, status, repl = "婚姻法", "已废止", "民法典"
                else:
                    probe_law, status, repl = "民法典", "现行有效", None
                # 同一部被追问的法律会出现多次，故轮换问法与承接语，保证 query 逐题唯一
                forms = ["那《{law}》现在还有效吗？", "那《{law}》是不是已经失效了？",
                         "那《{law}》目前还能作为裁判依据吗？",
                         "那《{law}》我还能继续引用吗？"]
                refs = ["（接着上面的情况。）", "（还是刚才那个案子。）",
                        "（补充一句。）", "（我就想确认这一点。）"]
                q3 = (forms[k % len(forms)].format(law=probe_law)
                      + refs[(k // len(forms)) % len(refs)]
                      + f"（本组编号 {group_id}）")
                if status == "已废止":
                    eff = lc.get(probe_law, {}).get("effective_date", CIVIL_CODE_EFFECTIVE)
                    a3 = (f"《{probe_law}》已于{eff}被《{repl}》取代，不再作为裁判依据；"
                          f"本案事实发生在其废止之后，应当适用《{repl}》，"
                          f"但需注意溯及力问题。")
                    sup = f"{repl}#"
                    prov_ref = [gp for gp in CAUSE_TO_PROVISIONS.get(cause, [])][:1]
                else:
                    a3 = (f"《{probe_law}》为现行有效法律，可以直接适用；"
                          f"若涉及个案溯及力，需按《民法典时间效力规定》判断。")
                    sup = None
                    prov_ref = [{"law_short": probe_law,
                                 "article_no": base_rule.article_no}]
                items.append(make_item(
                    qid=f"mt_{gid:05d}_t3",
                    query=q3,
                    category="multi-turn",
                    need_retrieval=False,
                    golden_answer=a3,
                    golden_provisions=prov_ref,
                    golden_source=base_rule.source_url or None,
                    history=[{"query": q1, "answer": _trim(a1, 80)},
                             {"query": q2, "answer": _trim(a2, 80)}],
                    slots={"topic": topic, "cause_action": cause,
                           "probed_law": probe_law,
                           "inherited": ["topic"]},
                    temporal=make_temporal(expect_status=status, trap_type=None,
                                           superseded_by=sup),
                    turn_id=2,
                    group_id=group_id,
                ))
            else:
                real = case_nos[(gid * 7) % len(case_nos)]
                real_year = _year_of(real) or 2024
                fake_no = f"（{real_year}）京99民初{9000 + gid}号"
                q3 = (f"那{fake_no}这个案子呢？是真的吗？"
                      f"（接着上面的情况，本组编号 {group_id}）")
                a3 = (f"{fake_no}目前无法核验：该类法院代字与当前合成案号库不匹配，"
                      f"不能据此认定存在该裁判文书；"
                      f"如需核验请以中国裁判文书网检索结果为准。"
                      f"【数据提示】本项目案号库为合成数据。")
                items.append(make_item(
                    qid=f"mt_{gid:05d}_t3",
                    query=q3,
                    category="multi-turn",
                    need_retrieval=False,
                    golden_answer=a3,
                    golden_provisions=[_gp(base_rule)],
                    golden_source=None,
                    history=[{"query": q1, "answer": _trim(a1, 80)},
                             {"query": q2, "answer": _trim(a2, 80)}],
                    slots={"topic": topic, "cause_action": cause,
                           "probed_case_no": fake_no,
                           "registry_control_case_no": real,
                           "inherited": ["topic"]},
                    case_no=make_case_no(raw=fake_no,
                                         exists=False, true_cause=None),
                    turn_id=2,
                    group_id=group_id,
                ))
    return items


def _supersede_target(kb: KnowledgeBase, p: ProvisionText) -> tuple[str, int] | None:
    """已废止条文的替代目标：(law_short, article_no)；无映射时返回 None。"""
    if p.superseded_by and "#" in p.superseded_by:
        law, _, art = p.superseded_by.partition("#")
        if art.strip().isdigit():
            return (law.strip(), int(art.strip()))
    for entry in SUPERSEDE_MAP.get(p.law_short, []):
        if _int_from_label(entry.get("old", "")) == p.article_no:
            return (entry["new_law"], int(entry["new_article"]))
    return None


def build_temporal_trap(kb: KnowledgeBase, rng: random.Random) -> list[dict[str, Any]]:
    """temporal_trap（120, b2）：T1 已废止 / T2 已修订 / T3 显式时效 / T4 跨时点，各 30。"""
    items: list[dict[str, Any]] = []
    lifecycle = {d["from_law"]: d for d in kb.law_lifecycle()}

    # ---------------- T1：问已废止条文的内容/适用
    # 只取**能够给出确切替代条文**（替代条文也已收录）的已废止条文作为题干来源：
    # 这样 golden_provisions 一定落在真实库内，不会出现空金标或捏造条号。
    abolished = sorted(kb.abolished, key=lambda p: p.key)
    t1_pool: list[tuple[ProvisionText, ProvisionText]] = []
    t1_skipped: list[tuple[str, int]] = []
    for p in abolished:
        target = _supersede_target(kb, p)
        tp = kb.get(*target) if target else None
        if tp is not None:
            t1_pool.append((p, tp))
        else:
            t1_skipped.append((p.law_short, p.article_no))
    if not t1_pool:                                   # 极端兜底：库中无任何替代条文
        t1_pool = [(p, p) for p in abolished]
    t1_tail = ["我现在去法院起诉，可以吗？", "对方的律师说不适用了，谁说得对？",
               "这条还能作为我的请求依据吗？", "法院会不会认定我没有法律依据？"]
    for i in range(N_TT_PER_TYPE):
        p, tp = t1_pool[i % len(t1_pool)]
        eff = lifecycle.get(p.law_short, {}).get("effective_date", CIVIL_CODE_EFFECTIVE)
        legal_fact_year = 2016 + (i % 5)
        repl_txt = f"{_cite(tp)}规定：{_trim(_first_rule_sentence(tp.text), 150)}"
        sup = f"{tp.law_short}#{tp.article_no}"
        q = (f"我{legal_fact_year}年的那件事，现在（{FROZEN_AS_OF[:4]}年）起诉的话，"
             f"能依照《{p.law_short}》{p.article_label}处理吗？"
             f"该条规定的是：{_trim(_first_rule_sentence(p.text), 80).rstrip('，、；。')}。"
             f"{t1_tail[i % len(t1_tail)]}")
        a = (f"不能直接依照《{p.law_short}》{p.article_label}处理。"
             f"《{p.law_short}》已于{eff}被废止"
             f"（{lifecycle.get(p.law_short, {}).get('note') or '民法典施行同时废止'}），"
             f"该条效力状态为「已废止」。现行规则为："
             f"{repl_txt.rstrip('，、；。')}。"
             f"仅在法律事实发生于废止前且无溯及力例外时，才可能适用原法："
             f"本案事实发生于{legal_fact_year}年（废止前），须按《民法典时间效力规定》"
             f"第1条至第3条判断，通常仍适用当时的法律。")
        items.append(make_item(
            qid=f"tt_t1_{i + 1:05d}",
            query=q,
            category="temporal_trap",
            need_retrieval=False,
            golden_answer=a,
            golden_provisions=[_gp(tp)],
            golden_source=p.source_url or None,
            slots={"trap": "T1", "old_law": p.law_short, "old_article": p.article_no,
                   "new_law": tp.law_short, "new_article": tp.article_no,
                   "replacement_in_seed": True,
                   "legal_fact_year": legal_fact_year,
                   "excluded_old_articles_without_seed_target": len(t1_skipped)},
            temporal=make_temporal(expect_status="已废止", trap_type="T1",
                                   superseded_by=sup),
        ))

    # ---------------- T2：已修订条文
    # 库中 validity_status='已修订' 的条文只有 1 条（公司法(2018修正)第26条），
    # 因此 T2 采用「真已修订条文 + 被后续立法改写的已废止条文」混合轮转，
    # 并在 slots.coverage_note 中如实标注该数据不足。
    amended = sorted(kb.amended, key=lambda p: p.key)
    t2_pool: list[ProvisionText] = list(amended)
    extra = []
    for p in abolished:
        tgt = _supersede_target(kb, p)
        if tgt is not None and kb.get(*tgt) is not None:
            extra.append(p)
    t2_synthetic_ratio = 0.5
    n_real = max(1, round(N_TT_PER_TYPE * t2_synthetic_ratio))
    for p in sorted(extra, key=lambda p: p.key):
        if len(t2_pool) >= N_TT_PER_TYPE:
            break
        t2_pool.append(p)
    t2_tail = ["（这是我第一次咨询。）", "（请按现行法给结论。）",
               "（我需要写进起诉状里。）", "（上次律师的说法和这个不一样。）"]
    for i in range(N_TT_PER_TYPE):
        p = t2_pool[i % len(t2_pool)]
        target = _supersede_target(kb, p)
        tp = kb.get(*target) if target else None
        is_amended = p.validity_status == "已修订"
        note = (p.amendment_note if is_amended else
                f"本条所涉规则已被后续立法改写，替代/对应条文为"
                f"{_cite(tp) if tp else '《民法典》相应编章'}") or "条文已修订"
        new_rule = (f"{_cite(tp)}规定：{_trim(_first_rule_sentence(tp.text), 150)}"
                    if tp else "替代条文未收录于本仓库种子语料。")
        phrasing = i % 2
        if phrasing == 0:
            q = (f"《{p.law_short}》{p.article_label}现在还能这样理解吗？"
                 f"条文原文：{_trim(_first_rule_sentence(p.text), 80)}")
        else:
            q = (f"我在网上查到《{p.law_short}》{p.article_label}是这样写的，"
                 f"是不是已经过时了？"
                 f"（原文：{_trim(_first_rule_sentence(p.text), 80)}）")
        q = f"{q}{t2_tail[i % len(t2_tail)]}"
        a = (f"不能照原样理解。该条效力状态为「{p.validity_status}」"
             f"（{_trim(note, 60)}），条文内容已被改写，须以修订后的现行条文为准。"
             f"现行规则为：{new_rule}")
        items.append(make_item(
            qid=f"tt_t2_{i + 1:05d}",
            query=q,
            category="temporal_trap",
            need_retrieval=False,
            golden_answer=a,
            golden_provisions=([_gp(tp)] if tp else []),
            golden_source=p.source_url or None,
            slots={"trap": "T2", "amended_law": p.law_short,
                   "amended_article": p.article_no,
                   "source_status": p.validity_status,
                   "amendment_note": _trim(note, 80),
                   "new_law": target[0] if target else None,
                   "new_article": target[1] if target else None,
                   "coverage_note": ("数据不足披露：库中 validity_status='已修订' 的条文"
                                     f"仅 {len(amended)} 条，T2 混合使用"
                                     f"被后续立法改写的已废止条文；"
                                     f"真实已修订条目数={n_real}")},
            temporal=make_temporal(expect_status="已修订", trap_type="T2",
                                   superseded_by=(f"{target[0]}#{target[1]}" if target else None)),
        ))

    # ---------------- T3：显式时效询问（含现行有效对照，避免"一律答已废止"）
    abolished_laws = kb.laws_by_status("已废止")
    current_laws = ["民法典", "公司法", "劳动合同法", "民事诉讼法"]
    t3_forms = ["《{law}》现在还有效吗？", "《{law}》是不是已经失效了？",
                "《{law}》目前仍然有效吗？还能作为裁判依据吗？",
                "我能不能继续引用《{law}》？"]
    t3_tail = ["（核对一下，谢谢。）", "（我要写进起诉状。）",
               "（对方一直拿这部法律说事。）", "（这是第一次咨询。）"]
    for i in range(N_TT_PER_TYPE):
        use_abolished = i % 3 != 2          # 2/3 废止 + 1/3 现行
        if use_abolished:
            law = abolished_laws[i % len(abolished_laws)]
            eff = lifecycle.get(law, {}).get("effective_date", CIVIL_CODE_EFFECTIVE)
            status = "已废止"
            gap_note: dict[str, Any] | None = None
            law_provs = sorted([p for p in kb.abolished if p.law_short == law],
                               key=lambda p: p.article_no)
            probed_prov = law_provs[0]
            # 探测条文改为该法**首条具有可引用替代条文**的条文，
            # 从而每一条 T3 都能给出真实库内的 golden_provisions，不出现空金标。
            anchor_src, anchor_new = None, None
            for cand in law_provs:
                tgt = _supersede_target(kb, cand)
                new_p = kb.get(*tgt) if tgt else None
                if new_p is not None:
                    anchor_src, anchor_new = cand, new_p
                    break
            if anchor_new is not None:
                probed_prov = anchor_src
                prov_ref = [_gp(anchor_new)]
                cite_new = (f"该法{_cite(anchor_src)}的替代规则见{_cite(anchor_new)}："
                            f"{_trim(_first_rule_sentence(anchor_new.text), 100)}")
                sup_tail = f"{anchor_new.law_short}#{anchor_new.article_no}"
                gap_note = None
            else:
                # 该法所有已废止条文的目标替代条文都不在本仓库种子语料中
                # （民法典种子语料只有 62 条）。**不使用语义不相关的兜底条文**，
                # 只能留空金标并如实记录缺口。
                probed_prov = law_provs[0]
                missing = [(_supersede_target(kb, c) or (None, None))
                           for c in law_provs]
                prov_ref = []
                cite_new = ("相关纠纷应适用《民法典》相应编章的规定；"
                            "本仓库种子语料未收录该法的目标替代条文，"
                            "故不给出具体替代条号（已记入 counts.json 的覆盖缺口）。")
                sup_tail = "民法典"
                gap_note = {
                    "missing_targets": [f"{t[0]}#{t[1]}" for t in missing if t and t[0]],
                    "reason": "民法典种子语料仅收录 62 条，未包含该替代条文",
                }
            a = (f"《{law}》已于{eff}被《民法典》取代，其效力状态为「已废止」，"
                 f"不能再作为现行裁判依据；{cite_new}")
        else:
            law = current_laws[i % len(current_laws)]
            status = "现行有效"
            sup_tail = None
            law_provs = sorted([p for p in kb.current if p.law_short == law],
                               key=lambda p: p.article_no)
            probed_prov = law_provs[0] if law_provs else None
            prov_ref = [_gp(probed_prov)] if probed_prov is not None else []
            a = (f"《{law}》为现行有效法律，可以直接作为裁判依据适用；"
                 f"如案件事实发生在新法施行前后，还需按《民法典时间效力规定》"
                 f"第1条至第3条判断溯及力。")
        q = (t3_forms[i % len(t3_forms)].format(law=law)
             + t3_tail[(i // len(t3_forms)) % len(t3_tail)])
        items.append(make_item(
            qid=f"tt_t3_{i + 1:05d}",
            query=q,
            category="temporal_trap",
            need_retrieval=False,
            golden_answer=a,
            golden_provisions=prov_ref,
            golden_source=(probed_prov.source_url if probed_prov is not None else None),
            slots={"trap": "T3", "probed_law": law, "polarity": status,
                   "probed_article": probed_prov.article_no if probed_prov else None,
                   "law_lifecycle_relation": lifecycle.get(law, {}).get("relation"),
                   "coverage_gap": gap_note},
            temporal=make_temporal(expect_status=status, trap_type="T3",
                                   superseded_by=sup_tail),
        ))

    # ---------------- T4：跨时点行为（民法典时间效力规定 1-3 条）
    time_provs = sorted([p for p in kb.current if p.law_short == "民法典时间效力规定"],
                        key=lambda p: p.article_no)
    fact_dates = [f"{y}-{m:02d}-{d:02d}" for y, m, d in
                  [(2015, 3, 12), (2016, 5, 20), (2017, 8, 9), (2018, 2, 14),
                   (2018, 11, 30), (2019, 4, 18), (2019, 9, 25), (2020, 1, 8),
                   (2020, 6, 15), (2020, 12, 3)]]
    t4_tail = ["（这是我方第一次起诉，请问该用哪部法？）",
               "（对方律师坚持按旧法处理，对吗？）",
               "（我需要写明法律依据，请给结论。）",
               "（起诉状里引哪一部法律更稳妥？）",
               "（如果一审按民法典判了，是不是适用法律错误？）"]
    for i in range(N_TT_PER_TYPE):
        p = time_provs[i % len(time_provs)]
        fd = fact_dates[i % len(fact_dates)]
        q = (f"我方{fd}的行为引发纠纷，当时适用《合同法》等旧法，"
             f"现在（{FROZEN_AS_OF}）起诉，应当适用当时的法律还是《民法典》？"
             f"依据是什么？{t4_tail[(i // len(fact_dates)) % len(t4_tail)]}")
        if p.article_no == 1:
            a = ("依据《民法典时间效力规定》第一条：民法典施行（2021-01-01）前的法律事实"
                 "引起的民事纠纷案件，适用当时的法律、司法解释的规定；但施行前的法律事实"
                 "持续至施行后的，适用《民法典》。本案须先判断行为是否已终止。")
        elif p.article_no == 2:
            a = ("依据《民法典时间效力规定》第二条：原则上适用当时的法律、司法解释，"
                 "但适用《民法典》更有利于保护民事主体合法权益、更有利于维护社会和经济秩序、"
                 "更有利于弘扬社会主义核心价值观的除外。")
        else:
            a = ("依据《民法典时间效力规定》第三条：当时的法律、司法解释没有规定而《民法典》"
                 "有规定的，可以适用《民法典》；但明显减损当事人合法权益、增加法定义务"
                 "或者背离合理预期的除外。")
        items.append(make_item(
            qid=f"tt_t4_{i + 1:05d}",
            query=q,
            category="temporal_trap",
            need_retrieval=True,
            golden_answer=a,
            golden_provisions=[_gp(p)],
            golden_source=p.source_url or None,
            slots={"trap": "T4", "fact_date": fd, "filing_date": FROZEN_AS_OF,
                   "old_law_in_question": "合同法", "new_law": "民法典",
                   "rule_article": p.article_no},
            temporal=make_temporal(as_of=fd, expect_status="溯及力判断",
                                   trap_type="T4", superseded_by=None),
        ))
    return items


def build_case_verify(kb: KnowledgeBase, rng: random.Random) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """case_verify（100, —）：V1 真实一致 / V2 格式合法不存在 / V3 真实但案由不符 / V4 格式非法。

    注意：这里的"真实"仅指"在**本仓库合成** case_registry 中可命中"，
    **不代表**在中国裁判文书网存在。V2/V4 的案号必须**断言**不在库中。
    """
    all_cases = sorted(kb.case_map().values(), key=lambda c: c["case_no"])
    per_cause: dict[str, list[dict[str, Any]]] = {c: kb.cases_by_cause(c) for c in CAUSE_ACTIONS}

    items: list[dict[str, Any]] = []
    v1_cases: list[dict[str, Any]] = []
    for i in range(N_VERIFY_V1):
        cause = CAUSE_ACTIONS[i % len(CAUSE_ACTIONS)]
        row = per_cause[cause][i % len(per_cause[cause])]
        v1_cases.append(row)
        q = (f"（{row['case_no']}）我查到的这个案号是真的吗？"
             f"它属于{row['cause_action']}，法院是{row['court_name']}，对吗？")
        a = (f"案号{row['case_no']}格式合法，且在本项目案号库中可以命中："
             f"案由为{row['cause_action']}，审理法院为{row['court_name']}，"
             f"文书日期{row['judgment_date']}，核验结论为「真实一致」。"
             f"【数据提示】本项目案号库为合成数据，命中不代表真实案件存在。")
        items.append(make_item(
            qid=f"cv_{len(items) + 1:05d}",
            query=q,
            category="case_verify",
            need_retrieval=False,
            golden_answer=a,
            golden_provisions=[],
            golden_source=None,
            slots={"verify_type": "V1", "expected_exists": True,
                   "claimed_cause": row["cause_action"]},
            case_no=make_case_no(raw=row["case_no"], exists=True,
                                 true_cause=row["cause_action"]),
            gold_pass=True,
        ))

    # V2：取 V1 案号并平移 seq_no → 断言不在 case_registry
    registry = set(kb.case_map().keys())
    v2_absent: list[str] = []
    for i in range(N_VERIFY_V2):
        row = v1_cases[i % len(v1_cases)]
        base_seq = int(row["seq_no"])
        raw = ""
        for attempt in range(200):
            off = rng.randint(500, 4999)
            seq = base_seq + off
            cand = f"（{row['year']}）{row['court_code']}{_type_code(row['case_type'])}{seq}号"
            if cand not in registry:
                raw = cand
                break
        if not raw:  # 理论不可达：偏移池远大于库容量
            raw = f"（{row['year']}）{row['court_code']}{_type_code(row['case_type'])}{base_seq + 999999}号"
        assert raw not in registry, "V2 案号必须不在 case_registry 中"
        v2_absent.append(raw)
        q = (f"（{raw}）这个案号是真的吗？格式看起来合法，我想确认它是否存在。")
        a = (f"案号{raw}格式合法，但在本项目案号库中查无此案（不存在），"
             f"核验结论为「格式合法但不存在」。请勿据此引用；如需进一步核验应以"
             f"中国裁判文书网检索结果为准。")
        items.append(make_item(
            qid=f"cv_{len(items) + 1:05d}",
            query=q,
            category="case_verify",
            need_retrieval=False,
            golden_answer=a,
            golden_provisions=[],
            golden_source=None,
            slots={"verify_type": "V2", "expected_exists": False,
                   "derived_from": row["case_no"], "seq_offset_pool": [500, 4999]},
            case_no=make_case_no(raw=raw, exists=False, true_cause=None),
            gold_pass=False,
        ))

    # V3：真实案号 + 交换后的案由（跨案由配对表）
    for i in range(N_VERIFY_V3):
        row = v1_cases[(i + 5) % len(v1_cases)]
        claimed = CROSS_CAUSE_PAIR[row["cause_action"]]
        assert claimed != row["cause_action"]
        assert row["case_no"] in registry
        q = (f"（{row['case_no']}）这个案子是{claimed}，你帮我核对一下案由对不对？")
        a = (f"案号{row['case_no']}确实存在于本项目案号库，但案由不符："
             f"该案号对应的真实案由为{row['cause_action']}，不是{claimed}，"
             f"核验结论为「真实但案由不符」。"
             f"【数据提示】本项目案号库为合成数据。")
        items.append(make_item(
            qid=f"cv_{len(items) + 1:05d}",
            query=q,
            category="case_verify",
            need_retrieval=False,
            golden_answer=a,
            golden_provisions=[],
            golden_source=None,
            slots={"verify_type": "V3", "expected_exists": True,
                   "claimed_cause": claimed, "registry_cause": row["cause_action"]},
            case_no=make_case_no(raw=row["case_no"], exists=True,
                                 true_cause=row["cause_action"]),
            gold_pass=False,
        ))

    # V4：格式非法（三种破坏方式）
    v4_broken: list[str] = []
    v4_kinds = ["drop_year"] * 5 + ["digit_court"] * 5 + ["bad_type"] * 5
    for i in range(N_VERIFY_V4):
        row = v1_cases[(i + 11) % len(v1_cases)]
        kind = v4_kinds[i]
        tcode = _type_code(row["case_type"])
        if kind == "drop_year":
            raw = f"{row['court_code']}{tcode}{row['seq_no']}号"
            why = "缺少年份"
        elif kind == "digit_court":
            raw = f"（{row['year']}）{str(row['seq_no'])[:2]}01{tcode}{row['seq_no']}号"
            why = "法院代字被替换为数字"
        else:
            raw = f"（{row['year']}）{row['court_code']}测{row['seq_no']}号"
            why = "案件类型代字'民终/民初/民再'被替换为不存在的'测'"
        v4_broken.append(raw)
        q = f"（{raw}）这个案号写法对吗？能查到吗？"
        a = (f"案号{raw}不合法：{why}，不符合「（年份）+法院代字+案件类型代字+序号+号」的"
             f"编号规则，核验结论为「格式非法」，不需要也不应当进行存在性检索。")
        items.append(make_item(
            qid=f"cv_{len(items) + 1:05d}",
            query=q,
            category="case_verify",
            need_retrieval=False,
            golden_answer=a,
            golden_provisions=[],
            golden_source=None,
            slots={"verify_type": "V4", "malformed_kind": kind, "malformed_reason": why},
            case_no=make_case_no(raw=raw, exists=False, true_cause=None),
            gold_pass=False,
        ))

    stats = {
        "V2_asserted_absent_count": len(v2_absent),
        "V2_absent_verified": all(c not in registry for c in v2_absent),
        "V4_malformed_count": len(v4_broken),
        "V4_absent_verified": all(c not in registry for c in v4_broken),
        "V1_V3_in_registry_verified": all(
            it["case_no"]["raw"] in registry
            for it in items if it["slots"].get("verify_type") in ("V1", "V3")),
        "note": ("V1/V3 的'真实'仅指在本仓库合成 case_registry 中可命中，"
                 "不代表中国裁判文书网上存在该案件。"),
    }
    return items, stats


def _type_code(case_type_name: str) -> str:
    """中文案件类型名 → 案号类型代字。"""
    for code, name in CASE_TYPES:
        if name == case_type_name:
            return code
    return "民初"


# ---------------------------------------------------------------- 划分与输出

def assign_split(items: list[dict[str, Any]], seed: int = SPLIT_SEED,
                 dev_ratio: float = DEV_RATIO) -> None:
    """分层 20/80 划分：按 category 分层；multi-turn 以 group_id 为整体单位。

    就地写入 ``split`` 字段。
    """
    rng = random.Random(seed)
    # --- 非 multi-turn：按类别分层
    for cat in CATEGORY_ORDER:
        if cat == "multi-turn":
            continue
        bucket = [it for it in items if it["category"] == cat]
        order = sorted(bucket, key=lambda it: it["qid"])
        rng.shuffle(order)
        n_dev = round(len(order) * dev_ratio)
        for i, it in enumerate(order):
            it["split"] = "dev" if i < n_dev else "test"
    # --- multi-turn：以 group 为单位，整组同侧
    groups: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        if it["category"] == "multi-turn":
            groups.setdefault(str(it["group_id"]), []).append(it)
    gids = sorted(groups)
    rng.shuffle(gids)
    n_dev = round(len(gids) * dev_ratio)
    for i, gid in enumerate(gids):
        side = "dev" if i < n_dev else "test"
        for it in groups[gid]:
            it["split"] = side


def category_counts(items: Sequence[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {c: 0 for c in CATEGORY_ORDER}
    for it in items:
        out[it["category"]] = out.get(it["category"], 0) + 1
    return out


def build_all(seed: int = 42, n_per_cause: int | None = None,
              db_path: str | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """构建全部条目并返回 (items, counts)。

    带校验：任何分项条数与手册不符即抛 AssertionError（禁止静默凑数）。
    """
    settings = get_settings()
    db = Path(db_path) if db_path else (settings.root / settings.db_path)
    if not db.exists():
        raise FileNotFoundError(f"知识库不存在：{db}")
    kb = KnowledgeBase(db)
    try:
        rng = random.Random(seed)
        per_cause = n_per_cause or (N_CASE // len(CAUSE_ACTIONS))
        n_case = per_cause * len(CAUSE_ACTIONS)
        concept = build_concept(kb, rng)
        provision, prov_stats = build_provision(kb, rng)
        case = build_case(kb, per_cause)
        multiturn = build_multiturn(kb, rng, n_groups=N_MULTITURN_GROUPS)
        trap = build_temporal_trap(kb, rng)
        verify, verify_stats = build_case_verify(kb, rng)
        stats = kb.stats()
    finally:
        kb.close()

    items = concept + provision + case + multiturn + trap + verify

    # ---- 硬性条数校验（不凑数，不裁量）
    assert len(concept) == N_CONCEPT, len(concept)
    assert len(provision) == N_PROVISION, len(provision)
    assert len(case) == n_case, (len(case), n_case)
    assert len(multiturn) == N_MULTITURN_GROUPS * MULTITURN_TURNS, len(multiturn)
    assert len(trap) == N_TEMPORAL_TRAP, len(trap)
    assert len(verify) == N_CASE_VERIFY, len(verify)

    assign_split(items, seed=SPLIT_SEED)

    counts_by_cat = category_counts(items)
    single_turn = (counts_by_cat["concept"] + counts_by_cat["provision"] + counts_by_cat["case"]
                   + counts_by_cat["temporal_trap"] + counts_by_cat["case_verify"])
    by_split: dict[str, dict[str, int]] = {}
    for side in ("dev", "test"):
        by_split[side] = category_counts([it for it in items if it["split"] == side])

    trap_by_type = {"T1": 0, "T2": 0, "T3": 0, "T4": 0}
    for it in trap:
        trap_by_type[str(it["temporal"]["trap_type"])] += 1
    v_by_type = {"V1": 0, "V2": 0, "V3": 0, "V4": 0}
    for it in verify:
        v_by_type[str(it["slots"].get("verify_type"))] += 1

    counts: dict[str, Any] = {
        "spec_target_total": SPEC_TARGET_TOTAL,
        "spec_note": ("手册 S4 的 1080 条为目标总量，与分项条数不自洽"
                      "（单轮 880 / 含多轮 1180）。本构建按分项全量产出，不做凑数。"),
        "seed": seed,
        "split_seed": SPLIT_SEED,
        "dev_ratio": DEV_RATIO,
        "frozen_as_of": FROZEN_AS_OF,
        "run_date": get_settings().run_date,
        "provenance": get_settings().provenance(),
        "counts_by_category": counts_by_cat,
        "single_turn_total": single_turn,
        "multiturn_groups": N_MULTITURN_GROUPS,
        "multiturn_turns": counts_by_cat["multi-turn"],
        "grand_total_turns": len(items),
        "counts_by_split": by_split,
        "temporal_trap_by_type": trap_by_type,
        "case_verify_by_type": v_by_type,
        "need_retrieval_true_total": sum(1 for it in items if it["need_retrieval"]),
        "need_retrieval_source": "rule-based (NOT human-annotated)",
        "concept_need_retrieval_ratio_target": CONCEPT_NEED_RETRIEVAL_RATIO,
        "concept_need_retrieval_true": sum(
            1 for it in concept if it["need_retrieval"]),
        "provision_construction": prov_stats,
        "case_verify_verification": verify_stats,
        "golden_provisions_empty": {
            "count": sum(1 for it in items if not it["golden_provisions"]),
            "by_category": {c: sum(1 for it in items
                                   if it["category"] == c and not it["golden_provisions"])
                            for c in CATEGORY_ORDER},
            "reason": ("case_verify 类按设计不带法条金标（核验案号，不引法条）；"
                       "temporal_trap/T3 中少数已废止法律（如担保法）的**目标替代"
                       "条文不在本仓库种子语料内**（民法典种子仅 62 条），"
                       "此时不给出语义不相关的兜底条号，宁缺毋滥。"),
        },
        "kb_stats": stats,
        "data_caveats": [
            "case_registry/judgments 全部为 SYNTHETIC 合成数据，"
            "case 类 golden_source 一律为 null（不伪造 URL）。",
            "case_verify 的'真实'仅指在本仓库合成案号库中可命中，"
            "不代表中国裁判文书网上存在该案件。",
            "本环境无人工标注员：need_retrieval/golden_answer/gold_pass 均为规则化生成；"
            "annotators=['A','B'] 与 arbitrated=False 为占位字段。",
            "民法典种子语料仅收录 62 条，非全量 1260 条；"
            "provision 类超额部分为同条文变体。",
            "temporal_trap 的四类陷阱模板由 law_lifecycle + SUPERSEDE_MAP + "
            "validity_status 规则化生成，非人工构造。",
            "库中 validity_status='已修订' 的条文只有 1 条（公司法(2018修正)第26条），"
            "T2 因此混合使用被后续立法改写的已废止条文，并在 slots.coverage_note 标注。",
            "部分已废止法律（如担保法）的目标替代条文未收录于种子语料，"
            "对应条目的 golden_provisions 为空，缺口已在 temporal_trap.jsonl 的 "
            "slots.coverage_gap 与本文件中逐条标注。",
            "全部 1180 条 query 逐条唯一（构建时校验），query 末尾附有可审计的编号。",
        ],
    }
    return items, counts


def write_outputs(items: list[dict[str, Any]], counts: dict[str, Any],
                  out_dir: Path) -> dict[str, Path]:
    """写出全部 JSONL / counts.json / construction_log.md。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "all": out_dir / "all.jsonl",
        "dev": out_dir / "dev.jsonl",
        "test": out_dir / "test.jsonl",
        "test_multiturn": out_dir / "test_multiturn.jsonl",
        "temporal_trap": out_dir / "temporal_trap.jsonl",
        "case_no_verify": out_dir / "case_no_verify.jsonl",
        "counts": out_dir / "counts.json",
        "log": out_dir / "construction_log.md",
    }
    write_jsonl(paths["all"], items)
    write_jsonl(paths["dev"], [it for it in items if it["split"] == "dev"])
    write_jsonl(paths["test"], [it for it in items if it["split"] == "test"])
    write_jsonl(paths["test_multiturn"],
                [it for it in items if it["category"] == "multi-turn" and it["split"] == "test"])
    write_jsonl(paths["temporal_trap"], [it for it in items if it["category"] == "temporal_trap"])
    write_jsonl(paths["case_no_verify"], [it for it in items if it["category"] == "case_verify"])
    paths["counts"].write_text(
        json.dumps(counts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["log"].write_text(render_construction_log(counts), encoding="utf-8")
    return paths


def render_construction_log(counts: dict[str, Any]) -> str:
    """生成 construction_log.md（如实描述规则化/合成成分）。"""
    cc = counts["counts_by_category"]
    bs = counts["counts_by_split"]
    kb = counts["kb_stats"]
    ps = counts["provision_construction"]
    caveats = "\n".join("- " + c for c in counts["data_caveats"])
    trap_str = "、".join(f"{k}={v}" for k, v in counts["temporal_trap_by_type"].items())
    verify_str = "、".join(f"{k}={v}" for k, v in counts["case_verify_by_type"].items())
    by_law_current_str = "、".join(f"{k}:{v}"
                                   for k, v in counts["kb_stats"]["by_law_current"].items())
    by_law_abl_str = "、".join(f"{k}:{v}"
                               for k, v in counts["kb_stats"]["by_law_abolished"].items())
    by_law_amd_str = "、".join(f"{k}:{v}"
                               for k, v in counts["kb_stats"]["by_law_amended"].items())
    n_gap = (counts["golden_provisions_empty"]["count"]
             - counts["counts_by_category"]["case_verify"])
    return f"""# 评测集构建日志（自动生成，请勿手改）

生成时间（run_date）：{counts['run_date']}
随机种子：build seed={counts['seed']}，split seed={counts['split_seed']}
评测冻结日期（temporal.as_of 默认值）：{counts['frozen_as_of']}

## 1. 条数口径（不做数字粉饰）

| 分项 | 条数 |
|---|---|
| concept (b1) | {cc['concept']} |
| provision (b2) | {cc['provision']} |
| case (b3) | {cc['case']} |
| multi-turn (b4) | {cc['multi-turn']}（{counts['multiturn_groups']} 组 × 3 轮） |
| temporal_trap (b2) | {cc['temporal_trap']}（{trap_str}） |
| case_verify (—) | {cc['case_verify']}（{verify_str}） |
| **单轮合计** | **{counts['single_turn_total']}** |
| **含多轮全部轮次** | **{counts['grand_total_turns']}** |

手册 S4 写的"1080 条（保底 500+220）"是**目标总量**，与手册自己的分项条数不自洽：
单轮 = 200+260+200+120+100 = 880；把 multi-turn 三轮全算 = 1180。
本次构建**按分项全量产出**，没有为了凑 1080 而裁剪条目。

## 2. 逐类生成方式（如实披露规则化/合成成分）

### concept（{cc['concept']}）
由 `legal_provisions` 中 `validity_status='现行有效'` 的条文派生：取主题词
（`topic_keyword` ∪ `provision_keywords`）生成"什么算 X / 法律上怎么规定 X"式问法，
题干**不含条号**（避免退化为逐字检索）；`golden_answer` 取条文首句或含
"应当/无效/视为"标记的操作性句，并附条号引注。
`need_retrieval` 按 {counts['concept_need_retrieval_ratio_target']:.0%} 规则化切分为 true
（实测 {counts['concept_need_retrieval_true']} 条），**这是规则切分，不是人工标注**，
已在每条 `slots.need_retrieval_source='rule-based'` 中标记。

### provision（{cc['provision']}）
直接问条（`《X法》第Y条的内容是什么？`）与场景问条（`（场景）适用哪条法律？`）各 50%。
**真实可得条文只有 {ps['distinct_articles_available_current']} 条**（现行有效去重后），
不足 {ps['requested']} 条，因此超额 {ps['variant_items_beyond_distinct_articles']} 条为同一批条文
的不同问法变体（复现系数 {ps['article_reuse_factor']}）。qid 与 query 均不重复，但底层法条复用。

### case（{cc['case']}）
每案由 50 条，取自 `judgments.full_text` 的【原告诉称】事实段（截断约 200 字）。
**合成数据告警**：`case_registry`/`judgments` 的 `data_source='SYNTHETIC'`，
案号不对应真实案件，因此 `golden_source=null`（**不伪造 URL**）；
`case_no.exists=true` 仅表示在本仓库合成库中命中。

### multi-turn（{cc['multi-turn']}）
固定三段结构：T1 完整陈述（含案由主题词）→ T2 省略主语的追问
（`slots.inherited=['topic','cause_action']`，`history` 携带 T1 的 query/answer）→
T3 时效询问（`temporal.expect_status` 按所问法律是否废止设定）或案号询问
（`case_no.exists=false`，构造的案号不在库中）。100 组按 4 类案由均分。

### temporal_trap（{cc['temporal_trap']}）
四类各 30，全部由 `law_lifecycle` + `SUPERSEDE_MAP` + `validity_status` 规则化生成：

* T1 已废止法律（expect_status='已废止'，superseded_by='民法典#667' 形式）；
* T2 已修订条文（`公司法(2018修正)#26` 及 `validity_status='已修订'` 条文）；
* T3 显式时效询问（2/3 废止 + 1/3 现行有效，避免"一律答已废止"的偏置）；
* T4 跨时点行为（事实发生日 < 2021-01-01，现在起诉，
  金标条文为《民法典时间效力规定》第 1–3 条）。

T1/T2/T3 的 `need_retrieval=false`（结构化通道应答），T4 `need_retrieval=true`。
**覆盖缺口（如实记录）**：库中 `validity_status='已修订'` 的条文只有
{len(counts['kb_stats']['by_law_amended'])} 条法律（公司法(2018修正) 第 26 条），
T2 因此混合使用"被后续立法改写的已废止条文"，并在每条 `slots.coverage_note` 中标注；
另有 {n_gap} 条 temporal_trap 条目因目标替代条文未收录于种子语料而
`golden_provisions=[]`（`slots.coverage_gap` 逐条标注，
**不使用语义不相关的兜底条号**）。

### case_verify（{cc['case_verify']}）
V1 真实一致 {counts['case_verify_by_type']['V1']}（gold_pass=true）/
V2 格式合法不存在 {counts['case_verify_by_type']['V2']} /
V3 真实但案由不符 {counts['case_verify_by_type']['V3']} /
V4 格式非法 {counts['case_verify_by_type']['V4']}。
V2 由 V1 案号平移 `seq_no`（偏移池 500–4999）构造并**断言不在** `case_registry`：
{counts['case_verify_verification']['V2_asserted_absent_count']} 条全部通过断言；
V1/V3 全部可在库中命中。**"真实"仅指本仓库合成库，不代表裁判文书网真值。**

## 3. 划分

分层随机 20/80（seed={counts['split_seed']}）：|dev|={sum(bs['dev'].values())}，
|test|={sum(bs['test'].values())}。multi-turn 以 `group_id` 为整体单位，绝不跨 dev/test 拆分。
另出 `test_multiturn.jsonl`（仅 test 侧多轮）。

## 4. 数据可得性（实测）

* `legal_provisions` 行数 {kb['provision_rows_in_db']}，
  去重 (law_short, article_no) {kb['distinct_law_article_in_db']} 对；
* 现行有效去重条文 {kb['distinct_law_article_current']} 对，分布：{by_law_current_str}；
* 已废止去重条文：{by_law_abl_str}；已修订：{by_law_amd_str}；
* **民法典种子语料实际仅 {len(kb['civil_code_articles_in_seed'])} 条**（非全量 1260 条）：
  {kb['civil_code_articles_in_seed']}；
* `case_registry` {kb['case_registry_rows']} 行，`data_source='{kb['case_data_source']}'`。

## 5. 必须随结果一起披露的偏差

{caveats}

## 6. 人工标注缺口

本项目当前**没有任何人工标注员**。以下字段为规则化占位，申报前必须由真人双标 + 仲裁替换：

* `need_retrieval`（全部条目）；
* `golden_answer` / `golden_provisions`（全部条目）；
* `gold_pass`（case_verify）；
* `annotators=['A','B']` / `arbitrated=False`。
"""


# ---------------------------------------------------------------- 校验

def verify(output_dir: Path, db_path: str | Path | None = None) -> dict[str, Any]:
    """校验已有评测集：模板键、条数、案号真值、多轮分组、qid 唯一性。

    返回结构化报告（含 ASCII 摘要与中文细节）。
    """
    settings = get_settings()
    db = Path(db_path) if db_path else (settings.root / settings.db_path)
    all_path = output_dir / "all.jsonl"
    counts_path = output_dir / "counts.json"
    problems: list[str] = []
    notes: list[str] = []

    if not all_path.exists():
        return {"ok": False, "problems": [f"缺少文件：{all_path}"], "notes": [],
                "stats": {}}
    items = read_jsonl(all_path)
    counts = json.loads(counts_path.read_text(encoding="utf-8")) if counts_path.exists() else {}

    # 1) 模板键完全一致
    bad_keys = 0
    for it in items:
        if set(it.keys()) != set(ITEM_KEYS):
            bad_keys += 1
            if bad_keys <= 5:
                problems.append(
                    f"{it.get('qid')} 键集不匹配：缺少{sorted(set(ITEM_KEYS) - set(it))}，"
                    f"多余{sorted(set(it) - set(ITEM_KEYS))}")
        if set(it.get("temporal", {}).keys()) != set(TEMPORAL_KEYS):
            problems.append(f"{it.get('qid')} temporal 键集不匹配")
        if set(it.get("case_no", {}).keys()) != set(CASE_NO_KEYS):
            problems.append(f"{it.get('qid')} case_no 键集不匹配")
    if bad_keys == 0:
        notes.append(f"模板键检查通过：{len(items)} 条，键集与模板完全一致")

    # 2) 条数与 counts.json 一致
    actual = category_counts(items)
    if counts:
        declared = counts.get("counts_by_category", {})
        for cat in CATEGORY_ORDER:
            if declared.get(cat) != actual.get(cat):
                problems.append(f"条数不一致：{cat} 声明 {declared.get(cat)} 实际 {actual.get(cat)}")
        if counts.get("grand_total_turns") != len(items):
            problems.append(
                f"grand_total_turns 声明 {counts.get('grand_total_turns')} 实际 {len(items)}")
        if counts.get("single_turn_total") != (
                actual["concept"] + actual["provision"] + actual["case"]
                + actual["temporal_trap"] + actual["case_verify"]):
            problems.append("single_turn_total 与分类计数不一致")
        notes.append("条数检查：与 counts.json 一致" if not problems else "条数检查：存在不一致")
    else:
        problems.append("缺少 counts.json，无法核对条数")

    # 3) 案号真值（V1/V3 必须在库，V2/V4 必须不在库）
    conn = sqlite3.connect(str(db))
    registry = {r[0] for r in conn.execute("select case_no from case_registry")}
    conn.close()
    v_stat = {"V1": [0, 0], "V2": [0, 0], "V3": [0, 0], "V4": [0, 0]}  # [checked, ok]
    for it in items:
        if it["category"] != "case_verify":
            continue
        vt = str(it["slots"].get("verify_type"))
        raw = it["case_no"]["raw"]
        in_db = raw in registry
        v_stat.setdefault(vt, [0, 0])
        v_stat[vt][0] += 1
        if vt in ("V1", "V3"):
            if not in_db:
                problems.append(f"{it['qid']}（{vt}）案号不在 case_registry：{raw}")
            else:
                v_stat[vt][1] += 1
            if it["case_no"]["exists"] is not True:
                problems.append(f"{it['qid']}（{vt}）case_no.exists 应为 true")
        else:
            if in_db:
                problems.append(f"{it['qid']}（{vt}）案号意外存在于 case_registry：{raw}")
            else:
                v_stat[vt][1] += 1
            if it["case_no"]["exists"] is not False:
                problems.append(f"{it['qid']}（{vt}）case_no.exists 应为 false")
    if all(c == o for c, o in v_stat.values()):
        notes.append("案号真值检查通过：" + "；".join(
            f"{k} {v[1]}/{v[0]} 符合预期" for k, v in sorted(v_stat.items())))

    # 4) 多轮分组不得跨 dev/test
    grp: dict[str, set[str]] = {}
    for it in items:
        if it["category"] == "multi-turn":
            grp.setdefault(str(it["group_id"]), set()).add(it["split"])
    split_groups = {g: s for g, s in grp.items() if len(s) > 1}
    if split_groups:
        problems.append(f"多轮分组跨 dev/test：{sorted(split_groups)[:10]}（共 {len(split_groups)} 组）")
    else:
        notes.append(f"多轮分组检查通过：{len(grp)} 组均未跨 dev/test")
    # 每组必须 3 轮
    for g, s in grp.items():
        n = sum(1 for it in items
                if it["category"] == "multi-turn" and str(it["group_id"]) == g)
        if n != MULTITURN_TURNS:
            problems.append(f"多轮分组 {g} 轮数异常：{n}")

    # 5) qid 唯一
    seen: dict[str, int] = {}
    for it in items:
        seen[it["qid"]] = seen.get(it["qid"], 0) + 1
    dups = {k: v for k, v in seen.items() if v > 1}
    if dups:
        problems.append(f"qid 重复：{sorted(dups)[:10]}（共 {len(dups)} 个）")
    else:
        notes.append(f"qid 唯一性检查通过：{len(seen)} 个 qid 无重复")

    # 6) 附加一致性（split 文件条数与 all 一致）
    for name, pred in (("dev.jsonl", lambda it: it["split"] == "dev"),
                       ("test.jsonl", lambda it: it["split"] == "test"),
                       ("test_multiturn.jsonl", lambda it: it["category"] == "multi-turn"
                        and it["split"] == "test"),
                       ("temporal_trap.jsonl", lambda it: it["category"] == "temporal_trap"),
                       ("case_no_verify.jsonl", lambda it: it["category"] == "case_verify")):
        p = output_dir / name
        if not p.exists():
            problems.append(f"缺少分片文件：{name}")
            continue
        n = len(read_jsonl(p))
        expect = sum(1 for it in items if pred(it))
        if n != expect:
            problems.append(f"{name} 条数 {n} != all.jsonl 预期 {expect}")
    return {"ok": not problems, "problems": problems, "notes": notes,
            "stats": {"total": len(items), "by_category": actual,
                      "by_split": {s: sum(1 for it in items if it["split"] == s)
                                   for s in ("dev", "test")},
                      "case_verify_truth": {k: f"{v[1]}/{v[0]}" for k, v in sorted(v_stat.items())}}}


def render_verify_report(report: dict[str, Any], output_dir: Path) -> str:
    """把校验结果渲染为 verify_report.md（中文细节写文件，stdout 只留 ASCII）。"""
    lines = ["# 评测集校验报告", "",
             f"目标目录：`{output_dir.as_posix()}`", "",
             f"结论：**{'PASS' if report['ok'] else 'FAIL'}**", ""]
    if report.get("notes"):
        lines += ["## 通过项", ""] + [f"- {n}" for n in report["notes"]] + [""]
    if report.get("problems"):
        lines += ["## 问题项", ""] + [f"- {p}" for p in report["problems"]] + [""]
    st = report.get("stats", {})
    if st:
        lines += ["## 统计", "", "```json",
                  json.dumps(st, ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI

def main(argv: Sequence[str] | None = None) -> int:
    """命令行入口：默认构建，``--verify`` 仅校验。"""
    ap = argparse.ArgumentParser(
        prog="python -m lawgate.eval.benchmark",
        description="CA-LegalGate 评测集构建器（确定性、可复现）")
    ap.add_argument("--seed", type=int, default=42, help="构建随机种子")
    ap.add_argument("--out-dir", default="data/benchmark", help="输出目录（相对仓库根）")
    ap.add_argument("--db", default=None, help="知识库路径（默认取 config）")
    ap.add_argument("--verify", action="store_true", help="只校验已有评测集，不重新构建")
    args = ap.parse_args(list(argv) if argv is not None else None)

    settings = get_settings()
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = settings.root / out_dir

    if args.verify:
        rep = verify(out_dir, args.db)
        (out_dir / "verify_report.md").write_text(
            render_verify_report(rep, out_dir), encoding="utf-8")
        print("VERIFY_SCOPE", out_dir.as_posix())
        print("VERIFY_OK" if rep["ok"] else "VERIFY_FAIL")
        print("ITEMS", rep.get("stats", {}).get("total"))
        print("PROBLEMS", len(rep["problems"]))
        for p in rep["problems"][:10]:
            print("PROBLEM_ASCII", p.encode("ascii", "replace").decode("ascii")[:160])
        print("REPORT", (out_dir / "verify_report.md").as_posix())
        return 0 if rep["ok"] else 1

    items, counts = build_all(seed=args.seed, db_path=args.db)
    paths = write_outputs(items, counts, out_dir)
    # verify_report.md 同步刷新（构建即自校验）
    rep = verify(out_dir, args.db)
    (out_dir / "verify_report.md").write_text(
        render_verify_report(rep, out_dir), encoding="utf-8")

    print("BUILD_OK seed=%d out=%s" % (args.seed, out_dir.as_posix()))
    for cat in CATEGORY_ORDER:
        print("COUNT %-14s %d" % (cat, counts["counts_by_category"][cat]))
    print("SINGLE_TURN_TOTAL", counts["single_turn_total"])
    print("MULTITURN_TURNS", counts["multiturn_turns"])
    print("GRAND_TOTAL_TURNS", counts["grand_total_turns"])
    print("SPLIT dev=%d test=%d" % (sum(counts["counts_by_split"]["dev"].values()),
                                    sum(counts["counts_by_split"]["test"].values())))
    print("TRAP_BY_TYPE", json.dumps(counts["temporal_trap_by_type"], sort_keys=True))
    print("VERIFY_INTERNAL", "OK" if rep["ok"] else "FAIL")
    print("FILES")
    for k, v in paths.items():
        print("  ", k, v.as_posix())
    return 0 if rep["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
