# -*- coding: utf-8 -*-
"""一次性探针：定位 TVC 误判——哪个句子触发了 invalid_law_citations。"""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lawgate.eval.metrics import RE_SENT_SPLIT, invalid_law_citations  # noqa: E402
try:
    from lawgate.knowledge.risk_terms import ABOLISH_MARKERS  # noqa: E402
except ImportError:
    from lawgate.eval.metrics import ABOLISH_MARKERS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
recs = [json.loads(x) for x in
        open(ROOT / "results/e1/legalgate_seed0_test.jsonl", encoding="utf-8") if x.strip()]
r = next(x for x in recs if x["qid"] == "tt_t1_00002")
ans = r["answer"]
print("ABOLISH_MARKERS =", ABOLISH_MARKERS)
print("RE_SENT_SPLIT =", RE_SENT_SPLIT.pattern)
print("=" * 70)
print("完整答案：")
print(ans)
print("=" * 70)
invalid = {"合同法", "物权法", "侵权责任法", "婚姻法", "继承法", "收养法", "担保法"}
for i, sent in enumerate(RE_SENT_SPLIT.split(ans)):
    if not sent.strip():
        continue
    hits = [l for l in invalid if l in sent]
    if not hits:
        continue
    exempt = [m for m in ABOLISH_MARKERS if m in sent]
    print(f"句{i}: 命中法名={hits} 同句豁免词={exempt or '无 → 记非法引用'}")
    print(f"    「{sent.strip()[:90]}」")
print("invalid_law_citations ->", invalid_law_citations(ans, invalid))
