# -*- coding: utf-8 -*-
"""解析质量自动审计（替代手册 S2.4 人工抽验的等价性检查）。

手册 S2.4 要求"随机抽 126 条人工核对，准确率 ≥95%"。人工核对无法由程序完成，
但在**官方原文尚未导入**的阶段，先做两项可以自动化的强检查：

  A. 条号连续性：`validate_continuity()`，民法典应为 1–1260 无缺口（导入官方原文后）；
  B. **往返一致性（round-trip）**：把解析结果按"条"重组回文本，与**源文本**做
     规范化比对（去空白 + 去（一）项标记）。若解析无丢失/无串行，则逐字相等。
     这是对"条/款/项切分正确"的充分性检查——比分位数抽样更强，因为它覆盖 100%。

残余风险（必须写进报告）：往返一致只能证明"没丢字、没串条"，不能证明"源文本
本身就是官方原文"。后者只能由 FLK 官方文件导入 + 人工抽验解决。
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass

from .flk_parser import (
    RE_ARTICLE,
    RE_CHAPTER,
    Provision,
    normalize_lines,
    render_article,
    validate_continuity,
)

RE_ITEM_MARK = re.compile(r"[（(][零〇一二三四五六七八九十0-9]+[)）]")
WS = re.compile(r"\s+")


def _strip_item_marks(s: str) -> str:
    return RE_ITEM_MARK.sub("", s)


def _norm(s: str) -> str:
    return WS.sub("", _strip_item_marks(s))


@dataclass
class LawAudit:
    law_short: str
    n_rows: int
    n_articles: int
    continuity: dict
    roundtrip_total: int
    roundtrip_mismatch: list[dict]
    truncated: bool

    @property
    def roundtrip_rate(self) -> float:
        if self.roundtrip_total == 0:
            return 1.0
        return 1.0 - len(self.roundtrip_mismatch) / self.roundtrip_total

    def to_dict(self) -> dict:
        return {
            "law_short": self.law_short,
            "n_rows": self.n_rows,
            "n_articles": self.n_articles,
            "continuity_max": self.continuity["max"],
            "continuity_missing": self.continuity["missing"],
            "continuity_extra": self.continuity["extra"],
            "roundtrip_total": self.roundtrip_total,
            "roundtrip_mismatch": len(self.roundtrip_mismatch),
            "roundtrip_rate": round(self.roundtrip_rate, 4),
            "roundtrip_examples": self.roundtrip_mismatch[:3],
            "source_truncated": self.truncated,
        }


def expected_articles_from_source(source_text: str) -> dict[tuple[int, str], str]:
    """从源文本重建 { (article_no, suffix): 期望正文 }（去掉项标记与章节标题）。"""
    lines = normalize_lines(source_text.splitlines())
    out: dict[tuple[int, str], str] = {}
    cur: tuple[int, str] | None = None
    buf: list[str] = []

    def flush():
        if cur is not None:
            out[cur] = _norm("".join(buf))

    for line in lines:
        m = RE_ARTICLE.match(line)
        if m:
            flush()
            from .flk_parser import cn2int

            cur = (cn2int(m.group(1)), m.group(3) or "")
            buf = [m.group(4) or ""]
            continue
        if RE_CHAPTER.match(line):
            continue
        if cur is not None:
            buf.append(line)
    flush()
    return out


def audit_law(source_text: str, provs: list[Provision],
              expected_max: int | None = None,
              is_truncated: bool = False) -> LawAudit:
    """对单部法律做连续性 + 往返一致性审计。"""
    expected = expected_articles_from_source(source_text)
    by_article: dict[tuple[int, str], list[Provision]] = {}
    for p in provs:
        by_article.setdefault((p.article_no, p.suffix), []).append(p)

    mismatches: list[dict] = []
    for key, exp in expected.items():
        got = _norm(render_article(by_article.get(key, [])))
        if got != exp:
            mismatches.append({
                "article": key[0],
                "suffix": key[1],
                "expected_head": exp[:60],
                "got_head": got[:60],
                "expected_len": len(exp),
                "got_len": len(got),
            })

    return LawAudit(
        law_short=provs[0].law_short if provs else "?",
        n_rows=len(provs),
        n_articles=len(by_article),
        continuity=validate_continuity(provs, expected_max=expected_max),
        roundtrip_total=len(expected),
        roundtrip_mismatch=mismatches,
        truncated=is_truncated,
    )


def sample_for_manual_audit(provs: list[Provision], n: int,
                            seed: int = 42) -> list[dict]:
    """按手册 S2.4 生成人工抽验清单（10% 抽样，供法学生签名核对）。"""
    rng = random.Random(seed)
    by_article: dict[int, list[Provision]] = {}
    for p in provs:
        by_article.setdefault(p.article_no, []).append(p)
    keys = sorted(by_article)
    picked = rng.sample(keys, min(n, len(keys)))
    rows = []
    for k in sorted(picked):
        rows.append({
            "article_no": k,
            "article_label": by_article[k][0].article_label,
            "n_rows": len(by_article[k]),
            "rendered": render_article(by_article[k]),
            "verdict": "",          # 人工填写 ok / mismatch
            "auditor": "",
            "date": "",
        })
    return rows
