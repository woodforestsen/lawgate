# -*- coding: utf-8 -*-
"""从开源结构化数据集生成 `data/raw/<law_short>.txt`（替代人工录入种子语料）。

## 为什么不用 FLK 直接下载原文

国家法律法规数据库（flk.npc.gov.cn）的检索/详情 API 实测可用（2026-09-13）：

    POST /law-search/search/list          {"searchContent":"民法典","searchType":2,...}
    GET  /law-search/search/flfgDetails    ?bbbs=<bbbs>
    GET  /law-search/amazonFile/previewLink?filePath=<ossWordPath>
    GET  /law-search/amazonFile/ofdGenerateLink?filePath=<ossWordPath>&_wr_*

但**文件本体取不到**：`flfgDetails` 的 `ossFile.*` 指向内网 OSS
（`flkoss.obs-bj2-internal.cucloud.cn`，AWS4 签名且 `X-Amz-SignedHeaders=host`，
换公网 host 一律 `SignatureDoesNotMatch`），且 `permission.download = 0`。
阅读器 `flkofd.npc.gov.cn/reader` 只返回 HTML 壳，正文经内网 file 参数加载。
因此 FLK 只能用于**元数据与抽样校验**，不能作为原文来源。

## 本脚本的做法

改用开源结构化数据集 `13098806890/laws-data`（MIT；原始文本源自官方公开渠道，
含结构化 JSON + 条文级引用关系）。该仓库**含带 TAB 字符的非法文件名**
（`最高人民法院\t最高人民检察院...`），Windows 下 `git checkout` 必然失败，
故本脚本用 `git cat-file blob HEAD:<path>` 直接从对象库读取，不依赖工作区。

## 用法

    git clone --depth 1 https://github.com/13098806890/laws-data.git <repo>
    python scripts/fetch_laws_dataset.py --repo <repo> --out data/raw
    # 随后交给既有入口导入：
    python scripts/import_law_text.py --all --verified-by <核对人> --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

TODAY = date.today().isoformat()

# law_short（须与 lawgate/knowledge/seed_corpus.py 的 LAW_SPECS 一致）
#   -> (laws-data 仓库内相对路径, 该法在数据集中的真实条数)
SOURCES: dict[str, tuple[str, int]] = {
    "民法典": ("json/法律/中华人民共和国民法典_20200528.json", 1260),
    "公司法": ("json/法律/中华人民共和国公司法_20231229.json", 266),
    "公司法(2018修正)": ("json/法律/中华人民共和国公司法_20181026.json", 218),
    "劳动合同法": ("json/法律/中华人民共和国劳动合同法_20121228.json", 98),
    "民事诉讼法": ("json/法律/中华人民共和国民事诉讼法_20230901.json", 306),
    "民法典时间效力规定": (
        "json/司法解释/最高人民法院关于适用《中华人民共和国民法典》时间效力的若干规定_20201229.json", 28),
}

# 仅接受"第X编/章/节"作为结构行；数据集里另有 "正文"/"一、一般规定" 之类的
# 分组名，若写入会被解析器当成正文并并入上一条，必须过滤。
STRUCT_RE = re.compile(r"^第[零〇一二三四五六七八九十百千0-9]+\s*[编章节]")


def git_blob(repo: Path, path: str) -> bytes:
    proc = subprocess.run(["git", "cat-file", "blob", f"HEAD:{path}"],
                          cwd=str(repo), capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip()[:300])
    return proc.stdout


def walk(doc: dict):
    """产出 ('struct', 标题) 或 ('article', 条文 dict)。兼容 parts / chapters 两种结构。"""
    parts = doc.get("parts") or []
    if parts:
        for p in parts:
            yield "struct", p.get("title") or ""
            for ch in p.get("chapters") or []:
                yield "struct", ch.get("title") or ""
                for sec in ch.get("sections") or []:
                    yield "struct", sec.get("title") or ""
                    for a in sec.get("articles") or []:
                        yield "article", a
                for a in ch.get("articles") or []:
                    yield "article", a
        return
    for ch in doc.get("chapters") or []:
        yield "struct", ch.get("title") or ""
        for sec in ch.get("sections") or []:
            yield "struct", sec.get("title") or ""
            for a in sec.get("articles") or []:
                yield "article", a
        for a in ch.get("articles") or []:
            yield "article", a


def flatten(doc: dict) -> tuple[list[str], int]:
    """展平成逐行文本（编/章/节/条/款/项各占一行），返回 (lines, 条文数)。"""
    lines: list[str] = []
    n_articles = 0
    for kind, item in walk(doc):
        if kind == "struct":
            title = (item or "").strip()
            if title and STRUCT_RE.match(title):
                lines.append(title)
            continue
        if not isinstance(item, dict):
            continue
        content = (item.get("content") or "").strip()
        if not content:
            continue
        n_articles += 1
        lines.extend(seg.strip() for seg in content.split("\n") if seg.strip())
    return lines, n_articles


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="laws-data 仓库路径（含 .git 即可）")
    ap.add_argument("--out", default="data/raw", help="输出目录（默认 data/raw）")
    ap.add_argument("--only", default=None, help="只处理某个 law_short")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    out_dir = Path(args.out)
    if not (repo / ".git").exists():
        print(f"[ERR] {repo} 不是 git 仓库（找不到 .git）", file=sys.stderr)
        return 2
    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    targets = {k: v for k, v in SOURCES.items()
               if args.only is None or k == args.only}
    if not targets:
        print(f"[ERR] 没有匹配的法名：{args.only}", file=sys.stderr)
        return 2

    report = []
    for law_short, (rel, expect_n) in targets.items():
        try:
            blob = git_blob(repo, rel)
            doc = json.loads(blob.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {law_short}: {exc}")
            report.append({"law_short": law_short, "ok": False, "error": str(exc)[:200]})
            continue

        lines, n_articles = flatten(doc)
        ok = n_articles == expect_n
        dest = out_dir / f"{law_short}.txt"
        if not args.dry_run:
            dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report.append({
            "law_short": law_short, "ok": ok, "source": rel,
            "dataset_title": doc.get("title"), "pub_date": doc.get("pub_date"),
            "effective_date": doc.get("effective_date"),
            "dataset_total_articles": doc.get("total_articles"),
            "extracted_articles": n_articles, "expected_articles": expect_n,
            "lines": len(lines), "bytes": len("\n".join(lines).encode("utf-8")),
            "file": str(dest), "dry_run": args.dry_run,
        })
        flag = "OK  " if ok else "WARN"
        print(f"[{flag}] {law_short:20s} 条文 {n_articles}/{expect_n}  行 {len(lines):5d}  "
              f"-> {dest.name}")

    summary = Path("data/kb/fetch_dataset_report.json")
    if not args.dry_run:
        summary.parent.mkdir(parents=True, exist_ok=True)
        summary.write_text(json.dumps(
            {"generated": TODAY, "repo": str(repo), "data": report},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告：{summary}")

    bad = [r for r in report if not r.get("ok")]
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
