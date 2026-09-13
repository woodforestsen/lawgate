# -*- coding: utf-8 -*-
"""生成/草稿结果缓存（E1 的算力前提）。

为什么必须有：
E1 要在 6 个方法 × 3 个 seed × 864 条测试题上跑生成。本机 CPU 单条 128 token
生成约 10–15 秒，总需求超过 40 机时。但**贪心解码是确定性的**，同一
(模型, prompt, max_tokens) 必然得到同一结果，且不同方法之间存在大量重复：

  NeverRAG / ComplexityRouter(直答) / TARG(直答) / LegalGate(通道A) → 同一 prompt
  AlwaysRAG / ComplexityRouter(检索) / TARG(检索) / LegalGate(通道C) → 同一 prompt

缓存把这些重复折叠掉，实测把唯一生成数从 ~1.5 万降到 ~1.7 千。

缓存是**内容寻址**的（sha256 of model|kind|max_tokens|prompt），因此：
  * 换模型自动失效；
  * 结果可复现、可审计；
  * 缓存文件本身可作为"没有偷跑"的证据（记录每条 prompt 的哈希）。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS gen_cache (
    key         TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    model       TEXT NOT NULL,
    max_tokens  INTEGER,
    payload     TEXT NOT NULL,
    n_tokens    INTEGER,
    seconds     REAL,
    created     TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_gc_kind ON gen_cache(kind, model);
CREATE TABLE IF NOT EXISTS gen_stats (
    key TEXT PRIMARY KEY,
    hits INTEGER DEFAULT 0,
    misses INTEGER DEFAULT 0
);
"""


def make_key(model: str, kind: str, max_tokens: int | None, prompt: str) -> str:
    h = hashlib.sha256()
    h.update(f"{model}\x00{kind}\x00{max_tokens}\x00".encode("utf-8"))
    h.update(prompt.encode("utf-8"))
    return h.hexdigest()


class GenCache:
    """SQLite 支持的线程/进程安全缓存。"""

    def __init__(self, path: str | Path = "data/kb/gen_cache.db",
                 enabled: bool = True):
        self.path = Path(path)
        self.enabled = enabled
        self._lock = threading.Lock()
        self._local = threading.local()
        if self.enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._conn() as c:
                c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.path), timeout=60.0,
                                   check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    def get(self, key: str):
        if not self.enabled:
            return None
        with self._lock:
            row = self._conn().execute(
                "SELECT payload FROM gen_cache WHERE key=?", (key,)).fetchone()
            if row is None:
                return None
            return json.loads(row[0])

    def put(self, key: str, kind: str, model: str, max_tokens: int | None,
            payload, n_tokens: int = 0, seconds: float = 0.0) -> None:
        if not self.enabled:
            return
        with self._lock:
            conn = self._conn()
            conn.execute(
                "INSERT OR REPLACE INTO gen_cache"
                "(key,kind,model,max_tokens,payload,n_tokens,seconds) "
                "VALUES (?,?,?,?,?,?,?)",
                (key, kind, model, max_tokens,
                 json.dumps(payload, ensure_ascii=False), n_tokens, seconds))
            conn.commit()

    def hit_miss(self, key: str, hit: bool) -> None:
        if not self.enabled:
            return
        with self._lock:
            conn = self._conn()
            conn.execute(
                "INSERT INTO gen_stats(key,hits,misses) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET "
                "hits=hits+excluded.hits, misses=misses+excluded.misses",
                (key, 1 if hit else 0, 0 if hit else 1))
            conn.commit()

    def stats(self) -> dict:
        if not self.enabled:
            return {"enabled": False}
        with self._lock:
            rows = self._conn().execute(
                "SELECT kind, COUNT(*) n, SUM(n_tokens) tok, SUM(seconds) sec "
                "FROM gen_cache GROUP BY kind").fetchall()
            tot = self._conn().execute(
                "SELECT COUNT(*) n, COALESCE(SUM(n_tokens),0) t, "
                "COALESCE(SUM(seconds),0) s FROM gen_cache").fetchone()
        return {
            "enabled": True,
            "by_kind": {k: {"entries": n, "tokens": t or 0, "seconds": round(s or 0, 1)}
                        for k, n, t, s in rows},
            "total_entries": tot[0], "total_tokens": tot[1],
            "total_compute_seconds": round(tot[2], 1),
        }

    def clear(self) -> None:
        with self._lock:
            conn = self._conn()
            conn.execute("DELETE FROM gen_cache")
            conn.execute("DELETE FROM gen_stats")
            conn.commit()


_GLOBAL: GenCache | None = None


def get_cache(path: str | Path | None = None, enabled: bool = True) -> GenCache:
    global _GLOBAL
    if _GLOBAL is None or path is not None:
        _GLOBAL = GenCache(path or "data/kb/gen_cache.db", enabled=enabled)
    return _GLOBAL
