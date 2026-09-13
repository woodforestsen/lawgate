# -*- coding: utf-8 -*-
"""评测集构建 CLI（manual S4）。

用法::

    python scripts/build_benchmark.py --seed 42 --out-dir data/benchmark
    python scripts/build_benchmark.py --verify

``--verify`` 校验已有评测集：模板键完全一致、条数与 counts.json 一致、
V2/V4 案号确实不在 case_registry（V1/V3 确实在）、多轮分组不跨 dev/test、
qid 无重复、分片文件条数与 all.jsonl 一致。

**stdout 只输出 ASCII**；中文细节写入 ``data/benchmark/verify_report.md``。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许直接 `python scripts/build_benchmark.py` 运行（把仓库根加入 sys.path）
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from lawgate.config import get_settings                      # noqa: E402
from lawgate.eval import benchmark as B                       # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """解析参数并执行构建或校验。"""
    ap = argparse.ArgumentParser(
        description="CA-LegalGate 评测集构建/校验")
    ap.add_argument("--seed", type=int, default=42, help="构建随机种子")
    ap.add_argument("--out-dir", default="data/benchmark", help="输出目录")
    ap.add_argument("--n-per-cause", type=int, default=None,
                    help="case 类每案由条数（默认 50）")
    ap.add_argument("--db", default=None, help="知识库路径（默认取 config）")
    ap.add_argument("--verify", action="store_true", help="只校验，不构建")
    args = ap.parse_args(argv)

    settings = get_settings()
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = settings.root / out_dir

    if args.verify:
        rep = B.verify(out_dir, args.db)
        (out_dir / "verify_report.md").parent.mkdir(parents=True, exist_ok=True)
        (out_dir / "verify_report.md").write_text(
            B.render_verify_report(rep, out_dir), encoding="utf-8")
        _print_ascii_summary(rep, out_dir, mode="VERIFY")
        return 0 if rep["ok"] else 1

    items, counts = B.build_all(seed=args.seed, n_per_cause=args.n_per_cause,
                                db_path=args.db)
    paths = B.write_outputs(items, counts, out_dir)
    rep = B.verify(out_dir, args.db)
    (out_dir / "verify_report.md").write_text(
        B.render_verify_report(rep, out_dir), encoding="utf-8")

    _print_ascii_summary(rep, out_dir, mode="BUILD")
    print("COUNT_SUMMARY")
    for cat in B.CATEGORY_ORDER:
        print("  %-14s %6d" % (cat, counts["counts_by_category"][cat]))
    print("  %-14s %6d" % ("SINGLE_TURN", counts["single_turn_total"]))
    print("  %-14s %6d" % ("GRAND_TOTAL", counts["grand_total_turns"]))
    print("FILES")
    for key, path in sorted(paths.items()):
        print("  %-16s %s" % (key, path.as_posix()))
    return 0 if rep["ok"] else 1


def _print_ascii_summary(rep: dict, out_dir: Path, mode: str) -> None:
    """ASCII 摘要（中文问题清单写入 md 报告）。"""
    st = rep.get("stats", {})
    print("MODE", mode)
    print("OUT_DIR", out_dir.as_posix())
    print("TOTAL_ITEMS", st.get("total", 0))
    print("BY_CATEGORY", _ascii(str(st.get("by_category", {}))))
    print("BY_SPLIT", _ascii(str(st.get("by_split", {}))))
    print("CASE_NO_TRUTH checked/ok", _ascii(str(st.get("case_verify_truth", {}))))
    print("RESULT", "PASS" if rep.get("ok") else "FAIL")
    print("PROBLEM_COUNT", len(rep.get("problems", [])))
    for p in rep.get("problems", [])[:10]:
        print("  PROBLEM", _ascii(p)[:180])
    print("REPORT", (out_dir / "verify_report.md").as_posix())


def _ascii(s: str) -> str:
    """把任意文本降级为 ASCII（中文变成 ``?``，避免 GBK 控制台乱码）。"""
    return s.encode("ascii", "replace").decode("ascii")


if __name__ == "__main__":
    sys.exit(main())
