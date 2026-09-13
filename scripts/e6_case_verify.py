# -*- coding: utf-8 -*-
"""E6 案号核验实验（手册 S6.9）——纯通道 B，无需模型，CPU 秒级完成。

1. 对 100 条 case_verify 题直接调用 ``CaseNoVerifier``；
2. 输出混淆矩阵（V1–V4 四类 × 五级判定）与二分类 P/R/F1；
3. 产出 figures/e6/confusion.png + results/e6/{records.jsonl,prf.json,confusion.json}。

**结论口径限制（必须随结果披露）**：本仓库 case_registry 为**合成**案号库
（data_source=SYNTHETIC），因此指标只反映"核验器在格式合法/不存在/案由不符/
格式非法四类输入上的判别能力"，**不能**表述为对中国裁判文书网的覆盖能力。

    python scripts/e6_case_verify.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lawgate.channel.b_case_verify import LEVELS, CaseNoVerifier  # noqa: E402
from lawgate.eval import io as eio  # noqa: E402
from lawgate.eval.metrics import case_verify_prf  # noqa: E402
from lawgate.gate.intent import extract_case_no  # noqa: E402

OUT = Path("results/e6")


def claimed_cause_of(item: dict) -> str | None:
    """取"提问方所主张的案由"。

    这三个位置在不同构造里都可能承载该信息，按优先级取：
      1. slots.claimed_cause（基准集构造器实际写入的位置）
      2. case_no.claimed_cause（早期字段命名）
      3. 从 query 正文里匹配已知案由（兜底，保证只给 query 也能测）
    """
    for path in (("slots", "claimed_cause"), ("case_no", "claimed_cause")):
        d = item.get(path[0]) or {}
        if isinstance(d, dict) and d.get(path[1]):
            return d[path[1]]
    from lawgate.knowledge.seed_cases import CAUSE_ACTIONS

    q = item.get("query", "") or ""
    for c in CAUSE_ACTIONS:
        if c in q or c.replace("纠纷", "") in q:
            return c
    return None


def run(limit: int | None = None) -> dict:
    items = eio.load_split("case_no_verify")
    if limit:
        items = items[:limit]
    vf = CaseNoVerifier()
    recs: list[dict] = []
    for it in items:
        raw = (it.get("case_no") or {}).get("raw") or it.get("query", "")
        claimed = claimed_cause_of(it)
        subtype = (it.get("slots") or {}).get("verify_type")
        parsed = extract_case_no(raw)
        v = vf.verify(parsed, claimed)
        recs.append({
            "qid": it.get("qid"), "split": it.get("split"),
            "verify_type": subtype, "case_no_raw": raw,
            "claimed_cause": claimed,
            "registry_cause": (it.get("case_no") or {}).get("true_cause"),
            "gold_pass": bool(it.get("gold_pass")),
            "got_level": v.level, "case_pass": bool(v.passed),
            "correct": bool(v.passed) == bool(it.get("gold_pass")),
            "detail": v.detail, "real_case": v.real_case,
            "parsed": v.parsed_detail,
        })

    # 混淆矩阵：真实子类 V1–V4 × 判定级别
    subtypes = ["V1", "V2", "V3", "V4"]
    matrix = [[0] * len(LEVELS) for _ in subtypes]
    for r in recs:
        si = subtypes.index(r["verify_type"]) if r["verify_type"] in subtypes else None
        li = LEVELS.index(r["got_level"]) if r["got_level"] in LEVELS else None
        if si is not None and li is not None:
            matrix[si][li] += 1

    prf = case_verify_prf(recs)
    by_sub = {}
    for s in subtypes:
        sub = [r for r in recs if r["verify_type"] == s]
        if sub:
            by_sub[s] = {"n": len(sub),
                         "correct": sum(1 for r in sub if r["correct"]),
                         "acc": round(sum(1 for r in sub if r["correct"]) / len(sub), 4),
                         "levels": dict(Counter(r["got_level"] for r in sub))}
    OUT.mkdir(parents=True, exist_ok=True)
    eio.write_jsonl(recs, OUT / "records.jsonl", overwrite=True)
    eio.write_json(prf, OUT / "prf.json")
    conf = {"labels": LEVELS, "subtypes": subtypes, "matrix": matrix,
            "by_subtype": by_sub,
            "n": len(recs),
            "data_caveat": ("案号库为合成数据（SYNTHETIC）；指标仅反映核验器判别能力，"
                            "不代表对真实裁判文书库的覆盖。"),
            "construct_caveat": (
                "**满分结果含构造成分，必须打折解读**：基准集的 V1–V4 四个子类"
                "正是按核验器的四级判定（核验通过 / 不存在 / 案由不符 / 格式非法）"
                "生成，两者一一对应，故 P=R=1.0 在构造上是可预期的。本实验能支持的"
                "结论是：**四级判定的实现与设计一致、对四类输入的分流无混淆**"
                "（例如不会把格式非法误判为『不存在』，也不会把案由不符漏放）；"
                "不能据此推断对真实裁判文书网的核验准确率。"
                "真实场景的困难在于『库里没有但确实存在』的假阴性，"
                "本合成库无法测量该误差——这一点须在论文限制章节明确写出。")}
    eio.write_json(conf, OUT / "confusion.json")

    # 出图
    try:
        from lawgate.eval.plotting import plot_confusion

        prov = eio.run_meta()
        plot_confusion(matrix, LEVELS, subtypes,
                       out_path="figures/e6/confusion.png", provenance=prov,
                       prf=prf)
    except Exception as exc:  # noqa: BLE001
        print(f"[e6] 出图失败（不影响指标）：{type(exc).__name__}: {exc}")
    return conf


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    conf = run(args.limit)
    print(json.dumps({"n": conf["n"], "matrix": conf["matrix"],
                      "by_subtype": conf["by_subtype"]}, ensure_ascii=False))
    prf = eio.read_json(OUT / "prf.json")
    print(json.dumps({k: prf[k] for k in ("precision", "recall", "f1",
                                          "tp", "fp", "fn", "tn")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
