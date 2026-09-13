# -*- coding: utf-8 -*-
"""确定性复杂度评分（手册风险表"意图分类 + 复杂度路由"的落点）。

== 为什么需要它 ==
E0 实测（figures/e0/e0_auc.json）显示：以神经不确定性信号做**全桶统一**门控，
汇总 AUC 仅 0.557（margin 0.555 / entropy 0.513 / variance 0.557 / neglogp 0.492），
未达手册 0.60 的下限 → 按手册风险表应"关闭信号门控，改意图分类 + 复杂度路由"。

但分桶结果揭示了更准确的图景（辛普森悖论）：

    b2 法条查询  margin 0.734 / entropy 0.740 / variance 0.820   ← 信号**有效**
    b1 概念咨询  margin 0.466 / entropy 0.194 / variance 0.190   ← 信号**反向**
    b4 多轮追问  margin 0.359 / entropy 0.343 / variance 0.278   ← 信号**反向**
    b3 案例检索  标签恒为 1，AUC 无定义

原因是 ``need_retrieval`` 标签由**类别规则**决定（concept 40% 随机、case 恒为 1、
provision 按问法分），在 b1/b4 内标签与"模型是否有把握"无关，甚至与词面重叠度
反相关（越像法条原文 → 模型越自信 → u 越低，却被标为需检索）。
把它们与 b2 汇总，正负相消，得到"信号无效"的假象。

== 因此本模块提供的是"混合路由"的另一半 ==
对信号真正有效的桶（b2）继续用神经信号；对信号无效的桶（b1/b3/b4）改用本模块的
确定性复杂度评分。两者都用**桶级阈值 τ_b 在 dev 上校准**，故手册的核心贡献
（桶级阈值校准 + 三通道异构）完整保留，只是门控信号按桶取用。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 每一维特征的权重（人工设定、固定不调参；调参会把本模块变成第二个不可解释的黑箱）
W_LONG = 0.30          # 查询长度 > 30 字
W_CASE_WORD = 0.30     # 出现"案例/判决/判例/类似/怎么判"
W_TOPIC = 0.20         # 命中法律主题词（民间借贷/劳动争议/…）
W_MULTI = 0.10         # 多轮追问（turn_id > 0）
W_NEGATION = 0.10      # 出现比较/例外/冲突问法（"区别""能不能""是否""谁承担"）

RE_LONG = re.compile(r".")
RE_CASE_WORD = re.compile(r"案例|判决|判例|类似|怎么判|如何判|判多久|裁判规则")
RE_COMPARE = re.compile(r"区别|能不能|是否|谁承担|哪个|更有利|冲突|例外|之外|反而|"
                        r"相比|比较|高于|低于|上限|下限")

# 词面高度"像法条"的问法：模型通常很有把握，无需检索（用于扣分）
RE_PROVISION_BOILER = re.compile(r"第[零〇一二三四五六七八九十百千0-9]+条|的内容是什么|"
                                 r"原文|条文|如下")


@dataclass
class ComplexityBreakdown:
    score: float
    features: dict

    def to_dict(self) -> dict:
        return {"score": round(self.score, 4),
                "features": {k: round(float(v), 4) for k, v in self.features.items()}}


def complexity_score(query: str, slots=None, history: list | None = None,
                     category: str | None = None) -> ComplexityBreakdown:
    """返回 [0,1] 复杂度评分（越大越该检索）及特征明细，全确定性。"""
    q = query or ""
    hist = history or []
    topic = getattr(slots, "topic", None) or (
        (slots or {}).get("topic") if isinstance(slots, dict) else None)
    art = getattr(slots, "article_no", None) or (
        (slots or {}).get("article_no") if isinstance(slots, dict) else None)

    feats = {
        "long": W_LONG * (1.0 if len(q) > 30 else 0.0),
        "case_word": W_CASE_WORD * (1.0 if RE_CASE_WORD.search(q) else 0.0),
        "topic": W_TOPIC * (1.0 if topic else 0.0),
        "multi_turn": W_MULTI * (1.0 if hist else 0.0),
        "compare": W_NEGATION * (1.0 if RE_COMPARE.search(q) else 0.0),
        # 扣分项：明确在问条号原文 → 无需检索
        "boilerplate_penalty": -0.30 * (1.0 if RE_PROVISION_BOILER.search(q)
                                        else 0.0),
        # 类别先验（case 类必然需要引用具体文书）
        "category_prior": (0.30 if category == "case" else
                           0.20 if category == "temporal_trap" else 0.0),
    }
    if art:
        feats["boilerplate_penalty"] -= 0.20

    raw = sum(feats.values())
    score = min(max(raw, 0.0), 1.0)
    return ComplexityBreakdown(score=score, features=feats)
