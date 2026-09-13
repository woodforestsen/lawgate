# -*- coding: utf-8 -*-
"""一次性探针：汇总 Stage 7 文档要引用的最终数字。"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def rd(p):
    p = ROOT / p
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


print("== E1 stats（重判后）==")
s = rd("results/e1/stats.json")
for c in s["comparisons"]:
    if c["method_a"] == "legalgate":
        print("legalgate vs alwaysrag:", json.dumps(c, ensure_ascii=False))
print("holm:", json.dumps(s["holm"], ensure_ascii=False))

print("\n== TARG 双臂 ==")
for tag in ("", "_taucal"):
    d = rd(f"results/e1/summary_targ{tag}_seed0_test.json")
    if d:
        print(f"targ{tag or '(τ=0.10手册值)'}: acc={d['acc']} rr={d['rr']} "
              f"tvc={d.get('tvc')} invalid={d.get('invalid_law_citation_rate')} n={d['n']}")

print("\n== τ 敏感性（tau_scale 三工作点 + 基准）==")
for scale in ("0.5", "0.75", "1.25"):
    d = rd(f"results/e1_tau{scale}/summary_legalgate_seed0_test.json")
    if d:
        print(f"τ×{scale}: acc={d['acc']} rr={d['rr']} tvc={d.get('tvc')}")
d = rd("results/e1/summary_legalgate_seed0_test.json")
print(f"τ×1.0: acc={d['acc']} rr={d['rr']} tvc={d.get('tvc')}")
tsc = rd("results/e1/tau_scale_comparison.json")
if tsc:
    print("tau_scale_comparison.json:", json.dumps(tsc, ensure_ascii=False)[:400])

print("\n== channel_mix ==")
cm = rd("results/e1/channel_mix.json")
print(json.dumps(cm, ensure_ascii=False)[:600] if cm else "缺失")

print("\n== E1 summary.csv（重判后）==")
print((ROOT / "results/e1/summary.csv").read_text(encoding="utf-8"))

print("== E3 report 关键行 ==")
e3 = rd("results/e3/e3_report.json")
if e3:
    for m, d in e3["per_method"].items():
        print(m, "overall:", d.get("overall"), "slot:", d.get("slot_inherit"))

print("\n== E5 report meta ==")
e5 = rd("results/e5/e5_report.json")
if e5:
    print(json.dumps({k: v for k, v in e5.items() if k not in ("per_method",)},
                     ensure_ascii=False)[:500])

print("\n== calibrate single_tau（A2/TARG 用）==")
cr = rd("results/calibrate_report.json")
print(json.dumps(cr.get("single_tau"), ensure_ascii=False))
