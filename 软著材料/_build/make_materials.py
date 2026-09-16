# -*- coding: utf-8 -*-
"""生成软著申报鉴别材料（PDF）：
1. 源程序鉴别材料.pdf —— lawgate/ 包全部 .py 拼接，前 30 页 + 后 30 页（每页 50 行）
2. 文档鉴别材料_设计说明书.pdf —— docs/system_manual.md 渲染（超 60 页则取前后各 30 页）
运行：python make_materials.py  （仓库根目录自适应）
"""
import os
import glob
import re
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

BUILD_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.dirname(BUILD_DIR)
REPO = os.path.dirname(OUT_DIR)

SOFT_NAME = "律核（CA-LegalGate）三通道法律问答路由系统"
VERSION = "V0.1.0"
LINES_PER_PAGE = 50

pdfmetrics.registerFont(TTFont("sun", r"C:\Windows\Fonts\simsun.ttc", subfontIndex=0))
pdfmetrics.registerFont(TTFont("nsun", r"C:\Windows\Fonts\simsun.ttc", subfontIndex=1))
pdfmetrics.registerFont(TTFont("hei", r"C:\Windows\Fonts\simhei.ttf"))

PAGE_W, PAGE_H = A4  # 595 x 842


def wrap_text(line, font, size, max_w):
    """按字宽折行（CJK 安全），返回多行列表；空行返回 ['']"""
    if line == "":
        return [""]
    out, cur = [], ""
    for ch in line:
        if pdfmetrics.stringWidth(cur + ch, font, size) > max_w:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    out.append(cur)
    return out


def collect_source_lines():
    """拼接 lawgate/ 全部 .py（按路径排序），带文件横幅，返回逻辑行列表"""
    files = sorted(glob.glob(os.path.join(REPO, "lawgate", "**", "*.py"), recursive=True))
    lines = []
    for f in files:
        rel = os.path.relpath(f, REPO).replace("\\", "/")
        lines.append("")
        lines.append("# " + "=" * 76)
        lines.append("# 文件: %s" % rel)
        lines.append("# " + "=" * 76)
        with open(f, "r", encoding="utf-8", errors="replace") as fh:
            for raw in fh.read().splitlines():
                lines.append(raw.rstrip("\n").replace("\t", "    "))
        lines.append("")
    return lines, files


def paginate(lines, font, size, max_w):
    """把逻辑行折行为物理行后按 50 行/页分页"""
    phys = []
    for ln in lines:
        phys.extend(wrap_text(ln, font, size, max_w))
    return [phys[i:i + LINES_PER_PAGE] for i in range(0, len(phys), LINES_PER_PAGE)]


def draw_page(c, page_lines, font, size, header, page_no, total_pages, note=None):
    c.setFont("hei", 9)
    c.drawString(50, PAGE_H - 40, header)
    c.drawRightString(PAGE_W - 50, PAGE_H - 40, "第 %d 页  共 %d 页" % (page_no, total_pages))
    c.setLineWidth(0.5)
    c.line(50, PAGE_H - 46, PAGE_W - 50, PAGE_H - 46)
    y = PAGE_H - 62
    if note:
        c.setFont("sun", 8.5)
        c.drawString(50, y, note)
        y -= 14
    c.setFont(font, size)
    lh = size * 1.28
    for ln in page_lines:
        c.drawString(50, y, ln)
        y -= lh
    c.showPage()


def make_source_pdf():
    lines, files = collect_source_lines()
    font, size, max_w = "nsun", 8.2, PAGE_W - 100
    pages = paginate(lines, font, size, max_w)
    total_all = len(pages)
    if total_all > 60:
        front = pages[:30]
        back = pages[-30:]
        sel = front + back
        note = "说明：全部源程序共 %d 页，按受理要求提交前 30 页与后 30 页（本页为前 30 页之第 1 页）。" % total_all
    else:
        sel = pages
        note = "说明：源程序不足 60 页（共 %d 页），按要求全部提交。" % total_all
    header = "%s %s  源程序鉴别材料" % (SOFT_NAME, VERSION)
    out = os.path.join(OUT_DIR, "源程序鉴别材料.pdf")
    c = canvas.Canvas(out, pagesize=A4)
    c.setTitle("%s %s 源程序鉴别材料" % (SOFT_NAME, VERSION))
    for i, pg in enumerate(sel, 1):
        draw_page(c, pg, font, size, header, i, len(sel), note=note if i == 1 else None)
    c.save()
    return out, len(sel), total_all, len(files)


# ---------------- 说明书渲染 ----------------

