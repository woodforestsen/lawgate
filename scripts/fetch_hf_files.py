# -*- coding: utf-8 -*-
"""直连下载 HF 模型文件到本地目录（绕过 huggingface_hub 的 httpx CA 问题）。

背景：本机 huggingface_hub(httpx) 报 CERTIFICATE_VERIFY_FAILED，但 urllib 直连可用。
本脚本用 urllib 按显式文件清单拉取，落到 models/<name>/ ，供 transformers /
sentence-transformers 直接以本地路径加载（完全离线可用）。

用法：
    python scripts/fetch_hf_files.py --manifest bge-small-zh-v1.5
    python scripts/fetch_hf_files.py --all
    python scripts/fetch_hf_files.py --probe          # 只测连同性与速度
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# 候选端点：官方优先，镜像兜底（本机 SSL 中间层对 huggingface.co 不稳定）
ENDPOINTS = [
    "https://huggingface.co",
    "https://hf-mirror.com",
    "https://huggingface.co.cn",
]

MANIFESTS = {
    "bge-small-zh-v1.5": {
        "repo": "BAAI/bge-small-zh-v1.5",
        "dest": "models/bge-small-zh-v1.5",
        "files": [
            "config.json",
            "model.safetensors",
            "tokenizer.json",
            "tokenizer_config.json",
            "vocab.txt",
            "special_tokens_map.json",
            "modules.json",
            "sentence_bert_config.json",
            "config_sentence_transformers.json",
            "1_Pooling/config.json",
        ],
        "optional": ["README.md", "tokenizer.model"],
    },
    "qwen2.5-0.5b-instruct": {
        "repo": "Qwen/Qwen2.5-0.5B-Instruct",
        "dest": "models/qwen2.5-0.5b-instruct",
        "files": [
            "config.json",
            "generation_config.json",
            "model.safetensors",
            "tokenizer.json",
            "tokenizer_config.json",
            "vocab.json",
            "merges.txt",
        ],
        "optional": [],
    },
    "qwen2.5-1.5b-instruct": {
        "repo": "Qwen/Qwen2.5-1.5B-Instruct",
        "dest": "models/qwen2.5-1.5b-instruct",
        "files": [
            "config.json",
            "generation_config.json",
            "model.safetensors",
            "tokenizer.json",
            "tokenizer_config.json",
            "vocab.json",
            "merges.txt",
        ],
        "optional": [],
    },
}

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "*/*",
}


def _opener() -> urllib.request.OpenerDirector:
    ctx = ssl.create_default_context()
    try:
        import certifi

        ctx.load_verify_locations(certifi.where())
    except Exception:  # noqa: BLE001
        pass
    # 中间层证书链不完整时兜底（仅用于公开模型文件下载）
    if os.environ.get("LAWGATE_INSECURE_SSL") == "1":
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))


def head_ok(opener, url: str, timeout: float = 15.0) -> bool:
    try:
        req = urllib.request.Request(url, headers=UA, method="HEAD")
        with opener.open(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def pick_endpoint(opener, repo: str) -> str | None:
    for ep in ENDPOINTS:
        url = f"{ep}/{repo}/resolve/main/config.json"
        if head_ok(opener, url):
            return ep
    return None


def download(opener, url: str, dest: Path, timeout: float = 300.0) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    t0 = time.time()
    req = urllib.request.Request(url, headers=UA)
    try:
        with opener.open(req, timeout=timeout) as r, open(tmp, "wb") as f:
            total = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                total += len(chunk)
        tmp.replace(dest)
        return {"ok": True, "bytes": total, "seconds": round(time.time() - t0, 1),
                "speed_MBps": round(total / 1e6 / max(time.time() - t0, 1e-6), 2)}
    except Exception as exc:  # noqa: BLE001
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def fetch_manifest(name: str, opener, force: bool = False) -> dict:
    spec = MANIFESTS[name]
    dest_root = Path(spec["dest"])
    ep = pick_endpoint(opener, spec["repo"])
    if not ep:
        return {"name": name, "ok": False, "error": "所有端点均不可达"}
    out = {"name": name, "repo": spec["repo"], "endpoint": ep, "files": {}}
    for fn in spec["files"]:
        dest = dest_root / fn
        if dest.exists() and dest.stat().st_size > 0 and not force:
            out["files"][fn] = {"ok": True, "skipped": True,
                                "bytes": dest.stat().st_size}
            continue
        url = f"{ep}/{spec['repo']}/resolve/main/{fn}"
        r = download(opener, url, dest)
        out["files"][fn] = r
        print(f"  {fn:36s} {'OK' if r['ok'] else 'FAIL'} {r.get('bytes','')} {r.get('speed_MBps','')}MB/s",
              flush=True)
    for fn in spec.get("optional", []):
        dest = dest_root / fn
        if dest.exists():
            continue
        download(opener, f"{ep}/{spec['repo']}/resolve/main/{fn}", dest)
    out["ok"] = all(v.get("ok") for v in out["files"].values())
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", action="append", default=[])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    opener = _opener()

    if args.probe:
        res = {}
        for ep in ENDPOINTS:
            res[ep] = head_ok(opener, f"{ep}/BAAI/bge-small-zh-v1.5/resolve/main/config.json")
        print(json.dumps(res, indent=2))
        return 0

    names = args.manifest or (list(MANIFESTS) if args.all else [])
    if not names:
        ap.print_help()
        return 2

    results = []
    for name in names:
        print(f"[fetch] {name}", flush=True)
        r = fetch_manifest(name, opener, force=args.force)
        print(f"  => ok={r.get('ok')} endpoint={r.get('endpoint')}", flush=True)
        results.append(r)

    Path("docs").mkdir(exist_ok=True)
    (Path("docs") / "model_download_report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if all(r.get("ok") for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
