# -*- coding: utf-8 -*-
"""构造算力预算内的分层子集（docs/deviations.md D19）。

== 为什么要子集 ==
本机 CPU-only，实测单条 80-token 生成约 7–8 秒（128-token 约 19 秒），且
**线程级并发无效**：实测 4 worker × 3 线程比 1 worker × 14 线程慢约 2 倍
（torch 的 intra-op 并行已吃满内存带宽，再叠加 inter-op 并发只会互相抢核，
详见 docs/deviations.md D19 的实测数据）。全量 E1（944 条 × 6 方法 × 3 种 prompt）
需 12–13 机时，超出本次交付窗口。

故按"**保杀手实验、精确分层、公开抽样过程**"的原则构造子集：
  * ``dev_calib`` 120 条——分桶校准用（保留全部时效陷阱与案号核验，其余按类别分层）
  * ``test_e1``   300 条——E1 主对比用（**保留全部** test 时效陷阱 96 与案号核验 80，
    多轮按"组"整体抽 20 组=60 条，其余按类别分层各抽若干）
  * ``test_e4``   多轮 20 组=60 条（E3 趋势）
抽样全过程确定性（seed=42）、可复现、写入 data/benchmark/subsets_manifest.json，
并如实披露"E1 结论建立在 test 的 31.8% 子集上（n=300/944）"。

    python scripts/make_subsets.py
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lawgate.eval import io as eio  # noqa: E402

BENCH = Path("data/benchmark")
SEED = 42


def stratified(items: list[dict], want: dict[str, int], seed: int) -> list[dict]:
    """按 category 分层抽取；want = {category: n}。不足则全取。"""
    rng = random.Random(seed)
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        by_cat[it.get("category", "?")].append(it)
    out: list[dict] = []
    for cat, n in want.items():
        pool = list(by_cat.get(cat, []))
        pool.sort(key=lambda x: str(x.get("qid")))
        rng.shuffle(pool)
        out += pool[:n]
    return out


def take_groups(items: list[dict], n_groups: int, seed: int) -> list[dict]:
    """按 group_id 整体抽取多轮组（保证不拆散组）。"""
    rng = random.Random(seed)
    groups: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        groups[str(it.get("group_id"))].append(it)
    keys = sorted(groups)
    rng.shuffle(keys)
    out: list[dict] = []
    for k in keys[:n_groups]:
        out += sorted(groups[k], key=lambda x: int(x.get("turn_id", 0)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()
    seed = args.seed

    dev = eio.load_split("dev")
    test = eio.load_split("test")

    # ---------------- dev_calib：120 条 ----------------
    dev_tt = [it for it in dev if it.get("category") == "temporal_trap"]
    dev_cv = [it for it in dev if it.get("category") == "case_verify"]
    dev_other = [it for it in dev if it.get("category") not in
                 ("temporal_trap", "case_verify")]
    want_other = {"concept": 16, "provision": 20, "case": 16, "multi-turn": 24}
    dev_calib = dev_tt + dev_cv + stratified(dev_other, want_other, seed)
    # 多轮按组抽，避免半个组
    mt_ids = {it["qid"] for it in dev_calib if it.get("category") == "multi-turn"}
    if mt_ids:
        groups_needed = {it["group_id"] for it in dev_calib
                         if it.get("category") == "multi-turn"}
        dev_calib = [it for it in dev_calib if it.get("category") != "multi-turn"]
        dev_calib += [it for it in dev_other
                      if it.get("category") == "multi-turn"
                      and it.get("group_id") in groups_needed]

    # ---------------- test_e1：300 条 ----------------
    t_tt = [it for it in test if it.get("category") == "temporal_trap"]
    t_cv = [it for it in test if it.get("category") == "case_verify"]
    t_mt = take_groups([it for it in test if it.get("category") == "multi-turn"],
                       20, seed)
    t_other = [it for it in test if it.get("category") in
               ("concept", "provision", "case")]
    test_e1 = t_tt + t_cv + t_mt + stratified(
        t_other, {"concept": 24, "provision": 24, "case": 16}, seed)

    eio.write_jsonl(dev_calib, BENCH / "dev_calib.jsonl", overwrite=True)
    eio.write_jsonl(test_e1, BENCH / "test_e1.jsonl", overwrite=True)
    eio.write_jsonl(t_mt, BENCH / "test_e4.jsonl", overwrite=True)

    manifest = {
        "seed": seed,
        "dev_calib": {"n": len(dev_calib),
                      "by_category": dict(Counter(i["category"] for i in dev_calib)),
                      "by_bucket": dict(Counter(i.get("bucket") for i in dev_calib)),
                      "of_dev_total": len(dev)},
        "test_e1": {"n": len(test_e1),
                    "by_category": dict(Counter(i["category"] for i in test_e1)),
                    "by_bucket": dict(Counter(i.get("bucket") for i in test_e1)),
                    "of_test_total": len(test)},
        "test_e4": {"n": len(t_mt), "n_groups": 20},
        "coverage_note": (
            "test_e1 保留 test 的**全部**时效陷阱（96）与**全部**案号核验（80），"
            "多轮按 20 组（60 条）整体入样，概念/法条/案例各分层 24/24/16。"
            f"故 E1 结论基于 test 的 {round(100 * len(test_e1) / len(test), 1)}% 子集"
            f"（n={len(test_e1)}/{len(test)}），必须随结果披露。"),
        "why": ("CPU-only 环境实测：80-token 生成约 7–8 s/条；线程并发无效"
                "（4×3 线程比 1×14 慢约 2 倍）。全量 944 条 × 6 方法需 12–13 机时。"),
    }
    eio.write_json(manifest, BENCH / "subsets_manifest.json")
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "by_bucket"}
                      for k, v in manifest.items()
                      if isinstance(v, dict) and "by_category" in v},
                     ensure_ascii=False, indent=2))
    print(f"dev_calib={len(dev_calib)} test_e1={len(test_e1)} test_e4={len(t_mt)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
