# -*- coding: utf-8 -*-
"""抓取 7 部**已废止**民事法律的官方全文，生成 `data/raw/<law_short>.txt`。

## 为什么单独做这一支

* `scripts/fetch_laws_dataset.py` 走开源结构化数据集（laws-data），但该仓库
  **只收现行有效法**，7 部被《民法典》废止的法律（合同法/物权法/侵权责任法/
  婚姻法/继承法/收养法/担保法）不在其中。
* 国家法律法规数据库（FLK）虽收录这 7 部（bbbs 已确认），但详情 API 只返回
  目录树，正文文件指向内网 OSS，取不到（见 fetch_laws_dataset.py 的说明）。
* 因此改从**中国人大网**（www.npc.gov.cn）的官方法律全文页抓取。该站与前述
  7 部法一一对应的页面已定位（见 SOURCES），内容含完整条文。

## 输出格式

与 fetch_laws_dataset.py 保持一致：编/章/节/条/款/项各占一行，交给既有入口
`scripts/import_law_text.py` 解析入库。

## 用法

    python scripts/fetch_npc_repealed.py                 # 抓取并写 data/raw/
    python scripts/fetch_npc_repealed.py --dump 继承法    # 只打印前若干行，供人工核对
    python scripts/fetch_npc_repealed.py --only 合同法
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

TODAY = date.today().isoformat()

BASE = "http://www.npc.gov.cn/zgrdw/npc/lfzt/rlyw/2016-07/01/content_{}.htm"

# law_short（须与 seed_corpus.LAW_SPECS 一致） -> (npc content id, 标题, 期望条数)
SOURCES: dict[str, tuple[int, str, int]] = {
    "合同法": (1992739, "中华人民共和国合同法", 428),
    "物权法": (1992736, "中华人民共和国物权法", 247),
    "担保法": (1992740, "中华人民共和国担保法", 96),
    "婚姻法": (1992746, "中华人民共和国婚姻法（2001修正）", 51),
    "继承法": (1992749, "中华人民共和国继承法", 37),
    "收养法": (1992751, "中华人民共和国收养法（1998修正）", 34),
    "侵权责任法": (1992753, "中华人民共和国侵权责任法", 92),
}

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "http://www.npc.gov.cn/",
}

# 结构行：第一条 / 第一章 / 第一节 / 第一编
RE_ARTICLE = re.compile(r"^第[零〇一二三四五六七八九十百千0-9]+条")
RE_STRUCT = re.compile(r"^第[零〇一二三四五六七八九十百千0-9]+\s*[编章节]")
# 页面噪声
NOISE_PAT = re.compile(
    r"(浏览字号|打印本页|关闭窗口|责任编辑|来源[:：]|上一篇|下一篇|分享到"
    r"|扫一扫|【字体|中国政府网|中国人大网|全国人大|网站地图|版权所有"
    r"|^[-—–]?\s*\d{1,4}\s*[-—–]?$"
    r"|^[·•]\s*\d+\s*[·•]$)")


def fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def html_to_lines(raw: str) -> list[str]:
    """HTML → 行列表。块级标签转换行，剥标签、解实体。"""
    s = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", " ", raw)
    s = re.sub(r"(?i)<(br|hr)\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|tr|h[1-6]|li|td|table|blockquote|center)\s*>", "\n", s)
    s = re.sub(r"(?is)<[^>]+>", "", s)
    s = html_mod.unescape(s)
    out: list[str] = []
    for line in s.split("\n"):
        line = (line.replace("\u3000", " ").replace("\xa0", " ")
                    .replace("\u200b", "").replace("\r", "").replace("\t", " "))
        line = re.sub(r"\s{2,}", " ", line).strip()
        if line:
            out.append(line)
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def cut_body(lines: list[str]) -> list[str]:
    """切除页头/目录：以"第一个结构行最后一次出现的位置"为正文起点。

    官方页面的结构是【标题 → 通过日期 → 目 录（只列编/章/节）→ 正文】，
    目录与正文的首个编/章标题文字相同（仅空白可能不同），故取**最后一次**
    出现位置即可跳过整段目录。
    """
    idxs = [i for i, ln in enumerate(lines) if RE_STRUCT.match(ln)]
    if not idxs:
        return lines
    key = _norm(lines[idxs[0]])
    same = [i for i in idxs if _norm(lines[i]) == key]
    return lines[same[-1]:]


def clean_body(lines: list[str]) -> list[str]:
    out = []
    for ln in lines:
        if NOISE_PAT.search(ln):
            continue
        # 纯标点/单字符残片
        if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", ln):
            continue
        # 通过/公布日期行（在正文之前已被 cut_body 去掉，此处兜底）
        if re.match(r"^[（(]?\s*(19|20)\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日", ln):
            continue
        out.append(ln)
    return out


def extract(lines: list[str]) -> tuple[list[str], int]:
    body = clean_body(cut_body(lines))
    n_articles = sum(1 for ln in body if RE_ARTICLE.match(ln))
    return body, n_articles


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/raw")
    ap.add_argument("--only", default=None)
    ap.add_argument("--dump", default=None, help="只抓某部法并打印前 40 行")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    targets = ({k: v for k, v in SOURCES.items() if k == args.dump} if args.dump
               else {k: v for k, v in SOURCES.items()
                     if args.only is None or k == args.only})
    if not targets:
        print(f"[ERR] 无匹配法名：{args.dump or args.only}", file=sys.stderr)
        return 2

    out_dir = Path(args.out)
    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    report, bad = [], 0
    for law_short, (cid, title, expect) in targets.items():
        url = BASE.format(cid)
        try:
            raw = fetch(url)
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
            print(f"[FAIL] {law_short}: {url} -> {exc}")
            report.append({"law_short": law_short, "ok": False, "url": url,
                           "error": str(exc)[:200]})
            bad += 1
            continue

        body, n = extract(html_to_lines(raw))
        ok = n == expect
        if not ok:
            bad += 1

        if args.dump:
            print(f"--- {law_short}  {url}\n  抽到条文 {n}（期望 {expect}）")
            for ln in body[:40]:
                print("   ", ln)
            print("    ...")
            for ln in body[-5:]:
                print("   ", ln)
            continue

        dest = out_dir / f"{law_short}.txt"
        if not args.dry_run:
            dest.write_text("\n".join(body) + "\n", encoding="utf-8")
        report.append({
            "law_short": law_short, "ok": ok, "source": "中国人大网",
            "title": title, "url": url, "extracted_articles": n,
            "expected_articles": expect, "lines": len(body),
            "bytes": len("\n".join(body).encode("utf-8")),
            "file": str(dest), "retrieval_date": TODAY, "dry_run": args.dry_run,
        })
        print(f"[{'OK  ' if ok else 'WARN'}] {law_short:12s} 条文 {n}/{expect}  "
              f"行 {len(body):4d} -> {dest.name}")

    if not args.dump and not args.dry_run:
        out = Path("data/kb/fetch_npc_repealed_report.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"generated": TODAY, "data": report},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告：{out}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
