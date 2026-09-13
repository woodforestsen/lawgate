# -*- coding: utf-8 -*-
"""评测 I/O：划分装载、结果落盘、汇总表、溯源图注。

硬规则（手册 S6.1）：所有脚本强制 ``--seed``，结果落盘后**不覆盖**；
若目标文件已存在则拒绝写入，除非显式 ``--overwrite``。
"""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path

from lawgate.config import get_settings


def load_split(name: str, bench_dir: str | Path | None = None) -> list[dict]:
    """按名字装载划分：dev / test / test_multiturn / temporal_trap /
    case_no_verify / all。"""
    d = Path(bench_dir or get_settings().bench_dir)
    p = d / f"{name}.jsonl"
    if not p.exists():
        raise FileNotFoundError(f"找不到划分文件 {p}；请先运行 scripts/build_benchmark.py")
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def load_results(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def write_jsonl(records: list[dict], path: str | Path, overwrite: bool = False) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and not overwrite:
        raise FileExistsError(
            f"{p} 已存在；结果不得静默覆盖（手册 S6.1）。加 --overwrite 才会重写。")
    with open(p, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return p


def result_path(out_dir: str | Path, method: str, seed: int,
                split: str, tag: str = "") -> Path:
    suffix = f"_{tag}" if tag else ""
    return Path(out_dir) / f"{method}{suffix}_seed{seed}_{split}.jsonl"


def write_json(obj, path: str | Path, overwrite: bool = True) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and not overwrite:
        return p
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def read_json(path: str | Path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def write_summary_csv(rows: list[dict], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        p.write_text("", encoding="utf-8")
        return p
    keys: list[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    return p


def prompt_hash(*parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def run_meta(settings=None, extra: dict | None = None) -> dict:
    s = settings or get_settings()
    d = s.provenance()
    d["written_at"] = datetime.now().isoformat(timespec="seconds")
    if extra:
        d.update(extra)
    return d


def stamp_footer(provenance: dict, extra: str = "") -> str:
    """图注文本：每张图都必须带硬件/模型/seed/日期（手册阶段五验收）。"""
    bits = [
        f"硬件：{provenance.get('hardware', '?')}",
        f"模型：{provenance.get('causal_model', '?')}",
        f"向量模型：{provenance.get('embed_model', '?')}",
        f"后端：{provenance.get('llm_backend', '?')}",
        f"日期：{provenance.get('date', '?')}",
        f"版本：v{provenance.get('version', '?')}",
    ]
    if extra:
        bits.append(extra)
    return " ｜ ".join(bits)
