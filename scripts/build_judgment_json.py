# -*- coding: utf-8 -*-
"""把 `cn-judgment-docs` 的民事判决书 CSV 转成 `import_judgments.py` 要的 JSON。

输入：`scripts/fetch_cn_judgment_docs.py` 下载的 `preprocessed_YYYY_MM.csv`
      （列：原始链接/案号/案件名称/法院/所属地区/案件类型/案件类型编码/审理程序/
        裁判日期/公开日期/当事人/案由/法律依据/全文）

输出：`data/judgments/<案由>.json`，每个文件是一个 JSON 数组，元素形如
      {"case_no": "（2021）京0117民初4324号",
       "court_name": "北京市平谷区人民法院",
       "cause_action": "民间借贷纠纷",
       "judgment_date": "2021-10-08",
       "full_text": "……",
       "source_url": "https://wenshu.court.gov.cn/..."}

清洗规则：
  1. 只保留 `scripts/import_judgments.py` 的 4 类目标案由；
  2. 案号必须能被 `judgment_parser.parse_case_no` 解析（格式非法整条丢弃，
     这样 E6 的"格式合法"子集才是干净的）；
  3. 全文缺失或过短（< 200 字）的丢弃——E6 的通道 C 语料需要可用正文；
  4. 按**规范化案号**去重（同一案号可能因多次公开重复出现）；
  5. 每类案由最多取 `--per-cause` 条（默认 500），保证四类均衡。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lawgate.knowledge.judgment_parser import parse_case_no  # noqa: E402

csv.field_size_limit(1 << 30)
TODAY = date.today().isoformat()

DEFAULT_CAUSES = ["民间借贷纠纷", "劳动争议", "离婚纠纷", "房屋租赁合同纠纷"]
MIN_TEXT_LEN = 200


def scan_csv(path: Path, causes: dict[str, list], seen: set[str],
             per_cause: int, stats: dict) -> None:
    with path.open(encoding="utf-8", newline="") as f:
        rd = csv.DictReader(f)
        for row in rd:
            stats["rows"] += 1
            cause = (row.get("案由") or "").strip()
            if cause not in causes:
                continue
            stats["cause_hit"][cause] = stats["cause_hit"].get(cause, 0) + 1
            if len(causes[cause]) >= per_cause:
                continue
            full = (row.get("全文") or "").strip()
            if len(full) < MIN_TEXT_LEN:
                stats["drop_short_text"] += 1
                continue
            raw_no = (row.get("案号") or "").strip()
            parsed = parse_case_no(raw_no)
            if not parsed:
                stats["drop_bad_case_no"] += 1
                continue
            key = parsed["normalized"]
            if key in seen:
                stats["drop_dup"] += 1
                continue
            seen.add(key)
            causes[cause].append({
                "case_no": key,
                "court_name": (row.get("法院") or "").strip(),
                "cause_action": cause,
                "judgment_date": (row.get("裁判日期") or "").strip(),
                "full_text": full,
                "source_url": (row.get("原始链接") or "").strip(),
            })
            stats["kept"] += 1
            stats["kept_by_cause"][cause] = stats["kept_by_cause"].get(cause, 0) + 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=".workbuddy/_probe/dl",
                    help="含 preprocessed_*.csv 的目录（会递归找）")
    ap.add_argument("--out", default="data/judgments")
    # 报告**不能**落在 --out 目录里：import_judgments.py 会 rglob("*.json") 把
    # 它当成一份没有案号的文书扫进去（表现为 skipped_no_case_no=1 / bad_examples=["None"]）。
    ap.add_argument("--report", default="data/kb/judgment_convert_report.json")
    ap.add_argument("--per-cause", type=int, default=500)
    ap.add_argument("--causes", default=",".join(DEFAULT_CAUSES))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = Path(args.src)
    files = sorted(src.rglob("preprocessed_*.csv"))
    # 跳过模型下载的 ._____temp 暂存目录里的半成品
    files = [f for f in files if "._____temp" not in str(f)]
    if not files:
        print(f"[ERR] {src} 下没有 preprocessed_*.csv", file=sys.stderr)
        return 2

    cause_list = [c.strip() for c in args.causes.split(",") if c.strip()]
    causes: dict[str, list] = {c: [] for c in cause_list}
    seen: set[str] = set()
    stats = {"rows": 0, "kept": 0, "drop_bad_case_no": 0, "drop_short_text": 0,
             "drop_dup": 0, "cause_hit": {}, "kept_by_cause": {}}

    print(f"扫描 {len(files)} 个 CSV：")
    for f in files:
        before = stats["kept"]
        scan_csv(f, causes, seen, args.per_cause, stats)
        print(f"   {f.name:<32} +{stats['kept'] - before:>5} 条 "
              f"（累计 {stats['kept']}）")

    out_dir = Path(args.out)
    print(f"\n命中行数（未去重/未限量）：{stats['cause_hit']}")
    print(f"入库条数（去重且限量后）：{stats['kept_by_cause']}")
    print(f"丢弃：案号非法 {stats['drop_bad_case_no']}、全文过短 "
          f"{stats['drop_short_text']}、重复 {stats['drop_dup']}")

    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        for cause, rows in causes.items():
            if not rows:
                print(f"   [warn] {cause} 无数据，未写文件")
                continue
            p = out_dir / f"{cause}.json"
            p.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            print(f"   写出 {p}  {len(rows)} 条  {p.stat().st_size/1048576:.1f}MB")

        report = {
            "date": TODAY, "src": [str(f) for f in files],
            "per_cause_cap": args.per_cause, "causes": cause_list,
            "total_kept": stats["kept"], "kept_by_cause": stats["kept_by_cause"],
            "cause_hit_rows": stats["cause_hit"],
            "dropped": {"bad_case_no": stats["drop_bad_case_no"],
                        "short_text": stats["drop_short_text"],
                        "duplicate": stats["drop_dup"]},
            "dataset": "ModelScope qazwsxplkj/cn-judgment-docs "
                       "(中国裁判文书网公开裁判文书第三方整理版)",
        }
        rp = Path(args.report)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                      encoding="utf-8")
        print(f"\n报告：{rp}")
    return 0 if stats["kept"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
