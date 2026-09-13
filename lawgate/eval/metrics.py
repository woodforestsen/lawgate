# -*- coding: utf-8 -*-
"""评测指标（手册 S6.1 / S6.5 的指标定义与自动判分）。

== 关于"自动判分"的诚实说明（务必随结果一并披露）==
本环境**没有人工标注员**，也没有可用的外部 LLM 裁判，因此所有 `correct` 都是
**机械代理判据（lexical/structural proxy）**，不是人工判定：

  provision   : 判据 = 答案是否给出正确的「法律名 + 条号」且非拒答
                + 是否与本法条正文有足够词面重叠（key-term recall）
  concept     : 判据 = 与 golden_answer 的关键词召回率 ≥ 阈值 且非拒答
  case        : 判据 = 是否提到对应案由或案号
  temporal_trap: 判据 = TVC（是否**不把失效法条当作现行依据**）
  case_verify : 判据 = 核验结论是否与 gold_pass 一致
  multi-turn  : 沿用其底层类别判据，另计槽位继承正确率

这些判据可以把"是否引用了正确的法条/是否识别了失效"测准（这正是本项目的核心
主张，且判据是结构性的、可复核的），但对"答案表述质量"只能给近似分。
`score_item` 同时输出 `refusal`、`answer_len`、`cite_*` 等原始字段，
以便后续用人工或 LLM 裁判重算，而无需重跑模型。

指标缩写（与手册一致）：
  acc  Accuracy                 准确率
  rr   Retrieval Rate           检索调用率（有检索调用的条目占比）
  arc  Average Retrieval Calls  **平均检索调用数**（手册所谓"NAC 类比指标 LAC"）
  lac  同 arc 的别名，保留手册命名，便于与手册表格对齐。
       *注*：本字段**不含**延迟维度；延迟单独由 p50_ms / p95_ms / mean_ms 报告。
       此前 docstring 曾写作"延迟×检索的复合代价"，与实现不符，已更正。
  tvc  Temporal Validity Correctness  时效性正确率
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from lawgate.knowledge.judgment_parser import RE_CASE_NO as _RE_CASE_NO  # D31 唯一出处
from lawgate.knowledge.risk_terms import (
    ABOLISH_MARKERS,
    REFUSAL_MARKERS,
    STOPWORDS,
    UNCERTAIN_MARKERS,
)

CN_NUM = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
          "七": 7, "八": 8, "九": 9, "十": 10, "百": 100, "千": 1000}

RE_CITE_LAW = re.compile(r"《([^》]{2,40})》")
RE_CITE_ARTICLE = re.compile(r"第\s*([零〇一二三四五六七八九十百千0-9]+)\s*条")
# 案号引用抽取用全仓库唯一出处（D31）；RE_CITE_CASENO 是它的历史别名，保留导入路径
RE_CITE_CASENO = _RE_CASE_NO
RE_SENT_SPLIT = re.compile(r"[。；\n]+")
# D34：继承陈述句豁免。通道 B 答案的"【法律沿革】合同法 → 民法典"、生成答案里的
# "合同法 → 民法典"式表述，陈述的是**沿革/替代关系**，不是"把失效法当现行依据"。
# 旧实现把这类句子记为非法引用，导致通道 B 对时效陷阱题的满分警示答案全部
# TVC=0（E1 实测 61 条误杀、E5 的 T1/T2 全灭），与 invalid_law_citations 的
# docstring 声明意图直接矛盾。修复方式：同句出现沿革标记时豁免。
RE_SUCCESSION = re.compile(r"【法律沿革】|→|->")
# D34-③：否定警示语豁免。"不能继续引用《担保法》作为现行有效法律依据"是**警示**，
# 不是"把失效法当现行依据"；旧实现只认 ABOLISH_MARKERS 的固定词，这类否定警示句
# 会被记为非法引用。词表对所有方法对称生效（不改 risk_terms.ABOLISH_MARKERS——
# 那份表同时被生成侧护栏使用，动它会改变路由/生成行为而不只是判分）。
NEGATION_WARN_MARKERS = ("不能继续引用", "不应引用", "不得引用", "不可引用",
                         "不能作为现行", "不可作为现行", "并非现行", "已非现行")


def _law_hit(law: str, sent: str) -> bool:
    """D34-②：失效法名在句中是否**真命中**。

    旧实现用子串匹配（``law in sent``），现行有效的《劳动合同法》因包含"合同法"
    被误记为引用已废止的《合同法》——判分器自己掉进了 T3"易混法名"陷阱。
    改为：《法名》完整形式，或前面不是汉字的裸法名（句首/标点后）才算命中。
    已知局限（如实披露）：句中裸提"依照合同法第52条"（前面是汉字）会漏检；
    本语料的失效法引用几乎都以《》形式或句首出现，漏检面很小。
    """
    if f"《{law}》" in sent:
        return True
    i = sent.find(law)
    while i >= 0:
        if i == 0 or not ("\u4e00" <= sent[i - 1] <= "\u9fff"):
            return True
        i = sent.find(law, i + 1)
    return False


def cn2int_safe(s: str) -> int:
    s = str(s).strip()
    if s.isdigit():
        return int(s)
    total, num = 0, 0
    for ch in s:
        v = CN_NUM.get(ch)
        if v is None:
            continue
        if v >= 10:
            total += (num or 1) * v
            num = 0
        else:
            num = v
    return total + num


# ---------------------------------------------------------------- 分词/关键词
try:
    import warnings

    warnings.filterwarnings("ignore")
    import jieba  # type: ignore

    jieba.setLogLevel(60)
    _HAS_JIEBA = True
except Exception:  # noqa: BLE001
    _HAS_JIEBA = False


def terms(text: str) -> list[str]:
    if _HAS_JIEBA:
        toks = [t.strip() for t in jieba.lcut(text or "") if t.strip()]
    else:
        t = re.sub(r"\s+", "", text or "")
        toks = [t[i:i + 2] for i in range(max(len(t) - 1, 0))]
    return [t for t in toks if len(t) >= 2 and t not in STOPWORDS
            and not t.isdigit() and not re.fullmatch(r"[，。、；：（）()《》%]+", t)]


def key_terms(text: str, top_n: int = 25) -> set[str]:
    ts = terms(text)
    if not ts:
        return set()
    freq: dict[str, int] = {}
    for t in ts:
        freq[t] = freq.get(t, 0) + 1
    ranked = sorted(freq.items(), key=lambda kv: (-kv[1], -len(kv[0])))
    return {t for t, _ in ranked[:top_n]}


def key_term_recall(answer: str, golden: str) -> float:
    g = key_terms(golden)
    if not g:
        return 0.0
    a = set(terms(answer))
    return len(g & a) / len(g)


# ------------------------------------------------------------------ 结构抽取
def extract_citations(answer: str) -> dict:
    text = answer or ""
    laws = set(RE_CITE_LAW.findall(text))
    arts = {cn2int_safe(x) for x in RE_CITE_ARTICLE.findall(text)}
    cases = {m.group(0) for m in RE_CITE_CASENO.finditer(text)}
    return {"laws": laws, "articles": arts, "case_nos": cases}


def is_refusal(answer: str) -> bool:
    a = answer or ""
    if len(a.strip()) < 8:
        return True
    return any(m in a for m in REFUSAL_MARKERS)


def is_uncertain(answer: str) -> bool:
    return any(m in (answer or "") for m in UNCERTAIN_MARKERS)


def cited_golden(cites: dict, golden_provisions: list[dict]) -> bool:
    """答案是否引用了任一 golden 条文（法律名 + 条号同时命中）。"""
    for g in golden_provisions or []:
        law = g.get("law_short") or ""
        art = g.get("article_no")
        if not law:
            continue
        law_hit = any(law in l or l in law for l in cites["laws"]) or law in \
            "".join(cites["laws"])
        art_hit = (art in cites["articles"]) if art else True
        if law_hit and art_hit:
            return True
    return False


def invalid_law_citations(answer: str, invalid_laws: set[str]) -> list[str]:
    """检测"把已失效/已废止法律当作现行依据"的引用。

    逐句判定：句中出现失效法名（D34-②：全名匹配，不再子串误命中）但该句
    **没有任何失效提示语、不是继承陈述（D34-①）、也不是否定警示（D34-③）**
    → 记一次非法引用。这样"《合同法》第52条已废止，现行见《民法典》"、
    "【法律沿革】合同法 → 民法典"与"不能继续引用《担保法》"都不会误报，
    而"根据《合同法》第52条，该合同无效"会被正确记为非法引用。
    """
    if not answer or not invalid_laws:
        return []
    bad: list[str] = []
    for sent in RE_SENT_SPLIT.split(answer):
        if not sent.strip():
            continue
        for law in invalid_laws:
            if not _law_hit(law, sent):
                continue
            if any(m in sent for m in ABOLISH_MARKERS):
                continue
            if RE_SUCCESSION.search(sent):      # D34-①：继承陈述句豁免
                continue
            if any(m in sent for m in NEGATION_WARN_MARKERS):   # D34-③
                continue
            bad.append(law)
    return sorted(set(bad))


# ------------------------------------------------------------------ 单条判分
@dataclass
class ItemMetrics:
    correct: bool = False
    tvc: int | None = None
    case_pass: bool | None = None
    invalid_law_cited: bool = False
    invalid_laws: list = field(default_factory=list)
    cited_golden: bool = False
    key_recall: float = 0.0
    refusal: bool = False
    uncertain: bool = False
    answer_len: int = 0
    slots_inherited_ok: int | None = None
    score_detail: str = ""

    def to_dict(self) -> dict:
        return {
            "correct": bool(self.correct), "tvc": self.tvc,
            "case_pass": self.case_pass,
            "invalid_law_cited": bool(self.invalid_law_cited),
            "invalid_laws": self.invalid_laws,
            "cited_golden": bool(self.cited_golden),
            "key_recall": round(float(self.key_recall), 4),
            "refusal": bool(self.refusal), "uncertain": bool(self.uncertain),
            "answer_len": int(self.answer_len),
            "slots_inherited_ok": self.slots_inherited_ok,
            "score_detail": self.score_detail,
        }


def score_item(answer: str, meta: dict, ctx: dict | None = None) -> ItemMetrics:
    """按类别对一条结果自动判分。ctx 需含 invalid_laws（失效法名集合）等。"""
    ctx = ctx or {}
    invalid_laws: set[str] = ctx.get("invalid_laws", set())
    m = ItemMetrics()
    answer = answer or ""
    m.answer_len = len(answer)
    m.refusal = is_refusal(answer)
    m.uncertain = is_uncertain(answer)
    cites = extract_citations(answer)
    category = meta.get("category", "")
    golden_provs = meta.get("golden_provisions") or []
    golden_answer = meta.get("golden_answer") or ""

    m.cited_golden = cited_golden(cites, golden_provs)
    m.key_recall = key_term_recall(answer, golden_answer) if golden_answer else 0.0
    bad = invalid_law_citations(answer, invalid_laws)
    m.invalid_laws = bad
    m.invalid_law_cited = bool(bad)

    temporal = meta.get("temporal") or {}
    expect_status = temporal.get("expect_status")
    as_of = temporal.get("as_of", "")

    if category == "provision":
        # 判据：引用正确条文 且（有正文重叠 或 答案较短但明确给出条文）
        body_ok = m.key_recall >= 0.30
        m.correct = bool(m.cited_golden and body_ok and not m.refusal)
        m.score_detail = (f"cited_golden={m.cited_golden} "
                          f"key_recall={m.key_recall:.2f} (>=0.30)")

    elif category == "concept":
        m.correct = bool(m.key_recall >= 0.40 and not m.refusal)
        m.score_detail = f"key_recall={m.key_recall:.2f} (>=0.40) refusal={m.refusal}"

    elif category == "case":
        causes = ctx.get("case_causes", [])
        cause_hit = any(c and c.replace("纠纷", "") in answer for c in causes) or \
            bool(cites["case_nos"])
        m.correct = bool(cause_hit and not m.refusal and len(answer) >= 20)
        m.score_detail = f"cause_or_caseno_hit={cause_hit} len={len(answer)}"

    elif category == "multi-turn":
        # 沿用底层意图：有 golden 条文则按 provision，否则按 concept
        if golden_provs:
            m.correct = bool(m.cited_golden and m.key_recall >= 0.25
                             and not m.refusal)
            m.score_detail = (f"[mt/provision] cited_golden={m.cited_golden} "
                              f"key_recall={m.key_recall:.2f}")
        else:
            m.correct = bool(m.key_recall >= 0.35 and not m.refusal)
            m.score_detail = f"[mt/concept] key_recall={m.key_recall:.2f}"

    elif category == "temporal_trap":
        # TVC：是否正确地"不把失效法条当现行依据"并给出正确的时效结论
        if expect_status in ("已废止", "已修订", "尚未生效", "部分失效"):
            warned = any(mk in answer for mk in ABOLISH_MARKERS) or \
                ("尚未生效" in answer) or ("未生效" in answer) or \
                any(mk in answer for mk in NEGATION_WARN_MARKERS)   # D34-③
            m.tvc = int(bool(warned) and not m.invalid_laws)
        elif expect_status == "现行有效":
            wrongly_abolished = ("已废止" in answer or "已失效" in answer) and \
                not any(mk in answer for mk in ("未废止", "仍然有效", "现行有效"))
            m.tvc = int(not wrongly_abolished and not m.refusal)
        else:
            m.tvc = int(not m.invalid_law_cited)
        m.correct = bool(m.tvc == 1)
        m.score_detail = (f"expect={expect_status} as_of={as_of} "
                          f"invalid_cited={m.invalid_laws} tvc={m.tvc}")

    elif category == "case_verify":
        gold_pass = meta.get("gold_pass")
        got_pass = ctx.get("case_passed")
        m.case_pass = None if got_pass is None else bool(got_pass)
        m.correct = bool(got_pass is not None and bool(got_pass) == bool(gold_pass))
        m.score_detail = f"gold_pass={gold_pass} got_pass={got_pass}"

    else:
        m.correct = bool(m.key_recall >= 0.35 and not m.refusal)
        m.score_detail = f"fallback key_recall={m.key_recall:.2f}"

    # 槽位继承正确性（多轮）
    if meta.get("turn_id", 0) > 0:
        want = set((meta.get("slots") or {}).get("inherited") or [])
        if want:
            got = set(ctx.get("slots_inherited") or [])
            m.slots_inherited_ok = int(want <= got)
        else:
            m.slots_inherited_ok = None
    return m


# ------------------------------------------------------------------ 汇总
def aggregate(records: list[dict]) -> dict:
    """对一批已判分记录聚合出方法级指标。"""
    n = len(records)
    if n == 0:
        return {"n": 0}
    acc = sum(1 for r in records if r.get("correct")) / n
    rr = sum(1 for r in records if r.get("n_retrieval_calls", 0) > 0) / n
    lac = sum(int(r.get("n_retrieval_calls", 0)) for r in records) / n
    tokens = [int(r.get("n_tokens", 0)) for r in records]
    lat = sorted(float(r.get("latency_ms", 0.0)) for r in records)

    def pct(p: float) -> float:
        if not lat:
            return 0.0
        i = min(int(round(p * (len(lat) - 1))), len(lat) - 1)
        return lat[i]

    tvc_vals = [r["tvc"] for r in records if r.get("tvc") is not None]
    cv = [r for r in records if r.get("case_pass") is not None]
    inv = sum(1 for r in records if r.get("invalid_law_cited"))
    slots = [r["slots_inherited_ok"] for r in records
             if r.get("slots_inherited_ok") is not None]
    return {
        "n": n,
        "acc": round(acc, 4),
        "rr": round(rr, 4),
        "arc": round(lac, 4),
        "lac": round(lac, 4),   # 手册命名别名；实现 = 平均检索调用数
        "tvc": round(sum(tvc_vals) / len(tvc_vals), 4) if tvc_vals else None,
        "tvc_n": len(tvc_vals),
        "case_verify_acc": round(
            sum(1 for r in cv if r["correct"]) / len(cv), 4) if cv else None,
        "case_verify_n": len(cv),
        "invalid_law_citation_rate": round(inv / n, 4),
        "slot_inherit_acc": round(sum(slots) / len(slots), 4) if slots else None,
        "slot_inherit_n": len(slots),
        "p50_ms": round(pct(0.50), 1),
        "p95_ms": round(pct(0.95), 1),
        "mean_ms": round(sum(lat) / len(lat), 1) if lat else 0.0,
        "mean_tokens": round(sum(tokens) / n, 1),
        "total_tokens": int(sum(tokens)),
        "n_refusal": sum(1 for r in records if r.get("refusal")),
    }


def case_verify_prf(records: list[dict]) -> dict:
    """E6 的二分类口径：pass（放行）vs 拦截。"""
    tp = fp = fn = tn = 0
    for r in records:
        gold = bool(r.get("gold_pass"))
        got = bool(r.get("case_pass"))
        if gold and got:
            tp += 1
        elif not gold and got:
            fp += 1
        elif gold and not got:
            fn += 1
        else:
            tn += 1
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return {"precision": round(prec, 4), "recall": round(rec, 4),
            "f1": round(f1, 4), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "threshold_note": "二分类口径 = 放行(核验通过) vs 拦截(任一非通过级别)；"
                              "本机案号库为合成数据，指标仅反映核验器判别能力"}
