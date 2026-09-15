# -*- coding: utf-8 -*-
"""从魔搭公开数据集 qazwsxplkj/cn-judgment-docs 下载民事判决书 CSV。

数据来源说明（必须随报告披露）：
  该数据集为「中国裁判文书网」**公开**裁判文书的第三方整理版（马克数据网），
  含 2021-01 ~ 2021-10 的民事判决书，字段：
  原始链接/案号/案件名称/法院/所属地区/案件类型/审理程序/裁判日期/公开日期/
  当事人/案由/法律依据/全文。
  本项目只取其中有案号、有全文的记录，用于替换合成案号库（G2）。

缓存与落盘一律放 D 盘（用户机器 C 盘紧张）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from modelscope.hub.file_download import dataset_file_download

REPO = "qazwsxplkj/cn-judgment-docs"
# 体积较小的两个月份（先小后大，够用即停）
FILES = [
    "preprocessed/preprocessed_2021_09.csv",
    "preprocessed/preprocessed_2021_08.csv",
    "preprocessed/preprocessed_2021_07.csv",
]
CACHE = Path(r"D:\桌面\lawgate\.workbuddy\_probe\ms_cache")
LOCAL = Path(r"D:\桌面\lawgate\.workbuddy\_probe\dl")

CACHE.mkdir(parents=True, exist_ok=True)
LOCAL.mkdir(parents=True, exist_ok=True)

for rel in FILES:
    dest = LOCAL / Path(rel).name
    if dest.exists() and dest.stat().st_size > 1_000_000:
        print(f"[skip] {dest.name} 已存在 {dest.stat().st_size/1048576:.1f}MB", flush=True)
        continue
    t0 = time.time()
    print(f"[get ] {rel} ...", flush=True)
    try:
        p = dataset_file_download(REPO, rel, cache_dir=str(CACHE), local_dir=str(LOCAL))
        print(f"[ok  ] {Path(p).name}  {time.time()-t0:.0f}s", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[fail] {rel}: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
        sys.exit(1)

print("[all done]", flush=True)
