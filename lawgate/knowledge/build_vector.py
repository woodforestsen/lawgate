# -*- coding: utf-8 -*-
"""向量库构建（手册 S2.6）：法条 + 合成文书分块 → 编码 → Chroma/Numpy。

用法：
    python -m lawgate.knowledge.build_vector                 # 全量重建
    python -m lawgate.knowledge.build_vector --backend numpy # 强制 numpy 后端
    python -m lawgate.knowledge.build_vector --dry-run       # 只报告规模

产物：data/kb/chroma/（chromadb）或 data/kb/chroma/{embeddings.npy,records.jsonl}
      data/kb/vector_build.json（规模 + 编码器溯源，供图注引用）
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lawgate.config import get_settings  # noqa: E402
from lawgate.knowledge.embed import get_embedder  # noqa: E402
from lawgate.knowledge.store import get_store  # noqa: E402


def chunk(text: str, size: int = 300, overlap: int = 50) -> list[str]:
    """手册 S2.6 的分块：300 字、50 字重叠。"""
    if size <= overlap:
        raise ValueError("size 必须大于 overlap")
    step = size - overlap
    return [text[i:i + size] for i in range(0, max(len(text) - overlap, 1), step)] or [text]


def collect_records(db_path: str, top_judgments: int = 2000,
                    include_judgments: bool = True) -> tuple[list, list, list]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    ids: list[str] = []
    docs: list[str] = []
    metas: list[dict] = []

    # 法条：按"条"聚合（把该条所有款项拼成一段），避免同一来源被切碎
    rows = conn.execute(
        """SELECT law_short, law_name, article_no, article_label, version,
                  validity_status, source_url,
                  GROUP_CONCAT(text, ' ') AS body
           FROM (SELECT * FROM legal_provisions ORDER BY law_short, article_no,
                        paragraph_no, item_idx)
           GROUP BY law_short, version, article_no
           ORDER BY law_short, article_no""").fetchall()
    for r in rows:
        ids.append(f"lp_{r['law_short']}_{r['version']}_{r['article_no']}")
        docs.append(f"{r['law_short']}{r['article_label']} {r['body']}")
        metas.append({
            "type": "provision",
            "law": r["law_short"],
            "article": r["article_no"],
            "article_label": r["article_label"],
            "validity_status": r["validity_status"],
            "version": r["version"],
            "url": r["source_url"] or "",
        })

    n_jud = 0
    if include_judgments:
        for r in conn.execute(
                "SELECT case_no, court_name, cause_action, chunks FROM judgments LIMIT ?",
                (top_judgments,)):
            for i, c in enumerate(json.loads(r["chunks"] or "[]")):
                ids.append(f"jd_{r['case_no']}_{i}")
                docs.append(c)
                metas.append({"type": "judgment", "case_no": r["case_no"],
                              "cause": r["cause_action"], "url": ""})
                n_jud += 1
    conn.close()
    return ids, docs, metas


def build(backend: str = "auto", db_path: str | None = None,
          col_path: str | None = None, top_judgments: int = 2000,
          include_judgments: bool = True, batch: int = 64) -> dict:
    s = get_settings()
    db_path = db_path or s.db_path
    col_path = col_path or s.chroma_path

    ids, docs, metas = collect_records(db_path, top_judgments=top_judgments,
                                       include_judgments=include_judgments)
    if not ids:
        raise RuntimeError("没有可入库的文档，请先运行 lawgate.knowledge.build_sqlite")

    t0 = time.time()
    emb_model = get_embedder()
    # 清空旧库（chromadb 用独立目录需先删；numpy 由 add 覆盖）
    p = Path(col_path)
    if p.exists() and backend == "numpy":
        for f in ("embeddings.npy", "records.jsonl"):
            (p / f).unlink(missing_ok=True)
    store = get_store(col_path, prefer=backend)

    all_emb = []
    for i in range(0, len(docs), batch):
        all_emb.append(emb_model.encode_documents(docs[i:i + batch]))
    import numpy as np

    mat = np.vstack(all_emb) if all_emb else np.zeros((0, 0), dtype=np.float32)
    store.add(ids=ids, docs=docs, metas=metas, embeddings=mat)

    n_prov = sum(1 for m in metas if m["type"] == "provision")
    n_jd = sum(1 for m in metas if m["type"] == "judgment")
    n_jud = len({m["case_no"] for m in metas if m["type"] == "judgment"})
    report = {
        "date": s.run_date,
        "backend": store.backend,
        "embed_backend": emb_model.name,
        "embed_dim": int(mat.shape[1]) if mat.size else 0,
        "n_docs": len(ids),
        "n_provisions": n_prov,
        "n_judgment_chunks": n_jd,
        "n_judgments": n_jud,
        "col_path": str(col_path),
        "seconds": round(time.time() - t0, 1),
        "hardware": s.hardware_note,
    }
    Path(s.kb_dir).mkdir(parents=True, exist_ok=True)
    (Path(s.kb_dir) / "vector_build.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["auto", "numpy", "chromadb"], default="auto")
    ap.add_argument("--db", default=None)
    ap.add_argument("--col", default=None)
    ap.add_argument("--top-judgments", type=int, default=2000)
    ap.add_argument("--no-judgments", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.dry_run:
        ids, docs, metas = collect_records(args.db or get_settings().db_path,
                                           top_judgments=args.top_judgments,
                                           include_judgments=not args.no_judgments)
        print(json.dumps({"n_docs": len(ids),
                          "n_provisions": sum(1 for m in metas if m["type"] == "provision"),
                          "n_judgment_chunks": sum(1 for m in metas if m["type"] == "judgment")},
                         indent=2))
        return 0

    r = build(backend=args.backend, db_path=args.db, col_path=args.col,
              top_judgments=args.top_judgments,
              include_judgments=not args.no_judgments)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
