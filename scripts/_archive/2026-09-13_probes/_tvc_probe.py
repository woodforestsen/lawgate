# -*- coding: utf-8 -*-
"""一次性探针：E1 legalgate 在 temporal_trap 条目上的路由/TVC 逐条透视。"""
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(p):
    return [json.loads(x) for x in open(p, encoding="utf-8") if x.strip()]


items = {it["qid"]: it for it in load(ROOT / "data/benchmark/test.jsonl")}
for m in ("legalgate", "alwaysrag", "neverrag"):
    recs = load(ROOT / f"results/e1/{m}_seed0_test.jsonl")
    tt = [r for r in recs if items.get(r["qid"], {}).get("category") == "temporal_trap"]
    ch = Counter(r.get("channel") for r in tt)
    tvc = Counter(r.get("tvc") for r in tt)
    tvc_by_ch = Counter((r.get("channel"), r.get("tvc")) for r in tt)
    print(f"{m}: n_temporal={len(tt)} channel={dict(ch)} tvc={dict(tvc)}")
    print(f"   (channel,tvc)={dict(tvc_by_ch)}")

# 按 trap_type 细分 legalgate
recs = load(ROOT / "results/e1/legalgate_seed0_test.jsonl")
by_trap = {}
for r in recs:
    it = items.get(r["qid"], {})
    if it.get("category") != "temporal_trap":
        continue
    t = (it.get("temporal") or {}).get("trap_type")
    d = by_trap.setdefault(t, Counter())
    d[(r.get("channel"), r.get("tvc"))] += 1
for t in sorted(by_trap):
    print(f"legalgate {t}: {dict(by_trap[t])}")

# 抽 3 条 TVC=0 的 legalgate 记录看答案
n = 0
for r in recs:
    it = items.get(r["qid"], {})
    if it.get("category") == "temporal_trap" and r.get("tvc") == 0 and n < 3:
        n += 1
        print("-" * 70)
        print("qid:", r["qid"], "trap:", (it.get("temporal") or {}).get("trap_type"),
              "channel:", r.get("channel"))
        print("query:", it.get("query", "")[:80])
        print("answer[:160]:", str(r.get("answer"))[:160].replace("\n", " "))
        tr = r.get("trace") or {}
        print("trace keys:", sorted(tr.keys())[:18])
        print("invalid_laws:", r.get("invalid_laws"), "warned:", tr.get("warned"),
              "refusal:", tr.get("refusal"))
