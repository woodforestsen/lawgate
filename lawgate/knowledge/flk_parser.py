# -*- coding: utf-8 -*-
"""法条解析器（手册 S2.2）——逐行状态机：编/章/节 → 条 → 款(自然段) → 项。

对《手册》原代码的必要修正（见 docs/deviations.md D3–D6）：
  D3. flush() 会在"一条多款多项"时把项文本既作为 item 行、又作为 paragraph 行
      重复入库；本实现：每款产出 1 条 paragraph 行（item_no=''），每项产出 1 条
      item 行，二者互不重复。
  D4. 项文本用正则捕获组（去掉"（一）"前缀），item_no 单独保存为 '(一)'；
      手册把整行 line 存入 text，导致前缀重复。
  D5. 增加 normalize_lines()：剔除页眉页码、合并被换行切断的句子，否则官方
      文本（docx/PDF 转 txt）会把一条拆成多款。
  D6. 支持"第X条之一"（修订法律常见）；article_no 取基础条号，article_label
      保留完整标签，并在 suffix 字段留痕、在条号连续性检查中单独报告。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

CN_NUM = {
    "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
    "八": 8, "九": 9, "十": 10, "百": 100, "千": 1000,
    "〇": 0, "壹": 1, "贰": 2, "叁": 3, "肆": 4, "伍": 5, "陆": 6, "柒": 7,
    "捌": 8, "玖": 9, "拾": 10,
}


def cn2int(s: str) -> int:
    """中文数字 → int。支持 一千二百六十 / 二十 / 十 / 二百零五 / 阿拉伯数字。"""
    s = str(s).strip()
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    total, num = 0, 0
    for ch in s:
        v = CN_NUM.get(ch)
        if v is None:
            continue
        if v >= 10:
            total += (num or 1) * v
            num = 0
        else:
            num = v
    return total + num


def int2cn(n: int) -> str:
    """int → 中文数字（用于生成 article_label，避免依赖源文本写法）。"""
    if n <= 0:
        return str(n)
    digits = "零一二三四五六七八九"
    units = ["", "十", "百", "千"]
    s = str(n)
    out = []
    length = len(s)
    for i, ch in enumerate(s):
        d = int(ch)
        pos = length - i - 1
        if d == 0:
            if out and out[-1] != "零" and pos > 0:
                out.append("零")
            continue
        if pos == 0:
            out.append(digits[d])
        elif pos == 1:
            out.append(("" if d == 1 and not out else digits[d]) + "十")
        else:
            out.append(digits[d] + units[pos] if pos < len(units) else digits[d])
    label = "".join(out).rstrip("零")
    return label or "零"


RE_ARTICLE = re.compile(r"^第([零〇一二三四五六七八九十百千0-9]+)条(之([零〇一二三四五六七八九十]+))?\s*(.*)$")
RE_CHAPTER = re.compile(r"^第([零〇一二三四五六七八九十百千0-9]+)([编章节])\s*(.*)$")
RE_ITEM = re.compile(r"^[（(]([零〇一二三四五六七八九十0-9]+)[)）]\s*(.*)$")
RE_ITEM_MARK = re.compile(r"[（(]([零〇一二三四五六七八九十0-9]+)[)）]")

# 页眉/页码/无关行
RE_NOISE = re.compile(
    r"^(\s*[-—–]?\s*\d{1,4}\s*[-—–]?\s*$"
    r"|.*中华人民共和国.*法律出版社.*"
    r"|^\s*第\s*\d+\s*页\s*$"
    r"|^\s*[·•]\s*\d+\s*[·•]\s*$)"
)
# 标题行（法律名/通过日期），非条文内容
RE_TITLE = re.compile(r"^(中华人民共和国.{2,30}(法|法典|条例|规定|解释|办法))\s*$")
RE_DATE_LINE = re.compile(
    r"^[（(]?\s*(19|20)\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日.*[)）]?\s*(通过|公布|施行|修订|修正)?.*$")

# 中文句末标点：用于判断"上一行未结束，需与下一行合并"
SENT_END = "。；：！？”)）】"


@dataclass
class Provision:
    law_short: str
    law_name: str
    law_level: str
    book: str
    chapter: str
    section: str
    article_no: int
    article_label: str
    paragraph_no: int
    item_no: str
    text: str
    item_idx: int = 0
    validity_status: str = "现行有效"
    effective_date: str = ""
    version: str = ""
    source_url: str = ""
    retrieval_date: str = ""
    suffix: str = ""
    publish_date: str = ""


def normalize_lines(raw_lines: list[str], join_wrapped: bool = True) -> list[str]:
    """清洗官方文本行：去页眉页码、去全角空格、合并被硬换行截断的句子。

    **不做 NFKC 归一化**：NFKC 会把全角标点与数字转成半角（'，'→',', '（'→'(',
    '１２３'→'123'），从而**改变法条原文**。法条库的全部价值在于文本保真，
    因此这里只处理空白字符。项标记的识别由正则同时接受全/半角括号来解决。
    """
    cleaned: list[str] = []
    for line in raw_lines:
        # 仅归一化不可见空白；保留中文标点与全角字符原样
        line = line.replace("\u3000", " ").replace("\xa0", " ").replace("\u200b", "")
        line = line.replace("\r", "").replace("\t", " ")
        line = re.sub(r" {2,}", " ", line).strip()
        if not line or RE_NOISE.match(line):
            continue
        cleaned.append(line)

    if not join_wrapped:
        return cleaned

    joined: list[str] = []
    for line in cleaned:
        if not joined:
            joined.append(line)
            continue
        prev = joined[-1]
        # 结构行（编/章/节/条/项）永不与上一行合并
        is_struct = bool(RE_ARTICLE.match(line) or RE_CHAPTER.match(line) or RE_ITEM.match(line))
        prev_is_struct_head = bool(RE_ARTICLE.match(prev) or RE_CHAPTER.match(prev))
        prev_ends_sentence = bool(prev) and prev[-1] in SENT_END
        # 上一行是条首且很短（"第一条"）→ 不合并
        if is_struct or prev_ends_sentence or prev_is_struct_head or len(prev) < 6:
            joined.append(line)
        else:
            joined[-1] = prev + line
    return joined


def parse_law_text(lines: list[str], law_short: str, law_name: str,
                   law_level: str = "法律", version: str = "",
                   effective_date: str = "", source_url: str = "",
                   retrieval_date: str = "", publish_date: str = "",
                   validity_status: str = "现行有效",
                   join_wrapped: bool = True) -> list[Provision]:
    """逐行状态机。返回 Provision 列表（每条 1 个 paragraph 行 + 每项 1 个 item 行）。"""
    lines = normalize_lines(lines, join_wrapped=join_wrapped)
    out: list[Provision] = []
    book = chapter = section = ""

    cur_no: int | None = None
    cur_label = ""
    cur_suffix = ""
    paragraphs: list[str] = []          # 每款的正文（不含项）
    items: dict[int, list[tuple[str, str]]] = {}   # 款序号 -> [(item_no, text)]
    para_idx = 0

    def mk(para_no: int, item_no: str, text: str, item_idx: int = 0) -> Provision:
        return Provision(
            law_short=law_short, law_name=law_name, law_level=law_level,
            book=book, chapter=chapter, section=section,
            article_no=cur_no or 0, article_label=cur_label,
            paragraph_no=para_no, item_no=item_no, text=text,
            item_idx=item_idx,
            validity_status=validity_status, effective_date=effective_date,
            version=version, source_url=source_url, retrieval_date=retrieval_date,
            suffix=cur_suffix, publish_date=publish_date,
        )

    def flush() -> None:
        nonlocal paragraphs, items, para_idx, cur_no, cur_label, cur_suffix
        if cur_no is not None:
            for p_i in range(1, len(paragraphs) + 1):
                ptext = paragraphs[p_i - 1]
                pitems = items.get(p_i, [])
                if ptext:
                    out.append(mk(p_i, "", ptext))
                for idx, (item_no, itext) in enumerate(pitems, start=1):
                    out.append(mk(p_i, item_no, itext, item_idx=idx))
        paragraphs, items, para_idx = [], {}, 0
        cur_no, cur_label, cur_suffix = None, "", ""

    for line in lines:
        if m := RE_ARTICLE.match(line):
            flush()
            base = cn2int(m.group(1))
            cur_suffix = m.group(3) or ""
            cur_no = base
            cur_label = f"第{m.group(1)}条" + (f"之{m.group(3)}" if cur_suffix else "")
            rest = m.group(4).strip()
            if rest:
                paragraphs = [rest]
                para_idx = 1
            else:
                paragraphs, para_idx = [], 0
            continue

        if m := RE_CHAPTER.match(line):
            # 结构行永远优先：正文极少以"第X章/节/编"开头，若不做此判定，
            # 紧随条文之后的章标题会被误并入上一款的正文（D5 相关修正）。
            flush()
            label = f"第{m.group(1)}{m.group(2)} {m.group(3)}".strip()
            if m.group(2) == "编":
                book, chapter, section = label, "", ""
            elif m.group(2) == "章":
                chapter, section = label, ""
            else:
                section = label
            continue

        if cur_no is None:
            continue

        if m := RE_ITEM.match(line):
            target = para_idx if para_idx > 0 else 1
            if para_idx == 0:
                # 条首无引语、直接列项（如"第X条 具备下列条件的……"被拆开）
                paragraphs = [""]
                para_idx = 1
            items.setdefault(target, []).append(
                (f"({m.group(1)})", m.group(2).strip()))
            continue

        if para_idx == 0:
            paragraphs = [line]
            para_idx = 1
        else:
            paragraphs.append(line)
            para_idx += 1

    flush()
    return out


def parse_law_file(path: Path, law_short: str, law_name: str, **kw) -> list[Provision]:
    """读 .txt / .md 直接解析；.docx 用 python-docx 抽段落后解析。"""
    path = Path(path)
    if path.suffix.lower() == ".docx":
        import docx  # python-docx

        doc = docx.Document(str(path))
        lines = [p.text for p in doc.paragraphs]
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
    return parse_law_text(lines, law_short, law_name, **kw)


def validate_continuity(provs: list[Provision], expected_max: int | None = None) -> dict:
    """条号连续性检查。返回 {'max','missing','extra','n_unique','suffixes'}。"""
    base = {p.article_no for p in provs if p.article_no > 0}
    if not base:
        return {"max": 0, "missing": [], "extra": [], "n_unique": 0, "suffixes": []}
    hi = expected_max or max(base)
    missing = [n for n in range(1, hi + 1) if n not in base]
    extra = sorted(n for n in base if n > hi)
    suffixes = sorted({p.suffix for p in provs if p.suffix})
    return {"max": hi, "missing": missing, "extra": extra,
            "n_unique": len(base), "suffixes": suffixes}


def split_by_article(provs: list[Provision]) -> dict[int, list[Provision]]:
    d: dict[int, list[Provision]] = {}
    for p in provs:
        d.setdefault(p.article_no, []).append(p)
    return d


def render_article(rows: list[Provision]) -> str:
    """把一条的款项拼回可读文本（供人工抽验与往返一致性比对）。

    按 (paragraph_no, item_idx) 排序——**不能**按 item_no 字符串排序：
    "一/三/二/四" 的 Unicode 码位并非数字序（三 U+4E09 < 二 U+4E8C），
    字符串排序会把项序打乱（见 docs/deviations.md D7）。
    """
    rows = sorted(rows, key=lambda r: (r.paragraph_no, r.item_idx))
    parts: list[str] = []
    for r in rows:
        if r.item_no:
            parts.append(f"{r.item_no} {r.text}")
        else:
            parts.append(r.text)
    return " ".join(parts)
