# -*- coding: utf-8 -*-
"""一次性探针：Stage 6 质检（D33 实验补完的机器验收）。

检查项：
  1. docs/pipeline_steps.json 全部步骤 ok；
  2. 新结果 jsonl 中无 "[ERROR]" 答案 / channel=error 记录；
  3. E1 各方法 n=944（split=test），τ 缩放三工作点存在；
  4. core_assertion.json / stats.json / summary.csv 存在并打印关键数字；
  5. E6 复跑与旧快照逐位一致（确定性通道回归）；
  6. A4 预期：k>=8 时逐条路由决策与 k=20 一致（信号聚合只用前 8 位）；
  7. trace 台账：e1 legalgate/targ 的 draft_source 应全为 local（或 channel_b）；
  8. 花费台账：gen_cache 里 deepseek generate 条目数 / token 数，0.5B 草稿条目数；
  9. 五张关键图存在且非空。
"""
from __future__ import annotations

import glob
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

fail = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global fail
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"　{detail}" if detail else ""))
    if not ok:
        fail += 1


def load_jsonl(p):
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


print("== 1. 流水线步骤台账 ==")
steps = json.loads((ROOT / "docs/pipeline_steps.json").read_text(encoding="utf-8"))
bad = [s for s in steps if not s["ok"]]
check("pipeline_steps 全部 ok", not bad,
      f"{len(steps)} 步" + (f"，失败：{[s['step'] for s in bad]}" if bad else ""))

print("== 2. 无 ERROR 记录 ==")
pats = ["results/e1/*.jsonl", "results/e1_tau*/*.jsonl", "results/e2_a*/*.jsonl",
        "results/e3/*.jsonl", "results/e5/*.jsonl", "results/e6/*.jsonl",
        "results/dev_baseline/*.jsonl", "results/e1/shards/*.jsonl",
        "results/e2_a*/shards/*.jsonl", "results/e3/shards/*.jsonl",
        "results/e5/shards/*.jsonl"]
n_rec = n_err = 0
err_files = []
for pat in pats:
    for fp in glob.glob(str(ROOT / pat)):
        if "part" in Path(fp).name and "shards" not in fp:
            continue
        for r in load_jsonl(fp):
            n_rec += 1
            a = str(r.get("answer") or "")
            if a.startswith("[ERROR]") or str(r.get("channel")) == "error":
                n_err += 1
                err_files.append(fp)
check("扫描结果记录无 [ERROR]/channel=error", n_err == 0,
      f"共扫 {n_rec} 条，异常 {n_err} 条" + (f"：{sorted(set(err_files))[:5]}" if err_files else ""))

print("== 3. E1 完整度（split=test, n=944）==")
for m in ("neverrag", "alwaysrag", "targ", "complexity", "legal_llm", "legalgate"):
    p = ROOT / f"results/e1/summary_{m}_seed0_test.json"
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        check(f"{m} n=944", d.get("n") == 944, f"n={d.get('n')} acc={d.get('acc')} rr={d.get('rr')}")
    else:
        check(f"{m} summary 存在", False, str(p))
for scale in ("0.5", "0.75", "1.25"):
    d = f"results/e1_tau{scale}"
    files = glob.glob(str(ROOT / d / "summary_*.json"))
    check(f"τ×{scale} 工作点存在", bool(files), d)

print("== 4. E1 断言与统计 ==")
for fn in ("summary.csv", "core_assertion.json", "stats.json"):
    p = ROOT / "results/e1" / fn
    check(f"results/e1/{fn} 存在", p.exists())
ca = ROOT / "results/e1/core_assertion.json"
if ca.exists():
    print("  核心断言：" + json.dumps(json.loads(ca.read_text(encoding="utf-8")),
                                     ensure_ascii=False))
st = ROOT / "results/e1/stats.json"
if st.exists():
    s = json.loads(st.read_text(encoding="utf-8"))
    print("  vs alwaysrag 检验概览：")
    for k, v in (s.get("vs_alwaysrag") or {}).items():
        print(f"    {k}: {json.dumps(v, ensure_ascii=False)[:220]}")
    print(f"    holm: {json.dumps(s.get('holm_vs_alwaysrag'), ensure_ascii=False)[:300]}")

print("== 5. E6 回归（与 2026-09-11 快照逐位一致）==")
snap = ROOT / "results/_archive/2026-09-11_qwen1.5b_tok80/e6_snapshot_before_rerun"
for fn in ("prf.json", "confusion.json"):
    old = (snap / fn).read_text(encoding="utf-8")
    new = (ROOT / f"results/e6/{fn}").read_text(encoding="utf-8")
    o, n = json.loads(old), json.loads(new)
    for d in (o, n):
        d.get("provenance", {}).pop("written_at", None)
    check(f"{fn} 数值一致（忽略 written_at）", o == n)

print("== 6. A4：k>=8 路由决策应与 k=20 逐条一致 ==")
base = {r["qid"]: (r.get("channel"), bool(r.get("retrieval_used")))
        for r in load_jsonl(ROOT / "results/e2_a1/legalgate_full_seed0_test_e1.jsonl")} \
    if (ROOT / "results/e2_a1/legalgate_full_seed0_test_e1.jsonl").exists() else {}
for k in (8, 16, 32, 64):
    p = ROOT / f"results/e2_a4/legalgate_k{k}_seed0_test_e1.jsonl"
    if not p.exists():
        check(f"k{k} 结果存在", False, str(p))
        continue
    diff = sum(1 for r in load_jsonl(p)
               if base.get(r["qid"]) != (r.get("channel"), bool(r.get("retrieval_used"))))
    check(f"k{k} 与 k20 路由逐条一致", diff == 0, f"不一致 {diff} 条")

print("== 7. 草稿来源台账（e1 legalgate/targ）==")
for m in ("legalgate", "targ"):
    p = ROOT / f"results/e1/{m}_seed0_test.jsonl"
    if not p.exists():
        continue
    src: dict = {}
    for r in load_jsonl(p):
        s = (r.get("trace") or {}).get("draft_source")
        src[s] = src.get(s, 0) + 1
    bad_src = {k: v for k, v in src.items() if k not in ("local", "channel_b（未取草稿）", None)}
    check(f"{m} 草稿来源全为 local/channel_b", not bad_src, json.dumps(src, ensure_ascii=False))

print("== 8. 花费台账（gen_cache.db）==")
db = ROOT / "data/kb/gen_cache.db"
if db.exists():
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    for model_like, label in (("deepseek%", "DeepSeek API generate"),
                              ("%Qwen2.5-0.5B%", "本机 0.5B 草稿")):
        rows = con.execute(
            "SELECT kind, COUNT(*), COALESCE(SUM(n_tokens),0) FROM gen_cache "
            "WHERE model LIKE ? GROUP BY kind", (model_like,)).fetchall()
        print(f"  {label}: {rows}")
    con.close()

print("== 9. 关键图存在且非空 ==")
for fig in ("figures/e1/pareto_rr_acc.png", "figures/e3/ablation.png",
            "figures/e4/multiturn_trend.png", "figures/e5/tvc_by_trap.png",
            "figures/e6/confusion.png"):
    p = ROOT / fig
    check(fig, p.exists() and p.stat().st_size > 10_000,
          f"{p.stat().st_size}B" if p.exists() else "缺失")

print()
print(f"结果：{'全部通过' if fail == 0 else str(fail) + ' 项未通过'}")
raise SystemExit(0 if fail == 0 else 1)
