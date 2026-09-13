# -*- coding: utf-8 -*-
"""草稿生成（手册 S3.5）：取前 k 个 token 的 logprob 分布作为门控输入。

手册原本把草稿逻辑写成一个独立 ``DraftGenerator``（自带 transformers 加载）。
本实现把它收敛为 ``llm_base`` 后端的薄封装——因为**部署时草稿与正式生成必须
共享同一模型与前缀 KV**（手册 S3.5 尾注也强调这一点），拆成两个模型实例会
把 prefill 成本翻倍。

``DraftGenerator`` 保留，以兼容手册的调用习惯与脚本。
"""
from __future__ import annotations

from lawgate.channel.llm_base import BaseLLM, DraftStats, get_llm
from lawgate.config import get_settings


class DraftGenerator:
    """薄封装：复用同一个 LLM 后端做草稿。"""

    def __init__(self, llm: BaseLLM | None = None, model_path: str | None = None,
                 k: int = 20, **kw):
        if llm is not None:
            self.llm = llm
        elif model_path:
            self.llm = _load_specific(model_path, **kw)
        else:
            self.llm = get_llm()
        self.k = int(k or get_settings().k_draft)

    def draft_logprobs(self, query: str, history: list | None = None,
                       k: int | None = None) -> DraftStats:
        return self.llm.draft_logprobs(query, history, k=int(k or self.k))

    # 兼容手册写法：返回 [(logprob, token), ...] 的 top-1 序列
    def draft_top1(self, query: str, history: list | None = None,
                   k: int | None = None) -> list[tuple[float, str]]:
        st = self.draft_logprobs(query, history, k)
        return [(p[0][0], p[0][1]) if p else (0.0, "") for p in st.logprobs]

    def describe(self) -> dict:
        return self.llm.describe()


def _load_specific(model_path: str, **kw) -> BaseLLM:
    from lawgate.channel.llm_base import HFLLM

    return HFLLM(model_path, **kw)
