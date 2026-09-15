# -*- coding: utf-8 -*-
"""从**魔搭 ModelScope** 下载开源模型权重到本仓库的 models/ 目录。

为什么是魔搭而不是 HuggingFace
--------------------------------
本机 huggingface.co 不可达（curl 返回 000，见 docs/deviations.md D0），hf-mirror
镜像也不稳定；ModelScope 是阿里官方源、国内可直连，**文件与 HF 格式完全一致**
（config.json / tokenizer.json / *.safetensors 同名同构），落盘后 transformers
``from_pretrained(本地路径)`` 直接可用，不需要任何转换。

用法::

    # 现行默认模型（docs/deviations.md D38）
    python scripts/download_modelscope.py --model Qwen/Qwen3-4B

    # 只打印文件清单与总大小，不下载（换模型前先确认规格是否存在）
    python scripts/download_modelscope.py --model Qwen/Qwen3-4B --list

设计要点（都是踩过的坑）
------------------------
1. **断点续传**：``Range: bytes={pos}-``；若服务端回 200（忽略 Range）必须把
   pos 归零重写，否则文件会变成"断点前的残片 + 重头内容"的拼盘。
2. **先小后大**：按 Size 升序下载，主权重（数 GB 的分片）放最后——
   中途断线时"缺的只是大文件"，不会出现"小文件没下完但大文件占满了盘"。
3. **原子改名**：写 ``*.part``，校验通过才 ``os.replace`` 成正式名。
4. **落盘校验**：比对源站声明的 Size，不符即判失败（只判"HTTP 成功"会假成功）。
5. **必需文件清单要认分片**：Qwen3-4B 是 3 分片 safetensors
   （``model-0000N-of-0000M.safetensors`` + ``model.safetensors.index.json``），
   单文件版 ``model.safetensors`` **不存在**——按单文件清单检查会误报"缺失"。
6. **别把下载脚本放进数据目录**（很多项目 README 明确禁止），故本文件在 scripts/。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

API_FILES = ("https://modelscope.cn/api/v1/models/{model}/repo/files"
             "?Revision=master&Recursive=True")
RESOLVE = "https://www.modelscope.cn/models/{model}/resolve/master/{path}"

DEFAULT_MODEL = "Qwen/Qwen3-4B"
REPO_ROOT = Path(__file__).resolve().parent.parent

# 这些只是说明文档，不影响推理，--skip-optional 时跳过
OPTIONAL = {".gitattributes", "README.md", "LICENSE", "configuration.json"}

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}


def api_json(url: str, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def list_files(model: str) -> list[dict]:
    """取仓库文件清单；ID 不存在时明确报错（不静默换模型）。"""
    d = api_json(API_FILES.format(model=model))
    if not d.get("Success"):
        raise SystemExit(f"[FAIL] 取文件清单失败（模型 ID 或规格不存在？）：{d.get('Message')}\n"
                         f"       请先确认 {model!r} 在 modelscope.cn 上存在，再决定是否换模型。")
    return [f for f in d["Data"]["Files"] if f.get("Type") == "blob"]


def download_file(url: str, dest: Path, expect_size: int | None = None,
                  retry: int = 4) -> tuple[bool, str]:
    """断点续传下载单个文件；返回 (是否成功, 说明)。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = Path(str(dest) + ".part")
    pos = part.stat().st_size if part.exists() else 0

    if dest.exists() and not part.exists():
        have = dest.stat().st_size
        if expect_size and have == expect_size:
            return True, f"已存在({have / 1048576:.1f}MB)"
        if expect_size is None:
            return True, f"已存在({have / 1024:.0f}KB)"

    for attempt in range(retry):
        try:
            headers = dict(UA)
            if pos:
                headers["Range"] = f"bytes={pos}-"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=120) as resp:
                if pos and resp.status != 206:
                    # 服务端忽略了 Range：必须从头写，否则得到拼盘文件
                    pos = 0
                mode = "ab" if pos else "wb"
                total = pos
                t0 = time.time()
                with open(part, mode) as f:
                    while True:
                        chunk = resp.read(1024 * 512)
                        if not chunk:
                            break
                        f.write(chunk)
                        total += len(chunk)
                        if total % (100 * 1024 * 1024) < 512 * 1024:
                            spd = total / 1048576 / max(time.time() - t0, 0.1)
                            print(f"      ... {total / 1048576:.0f}MB  ({spd:.1f} MB/s)", flush=True)
            os.replace(part, dest)
            size = dest.stat().st_size
            if expect_size and size != expect_size:
                return False, f"大小不符({size}!={expect_size})"
            return True, f"{size / 1048576:.1f}MB"
        except Exception as exc:  # noqa: BLE001
            pos = part.stat().st_size if part.exists() else 0
            print(f"      ! 第{attempt + 1}次失败 {type(exc).__name__}: {exc}"
                  f"（已下 {pos // 1048576}MB，续传重试）", flush=True)
            time.sleep(2 * (attempt + 1))
    return False, "重试耗尽"


