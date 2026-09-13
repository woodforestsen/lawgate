# -*- coding: utf-8 -*-
"""离线/在线模型拉取器。

手册 S0 要求 Qwen2.5-1.5B-Instruct（冒烟 + 门控实验）与 bge-small-zh-v1.5（向量库）。
本脚本按需拉取到统一缓存目录，已存在则跳过，网络不可用时给出明确降级提示。

用法：
    python scripts/fetch_models.py --list
    python scripts/fetch_models.py --all
    python scripts/fetch_models.py --only BAAI/bge-small-zh-v1.5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ALLOW = {
    "pattern": ["Qwen/Qwen2.5-0.5B-Instruct.safetensors", "*.json", "*.txt", "*.py"],
}

TARGETS = [
    {
        "repo": "BAAI/bge-small-zh-v1.5",
        "role": "通道 C 向量编码（手册指定）",
        "weight": "~95MB",
        "critical": True,
    },
    {
        "repo": "Qwen/Qwen2.5-1.5B-Instruct",
        "role": "门控草稿 logprobs + 通道 A/C 生成（手册 S0.4 指定）",
        "weight": "~3.1GB",
        "critical": True,
    },
    {
        "repo": "Qwen/Qwen2.5-0.5B-Instruct",
        "role": "CPU 降级生成后端（已本地缓存）",
        "weight": "~1.0GB",
        "critical": False,
    },
]


def cache_dir() -> Path:
    env = os.environ.get("LAWGATE_MODEL_CACHE") or os.environ.get("HF_HOME")
    if env:
        return Path(env) / "hub"
    return Path("E:/ModelCache/huggingface/hub")


def is_cached(repo: str) -> bool:
    from huggingface_hub import try_to_load_from_cache

    try:
        hit = try_to_load_from_cache(repo, "config.json")
        if not isinstance(hit, str):
            return False
        # 有 config 还不够，需有实际权重
        for fn in ("model.safetensors", "pytorch_model.bin"):
            if isinstance(try_to_load_from_cache(repo, fn), str):
                return True
        hits = try_to_load_from_cache(repo, "model.safetensors.index.json")
        return isinstance(hits, str)
    except Exception:  # noqa: BLE001
        return False


def fetch(repo: str, allow_patterns=None) -> dict:
    from huggingface_hub import snapshot_download

    t0 = time.time()
    try:
        path = snapshot_download(
            repo_id=repo,
            cache_dir=str(cache_dir()),
            allow_patterns=allow_patterns,
            max_workers=8,
        )
        return {"repo": repo, "ok": True, "path": path,
                "seconds": round(time.time() - t0, 1)}
    except Exception as exc:  # noqa: BLE001
        return {"repo": repo, "ok": False, "error": f"{type(exc).__name__}: {exc}",
                "seconds": round(time.time() - t0, 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--skip-cached", action="store_true", default=True)
    args = ap.parse_args()

    if args.list:
        for t in TARGETS:
            print(f"{t['repo']:32s} cached={is_cached(t['repo'])!s:5s} {t['weight']:8s} {t['role']}")
        return 0

    want = args.only or [t["repo"] for t in TARGETS] if (args.all or args.only) else []
    if not want:
        ap.print_help()
        return 2

    results = []
    for repo in want:
        if args.skip_cached and is_cached(repo):
            print(f"[skip] {repo} 已在缓存", flush=True)
            results.append({"repo": repo, "ok": True, "cached": True})
            continue
        print(f"[get ] {repo} …", flush=True)
        r = fetch(repo)
        print(f"       -> {r}", flush=True)
        results.append(r)

    Path("docs").mkdir(exist_ok=True)
    (Path("docs") / "model_fetch_report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0 if all(r.get("ok") for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
