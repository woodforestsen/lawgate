# -*- coding: utf-8 -*-
"""重排器（手册 S3.8 的 `rerank=True` 路径）。

手册给的"文档内含 query 关键词加分"过于粗糙：它把 query 当字符集合，对长文档
天然有利。这里改用两段式：

  1. BM25（rank_bm25）在候选集内打分——对"同一法条/同一案由的相近表述"有效；
  2. 条款号/案号精确匹配加权——法律检索里"条号命中"必须压过一般词面相似。

最终分数 = 0.6·BM25归一 + 0.25·字符覆盖率 + 0.15·结构命中（条号/案号）。
中文分词优先 jieba，缺失则退化为字符二元组。``reranker`` 名称写入溯源。
"""
from __future__ import annotations

import re
import warnings

from lawgate.knowledge.judgment_parser import RE_CASE_NO  # D31 唯一出处

_ARTICLE_RE = re.compile(r"第([零〇一二三四五六七八九十百千0-9]+)条")


def _case_nos(text: str) -> set[str]:
    """把文本里出现的**每一个**合法案号抽成规范化串（D31 唯一出处）。

    旧实现用本地无捕获组的案号正则（与判断库口径分叉）；现统一走 judgment_parser。
    RE_CASE_NO 的 5 个捕获组（年份/法院/类型代字/审级/序号）逐条重组完整串。
    """
    out: set[str] = set()
    for m in RE_CASE_NO.finditer(text or ""):
        year, court, cat, subcat, seq = m.groups()
        out.add(f"（{year}）{court}{cat}{subcat or ''}{int(seq)}号")
    return out

try:  # jieba 会触发 pkg_resources 弃用告警，屏蔽之
    warnings.filterwarnings("ignore", category=UserWarning, module="jieba")
    import jieba  # type: ignore

    jieba.setLogLevel(60)
    _HAS_JIEBA = True
except Exception:  # noqa: BLE001
    _HAS_JIEBA = False


def tokenize(text: str) -> list[str]:
    if _HAS_JIEBA:
        return [t for t in jieba.lcut(text) if t.strip()]
    t = "".join(text.split())
    return [t[i:i + 2] for i in range(max(len(t) - 1, 1))] or [t]


class Reranker:
    def __init__(self, use_bm25: bool = True):
        self.use_bm25 = use_bm25
        self.name = ("bm25+char+struct" if use_bm25 else "char+struct")

    def rerank(self, query: str, hits: list, top_n: int = 5) -> list:
        """hits: list[Hit]（含 .text/.meta/.score）。返回重排后的前 top_n 条。"""
        if not hits:
            return []
        docs = [h.text for h in hits]
        bm = self._bm25(query, docs) if self.use_bm25 else [0.0] * len(docs)

        q_chars = set("".join(query.split()))
        q_arts = set(_ARTICLE_RE.findall(query))
        q_cases = set(_case_nos(query))   # D31：案号抽取走唯一出处（库口径）

        scored = []
        for i, h in enumerate(hits):
            doc = h.text
            cover = len(q_chars & set("".join(doc.split()))) / max(len(q_chars), 1)
            meta = h.meta or {}
            struct = 0.0
            if q_arts:
                doc_arts = set(_ARTICLE_RE.findall(doc))
                lbl = str(meta.get("article", ""))
                doc_arts |= {lbl} if lbl else set()
                if q_arts & doc_arts:
                    struct = 1.0
            if q_cases and any(c in doc for c in q_cases):
                struct = 1.0
            final = 0.60 * bm[i] + 0.25 * cover + 0.15 * struct
            scored.append((final, h))

        scored.sort(key=lambda t: -t[0])
        out = []
        for final, h in scored[:top_n]:
            h2 = type(h)(h.doc_id, h.text, dict(h.meta or {}), float(final))
            setattr(h2, "vector_score", h.score)
            out.append(h2)
        return out

    @staticmethod
    def _bm25(query: str, docs: list[str]) -> list[float]:
        try:
            from rank_bm25 import BM25Okapi

            corpus = [tokenize(d) for d in docs]
            if not any(corpus):
                return [0.0] * len(docs)
            bm = BM25Okapi(corpus)
            raw = bm.get_scores(tokenize(query))
            hi = float(max(raw)) if len(raw) else 0.0
            return [float(x) / hi if hi > 0 else 0.0 for x in raw]
        except Exception:  # noqa: BLE001
            return [0.0] * len(docs)
