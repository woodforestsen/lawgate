# -*- coding: utf-8 -*-
"""文本向量编码器（手册 S2.6）。

优先使用手册指定的 **BAAI/bge-small-zh-v1.5**（本地目录 models/bge-small-zh-v1.5，
由 scripts/fetch_hf_files.py 拉取）。若模型不可用，退回纯 numpy 的
字符 n-gram 哈希编码器，保证全链路在完全离线下仍可运行——两条路径通过
``Embedder.name`` 区分，且**必须**写进结果溯源，避免把降级结果当成正式结果。

bge 系列要求查询侧加指令前缀；文档侧不加。这是 bge 的既定用法，写错会明显
拉低检索召回，故本模块把 ``encode_queries`` / ``encode_documents`` 分开。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

BGE_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


def _l2norm(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return m / n


class Embedder:
    """编码器基类。"""

    name: str = "base"
    dim: int = 0

    def encode_documents(self, texts: list[str]) -> np.ndarray:  # pragma: no cover
        raise NotImplementedError

    def encode_queries(self, texts: list[str]) -> np.ndarray:
        return self.encode_documents(texts)

    # 兼容 sentence-transformers 的调用签名
    def encode(self, texts, normalize_embeddings: bool = True, **kw) -> np.ndarray:
        return self.encode_documents(list(texts))


class BGEEmbedder(Embedder):
    """bge-small-zh-v1.5（或任意 sentence-transformers 模型）。"""

    def __init__(self, model_path: str, device: str = "cpu"):
        from sentence_transformers import SentenceTransformer

        self.model_path = model_path
        self.model = SentenceTransformer(model_path, device=device)
        self.name = Path(model_path).name if Path(model_path).is_dir() else model_path
        # 兼容 sentence-transformers 3.x/5.x 的方法改名
        self.dim = 0
        for meth in ("get_embedding_dimension", "get_sentence_embedding_dimension"):
            fn = getattr(self.model, meth, None)
            if fn is None:
                continue
            try:
                self.dim = int(fn())
                break
            except Exception:  # noqa: BLE001
                continue

    def _enc(self, texts: list[str], prompt: str | None) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim or 512), dtype=np.float32)
        kw = {"normalize_embeddings": True, "show_progress_bar": False}
        if prompt:
            kw["prompt"] = prompt
        v = self.model.encode(texts, **kw)
        return np.asarray(v, dtype=np.float32)

    def encode_documents(self, texts: list[str]) -> np.ndarray:
        return self._enc(texts, None)

    def encode_queries(self, texts: list[str]) -> np.ndarray:
        return self._enc(texts, BGE_QUERY_INSTRUCTION)


class HashingCharEmbedder(Embedder):
    """纯 numpy 字符 n-gram 哈希编码（离线兜底，无任何外部依赖）。

    对中文法律文本，字符 bigram/trigram 哈希 + 次线性 TF 权重已足以支撑
    "找到同一法条/同一案由的相近表述"这一档召回；但它不是语义模型，
    结果中必须标注 ``embed_backend=HashingCharEmbedder``。
    """

    def __init__(self, dim: int = 512, ngrams: tuple[int, ...] = (1, 2, 3)):
        self.dim = dim
        self.ngrams = ngrams
        self.name = f"HashingCharEmbedder(dim={dim},ngram={'-'.join(map(str, ngrams))})"

    def _one(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        t = "".join(text.split())
        for n in self.ngrams:
            if len(t) < n:
                continue
            for i in range(len(t) - n + 1):
                g = t[i:i + n].encode("utf-8")
                h = int.from_bytes(hashlib.blake2b(g, digest_size=8).digest(), "little")
                idx = h % self.dim
                sign = 1.0 if (h >> 63) & 1 else -1.0
                v[idx] += sign
        # 次线性 TF + L2 归一化
        v = np.sign(v) * np.log1p(np.abs(v))
        return v

    def encode_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return _l2norm(np.stack([self._one(t) for t in texts]))


def get_embedder(prefer: str | None = None, device: str = "cpu",
                 allow_fallback: bool = True) -> Embedder:
    """返回可用的编码器：优先 bge 本地目录，其次 HF 缓存，最后哈希兜底。"""
    from lawgate.config import EMBED_MODEL_CANDIDATES, get_settings

    s = get_settings()
    cands: list[str] = []
    if prefer:
        cands.append(prefer)
    if s.embed_model:
        cands.append(s.embed_model)
    cands += EMBED_MODEL_CANDIDATES

    seen: set[str] = set()
    for c in cands:
        if not c or c in seen:
            continue
        seen.add(c)
        # 只尝试本地目录，避免离线环境下卡在下载
        if not Path(c).is_dir():
            continue
        try:
            return BGEEmbedder(c, device=device)
        except Exception as exc:  # noqa: BLE001
            print(f"[embed] {c} 加载失败（{type(exc).__name__}: {exc}），尝试下一个")
    if not allow_fallback:
        raise RuntimeError("没有可用的语义编码模型")
    print("[embed] 语义模型不可用，退回 HashingCharEmbedder（离线兜底，结果需标注）")
    return HashingCharEmbedder()
