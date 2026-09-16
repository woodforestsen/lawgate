# -*- coding: utf-8 -*-
"""LawBench 官方口径离线判分 + 离线 τ 扫描（实验方案第 4 步）。

## 为什么是"离线重算"

E1 主对比的生成是 CPU 上最贵的一环（单条 10–15 s）。判分口径一旦要改
（例如从 lawgate 自带的代理判据换成 LawBench 官方判据），重跑生成是纯浪费。
``lawgate/eval/run_exp.py`` 已把每条的 ``answer`` 原文落进结果 jsonl，
本脚本只读这些文件重算指标，**不触碰模型**。

## 官方口径（逐字对齐 data/external/LawBench/evaluation/）

* ``1-2`` → ``evaluation_functions/jec_kd.compute_jec_kd``（Accuracy）
* ``3-6`` → ``evaluation_functions/jec_ac.compute_jec_ac``（Accuracy）
* ``3-1`` → ``evaluation_functions/ljp_article.compute_ljp_article``（F1）

三者共用 ``utils/function_utils.multi_choice_judge`` 的**严格**判据：

    预测里出现正确答案字母，且**其它选项字母一个都不出现** → 1 分，否则 0 分。
    四个字母一个都没出现 → 记一次 abstention（弃答）。

注意这条判据比"抽第一个字母"严得多：模型只要在答案里多写一句
"选项 A 不正确"，四个字母就出现两个 → 直接判错。这是 LawBench 官方的真实
口径（与官方榜单可比），**不能**为了好看换成宽松抽取——故本脚本同时输出

* ``acc_official``：官方严格口径（**对外引用/与榜单对比用这个**）；
* ``acc_lenient``：格式宽容抽取（优先 [正确答案]X<eoa>，退化到"正确答案"后
  的单字母、再到全文唯一独立字母）。它衡量"其实答对了只是格式没跟"的比例，
  用于诊断，**不作为结论指标**，差额即格式合规损失。

## 离线 τ 扫描为什么成立

``LegalGate`` 的通道 A（不检索）与 ``NeverRAG``、通道 C（检索 top-k）与
``AlwaysRAG`` 走的是**同一个** ``llm.generate(query, history, context=...)``
调用，即 prompt 逐字相同（同 context、同 max_tokens），因此 GenCache 里
同一题的两份生成（无上下文 / 有上下文）在跑完三方法后都已存在。
于是"把 τ 改大改小"等价于"在同一条 u 上换一个阈值，再在已有的两份答案里
按新决策取一份"，**无需重新生成**：

    通道 = "C" if u > τ else "A"
    answer(τ) = alwaysrag[qid].answer if 通道=="C" else neverrag[qid].answer
    rr(τ)     = P(通道=="C")

两个必须披露的边界（写进 docs/deviations.md D40）：

1. 通道 B（确定性查库早退）的条目 ``u`` 为 None，**不参与 τ 扫描**，
   在报告里单列为 ``channel_b``，避免把"没用门控"的题混进 acc–τ 曲线；
2. 该等价性依赖"LegalGate 与两个基线用同一 prompt"。若将来给 LegalGate
   加了独立的系统提示或改 context 组装方式，这条等价性即失效，
   τ 扫描必须改回真跑。

用法：
    python scripts/score_lawbench.py --results results/e1_lawbench
    python scripts/score_lawbench.py --results results/e1_lawbench --tau-scan
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lawgate.eval.io import load_split  # noqa: E402

# ------------------------------------------------------------------ 官方判据
OPTION_LIST = ["A", "B", "C", "D"]
RE_ARTICLE_SUB = re.compile(r"第(.*?)条")
RE_PARAGRAPH_SUB = re.compile(r"第(.*?)款")

CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNITS = {"十": 10, "百": 100, "千": 1000}


def cn2an_like(s: str) -> str:
    """官方用 cn2an.transform(s,"cn2an")；本机未装 cn2an，这里做等价替换。

    只需覆盖"第X条"里会出现的写法（≤ 四百五十二）：把连续的中文数字片段
    转成阿拉伯数字，其余字符原样保留。
    """
    out, buf = [], []

    def flush():
        if not buf:
            return
        txt = "".join(buf)
        buf.clear()
        total, num = 0, 0
        for ch in txt:
            if ch in CN_DIGITS:
                num = CN_DIGITS[ch]
            elif ch in CN_UNITS:
                total += (num or 1) * CN_UNITS[ch]
                num = 0
            else:
                out.append(ch)
        out.append(str(total + num))

    for ch in s:
        if ch in CN_DIGITS or ch in CN_UNITS:
            buf.append(ch)
        else:
            flush()
            out.append(ch)
    flush()
    return "".join(out)


def multi_choice_judge(prediction: str, option_list: list[str],
                       answer_token: str) -> dict:
    """逐字复刻 LawBench ``utils/function_utils.multi_choice_judge``。"""
    count_dict, abstention, accuracy = {}, 0, 0
    for option in option_list:
        option_count = prediction.count(option)
        count_dict[option] = 1 if option_count > 0 else 0

    if sum(count_dict.values()) == 0:
        abstention = 1
    elif count_dict[answer_token] == 1 and sum(count_dict.values()) == 1:
        accuracy = 1
    return {"score": accuracy, "abstention": abstention}


def mcq_official(prediction: str, gold_letter: str) -> dict:
    return multi_choice_judge(prediction or "", OPTION_LIST, gold_letter)


def ljp_article_official(prediction: str, gold_articles: list[int]) -> dict:
    """逐条复刻 LawBench ``ljp_article.compute_ljp_article`` 的单条 F1。"""
    gt_set = set(gold_articles)
    digested, abstention = [], 0

    for chunk in (prediction or "").split("、"):
        chunk = chunk.replace("万元", "元")
        chunk = RE_PARAGRAPH_SUB.sub("", chunk)          # 去掉"第X款"
        chunk = RE_ARTICLE_SUB.sub(r"\1", chunk)         # 只留"第X条"里的 X
        chunk = cn2an_like(chunk)
        nums = re.findall(r"\d+", chunk)
        if not nums:
            continue
        digested.append(int(nums[0]))                    # 多个数字只取第一个

    pred_set = set(digested)
    if not pred_set:
        abstention = 1
    tp = len(gt_set & pred_set)
    precision = tp / len(pred_set) if pred_set else 0.0
    recall = tp / len(gt_set) if gt_set else 0.0
    f1 = (2 * precision * recall / (precision + recall)
          if (precision + recall) else 0.0)
    return {"score": f1, "abstention": abstention,
            "precision": precision, "recall": recall,
            "pred": sorted(pred_set), "gold": sorted(gt_set)}


# ------------------------------------------------------------------ 宽松抽取
RE_MARK = re.compile(r"\[正确答案\]\s*[:：]?\s*([A-D])")
RE_ANS = re.compile(r"正确答案\s*[:：]?\s*([A-D])")
RE_STANDALONE = re.compile(r"(?<![A-Za-z])([A-D])(?![A-Za-z])")


def mcq_lenient(prediction: str) -> str | None:
    """格式宽容抽取： [正确答案]X<eoa> → 正确答案X → 全文唯一独立字母。"""
    t = prediction or ""
    m = RE_MARK.search(t)
    if m:
        return m.group(1)
    m = RE_ANS.search(t)
    if m:
        return m.group(1)
    cands = {c for c in RE_STANDALONE.findall(t)}
    return cands.pop() if len(cands) == 1 else None


def ljp_lenient(prediction: str) -> list[int]:
    """宽松抽取条号：优先 [法条]…<eoa> 区块，否则全文所有"第X条"。"""
    t = prediction or ""
    seg = t
    m = re.search(r"\[法条\](.*?)(?:<eoa>|$)", t, re.S)
    if m:
        seg = m.group(1)
    elif "<eoa>" in t:
        seg = t.split("<eoa>")[0]
    DIG = "零〇一二三四五六七八九十百千0-9"
    nums = []
    for x in re.findall(rf"第\s*([{DIG}]+)\s*条", seg):
        nums.append(int(x) if x.isdigit() else _cn2int(x))
    return sorted({n for n in nums if n})


def _cn2int(s: str) -> int:
    from lawgate.knowledge.flk_parser import cn2int
    return cn2int(s)


# ------------------------------------------------------------------ 判分主体
def task_of(item: dict) -> str:
    t = (item.get("slots") or {}).get("lawbench_task")
    if t:
        return t
    return {"concept": "1-2", "provision": "3-1", "case": "3-6"}.get(
        item.get("category", ""), "?")


def load_items(split: str) -> dict[str, dict]:
    return {r["qid"]: r for r in load_split(split)}


def score_one(item: dict, answer: str) -> dict:
    task = task_of(item)
    if task == "3-1":
        gold_arts = [p["article_no"] for p in (item.get("golden_provisions") or [])]
        off = ljp_article_official(answer, gold_arts)
        pred = ljp_lenient(answer)
        tp = len(set(gold_arts) & set(pred))
        prec = tp / len(pred) if pred else 0.0
        rec = tp / len(gold_arts) if gold_arts else 0.0
        f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
        return {"task": task, "official": off["score"],
                "lenient": round(f1, 4), "abstention": off["abstention"],
                "detail": {"gold": sorted(set(gold_arts)),
                           "pred_official": off["pred"], "pred_lenient": pred}}
    gold_letter = (item.get("golden_answer") or "").strip()[:1]
    off = mcq_official(answer, gold_letter)
    got = mcq_lenient(answer)
    return {"task": task, "official": off["score"],
            "lenient": int(got == gold_letter),
            "abstention": off["abstention"],
            "detail": {"gold": gold_letter, "pred_lenient": got}}


def agg(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    lat = sorted(float(r.get("latency_ms", 0.0)) for r in rows)
    return {
        "n": n,
        "acc_official": round(sum(r["official"] for r in rows) / n, 4),
        "acc_lenient": round(sum(r["lenient"] for r in rows) / n, 4),
        "abstention_rate": round(sum(r["abstention"] for r in rows) / n, 4),
        "rr": round(sum(1 for r in rows if r.get("n_retrieval_calls", 0) > 0) / n, 4),
        "lac": round(sum(int(r.get("n_retrieval_calls", 0)) for r in rows) / n, 4),
        "mean_ms": round(sum(lat) / n, 1) if lat else 0.0,
        "p50_ms": round(lat[len(lat) // 2], 1) if lat else 0.0,
        "mean_tokens": round(sum(int(r.get("n_tokens", 0)) for r in rows) / n, 1),
        "n_channel_b": sum(1 for r in rows if r.get("channel") == "B"),
        "n_channel_a": sum(1 for r in rows if r.get("channel") == "A"),
        "n_channel_c": sum(1 for r in rows if r.get("channel") == "C"),
        "n_error": sum(1 for r in rows if str(r.get("answer", "")).startswith("[ERROR]")),
    }


def find_result_files(res_dir: Path, methods: list[str], seed: int,
                      split: str, tag: str) -> dict[str, Path]:
    out = {}
    suf = f"_{tag}" if tag else ""
    for m in methods:
        p = res_dir / f"{m}{suf}_seed{seed}_{split}.jsonl"
        if not p.exists():
            # 分片未合并时回退到 shards/ 下
            parts = sorted((res_dir / "shards").glob(
                f"{m}{suf}_seed{seed}_{split}_part*.jsonl"))
            if parts:
                out[m] = parts
                continue
        out[m] = p
    return out


def read_jsonl(p) -> list[dict]:
    if isinstance(p, list):
        rows = []
        for q in p:
            rows += read_jsonl(q)
        return rows
    if not Path(p).exists():
        return []
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines()
            if l.strip()]


def tau_scan(gate_rows: list[dict], items: dict, alt: dict[str, dict],
             grid: list[float]) -> list[dict]:
    """在已生成的两种答案之间按 τ 重新选路，重算 acc / RR（不重新生成）。"""
    out = []
    for tau in grid:
        n = 0
        acc = 0.0
        rr = 0
        acc_missing = 0
        for r in gate_rows:
            u = r.get("u")
            if u is None:
                continue
            qid = r["qid"]
            item = items.get(qid)
            if item is None:
                continue
            go_c = float(u) > tau
            src = alt["alwaysrag"].get(qid) if go_c else alt["neverrag"].get(qid)
            if not src or not str(src.get("answer", "")).strip():
                acc_missing += 1
                continue
            n += 1
            rr += 1 if go_c else 0
            acc += score_one(item, src["answer"])["official"]
        out.append({"tau": round(tau, 6), "n": n,
                    "acc_official": round(acc / n, 4) if n else None,
                    "rr": round(rr / n, 4) if n else None,
                    "n_missing_answer": acc_missing})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results/e1_lawbench")
    ap.add_argument("--split", default="lawbench_test")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--tag", default="")
    ap.add_argument("--methods", default="legalgate,alwaysrag,neverrag")
    ap.add_argument("--tau-scan", action="store_true",
                    help="在 legalgate 已有 u 上做离线 τ 扫描（需 alwaysrag/"
                         "neverrag 结果齐备）")
    ap.add_argument("--out", default=None, help="报告前缀（默认 <results>/report）")
    args = ap.parse_args()

    res_dir = Path(args.results)
    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    items = load_items(args.split)
    files = find_result_files(res_dir, methods, args.seed, args.split, args.tag)

    per_method: dict[str, dict] = {}
    raw_by_method: dict[str, list[dict]] = {}
    for m in methods:
        p = files.get(m)
        rows = read_jsonl(p) if p else []
        raw_by_method[m] = rows
        if not rows:
            print(f"⚠ 方法 {m} 无结果（{p}）", file=sys.stderr)
            continue
        scored = []
        for r in rows:
            it = items.get(r.get("qid"))
            if it is None:
                continue
            s = score_one(it, r.get("answer", ""))
            scored.append({**r, **{k: s[k] for k in
                                   ("task", "official", "lenient", "abstention")},
                           "detail": s["detail"]})
        by_task = {t: agg([x for x in scored if x["task"] == t])
                   for t in sorted({x["task"] for x in scored})}
        per_method[m] = {"overall": agg(scored), "by_task": by_task,
                         "n_records": len(rows), "result_file": str(p)}
        raw_by_method[m] = scored

    # ------------------------------------------------------------ τ 扫描
    taus = None
    if args.tau_scan and "legalgate" in raw_by_method \
            and raw_by_method.get("alwaysrag") and raw_by_method.get("neverrag"):
        alt = {k: {r["qid"]: r for r in raw_by_method[k]}
               for k in ("alwaysrag", "neverrag")}
        gate_rows = [r for r in raw_by_method["legalgate"] if r.get("u") is not None]
        grid = [round(x, 6) for x in
                ([0.0] + [i / 200 for i in range(1, 100)] + [1.0])]
        taus = {"n_gate_rows": len(gate_rows),
                "n_channel_b_rows": len(raw_by_method["legalgate"]) - len(gate_rows),
                "note": "τ 为全局绝对阈值（对所有桶同值）；曲线基于已生成答案重排，"
                        "未重新生成。通道 B 条目（u=None）不参与。",
                "curve": tau_scan(gate_rows, items, alt, grid)}
        # 实际 τ_b 对应的点
        taus["actual_tau_b"] = sorted({r.get("tau_b") for r in gate_rows
                                       if r.get("tau_b") is not None})

    report = {
        "split": args.split, "seed": args.seed, "tag": args.tag,
        "n_items": len(items),
        "scoring": "LawBench 官方口径（jec_kd/jec_ac 的 multi_choice_judge；"
                   "ljp_article 的 F1），另附宽松抽取对照值",
        "methods": per_method,
        "tau_scan": taus,
    }
    out_prefix = Path(args.out) if args.out else (res_dir / "report")
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    (out_prefix.with_suffix(".json")).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------ md
    L = ["# E1 · LawBench 真实基准判分报告", "",
         f"划分 {args.split} · seed {args.seed} · 共 {len(items)} 条", "",
         "判分口径：LawBench 官方（1-2/3-6 用 `multi_choice_judge` 严格口径，",
         "3-1 用 `ljp_article` F1）。`acc_lenient` 仅供诊断，不作结论。", ""]
    for m in methods:
        d = per_method.get(m)
        if not d:
            L.append(f"## {m}\n\n（无结果）\n")
            continue
        o = d["overall"]
        L += [f"## {m}", "",
              f"- 总体 acc（官方）：**{o['acc_official']}**"
              f"（宽松 {o['acc_lenient']}）",
              f"- 检索率 RR：{o['rr']}　平均检索调用 LAC：{o['lac']}",
              f"- 弃答率：{o['abstention_rate']}　通道分布 B/A/C："
              f"{o['n_channel_b']}/{o['n_channel_a']}/{o['n_channel_c']}",
              f"- 平均延迟 {o['mean_ms']} ms（p50 {o['p50_ms']} ms）"
              f"　平均 token {o['mean_tokens']}", "",
              "| 任务 | n | acc_official | acc_lenient | RR | 弃答率 |",
              "|---|---|---|---|---|---|"]
        for t, a in d["by_task"].items():
            L.append(f"| {t} | {a['n']} | {a['acc_official']} | "
                     f"{a['acc_lenient']} | {a['rr']} | {a['abstention_rate']} |")
        L.append("")
    if taus:
        L += ["## 离线 τ 扫描", "", taus["note"], "",
              f"- 参与扫描的条目：{taus['n_gate_rows']}"
              f"（通道 B 早退 {taus['n_channel_b_rows']} 条不参与）",
              f"- 实际桶级 τ_b：{taus['actual_tau_b']}", "",
              "| τ | n | acc_official | RR |", "|---|---|---|---|"]
        for row in taus["curve"]:
            if row["n"]:
                L.append(f"| {row['tau']} | {row['n']} | {row['acc_official']} | "
                         f"{row['rr']} |")
    (out_prefix.with_suffix(".md")).write_text("\n".join(L) + "\n",
                                               encoding="utf-8")

    # ------------------------------------------------------------------ csv
    with open(out_prefix.with_suffix(".csv"), "w", encoding="utf-8",
              newline="") as f:
        w = csv.writer(f)
        w.writerow(["method", "task", "n", "acc_official", "acc_lenient",
                    "rr", "lac", "abstention_rate", "mean_ms", "p50_ms",
                    "mean_tokens", "n_channel_b", "n_channel_a", "n_channel_c"])
        for m in methods:
            d = per_method.get(m)
            if not d:
                continue
            for t, a in list(d["by_task"].items()) + [("ALL", d["overall"])]:
                w.writerow([m, t, a.get("n"), a.get("acc_official"),
                            a.get("acc_lenient"), a.get("rr"), a.get("lac"),
                            a.get("abstention_rate"), a.get("mean_ms"),
                            a.get("p50_ms"), a.get("mean_tokens"),
                            a.get("n_channel_b"), a.get("n_channel_a"),
                            a.get("n_channel_c")])

    # 逐条明细（便于复核 + 供 plotting 复用）
    with open(out_prefix.parent / "per_item.jsonl", "w", encoding="utf-8") as f:
        for m in methods:
            for r in raw_by_method.get(m, []):
                f.write(json.dumps({k: r.get(k) for k in
                                    ("qid", "task", "method", "golden_answer",
                                     "official", "lenient", "abstention",
                                     "n_retrieval_calls", "channel", "u",
                                     "tau_b", "latency_ms", "answer", "detail")},
                                   ensure_ascii=False) + "\n")

    print(json.dumps({m: per_method[m]["overall"] | {
        "by_task": {t: {"n": a["n"], "acc": a["acc_official"], "rr": a["rr"]}
                    for t, a in per_method[m]["by_task"].items()}}
        for m in per_method}, ensure_ascii=False, indent=2))
    print(f"\n✅ 报告 → {out_prefix}.md / .json / .csv，逐条 → "
          f"{out_prefix.parent / 'per_item.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
