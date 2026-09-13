# -*- coding: utf-8 -*-
"""向量库封装（手册 S2.6 / 通道 C 的检索后端）。

两条后端：
  * ``ChromaStore``——chromadb 持久化集合（手册指定）；
  * ``NumpyStore``——npz + jsonl 的精确余弦检索（零依赖兜底）。

环境实测中 chromadb 可导入，但离线/受限环境下其持久化与遥测可能失败，
故 `get_store()` 做可用性探测并自动降级，且把 ``backend`` 写进溯源。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class Hit:
    doc_id: str
    text: str
    meta: dict
    score: float  # 余弦相似度（越大越相关）


class BaseStore:
    backend: str = "base"
    dim: int = 0

    def add(self, ids, docs, metas, embeddings) -> None:  # pragma: no cover
        raise NotImplementedError

    def query(self, embedding: np.ndarray, top_k: int = 8) -> list[Hit]:  # pragma: no cover
        raise NotImplementedError

    def count(self) -> int:  # pragma: no cover
        raise NotImplementedError


class NumpyStore(BaseStore):
    """精确余弦检索。数据量大时慢，但本文档级规模（数千条）完全够用且完全可控。"""

    backend = "numpy"

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self._emb = np.zeros((0, 0), dtype=np.float32)
        self._ids: list[str] = []
        self._docs: list[str] = []
        self._metas: list[dict] = []
        self.load()

    # ---------------------------------------------------------------- 持久化
    @property
    def _emb_file(self) -> Path:
        return self.path / "embeddings.npy"

    @property
    def _meta_file(self) -> Path:
        return self.path / "records.jsonl"

    def save(self) -> None:
        np.save(self._emb_file, self._emb)
        with open(self._meta_file, "w", encoding="utf-8") as f:
            for i, d, m in zip(self._ids, self._docs, self._metas):
                f.write(json.dumps({"id": i, "doc": d, "meta": m},
                                   ensure_ascii=False) + "\n")

    def load(self) -> bool:
        if not (self._emb_file.exists() and self._meta_file.exists()):
            return False
        self._emb = np.load(self._emb_file).astype(np.float32)
        self._ids, self._docs, self._metas = [], [], []
        with open(self._meta_file, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                self._ids.append(r["id"])
                self._docs.append(r["doc"])
                self._metas.append(r["meta"])
        self.dim = int(self._emb.shape[1]) if self._emb.size else 0
        return True

    # ---------------------------------------------------------------- 接口
    def add(self, ids, docs, metas, embeddings) -> None:
        emb = np.asarray(embeddings, dtype=np.float32)
        if self._emb.size == 0:
            self._emb = emb
        else:
            self._emb = np.vstack([self._emb, emb])
        self._ids += list(ids)
        self._docs += list(docs)
        self._metas += [dict(m) for m in metas]
        self.dim = int(self._emb.shape[1])
        self.save()

    def query(self, embedding: np.ndarray, top_k: int = 8) -> list[Hit]:
        if self._emb.size == 0:
            return []
        q = np.asarray(embedding, dtype=np.float32).reshape(-1)
        qn = np.linalg.norm(q) or 1.0
        sims = (self._emb @ q) / (np.linalg.norm(self._emb, axis=1) * qn + 1e-12)
        k = min(top_k, len(sims))
        idx = np.argpartition(-sims, k - 1)[:k]
        idx = idx[np.argsort(-sims[idx])]
        return [Hit(self._ids[i], self._docs[i], self._metas[i], float(sims[i]))
                for i in idx]

    def count(self) -> int:
        return len(self._ids)


class ChromaStore(BaseStore):
    """chromadb 持久化集合（手册指定的后端）。"""

    backend = "chromadb"

    def __init__(self, path: str | Path, collection: str = "legal"):
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(
            path=str(self.path),
            settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True),
        )
        self.col = self.client.get_or_create_collection(
            name=collection, metadata={"hnsw:space": "cosine"})
        self.dim = 0

    def add(self, ids, docs, metas, embeddings) -> None:
        emb = np.asarray(embeddings, dtype=np.float32)
        self.dim = int(emb.shape[1]) if emb.size else 0
        # chromadb 元数据不接受 None，需清洗
        clean = [{k: ("" if v is None else v) for k, v in m.items()} for m in metas]
        self.col.upsert(ids=list(ids), documents=list(docs), metadatas=clean,
                        embeddings=emb.tolist())

    def query(self, embedding: np.ndarray, top_k: int = 8) -> list[Hit]:
        q = np.asarray(embedding, dtype=np.float32).reshape(-1)
        r = self.col.query(query_embeddings=[q.tolist()], n_results=top_k,
                           include=["documents", "metadatas", "distances"])
        docs = (r.get("documents") or [[]])[0]
        metas = (r.get("metadatas") or [[]])[0]
        dists = (r.get("distances") or [[]])[0]
        ids = (r.get("ids") or [[]])[0]
        return [Hit(i, d, m or {}, 1.0 - float(dist))
                for i, d, m, dist in zip(ids, docs, metas, dists)]

    def count(self) -> int:
        return int(self.col.count())


def get_store(path: str | Path, collection: str = "legal",
              prefer: str = "auto") -> BaseStore:
    """优先 chromadb，失败则 numpy。prefer='numpy' 可强制兜底。"""
    if prefer != "numpy":
        try:
            return ChromaStore(path, collection=collection)
        except Exception as exc:  # noqa: BLE001
            print(f"[store] chromadb 不可用（{type(exc).__name__}: {exc}），改用 NumpyStore")
    return NumpyStore(path)