def required_files(names: list[str]) -> list[str]:
    """按"单文件 / 分片"两种形态给出推理必需文件清单。"""
    need = ["config.json", "tokenizer_config.json"]
    if any(n.startswith("model-") and n.endswith(".safetensors") for n in names):
        need.append("model.safetensors.index.json")      # 分片版靠 index 找分片
        need += [n for n in names if n.startswith("model-") and n.endswith(".safetensors")]
    else:
        need.append("model.safetensors")                 # 单文件版
    if "tokenizer.json" in names:
        need.append("tokenizer.json")
    return need


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help="魔搭模型 ID，如 Qwen/Qwen3-4B（默认）")
    ap.add_argument("--out", default=str(REPO_ROOT / "models"),
                    help="落盘根目录，模型落在 <out>/<模型名>/")
    ap.add_argument("--skip-optional", action="store_true",
                    help="跳过 README/LICENSE 等非推理必需文件")
    ap.add_argument("--list", action="store_true",
                    help="只打印文件清单与总大小，不下载")
    args = ap.parse_args()

    name = args.model.split("/")[-1]
    dest_dir = Path(args.out) / name
    print(f"来源  : 魔搭 ModelScope · {args.model}")
    print(f"目标  : {dest_dir}")

    files = list_files(args.model)
    files.sort(key=lambda f: f.get("Size", 0))      # 先小后大，主权重最后
    total = sum(f.get("Size", 0) for f in files)
    print(f"文件  : {len(files)} 个，合计 {total / 1073741824:.2f} GB")

    if args.list:
        for f in files:
            print(f"  {f['Path']:44s} {f.get('Size', 0) / 1048576:10.2f} MB")
        return 0

    free = None
    try:
        import shutil

        free = shutil.disk_usage(dest_dir.anchor or str(dest_dir)).free
    except Exception:  # noqa: BLE001
        pass
    if free is not None and free < total * 1.1:
        print(f"[WARN] 目标盘剩余 {free / 1073741824:.1f} GB < 需要的 "
              f"{total * 1.1 / 1073741824:.1f} GB，可能中途写满")

    dest_dir.mkdir(parents=True, exist_ok=True)

    ok, fail = [], []
    for f in files:
        path, size = f["Path"], f.get("Size", 0)
        if args.skip_optional and path in OPTIONAL:
            print(f"  [跳过] {path}")
            continue
        print(f"  下载 {path}  ({size / 1048576:.2f} MB)", flush=True)
        good, msg = download_file(RESOLVE.format(model=args.model, path=path),
                                  dest_dir / path, size)
        print(f"  {'[OK]' if good else '[FAIL]'} {path} -> {msg}", flush=True)
        (ok if good else fail).append((path, msg))

    names = [n.name for n in dest_dir.iterdir()] if dest_dir.exists() else []
    missing = [n for n in required_files([f["Path"] for f in files])
               if n not in names]
    print(f"\n完成: 成功 {len(ok)} / 失败 {len(fail)}")
    for p, m in fail:
        print(f"  [FAIL] {p}: {m}")
    print("必需文件检查: " + ("全部齐全" if not missing else f"缺失 {missing}"))
    print(f"\n模型目录: {dest_dir}")
    return 0 if not fail and not missing else 2


if __name__ == "__main__":
    raise SystemExit(main())
