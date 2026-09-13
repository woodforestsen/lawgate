# -*- coding: utf-8 -*-
"""通道 C·语义检索 + 重排（手册 S3.8）。

对外两个接口：
  * ``search(query, top_k, rerank) -> str``  供通道 C 拼参考资料；
  * ``search_hits(query, top_k, rerank) -> list[Hit]``  供评测与溯源（含 scores）。

``Retriever`` 惰性加载编码器与向量库，避免评测脚本导入时即付出加载成本。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from lawgate.config import get_settings
from lawgate.knowledge.embed import Embedder, get_embedder
from lawgate.knowledge.rerank import Reranker
from lawgate.knowledge.store import BaseStore, Hit, get_store


@dataclass
class RetrievalResult:
    query: str
    hits: list = field(default_factory=list)
    context: str = ""
    top_k: int = 8
    reranked: bool = True
    seconds: float = 0.0
    backend: str = ""
    embed_backend: str = ""

    def to_dict(self) -> dict:
        return {"top_k": self.top_k, "reranked": self.reranked,
                "seconds": round(self.seconds, 3), "backend": self.backend,
                "embed_backend": self.embed_backend,
                "hits": [{"doc_id": h.doc_id, "score": round(h.score, 4),
                          "type": (h.meta or {}).get("type"),
                          "law": (h.meta or {}).get("law"),
                          "article": (h.meta or {}).get("article"),
                          "case_no": (h.meta or {}).get("case_no")}
                         for h in self.hits]}


class Retriever:
    def __init__(self, col_path: str | None = None, backend: str = "auto",
                 embedder: Embedder | None = None, reranker: Reranker | None = None):
        s = get_settings()
        self.col_path = col_path or s.chroma_path
        self.backend_pref = backend
        self._store: BaseStore | None = None
        self._emb: Embedder | None = embedder
        self.reranker = reranker or Reranker()
        # chromadb 客户端与 sentence-transformers 的会话状态都不是线程安全的；
        # 检索本身仅 ~50ms（相对生成 10s 可忽略），故整体加锁串行最稳妥。
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 惰性资源
    @property
    def store(self) -> BaseStore:
        if self._store is None:
            self._store = get_store(self.col_path, prefer=self.backend_pref)
        return self._store

    @property
    def embedder(self) -> Embedder:
        if self._emb is None:
            self._emb = get_embedder()
        return self._emb

    @property
    def backend(self) -> str:
        return getattr(self.store, "backend", "?")

    def count(self) -> int:
        return self.store.count()

    # ---------------------------------------------------------------- 检索
    def search_hits(self, query: str, top_k: int = 8, rerank: bool = True) -> list:
        qv = self.embedder.encode_queries([query])[0]
        cand_k = max(top_k, top_k * 3) if rerank else top_k
        hits = self.store.query(qv, top_k=cand_k)
        if rerank and hits:
            hits = self.reranker.rerank(query, hits, top_n=top_k)
        return hits[:top_k]

    def retrieve(self, query: str, top_k: int = 8, rerank: bool = True,
                 max_chars: int = 300) -> RetrievalResult:
        t0 = time.time()
        with self._lock:
            hits = self.search_hits(query, top_k=top_k, rerank=rerank)
        ctx_parts = []
        for h in hits:
            tag = ""
            m = h.meta or {}
            if m.get("type") == "provision":
                tag = f"[{m.get('law')}{m.get('article_label', '')}]"
            elif m.get("type") == "judgment":
                tag = f"[{m.get('case_no')} {m.get('cause', '')}]"
            ctx_parts.append(f"{tag} {h.text[:max_chars]}")
        return RetrievalResult(
            query=query, hits=hits, context="\n\n".join(ctx_parts),
            top_k=top_k, reranked=rerank, seconds=time.time() - t0,
            backend=self.backend, embed_backend=self.embedder.name)

    def search(self, query: str, top_k: int = 8, rerank: bool = True) -> str:
        """兼容手册签名：直接返回拼接好的参考资料文本。"""
        return self.retrieve(query, top_k=top_k, rerank=rerank).context
