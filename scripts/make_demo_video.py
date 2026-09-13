# -*- coding: utf-8 -*-
"""CA-LegalGate（律核）功能演示短片：逐帧渲染器（PIL），产出 frames/ 后由 ffmpeg 合成 MP4。

视频内容取自仓库真实产物（本文件 2026-09-11 生成，早于 D37）：
  * README.md（系统定位、架构、诚实清单）
  * 原 docs/smoke_report.md / docs/smoke_raw.jsonl（2026-09-11 实测端到端冒烟，20/20；
    该两份产物与生成脚本 scripts/smoke.py 已于 2026-09-16 删除，见 docs/deviations.md D37，
    视频内画面为当时的真实记录）
  * configs/base.yaml、lawgate/channel/llm_base.py（GPU/vLLM 支持）
帧率 30、1280x720、约 58 秒。渲染到 frames/，编码用仓库外命令单独执行。
"""
import math
import os
import sys
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1280, 720
FPS = 30
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frames_demo")

MSYH = r"C:\Windows\Fonts\msyh.ttc"
MSYHBD = r"C:\Windows\Fonts\msyhbd.ttc"

INK = (232, 238, 247)
MUT = (148, 163, 184)
DIM = (110, 124, 145)
PANEL = (17, 26, 44)
PANEL2 = (22, 33, 54)
LINE = (38, 52, 78)
GOLD = (212, 175, 55)
CYAN = (56, 189, 248)
GREEN = (74, 222, 128)
ORANGE = (251, 146, 60)
RED = (248, 113, 113)
BLUE = (96, 165, 250)

_fonts = {}


def font(size, bold=False):
    key = (size, bold)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(MSYHBD if bold else MSYH, size)
    return _fonts[key]


def clip01(x):
    return 0.0 if x < 0 else (1.0 if x > 1 else x)


def ease(x):
    x = clip01(x)
    return 1 - (1 - x) ** 3


def seg(t, start, dur):
    if dur <= 0:
        return 1.0 if t >= start else 0.0
    return clip01((t - start) / dur)


def lerp(a, b, t):
    return a + (b - a) * t


_wrapcache = {}


def wrap(s, f, maxw):
    key = (id(f), s, maxw)
    if key in _wrapcache:
        return _wrapcache[key]
    lines = []
    for part in s.split("\n"):
        cur = ""
        for ch in part:
            if f.getlength(cur + ch) <= maxw or not cur:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
        lines.append(cur)
    _wrapcache[key] = lines
    return lines


def T(d, xy, s, f, fill, al=1.0, anchor=None):
    if al <= 0.003:
        return
    d.text(xy, s, font=f, fill=fill + (int(255 * clip01(al)),), anchor=anchor)


def R(d, box, r, fill=None, outline=None, width=1, al=1.0):
    if al <= 0.003:
        return
    def mk(c):
        c = tuple(c)
        base_a = (c[3] / 255) if len(c) == 4 else 1.0
        return c[:3] + (int(255 * clip01(al) * base_a),)
    if fill is not None:
        fill = mk(fill)
    if outline is not None:
        outline = mk(outline)
    d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)


def block(d, xy, s, f, fill, maxw, al=1.0, reveal=None, lh=1.42, color_fn=None):
    """绘制自动换行文本；reveal=已揭示字符数；返回 (底部y, 光标点)。"""
    x0, y0 = xy
    lhpx = int(f.size * lh)
    lines = wrap(s, f, maxw)
    n = 0
    cur_x, cur_y = x0, y0
    for ln in lines:
        if reveal is not None and n >= reveal:
            break
        shown = ln if reveal is None else ln[: max(0, reveal - n)]
        n += len(ln)
        col = color_fn(ln) if color_fn else fill
        cur_x = x0 + f.getlength(shown)
        cur_y = y0
        if shown:
            T(d, (x0, y0), shown, f, col, al)
        y0 += lhpx
    return y0, (cur_x + 2, cur_y)


