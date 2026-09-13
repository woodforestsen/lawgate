# -*- coding: utf-8 -*-
"""一次性探针：E3 legalgate turn2 全错 + 槽位继承错误率 1.0 的根因。"""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]

items = {}
for l in open(ROOT / "data/benchmark/test_e4.jsonl", encoding="utf-8"):
    if l.strip():
        it = json.loads(l)
        items[it["qid"]] = it

recs = [json.loads(l) for l in
        open(ROOT / "results/e3/legalgate_seed0_test_e4.jsonl", encoding="utf-8") if l.strip()]
t2 = [r for r in recs if int(r.get("turn_id", 0)) == 2]
print(f"turn2 记录数：{len(t2)}，correct 数：{sum(1 for r in t2 if r.get('correct'))}")
from collections import Counter
print("turn2 channel 分布:", Counter(r.get("channel") for r in t2))
print("turn2 score_detail 样例:", Counter((r.get("score_detail") or "")[:40] for r in t2).most_common(5))

for r in t2[:3]:
    it = items.get(r["qid"], {})
    tr = r.get("trace") or {}
    print("=" * 70)
    print("qid:", r["qid"], "channel:", r.get("channel"), "correct:", r.get("correct"))
    print("query:", it.get("query", "")[:70])
    print("history(前2轮q):", [h.get("query", "")[:30] for h in (it.get("history") or [])])
    print("golden_answer[:80]:", str(it.get("golden_answer"))[:80])
    print("slots.inherited:", (it.get("slots") or {}).get("inherited"))
    print("trace.slots_inherited:", tr.get("slots_inherited"))
    print("trace.decision:", str(tr.get("decision"))[:100])
    print("answer[:200]:", str(r.get("answer"))[:200].replace("\n", " | "))
    print("key_recall:", r.get("key_recall"), "refusal:", r.get("refusal"))

# turn0/1 的 slots_inherited 情况
for t in (0, 1):
    sub = [r for r in recs if int(r.get("turn_id", 0)) == t]
    has = sum(1 for r in sub if (r.get("trace") or {}).get("slots_inherited") is not None)
    nonempty = sum(1 for r in sub if (r.get("trace") or {}).get("slots_inherited"))
    print(f"turn{t}: n={len(sub)} trace有slots_inherited字段={has} 非空={nonempty}")