def md_to_flow(md):
    """把 markdown 解析为 (kind, text) 流：h1-h4 / para / li / code / table / blank"""
    flow, in_code = [], False
    for raw in md.splitlines():
        line = raw.rstrip("\n")
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            flow.append(("code", line))
            continue
        s = line.strip()
        if not s:
            flow.append(("blank", ""))
            continue
        if s.startswith("|"):
            flow.append(("table", line.rstrip()))
            continue
        if s.startswith("#### "):
            flow.append(("h4", s[5:]))
        elif s.startswith("### "):
            flow.append(("h3", s[4:]))
        elif s.startswith("## "):
            flow.append(("h2", s[3:]))
        elif s.startswith("# "):
            flow.append(("h1", s[2:]))
        elif s.startswith(("> ", ">-")):
            flow.append(("quote", s[2:] if s.startswith("> ") else s[1:]))
        elif s.startswith(("- ", "* ")) or (len(s) > 2 and s[0].isdigit() and s[1] in ".)"):
            flow.append(("li", s))
        elif set(s) <= set("-:= "):
            continue
        else:
            flow.append(("para", s))
    return flow


STYLE = {
    "h1": ("hei", 15, 24), "h2": ("hei", 13, 20), "h3": ("hei", 11.5, 17), "h4": ("hei", 10.5, 15),
    "para": ("sun", 10.5, 16), "li": ("sun", 10.5, 16), "quote": ("sun", 9.5, 14),
    "code": ("nsun", 8.5, 12), "table": ("nsun", 8.5, 12),
}


def make_manual_pdf():
    src = os.path.join(REPO, "docs", "system_manual.md")
    with open(src, "r", encoding="utf-8") as fh:
        flow = md_to_flow(fh.read())
    max_w = PAGE_W - 100
    # 先排版成 (style, line) 物理行序列
    phys = []
    for kind, text in flow:
        if kind == "blank":
            phys.append(("blank", ""))
            continue
        font, size, lh = STYLE[kind]
        indent = "    " if kind in ("code", "table") else ("  " if kind == "li" else "")
        for w in wrap_text(indent + text, font, size, max_w):
            phys.append((kind, w))
    # 分页：按行高累计，页容量 = 可用高度
    usable = PAGE_H - 110
    pages, cur, h = [], [], 0.0
    for kind, line in phys:
        lh = 6 if kind == "blank" else STYLE[kind][2]
        if h + lh > usable and cur:
            pages.append(cur)
            cur, h = [], 0.0
        cur.append((kind, line))
        h += lh
    if cur:
        pages.append(cur)
    total_all = len(pages)
    if total_all > 60:
        sel = pages[:30] + pages[-30:]
        note = "说明：全文共 %d 页，按受理要求提交前 30 页与后 30 页（本页为前 30 页之第 1 页）。" % total_all
    else:
        sel = pages
        note = "说明：全文共 %d 页，按要求全部提交。" % total_all
    header = "%s %s  文档鉴别材料（设计说明书）" % (SOFT_NAME, VERSION)
    out = os.path.join(OUT_DIR, "文档鉴别材料_设计说明书.pdf")
    c = canvas.Canvas(out, pagesize=A4)
    c.setTitle("%s %s 文档鉴别材料（设计说明书）" % (SOFT_NAME, VERSION))
    total = len(sel)
    for pno, pg in enumerate(sel, 1):
        c.setFont("hei", 9)
        c.drawString(50, PAGE_H - 40, header)
        c.drawRightString(PAGE_W - 50, PAGE_H - 40, "第 %d 页  共 %d 页" % (pno, total))
        c.setLineWidth(0.5)
        c.line(50, PAGE_H - 46, PAGE_W - 50, PAGE_H - 46)
        y = PAGE_H - 66
        if pno == 1:
            c.setFont("sun", 8.5)
            c.drawString(50, y, note)
            y -= 14
        for kind, line in pg:
            if kind == "blank":
                y -= 6
                continue
            font, size, lh = STYLE[kind]
            c.setFont(font, size)
            c.drawString(50, y, line)
            y -= lh
        c.showPage()
    c.save()
    return out, total, total_all


if __name__ == "__main__":
    o1, n1, all1, nf = make_source_pdf()
    print("[源程序] %s | 提交 %d 页（全部 %d 页），含 %d 个源文件" % (o1, n1, all1, nf))
    o2, n2, all2 = make_manual_pdf()
    print("[说明书] %s | 提交 %d 页（全文 %d 页）" % (o2, n2, all2))
