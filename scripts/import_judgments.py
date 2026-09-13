# -*- coding: utf-8 -*-
"""导入真实裁判文书，替换合成案号库（**正式申报前的必做步骤**）。

背景（见 docs/deviations.md D0/D15）：本机无法访问中国裁判文书网，案号库是
合成数据，E6 因此无法测量"库里没有但确实存在"的假阴性。本脚本用于导入真实文书。

输入格式：一个目录，内含每篇一个 JSON，字段（缺什么补什么）：
    {"case_no": "（2022）沪01民终12345号",
     "court_name": "上海市第一中级人民法院",
     "cause_action": "民间借贷纠纷",
     "judgment_date": "2022-06-01",
     "full_text": "……",
     "source_url": "https://wenshu.court.gov.cn/..."}
若 `case_no` 缺失，用 `lawgate.knowledge.judgment_parser` 的正则从 `full_text` 抽取。

用法：
    python scripts/import_judgments.py --src data/judgments --replace-synthetic
    python scripts/import_judgments.py --src data/judgments --dry-run
    python scripts/verify_case_no.py --sample 200      # 抽样人工核验

行为：
  * 解析案号 → 结构化字段（年份/法院代字/类型/序号）入库 `case_registry`
  * 文书分块（300/50）入 `judgments`
  * `data_source` 标记为 `CJWS`
  * `--replace-synthetic` 会删除全部 `data_source='SYNTHETIC'` 的行
    （**E6 的结论口径随之从"合成库判别能力"升级为"真实文书库覆盖"，需重跑 E6**）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lawgate.config import get_settings  # noqa: E402
from lawgate.knowledge.build_sqlite import connect  # noqa: E402
from lawgate.knowledge.build_vector import chunk  # noqa: E402
# 案号解析的唯一出处（docs/deviations.md D31）：原先本脚本自带一份
# extract/parse，字段名与 knowledge/judgment_parser 不一致（易"改一处漏一处"），
# 现统一 import，本脚本不再定义自己的案号正则/映射。
from lawgate.knowledge.judgment_parser import (  # noqa: E402
    extract_case_no,
    parse_case_no,
)

TODAY = date.today().isoformat()
CAUSE_ACTIONS = ["民间借贷纠纷", "劳动争议", "离婚纠纷", "房屋租赁合同纠纷"]


def load_sources(src: Path) -> list[dict]:
    out = []
    for p in sorted(src.rglob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(d, list):
            out += [x for x in d if isinstance(x, dict)]
        else:
            out.append(d)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="data/judgments")
    ap.add_argument("--replace-synthetic", action="store_true",
                    help="删除全部 SYNTHETIC 案号与文书")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    s = get_settings()
    src = Path(args.src)
    if not src.exists():
        print(f"源目录不存在：{src}（请先放置真实文书 JSON）")
        return 2

    docs = load_sources(src)
    if args.limit:
        docs = docs[: args.limit]
    if not docs:
        print(f"{src} 下没有可读的 JSON 文书")
        return 2

    conn = connect(s.db_path)
    ok = skipped = 0
    causes: dict[str, int] = {}
    bad: list[str] = []

    if not args.dry_run and args.replace_synthetic:
        conn.execute("DELETE FROM judgments WHERE data_source='SYNTHETIC'")
        conn.execute("DELETE FROM case_registry WHERE data_source='SYNTHETIC'")
        conn.commit()
        print("已删除 SYNTHETIC 案号与文书")

    for d in docs:
        full = d.get("full_text") or ""
        raw_no = d.get("case_no") or extract_case_no(full)
        parsed = parse_case_no(raw_no or "")
        if not parsed:
            skipped += 1
            bad.append(str(raw_no)[:40])
            continue
        cause = d.get("cause_action") or ""
        causes[cause] = causes.get(cause, 0) + 1
        if args.dry_run:
            ok += 1
            continue
        conn.execute(
            """INSERT OR REPLACE INTO case_registry
               (case_no,year,court_code,court_name,case_type,seq_no,cause_action,
                judgment_date,exists_in_db,source_url,doc_hash,data_source)
               VALUES (?,?,?,?,?,?,?,?,1,?,?,?)""",
            (parsed["normalized"], parsed["year"], parsed["court_code"],
             d.get("court_name"), parsed["case_type"], parsed["seq_no"], cause,
             d.get("judgment_date"), d.get("source_url"),
             hashlib.md5(full.encode("utf-8")).hexdigest(), "CJWS"))
        chunks = chunk(full) if full else []
        conn.execute(
            """INSERT INTO judgments(case_no,court_name,cause_action,full_text,
                                     chunks,data_source) VALUES (?,?,?,?,?,?)""",
            (parsed["normalized"], d.get("court_name"), cause, full,
             json.dumps(chunks, ensure_ascii=False), "CJWS"))
        ok += 1
    if not args.dry_run:
        conn.commit()

    report = {
        "date": TODAY, "src": str(src), "n_files": len(docs),
        "imported": ok, "skipped_no_case_no": skipped, "dry_run": args.dry_run,
        "causes": causes,
        "missing_causes": [c for c in CAUSE_ACTIONS if c not in causes],
        "bad_examples": bad[:10],
        "next_steps": [
            "重跑 python -m lawgate.knowledge.build_vector（重建向量库）",
            "重跑 python scripts/e6_case_verify.py（E6 结论口径升级为真实文书库）",
            "重跑 python scripts/build_benchmark.py --seed 42（case 类金标换代）",
            "更新 docs/DATA_GAP.md 中该缺口的完成状态",
        ],
    }
    out = Path(s.kb_dir) / "judgment_import_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    conn.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
