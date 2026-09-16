# -*- coding: utf-8 -*-
"""LawBench（open-compass）三个子任务 → lawgate 评测 schema 转换。

## 任务映射（data/external/LawBench/data/zero_shot/*.json，各 500 条）

    1-2 法律知识问答（JEC-QA 单选）→ category=concept,   bucket=b1
    3-1 法条预测（刑法条文）        → category=provision, bucket=b2
    3-6 案例分析（JEC-QA 单选）     → category=case,      bucket=b3

官方判分函数见 ``data/external/LawBench/evaluation/main.py`` 的 funct_dict：
    1-2 → jec_kd.compute_jec_kd      （Accuracy，multi_choice_judge）
    3-6 → jec_ac.compute_jec_ac      （Accuracy，multi_choice_judge）
    3-1 → ljp_article.compute_ljp_article（F1）

## 抽样

seed=42 每任务分层抽样（1-2 抽 70 / 3-1 抽 70 / 3-6 抽 60，共 200 条），
写入 ``data/lawbench/test.jsonl``；``--limit-per-task N`` 取前 N 条供冒烟。

## 字段口径

* 判分必需：query / category / golden_answer / golden_provisions（仅 3-1）。
* need_retrieval / annotators / arbitrated 是自建基准的构建期字段，
  判分不使用；真实数据集无此类金标，置 null/[] 并在 slots 里留痕
  （need_retrieval_source=external_benchmark）。
* 1-2/3-6 的 golden_answer 是选项字母（A–D）；3-1 的 golden_answer 是原文
  「法条:刑法第N条」，golden_provisions=[{law_short:"刑法",article_no:N}]。

## 实测金标形态（2026-09-15 核验，勿再凭印象改）

* 1-2 金标恒为 ``正确答案：X。``（**全角冒号**），500/500；
* 3-6 金标恒为 ``正确答案:X。``（**半角冒号**），500/500；
  ——这正是官方 jec_kd / jec_ac 各自 assert 的两种写法，故解析必须**同时**兼容
  全角与半角冒号，否则 500 条全被判成"解析失败"。
* 3-1 金标 **并非全是单条**：实测 500 条中 **161 条是多条**，形如
  ``法条:刑法第234、275条``（"、" 分隔，只有最后一个带"条"字），
  其余 339 条为单条 ``法条:刑法第264条``。**必须按官方 ljp_article 的写法
  解析**（先去掉 ``法条:刑法第`` 前缀与全部 ``条`` 字，再按 ``、`` 切分），
  用 ``第(\\d+)条`` 正则去匹配多条形态会**漏掉前几条并整体解析失败**。
  条号范围实测 114–417，全部 ≤452（现行刑法共 452 条），无"之一"条文。

用法：
    python scripts/build_lawbench.py                     # 生成 200 条
    python scripts/build_lawbench.py --limit-per-task 5  # 冒烟用小样本
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lawgate.knowledge.flk_parser import cn2int  # noqa: E402

SRC = ROOT / "data" / "external" / "LawBench" / "data" / "zero_shot"
OUT_DIR = ROOT / "data" / "lawbench"

# lawgate/eval/benchmark.py ITEM_KEYS 的 schema（18 键，顺序敏感）
ITEM_KEYS = [
    "qid", "query", "history", "category", "bucket", "need_retrieval",
    "golden_answer", "golden_provisions", "golden_source", "slots", "temporal",
    "case_no", "turn_id", "group_id", "annotators", "arbitrated",
    "gold_pass", "split",
]

# task_id -> (文件, qid 前缀, category, bucket, 抽样条数)
TASKS = {
    "1-2": ("1-2.json", "lb12", "concept", "b1", 70),
    "3-1": ("3-1.json", "lb31", "provision", "b2", 70),
    "3-6": ("3-6.json", "lb36", "case", "b3", 60),
}

# 官方 jec_kd/jec_ac 都取 answer[5]（"正确答案" 4 字 + 冒号 1 字 = 索引 4，
# 故选项字母必在索引 5）；这里用正则同时兼容全角/半角冒号与后续干扰字符。
RE_ANS_LETTER = re.compile(r"^正确答案\s*[:：]\s*([A-D])")
RE_ARTICLE = re.compile(r"第([零〇一二三四五六七八九十百千0-9]+)条")


def gold_letter(ans: str) -> str | None:
    """单选题金标 → 选项字母。原则：只用官方形态（开头 正确答案[:：]X）。"""
    m = RE_ANS_LETTER.match(ans.strip())
    return m.group(1) if m else None


def gold_articles(ans: str) -> list[dict]:
    """3-1 金标 → 条号列表，严格复刻官方 ljp_article 的解析步骤。

    官方步骤（evaluation_functions/ljp_article.py）：
        answer.replace("法条:刑法第","").replace("条","").split("、") → int()
    形如 ``法条:刑法第266、385、383、386条`` → [266,385,383,386]。
    """
    s = ans.strip()
    if s.startswith("法条:刑法第"):
        body = s[len("法条:刑法第"):].replace("条", "")
        out = []
        for chunk in body.split("、"):
            chunk = chunk.strip()
            if not chunk:
                continue
            if chunk.isdigit():
                out.append(int(chunk))
            else:                        # 兜底：中文数字写法
                n = cn2int(chunk)
                if n:
                    out.append(n)
        if out:
            return [{"law_short": "刑法", "article_no": n} for n in out]
    # 非常规形态兜底（实测 500 条不触发，保留以防上游改版）
    return [{"law_short": "刑法", "article_no": cn2int(base)}
            for base in RE_ARTICLE.findall(s)]


def convert(task: str, src_items: list[dict], n: int, seed: int,
            limit_per_task: int | None) -> list[dict]:
    import random

    _, prefix, category, bucket, _ = TASKS[task]
    if limit_per_task:
        picked = list(range(min(limit_per_task, len(src_items))))
    else:
        rng = random.Random(seed)
        picked = sorted(rng.sample(range(len(src_items)),
                                   min(n, len(src_items))))
    out = []
    for i, idx in enumerate(picked, start=1):
        src = src_items[idx]
        query = src["instruction"].strip() + "\n" + src["question"].strip()
        gold_raw = src["answer"].strip()
        if task in ("1-2", "3-6"):
            letter = gold_letter(gold_raw)
            if letter is None:
                raise ValueError(f"{task}[{idx}] 金标无法解析出选项字母: "
                                 f"{gold_raw!r}")
            golden_answer, golden_provisions = letter, []
        else:
            provisions = gold_articles(gold_raw)
            if not provisions:
                raise ValueError(f"{task}[{idx}] 金标无法解析出法条: {gold_raw!r}")
            # 3-1 的 golden_answer 保留**官方原文**（"法条:刑法第264条"）：
            #   * 官方 F1 口径读的是 golden_provisions（已结构化），不读它；
            #   * 但 lawgate 自带的代理判分（metrics.score_item 的 provision 支）
            #     用 key_term_recall(answer, golden_answer) 判"是否有正文重叠"，
            #     若把它写成光秃秃的 "264" 则关键词语料为空、该支恒判 False，
            #     保留原文才能让这条辅助信号仍然可用。
            golden_answer = gold_raw
            golden_provisions = provisions
        out.append({
            "qid": f"{prefix}_{i:04d}",
            "query": query,
            "history": [],
            "category": category,
            "bucket": bucket,
            "need_retrieval": None,
            "golden_answer": golden_answer,
            "golden_provisions": golden_provisions,
            "golden_source": f"LawBench-{task}",
            "slots": {
                "lawbench_task": task,
                "src_idx": idx,
                "gold_answer_raw": gold_raw,
                "need_retrieval_source": "external_benchmark",
            },
            "temporal": {},
            "case_no": None,
            "turn_id": 0,
            "group_id": "",
            "annotators": [],
            "arbitrated": False,
            "gold_pass": None,
            "split": "test",
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--limit-per-task", type=int, default=None,
                    help="每任务取前 N 条（冒烟测试用），不给则按 TASKS 抽样")
    ap.add_argument("--out", default=None,
                    help="输出路径（默认 data/lawbench/test.jsonl）")
    args = ap.parse_args()

    rows: list[dict] = []
    for task, spec in TASKS.items():
        src_items = json.loads((SRC / spec[0]).read_text(encoding="utf-8"))
        converted = convert(task, src_items, spec[4], args.seed,
                            args.limit_per_task)
        for r in converted:
            assert list(r.keys()) == ITEM_KEYS, f"schema 键不一致: {list(r.keys())}"
        rows.extend(converted)
        n_gold = sum(len(r["golden_provisions"]) for r in converted)
        print(f"{task}: 转换 {len(converted)} 条 "
              f"(category={spec[2]}, bucket={spec[3]}, 金标条文合计 {n_gold})")

    out_path = Path(args.out) if args.out else (OUT_DIR / "test.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"✅ 共 {len(rows)} 条 → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