def chip(d, xy, s, f, col, al=1.0, padx=10, pady=5, bg=(30, 44, 70), outline=True):
    x, y = xy
    w = f.getlength(s) + padx * 2
    h = int(f.size * 1.55)
    R(d, (x, y, x + w, y + h), h // 2, fill=bg, outline=col if outline else None, width=1, al=al)
    T(d, (x + padx, y + (h - f.size) // 2 - 1), s, f, col, al)
    return x + w + 8


def arrow(d, p0, p1, col, al=1.0, width=2, head=9):
    if al <= 0.003:
        return
    x0, y0 = p0
    x1, y1 = p1
    L = math.hypot(x1 - x0, y1 - y0) or 1
    ux, uy = (x1 - x0) / L, (y1 - y0) / L
    bx, by = x1 - ux * head, y1 - uy * head
    acol = col + (int(255 * clip01(al)),)
    d.line([x0, y0, bx, by], fill=acol, width=width)
    px, py = -uy, ux
    d.polygon([(x1, y1), (bx + px * head * 0.55, by + py * head * 0.55),
               (bx - px * head * 0.55, by - py * head * 0.55)], fill=acol)


def pulse(d, p0, p1, t0, col, al_dur=0.7, r=5):
    """沿箭头飞一个高亮点（元素“出现”时的一次性动画）。"""
    p = seg(t0, 0.0, al_dur)
    if 0 < p < 1:
        x, y = lerp(p0[0], p1[0], ease(p)), lerp(p0[1], p1[1], ease(p))
        a = math.sin(math.pi * p)
        d.ellipse((x - r, y - r, x + r, y + r), fill=col + (int(230 * a),))


def warn_mark(d, cxy, col, al):
    x, y = cxy
    r = 11
    d.ellipse((x - r, y - r, x + r, y + r), outline=col + (int(255 * al),), width=2)
    T(d, (x, y - 1), "!", font(20, True), col, al, anchor="mm")


def heading(d, t, s, note=None):
    al = ease(seg(t, 0.0, 0.6))
    dy = (1 - al) * 18
    T(d, (64, 42 + dy), s, font(34, True), INK, al)
    R(d, (64, 92 + dy, 64 + 54 * al, 95 + dy), 1, fill=GOLD, al=al)
    if note:
        T(d, (W - 64, 56 + dy), note, font(20), DIM, ease(seg(t, 0.3, 0.6)), anchor="ra")


def card(d, box, al, accent=None, r=14):
    R(d, box, r, fill=PANEL2 + (int(255 * al * 0.96),), outline=LINE, width=1, al=al)
    if accent:
        x0, y0, x1, y1 = box
        R(d, (x0, y0 + 10, x0 + 4, y1 - 10), 2, fill=accent, al=al)


# ---------- 背景 ----------
def make_bg():
    base = Image.new("RGB", (W, H))
    px = base.load()
    for y in range(H):
        t = y / H
        c = (int(lerp(9, 16, t)), int(lerp(14, 26, t)), int(lerp(26, 44, t)))
        for x in range(W):
            px[x, y] = c
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-260, -380, 880, 260), fill=(56, 120, 248, 30))
    gd.ellipse((520, -300, 1560, 300), fill=(212, 175, 55, 20))
    glow = glow.filter(ImageFilter.GaussianBlur(140))
    base = Image.alpha_composite(base.convert("RGBA"), glow)
    d = ImageDraw.Draw(base)
    for gx in range(0, W, 80):
        d.line([gx, 0, gx, H], fill=(255, 255, 255, 6), width=1)
    for gy in range(0, H, 80):
        d.line([0, gy, W, gy], fill=(255, 255, 255, 6), width=1)
    return base


BG = make_bg()

# ---------- 场景 ----------

def s1_title(d, t, dur):
    al = ease(seg(t, 0.1, 0.9))
    y = lerp(300, 258, al)
    T(d, (W // 2, y), "CA-LegalGate · 律核", font(74, True), GOLD, al, anchor="mm")
    uw = int(560 * ease(seg(t, 0.5, 1.0)))
    R(d, (W // 2 - uw // 2, 316, W // 2 + uw // 2, 319), 1, fill=GOLD, al=0.85)
    a2 = ease(seg(t, 0.8, 0.9))
    T(d, (W // 2, lerp(372, 362, a2)), "三通道异构法律问答路由系统", font(40, True), INK, a2, anchor="mm")
    chips = ["免训练", "检索调用能省则省", "每个结论可审计"]
    x = W // 2 - 330
    for i, c in enumerate(chips):
        a = ease(seg(t, 1.6 + i * 0.25, 0.5))
        f = font(26)
        w = f.getlength(c) + 40
        chip(d, (x, 428), c, f, CYAN if i != 1 else GOLD, a)
        x += w + 14
    a3 = ease(seg(t, 2.7, 0.9))
    T(d, (W // 2, 520), "—— 功能演示短片 · 内容取自仓库真实冒烟记录 ——", font(24), MUT, a3, anchor="mm")


def s2_pain(d, t, dur):
    heading(d, t, "中文法律问答的三个痛点")
    cards = [
        ("① 幻觉引用", "编造法条编号与案号，答得一本正经，来源却不存在。", RED),
        ("② 引用已废止法", "2021 年《担保法》《合同法》《婚姻法》等已废止，模型照引不误。", ORANGE),
        ("③ 每题必检索", "向量检索次次都调：更慢、更贵，还未必更准。", BLUE),
    ]
    xs = [64, 460, 856]
    for i, (ti, body, col) in enumerate(cards):
        a = ease(seg(t, 0.5 + i * 0.9, 0.7))
        if a <= 0:
            continue
        y0 = lerp(150 + 14 * (1 - a), 150, a)
        box = (xs[i], y0, xs[i] + 360, y0 + 244)
        card(d, box, a, col)
        T(d, (xs[i] + 24, y0 + 22), ti, font(28, True), col, a)
        block(d, (xs[i] + 24, y0 + 72), body, font(22), INK, 312, a, lh=1.5)
    a4 = ease(seg(t, 3.6, 0.8))
    box = (64, 440, 1216, 596)
    card(d, box, a4, GOLD)
    T(d, (92, 456), "CA-LegalGate 的目标", font(24, True), GOLD, a4)
    T(d, (92, 494), "不牺牲准确率，把检索调用压下来；", font(24, True), INK, a4)
    T(d, (92, 532), "每一次路由决策、每一条法条来源、每一个案号核验结论，都可审计。", font(24, True), INK, a4)
    T(d, (64, 640), "三通道：B 确定性结构化 / A 门控直答 / C 门控检索增强，由不确定性门控与桶级阈值 τ_b 决定走向。",
      font(21), DIM, ease(seg(t, 4.6, 0.9)))


def node(d, box, title, subs, chips_, t0, col):
    a = ease(seg(t0, 0.0, 0.6))
    if a <= 0:
        return
    x0, y0, x1, y1 = box
    R(d, (x0, y0, x1, y1), 12, fill=PANEL + (int(240 * a),), outline=col, width=2, al=a)
    T(d, ((x0 + x1) // 2, y0 + 18), title, font(24, True), INK, a, anchor="ma")
    yy = y0 + 54
    for s in subs:
        T(d, ((x0 + x1) // 2, yy), s, font(19), MUT, a, anchor="ma")
        yy += 26
    cx = x0 + 14
    for c in chips_:
        cx = chip(d, (cx, y1 - 38), c, font(17), col, a)


def s3_arch(d, t, dur):
    heading(d, t, "系统架构：一次提问如何被路由", "对应 lawgate/router.py")
    q = (520, 116, 760, 160)
    node(d, q, "用户提问（+多轮历史）", [], [], 0.3, BLUE)
    it = (470, 190, 810, 236)
    node(d, it, "确定性意图检测 · 槽位抽取", [], [], 0.9, CYAN)
    aE = ease(seg(t, 1.5, 0.5))
    T(d, (430, 262), "命中条号/案号/槽位齐全？", font(19), DIM, aE, anchor="ma")
    b = (100, 296, 560, 452)
    node(d, b, "通道 B · 确定性结构化（SQLite/FTS）",
         ["P1 案号四级核验   P2 法条+时效", "P3 法律效力询问   P4 主题条文检索"], ["0 次检索", "毫秒级"], 1.8, GREEN)
    g = (700, 296, 1180, 452)
    node(d, g, "门控 Gate（免训练）",
         ["前 20 token 草稿 → 不确定性 u", "hybrid：b2 桶用神经信号，", "b1/b3/b4 用复杂度评分 · τ_b 逐桶校准"], [], 2.6, GOLD)
    A = (660, 500, 890, 600)
    node(d, A, "通道 A 直接生成", ["LLM 裸答"], ["0 次检索"], 4.6, CYAN)
    C = (930, 500, 1180, 600)
    node(d, C, "通道 C 检索增强", ["bge+Chroma→混合重排"], ["1 次检索"], 5.3, ORANGE)
    tr = (250, 632, 1030, 690)
    tr_a = ease(seg(t, 7.0, 0.6))
    if tr_a > 0:
        R(d, tr, 12, fill=(24, 40, 30) + (int(240 * tr_a),), outline=GREEN, width=2, al=tr_a)
        T(d, (W // 2, 646), "统一 trace：通道 / u / τ_b / 时效状态 / 案号核验 / 来源 URL / 检索次数 —— 全部落盘",
          font(21, True), GREEN, tr_a, anchor="ma")
    # 连线
    if seg(t, 0.9, 0.1) > 0:
        arrow(d, (640, 160), (640, 188), CYAN, ease(seg(t, 0.9, 0.4)))
        pulse(d, (640, 160), (640, 188), t - 0.9, CYAN)
    if seg(t, 1.5, 0.1) > 0:
        arrow(d, (560, 236), (380, 294), GREEN, ease(seg(t, 1.5, 0.5)))
        pulse(d, (560, 236), (380, 294), t - 1.5, GREEN)
        T(d, (410, 262), "是 → 优先走 B", font(18), GREEN, ease(seg(t, 1.5, 0.5)), anchor="ma")
    if seg(t, 2.2, 0.1) > 0:
        arrow(d, (720, 236), (920, 294), GOLD, ease(seg(t, 2.2, 0.5)))
        pulse(d, (720, 236), (920, 294), t - 2.2, GOLD)
        T(d, (880, 262), "否 / B 查无此条", font(18), GOLD, ease(seg(t, 2.2, 0.5)), anchor="ma")
    if seg(t, 4.9, 0.1) > 0:
        arrow(d, (820, 452), (775, 498), CYAN, ease(seg(t, 4.9, 0.5)))
        T(d, (840, 478), "u ≤ τ_b", font(18), CYAN, ease(seg(t, 4.9, 0.5)), anchor="la")
    if seg(t, 5.6, 0.1) > 0:
        arrow(d, (1000, 452), (1060, 498), ORANGE, ease(seg(t, 5.6, 0.5)))
        T(d, (1040, 478), "u > τ_b", font(18), ORANGE, ease(seg(t, 5.6, 0.5)), anchor="la")
    if seg(t, 7.0, 0.1) > 0:
        arrow(d, (330, 452), (430, 630), GREEN, ease(seg(t, 7.0, 0.5)))
        arrow(d, (775, 600), (740, 630), CYAN, ease(seg(t, 7.2, 0.5)))
        arrow(d, (1055, 600), (1000, 632), ORANGE, ease(seg(t, 7.4, 0.5)))
    if seg(t, 6.2, 0.1) > 0:
        T(d, (330, 500), "查无此条 / 槽位不全\n→ 交给门控", font(18), DIM, ease(seg(t, 6.2, 0.6)))


def s4_demoB(d, t, dur):
    heading(d, t, "真实演示 · 通道 B：不检索也答得准", "冒烟用例 B-P3-1 · 2026-09-11 实测")
    q = "担保法现在还有用吗？"
    aq = ease(seg(t, 0.3, 0.3))
    n = int(max(0, t - 0.3) * 14)
    f_q = font(28, True)
    shown = q[:max(n, 0)]
    if shown:
        qw = f_q.getlength(q) + 44
        bx1 = 1216
        bx0 = bx1 - max(qw, 320)
        R(d, (bx0, 112, bx1, 168), 12, fill=(46, 36, 14), al=aq)
        R(d, (bx0, 112, bx1, 168), 12, outline=GOLD, width=1, al=aq * 0.7)
        T(d, (bx0 + 22, 124), shown, f_q, INK, aq)
    a1 = ease(seg(t, 1.7, 0.6))
    T(d, (1216, 186), "意图：法律名 + 效力问句 → 通道 B · P3 法律效力检查（SQLite 直接命中）",
      font(21), GREEN, a1, anchor="ra")
    a2 = ease(seg(t, 2.3, 0.5))
    cx = 760
    for c in ["channel=B", "law_validity_check", "检索调用=0"]:
        cx = chip(d, (cx, 214), c, font(17), GREEN, a2)
        cx += 4
    box = (80, 268, 1216, 496)
    a3 = ease(seg(t, 2.9, 0.7))
    if a3 > 0:
        y0 = lerp(282, 268, a3)
        box = (80, y0, 1216, y0 + 228)
        card(d, box, a3, ORANGE)
        T(d, (104, y0 + 16), "答", font(22, True), ORANGE, a3)
        warn_mark(d, (152, y0 + 30), ORANGE, a3)
        T(d, (172, y0 + 16), "《担保法》已于 2021-01-01 被废止", font(26, True), ORANGE, a3)
        lines = ["现行规定见《民法典》。",
                 "【现行规定】《民法典》第686条：约定不明时按一般保证承担责任（与担保法相反）。",
                 "【法律沿革】担保法 → 民法典。"]
        yy = y0 + 62
        for i, ln in enumerate(lines):
            ar = ease(seg(t, 3.4 + i * 0.55, 0.5))
            T(d, (104, yy), ln, font(23), INK, ar)
            yy += 42
    ab = ease(seg(t, 5.4, 0.7))
    if ab > 0:
        box = (80, 528, 1216, 616)
        R(d, box, 12, fill=(13, 22, 16) + (int(240 * ab),), outline=(46, 90, 60), width=1, al=ab)
        T(d, (104, 540), "trace 快照（随答案一并返回，可复核）", font(19, True), GREEN, ab)
        cx = 104
        for c in ["validity=已废止", "n_retrieval_calls=0", "source=flk.npc.gov.cn", "must_show_warning=true"]:
            cx = chip(d, (cx, 572), c, font(17), MUT, ab, bg=(24, 34, 40)) + 4
    T(d, (64, 660), "答案文本与路由结果均为 2026-09-11 真实端到端冒烟记录（docs/smoke_raw.jsonl），非摆拍。",
      font(18), DIM, ease(seg(t, 6.4, 0.8)))


def s5_demoC(d, t, dur):
    heading(d, t, "真实演示 · 门控放行检索：该查才查", "冒烟用例 G-C-1 · 2026-09-11 实测")
    q = "交通事故中，保险公司在交强险和商业三者险范围内的赔偿责任应当如何划分？"
    aq = ease(seg(t, 0.3, 0.4))
    f_q = font(25, True)
    lines = wrap(q, f_q, 700)
    bh = len(lines) * 38 + 30
    bx0 = 1216 - (700 + 44)
    R(d, (bx0, 110, 1216, 110 + bh), 12, fill=(46, 36, 14), al=aq)
    R(d, (bx0, 110, 1216, 110 + bh), 12, outline=GOLD, width=1, al=aq * 0.7)
    yy = 126
    n = int(max(0, t - 0.3) * 26)
    used = 0
    for ln in lines:
        shown = ln[: max(0, min(n - used, len(ln)))]
        used += len(ln)
        if shown:
            T(d, (bx0 + 22, yy), shown, f_q, INK, aq)
        yy += 38
        if n < used:
            break
    gp = (80, 110 + bh + 24, 1216, 110 + bh + 24 + 210)
    ag = ease(seg(t, 1.6, 0.6))
    if ag > 0:
        card(d, gp, ag, GOLD)
        T(d, (104, gp[1] + 14), "门控决策（hybrid：b1 概念题 → 确定性复杂度评分）", font(22, True), INK, ag)
        bar_x0, bar_x1, bar_y = 130, 1140, gp[1] + 86
        R(d, (bar_x0, bar_y - 7, bar_x1, bar_y + 7), 7, fill=(30, 41, 60), al=ag)
        ut = ease(seg(t, 2.1, 1.1))
        uval = 0.5 * ut
        px = bar_x0 + (bar_x1 - bar_x0) * uval
        R(d, (bar_x0, bar_y - 7, px, bar_y + 7), 7, fill=GOLD, al=ag)
        tx = bar_x0 + (bar_x1 - bar_x0) * 0.29
        d.line([tx, bar_y - 20, tx, bar_y + 20], fill=RED + (int(255 * ag),), width=3)
        T(d, (tx, bar_y + 26), "τ_b1 = 0.29（dev 集校准）", font(18), RED, ag, anchor="ma")
        T(d, (px, bar_y - 34), f"u = {uval:.2f}", font(22, True), GOLD, ag, anchor="ma")
        av = ease(seg(t, 3.4, 0.6))
        T(d, (130, gp[1] + 146), "u > τ_b → 模型自感不确定 → 允许 1 次检索 → 通道 C", font(24, True), ORANGE, av)
    ab = ease(seg(t, 4.3, 0.7))
    if ab > 0:
        box = (80, gp[3] + 18, 1216, gp[3] + 150)
        card(d, box, ab, ORANGE)
        T(d, (104, box[1] + 12), "通道 C 回答（节选）：依据《道路交通安全法》第七十六条 —— 交强险限额内先由保险公司赔偿，",
          font(22), INK, ab)
        T(d, (104, box[1] + 46), "不足部分按过错比例分担；机动车与行人之间适用无过错方倾斜规则。", font(22), INK, ab)
        cx = 104
        for c in ["channel=C", "u=0.50 > τ=0.29", "n_retrieval_calls=1", "top-8 重排后引用"]:
            cx = chip(d, (cx, box[1] + 86), c, font(17), MUT, ab, bg=(24, 34, 40)) + 4
    T(d, (64, 660), "对比基线：always-rag 每题 1 次检索、never-rag 从不检索；本系统按“该不该查”逐题裁决。",
      font(18), DIM, ease(seg(t, 5.6, 0.8)))


def s6_trace(d, t, dur):
    heading(d, t, "每个答案自带“审计线索”", "trace 字段见 README §2")
    code_lines = [
        "trace = {",
        "  channel:  'B' | 'A' | 'C',",
        "  decision: 'route=B reason=law_validity',",
        "  u / u_signal / u_complexity,",
        "  tau_b:    0.29 …（逐桶校准值）,",
        "  bucket:   b1概念 b2法条 b3案例 b4多轮,",
        "  slots / slots_inherited,",
        "  validity_status / case_verify,",
        "  source_url（flk.npc.gov.cn）,",
        "  n_retrieval_calls / latency_ms,",
        "}",
    ]
    box = (64, 120, 660, 620)
    a0 = ease(seg(t, 0.3, 0.6))
    if a0 > 0:
        R(d, box, 12, fill=(10, 16, 28) + (int(242 * a0),), outline=LINE, width=1, al=a0)
        yy = 142
        for i, ln in enumerate(code_lines):
            al = ease(seg(t, 0.7 + i * 0.16, 0.35))
            col = GOLD if i == 0 or ln == "}" else (DIM if ":" not in ln else CYAN)
            if "=" in ln and i != 0:
                k, _, v = ln.partition(":")
                T(d, (92, yy), k + ":", font(21, True), col, al)
                T(d, (92 + font(21, True).getlength(k + ":"), yy), ln.partition(":")[2], font(21), INK, al)
            else:
                T(d, (92, yy), ln, font(21, True), col, al)
            yy += 40
    stats = [
        ("端到端冒烟 20/20", "三通道 × 真实 HTTP /chat，断言按设计契约写死（2026-09-11）", GREEN),
        ("评测集 1180 条 · 自校验 PASS", "六类题目分层划分 dev/test；多轮按组不跨划分", CYAN),
        ("向量库 1295 文档 · 512 维", "bge-small-zh-v1.5 编码，ChromaDB 持久化，37.8 s 建库", BLUE),
        ("诚实披露写进产品", "案号库=SYNTHETIC、法条=人工转录种子语料，逐条标注、不冒充官方原文", ORANGE),
    ]
    ys = [120, 248, 376, 504]
    for i, (ti, sub, col) in enumerate(stats):
        a = ease(seg(t, 1.6 + i * 0.7, 0.7))
        if a <= 0:
            continue
        box = (700, lerp(ys[i] + 12, ys[i], a), 1216, lerp(ys[i] + 12, ys[i], a) + 112)
        card(d, box, a, col)
        T(d, (724, box[1] + 14), ti, font(25, True), col, a)
        block(d, (724, box[1] + 54), sub, font(19), MUT, 468, a, lh=1.35)


def s7_gpu(d, t, dur):
    heading(d, t, "算力升级：换到 GPU 机器能快多少？", "configs/base.yaml · llm_base.py")
    lb = (64, 128, 616, 470)
    a1 = ease(seg(t, 0.4, 0.7))
    if a1 > 0:
        card(d, lb, a1, RED)
        T(d, (92, 148), "本机现状 · CPU-only", font(27, True), RED, a1)
        items = ["Qwen2.5-1.5B · fp32 · 18 逻辑核", "生成约 12–15 s / 条（80~128 tokens）",
                 "0.5B 吞吐实测 ≈ 12 tok/s", "E1 全量流水线：小时级起跑", "7B 模型：显存与算力都够不着"]
        yy = 200
        for i, s in enumerate(items):
            al = ease(seg(t, 0.9 + i * 0.35, 0.5))
            T(d, (92, yy), "· " + s, font(23), INK, al)
            yy += 50
    rb = (664, 128, 1216, 470)
    a2 = ease(seg(t, 2.0, 0.7))
    if a2 > 0:
        card(d, rb, a2, GREEN)
        T(d, (692, 148), "有 CUDA 的机器 · 代码已备好", font(27, True), GREEN, a2)
        items = ["检测到 CUDA → 自动 4-bit 装载（bnb）", "vLLM 后端：批量推理 + PagedAttention",
                 "1.5B fp16 ≈ 3 GB 显存即可跑", "24 GB 显存：可升 7B（补偏差 D9）", "通道 B 本就毫秒级，不受影响"]
        yy = 200
        for i, s in enumerate(items):
            al = ease(seg(t, 2.5 + i * 0.35, 0.5))
            T(d, (692, yy), "· " + s, font(23), INK, al)
            yy += 50
    aa = ease(seg(t, 3.0, 0.6))
    arrow(d, (622, 300), (660, 300), GOLD, aa, width=4, head=12)
    ab = ease(seg(t, 4.4, 0.7))
    if ab > 0:
        box = (64, 494, 1216, 560)
        R(d, box, 12, fill=(10, 16, 28) + (int(242 * ab),), outline=LINE, width=1, al=ab)
        T(d, (92, 512), "configs/base.yaml 只需三行：", font(21, True), MUT, ab)
        code = "device: cuda:0    load_in_4bit: true    llm_backend: vllm"
        f_c = font(23, True)
        T(d, (92 + f_c.getlength("configs/base.yaml 只需三行：") + 26, 511),
          code, f_c, GOLD, ab)
    ac = ease(seg(t, 5.4, 0.8))
    T(d, (W // 2, 592), "生成吞吐预计 ×10 以上，单条回答从 12–15 秒进入 1 秒内；小时级实验缩到分钟级。",
      font(24, True), INK, ac, anchor="mm")
    T(d, (W // 2, 640), "注：×10–50 为公开硬件常见量级（非本机实测承诺）；门控草稿与向量编码同样受益。",
      font(18), DIM, ac, anchor="mm")


def s8_outro(d, t, dur):
    a1 = ease(seg(t, 0.2, 0.9))
    T(d, (W // 2, lerp(340, 300, a1)), "CA-LegalGate · 律核", font(58, True), GOLD, a1, anchor="mm")
    a2 = ease(seg(t, 1.1, 0.9))
    T(d, (W // 2, 386), "让每一次法律回答，有来源、可审计。", font(32, True), INK, a2, anchor="mm")
    a3 = ease(seg(t, 2.1, 0.9))
    T(d, (W // 2, 470), "三通道异构路由 · 免训练门控 · 桶级阈值校准 · 全链路 trace", font(22), MUT, a3, anchor="mm")
    T(d, (W // 2, 560), "本视频由仓库真实产物逐帧渲染生成（PIL + ffmpeg）", font(18), DIM, a3, anchor="mm")


SCENES = [
    ("title", 5.0, s1_title),
    ("pain", 7.0, s2_pain),
    ("arch", 10.0, s3_arch),
    ("demoB", 9.0, s4_demoB),
    ("demoC", 8.0, s5_demoC),
    ("trace", 6.0, s6_trace),
    ("gpu", 9.0, s7_gpu),
    ("outro", 4.0, s8_outro),
]
TOTAL = sum(s[1] for s in SCENES)
FADE = 0.32


def render_frame(gt):
    acc = 0.0
    for _, dur, fn in SCENES:
        if gt < acc + dur or fn is SCENES[-1][2]:
            local = gt - acc
            break
        acc += dur
    base = BG.copy()
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    fn(d, local, dur)
    # 场景首尾黑场过渡
    edge = min(local, dur - local)
    if edge < FADE:
        fa = 1 - edge / FADE
        R(d, (0, 0, W, H), 0, fill=(5, 8, 15), al=fa)
    out = Image.alpha_composite(base, ov)
    # 角标
    dd = ImageDraw.Draw(out)
    dd.text((W - 24, H - 30), "CA-LegalGate 演示", font=font(15), fill=(120, 132, 152, 200), anchor="ra")
    return out.convert("RGB")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    n = int(TOTAL * FPS)
    only = None
    if len(sys.argv) > 1 and sys.argv[1] == "--stills":
        # 抽帧自检模式：每个场景 2 张代表帧
        for i, (name, dur, _) in enumerate(SCENES):
            for frac, tag in ((0.55, "mid"), (0.92, "late")):
                gt = sum(s[1] for s in SCENES[:i]) + dur * frac
                p = os.path.join(OUT_DIR, f"still_{name}_{tag}.png")
                render_frame(min(gt, TOTAL - 0.1)).save(p)
                print("still:", p)
        return
    if len(sys.argv) > 2 and sys.argv[1] == "--range":
        a, b = [int(x) for x in sys.argv[2].split(":")]
        rng = range(a, b)
    else:
        rng = range(n)
    for idx in rng:
        img = render_frame(idx / FPS)
        img.save(os.path.join(OUT_DIR, f"f_{idx:05d}.png"), optimize=False)
        if idx % 150 == 0:
            print(f"frame {idx}/{n}", flush=True)
    print("done", n, "frames")


if __name__ == "__main__":
    main()
