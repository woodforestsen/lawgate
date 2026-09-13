# -*- coding: utf-8 -*-
"""一次性探针：Stage 4 检查点（步骤台账 + dev 基线摘要）。"""
import json

steps = json.load(open("docs/pipeline_steps.json", encoding="utf-8"))
for s in steps:
    print(s["step"], "OK" if s["ok"] else "FAIL " + str(s["error"]), f"{s['seconds']}s")
for m in ("neverrag", "alwaysrag"):
    d = json.load(open(f"results/dev_baseline/summary_{m}_seed0_dev_calib.json",
                       encoding="utf-8"))
    keys = ("acc", "rr", "n", "lac", "arc", "tvc", "invalid_law_citation_rate",
            "p50_ms", "p95_ms", "mean_tokens")
    print(m, {k: d.get(k) for k in keys})
