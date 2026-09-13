# -*- coding: utf-8 -*-
"""论文级结果绘图模块（CA-LegalGate）。

设计约定
--------
1. **不会崩**：每个绘图函数在输入缺失 / 为空时都退化为一张写着 ``NO DATA`` 的
   占位图，绝不抛异常——跑完实验后一次性重画所有图时，缺一个结果文件不应该
   中断整批出图。
2. **中文可渲染**：matplotlib 自带字体没有 CJK 字形，中文会变成豆腐块。本模块
   通过 :func:`setup_cjk_font` 主动 ``addfont()`` 注册系统 CJK 字体（Windows 上
   优先微软雅黑 ``msyh.ttc``），并关闭 ``axes.unicode_minus``，避免负号也变方块。
3. **每图必带溯源图注**：项目规范要求所有图带硬件 / 模型 / 日期图注，
   :func:`provenance_footer` 统一负责，并支持附加 "DEMO DATA" 之类的额外行。
4. **只写 PNG**：dpi=150、``bbox_inches="tight"``、白底，返回保存后的 ``Path``。
5. 控制台输出一律 ASCII（GBK 控制台会把中文打成乱码），图内文字用中文。

坐标 / 约定
-----------
* 每条评测记录（JSONL）字段见项目手册：``qid, category, bucket, method, seed,
  split, correct, retrieval_used, latency_ms, n_tokens, u, tau_b, channel, tvc,
  case_pass, invalid_law_cited, slots_inherited_ok, answer``。
* 方法摘要 ``results/<exp>/summary.csv`` 列：``method,seed,split,n,acc,rr,lac,
  tvc,p50_ms,p95_ms,mean_tokens,pareto_acc_note``。
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")  # 无显示环境下也能出图

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch, Rectangle  # noqa: E402

# --------------------------------------------------------------------------
# 顶层约定常量
# --------------------------------------------------------------------------
FIG_DPI = 150
SAVE_KW: dict[str, Any] = {"dpi": FIG_DPI, "bbox_inches": "tight", "facecolor": "white"}

NO_DATA_TEXT = "NO DATA"
DEMO_MARK = "DEMO DATA / NOT REAL RESULTS"

#: 三段式门控决策阈值（spec：AUC ≥0.75 绿 / 0.60–0.75 琥珀 / <0.60 红）
AUC_GREEN = 0.75
AUC_AMBER = 0.60
TIER_COLORS = {"green": "#2e7d32", "amber": "#ef6c00", "red": "#c62828", "na": "#757575"}
TIER_LABELS = {
    "green": "AUC≥0.75 信号可用",
    "amber": "0.60≤AUC<0.75 需谨慎",
    "red": "AUC<0.60 不可用",
    "na": "AUC 未知",
}
TIER_ZORDER = {"green": 3, "amber": 2, "red": 1, "na": 0}

#: 图中方法名 → 中文显示名
METHOD_LABELS: dict[str, str] = {
    "alwaysrag": "Always-RAG",
    "noRAG": "No-RAG",
    "norag": "No-RAG",
    "legalgate": "CA-LegalGate",
    "oracle": "Oracle-Router",
    "gated": "门控（CA-LegalGate）",
    "static_tau": "固定阈值 τ",
    "no_channel_b": "去信道B",
}
METHOD_COLORS: list[str] = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
    "#9467bd", "#8c564b", "#17becf", "#bcbd22", "#7f7f7f",
]
AUC_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#9467bd"]

# ==========================================================================
# 公共基础设施
# ==========================================================================


def method_label(method: Any) -> str:
    """方法名 → 图例显示名（未知方法原样返回，永不抛错）。"""
    key = str(method)
    return METHOD_LABELS.get(key, METHOD_LABELS.get(key.lower(), key))


def _cjk_capable_family_names() -> list[str]:
    """已注册字体里名字看起来支持中文的族名（用于 monospace 兜底）。"""
    tokens = ("YaHei", "SimHei", "SimSun", "Noto Sans SC", "Noto Serif SC", "PingFang",
              "Heiti", "WenQuanYi", "Source Han", "DengXian", "KaiTi", "FangSong",
              "JhengHei", "Microsoft JhengHei", "Songti", "STSong")
    try:
        names = sorted({f.name for f in font_manager.fontManager.ttflist})
    except Exception:  # noqa: BLE001
        return []
    return [n for n in names if any(t.lower() in n.lower() for t in tokens)]


def setup_cjk_font(verbose: bool = True) -> str | None:
    """注册系统 CJK 字体并设置 rcParams，返回生效的字体文件路径（找不到则 None）。

    幂等：重复调用只是重复 ``addfont``（matplotlib 内部按路径去重），不会报错。

    除了 ``font.sans-serif``，还会同步修好 ``font.monospace``：本机没有既等宽又
    含中文的字体（已用 fontTools 逐个探测确认），若不处理，文本里只要带中文就会
    退化成 DejaVu Sans Mono 而出现豆腐块——这是本项目最容易踩的坑之一。
    """
    # 优先级：微软雅黑 → 黑体 → 宋体 → 楷体 → 等线 → Noto → 文泉驿 → 苹方
    candidates = [
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\msyh.ttf"),
        Path(r"C:\Windows\Fonts\msyhbd.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path(r"C:\Windows\Fonts\simsun.ttc"),
        Path(r"C:\Windows\Fonts\simkai.ttf"),
        Path(r"C:\Windows\Fonts\Deng.ttf"),
        Path(r"C:\Windows\Fonts\NotoSansSC-VF.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        Path("/System/Library/Fonts/PingFang.ttc"),
    ]
    registered: list[str] = []
    first_ok: Path | None = None
    for path in candidates:
        if not path.exists():
            continue
        try:
            font_manager.fontManager.addfont(str(path))
            registered.append(font_manager.FontProperties(fname=str(path)).get_name())
            if first_ok is None:
                first_ok = path
        except Exception as exc:  # noqa: BLE001 - 字体损坏不能影响出图
            if verbose:
                print(f"[plotting] WARN cannot register font {path.name}: {type(exc).__name__}")

    if not registered:
        # 兜底：在已装字体中按名字猜
        for token in ("YaHei", "SimHei", "SimSun", "Noto Sans SC", "WenQuanYi",
                      "PingFang", "Heiti", "Source Han"):
            hit = next((n for n in _cjk_capable_family_names() if token.lower() in n.lower()), None)
            if hit and hit not in registered:
                registered.append(hit)

    if registered:
        ordered = [n for i, n in enumerate(registered) if n not in registered[:i]]
        base = list(matplotlib.rcParams.get("font.sans-serif", []) or [])
        matplotlib.rcParams["font.sans-serif"] = ordered + [b for b in base if b not in ordered]
        matplotlib.rcParams["font.family"] = "sans-serif"
        # monospace 也需要中文兜底（本机无中英等宽字体，见 docstring）
        mono = list(matplotlib.rcParams.get("font.monospace", []) or [])
        matplotlib.rcParams["font.monospace"] = ordered + [m for m in mono if m not in ordered]
        matplotlib.rcParams["axes.unicode_minus"] = False
        matplotlib.rcParams["pdf.fonttype"] = 42
        matplotlib.rcParams["ps.fonttype"] = 42
        if verbose:
            print(f"[plotting] CJK font ready: {ordered[0]} ({len(ordered)} registered)")
        return str(first_ok) if first_ok else None

    if verbose:
        print("[plotting] WARN no CJK font found: Chinese labels may render as boxes")
    matplotlib.rcParams["axes.unicode_minus"] = False
    return None


def rc_style_defaults() -> None:
    """统一的 rcParams 风格（幂等，可重复调用）。"""
    setup_cjk_font(verbose=False)
    matplotlib.rcParams.update(
        {
            "figure.dpi": FIG_DPI,
            "savefig.dpi": FIG_DPI,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "axes.grid": True,
            "grid.alpha": 0.25,
            "grid.linestyle": "--",
            "grid.color": "#9e9e9e",
            "axes.axisbelow": True,
            "axes.edgecolor": "#455a64",
            "axes.labelcolor": "#212121",
            "axes.titlesize": 12,
            "axes.labelsize": 11,
            "xtick.labelsize": 9.5,
            "ytick.labelsize": 9.5,
            "legend.fontsize": 9,
            "legend.framealpha": 0.92,
            "font.size": 10,
        }
    )


def _resolve_provenance(provenance: Mapping[str, Any] | None) -> dict:
    """缺省溯源信息取自 :func:`lawgate.config.get_settings`（导入失败则退回占位）。"""
    if provenance:
        return {str(k): v for k, v in dict(provenance).items()}
    try:
        from lawgate.config import get_settings

        return dict(get_settings().provenance())
    except Exception:  # noqa: BLE001 - 出图不应因配置模块失败而中断
        return {
            "hardware": "unknown",
            "device": "unknown",
            "causal_model": "unknown",
            "embed_model": "unknown",
            "date": "",
            "version": "unknown",
        }


def provenance_footer(
    fig: "plt.Figure",
    provenance: Mapping[str, Any] | None,
    extra_lines: "Sequence[str] | None" = None,
) -> str:
    """在整图底部写小号灰字溯源图注，返回写入的文本。

    行 1：硬件 / 设备 / 模型 / 嵌入 / 后端 / 日期 / 版本。
    行 2+：本图数据来源的补充说明（例如 DEMO DATA 标记）。

    容错：任何一步失败都只打印一行 ASCII 警告，绝不打断出图。
    底部预留高度请用 :func:`_footer_space` 计算并交给 ``tight_layout(rect=...)``。
    """
    prov = _resolve_provenance(provenance)
    parts = [
        f"硬件: {prov.get('hardware', 'n/a')}",
        f"设备: {prov.get('device', 'n/a')}",
        f"因果模型: {prov.get('causal_model', 'n/a')}",
        f"嵌入模型: {prov.get('embed_model', 'n/a')}",
        f"后端: {prov.get('llm_backend', 'n/a')}",
        f"日期: {prov.get('date', 'n/a')}",
        f"版本: {prov.get('version', 'n/a')}",
    ]
    line1 = " | ".join(parts)
    extras = [str(x) for x in (extra_lines or []) if str(x).strip()]
    try:
        fig.text(0.5, 0.010, line1, ha="center", va="bottom", fontsize=6.6,
                 color="#5f5f5f", wrap=False)
        for i, txt in enumerate(extras):
            fig.text(0.5, 0.033 + i * 0.025, txt, ha="center", va="bottom", fontsize=7.0,
                     color="#37474f", weight="bold" if txt.strip().upper().startswith("DEMO")
                     else "normal", wrap=False)
    except Exception as exc:  # noqa: BLE001 - 图注失败不影响主图
        print(f"[plotting] WARN provenance footer skipped: {type(exc).__name__}")
        return ""
    return line1 + (" || " + " || ".join(extras) if extras else "")


def _footer_space(note_lines: int = 0, extra_lines: int = 0) -> float:
    """计算底部图注区需要预留的高度（figure 坐标，0~1）。

    行高约 0.022（7.6pt / figsize 高度），自 ``y=0.034`` 起向上堆叠；
    再加最底部的溯源行（y=0.010）。

    注意：这里**只计算不设置**——设置留给 ``tight_layout(rect=(x0, y0, x1, y1))``，
    否则 ``subplots_adjust`` 会被随后的 ``tight_layout`` 覆盖掉（本项目踩过一次坑：
    注释文字与 x 轴标签叠在一起）。
    """
    return round(min(0.34, 0.052 + 0.022 * (max(0, note_lines) + max(0, extra_lines))), 4)


def _wrap_cjk(text: str, max_units: int = 118) -> list[str]:
    """按"显示宽度"折行（CJK 记 2 单位、ASCII 记 1），返回行列表。"""
    lines: list[str] = []
    cur, width = "", 0
    for ch in str(text):
        w = 2 if ord(ch) > 0x2E80 else 1
        if width + w > max_units and cur:
            lines.append(cur)
            cur, width = ch, w
        else:
            cur += ch
            width += w
    if cur:
        lines.append(cur)
    return lines or [""]


def _annotate_collision_free(
    ax: "plt.Axes",
    items: Sequence[tuple],
    gap_frac: float = 0.030,
    fontsize: float = 8.2,
    n_lines: int = 1,
) -> None:
    """在近处给点加文字标注，并做一次纵向贪心去重叠（避免标签叠在一起）。

    ``items`` 元素为 ``(x, y, text, color, kwargs|None)``。
    实现：先把数据坐标转成显示坐标，按"估计文字框高度（pt）"贪心排布，
    再转回数据坐标落笔；因此即使 y 轴被压缩（准确率都挤在 0.9 附近）也不会糊成一团。
    """
    if not items:
        return

    fig = ax.get_figure()
    try:  # 真实渲染文字尺寸（最准）
        renderer = fig.canvas.get_renderer()
    except Exception:  # noqa: BLE001 - 非 Agg 后端时退回估算
        renderer = None
    y0, y1 = ax.get_ylim()
    span = (y1 - y0) or 1.0
    y0_px = ax.transData.transform((0.0, y0))[1]
    y1_px = ax.transData.transform((0.0, y1))[1]
    y_bottom_px = min(y0_px, y1_px) + 2.0
    y_top_px = max(y0_px, y1_px) - 2.0
    pad_px = fontsize * 1.45 * fig.dpi / 72.0
    placed_px: list[float] = []
    try:
        sorted_items = sorted(items, key=lambda t: (float(t[1]), float(t[0])))
    except (TypeError, ValueError):
        sorted_items = list(items)
    for x, y, text, color, kw in sorted_items:
        opts = {"ha": "center", "va": "bottom", "fontsize": fontsize, "color": color}
        if kw:
            opts.update(kw)
        text_h = fontsize * 1.35 * n_lines
        if renderer is not None:
            try:
                tmp = ax.text(0.5, 0.5, str(text), transform=ax.transAxes, **opts)
                bb = tmp.get_window_extent(renderer=renderer)
                text_h = bb.height
                tmp.remove()
            except Exception:  # noqa: BLE001
                pass
        need = text_h + pad_px       # 与前一个标签块的完整间距（含文字高度）
        px = ax.transData.transform((float(x), float(y)))
        top_y = float(px[1]) + text_h  # 标签底边不高于数据点
        for st in sorted(placed_px):
            if top_y > st - pad_px:  # 该槽位被压住 → 整体上移
                top_y = st + need
        # 顶到框外就改成落到数据点下方（避免标签被挤出坐标区）
        if top_y - text_h > y_top_px:
            below = float(px[1]) - text_h - pad_px
            for st in sorted(placed_px, reverse=True):
                if below + text_h > st - pad_px:
                    below = st - need
            if below > y_bottom_px:
                top_y = below + text_h
        placed_px.append(top_y)
        y_label_bottom = top_y - text_h
        xytext = ax.transData.inverted().transform((px[0], y_label_bottom))
        yy = min(float(xytext[1]), y1 - 0.010 * span)
        yy = max(yy, float(y))
        kw_arrow = {"arrowstyle": "-", "color": color, "lw": 0.8, "alpha": 0.65} \
            if (y_label_bottom - float(px[1])) > 0.4 * text_h else None
        ax.annotate(str(text), xy=(float(x), float(y)), xytext=(float(xytext[0]), yy),
                    textcoords="data", zorder=8,
                    arrowprops=kw_arrow, **opts)


def _save(fig: "plt.Figure", out_path: str | Path) -> Path:
    """统一保存：PNG、dpi=150、tight bbox、白底、关闭 figure。"""
    path = Path(out_path)
    if path.suffix.lower() != ".png":
        path = path.with_suffix(".png")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, **SAVE_KW)
    plt.close(fig)
    return path


def _no_data(
    out_path: str | Path,
    title: str,
    provenance: Mapping[str, Any] | None = None,
    subtitle: str = "",
    extra_lines: "Sequence[str] | None" = None,
) -> Path:
    """生成 NO DATA 占位图（同样带溯源图注），返回路径。"""
    rc_style_defaults()
    fig = plt.figure(figsize=(7.2, 4.0), dpi=FIG_DPI)
    ax = fig.add_axes((0.06, 0.24, 0.88, 0.60))
    ax.set_xticks([])
    ax.set_yticks([])
    ax.grid(False)
    for spine in ax.spines.values():
        spine.set_linestyle("--")
        spine.set_color("#b71c1c")
    ax.set_title(str(title), fontsize=13, color="#212121", pad=8)
    ax.text(0.5, 0.58, NO_DATA_TEXT, ha="center", va="center", fontsize=30,
            color="#b71c1c", weight="bold", transform=ax.transAxes)
    if subtitle:
        ax.text(0.5, 0.34, str(subtitle), ha="center", va="center", fontsize=10,
                color="#424242", transform=ax.transAxes)
    provenance_footer(fig, provenance, extra_lines=extra_lines)
    return _save(fig, out_path)


# --------------------------------------------------------------------------
# 输入清洗 / 数值工具
# --------------------------------------------------------------------------


def _as_float(value: Any) -> float | None:
    """尽量转成 Python float；不可转 / NaN / inf 返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return None
        try:
            value = float(v)
        except ValueError:
            return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def _as_int(value: Any) -> int | None:
    f = _as_float(value)
    return None if f is None else int(round(f))


def _as_bool(value: Any) -> bool | None:
    """严格布尔解析：字符串 ``"true"/"false"`` 也认，其它一律 None。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, np.integer)):
        return bool(int(value)) if int(value) in (0, 1) else None
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1", "yes", "y", "t"):
            return True
        if low in ("false", "0", "no", "n", "f"):
            return False
    return None


def _clean_floats(seq: Iterable[Any]) -> list[float]:
    """过滤 NaN/inf/非数值，返回纯 Python float 列表。"""
    out: list[float] = []
    for x in seq:
        f = _as_float(x)
        if f is not None:
            out.append(f)
    return out


def _auc_of(labels: Sequence[int], scores: Sequence[float]) -> float | None:
    """AUC（等价于 Mann-Whitney U），只依赖 numpy，缺失/单类别返回 None。"""
    y = np.asarray(list(labels), dtype=float)
    s = np.asarray(list(scores), dtype=float)
    if y.size == 0 or y.size != s.size:
        return None
    mask = np.isfinite(y) & np.isfinite(s)
    y, s = y[mask], s[mask]
    n_pos = int((y == 1).sum())
    n_neg = int(y.size - n_pos)
    if n_pos == 0 or n_neg == 0:
        return None
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(s.size, dtype=float)
    sorted_s = s[order]
    i = 0
    while i < s.size:  # 处理并列分数取平均秩
        j = i
        while j + 1 < s.size and sorted_s[j + 1] == sorted_s[i]:
            j += 1
        ranks[order[i: j + 1]] = 0.5 * (i + j) + 1.0
        i = j + 1
    return float((ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def _mean_std(values: Sequence[float]) -> tuple[float, float]:
    arr = np.asarray(list(values), dtype=float)
    if arr.size == 0:
        return float("nan"), 0.0
    if arr.size == 1:
        return float(arr[0]), 0.0
    return float(arr.mean()), float(arr.std(ddof=1))


def _autoscale_ylim(values: Sequence[float], lo: float = 0.0, hi: float = 1.0,
                    pad_frac: float = 0.10) -> tuple[float, float]:
    """按数据范围给 y 轴留边（避免 0.8~0.9 的差别被拉平看不出来）。"""
    finite = [v for v in values if v is not None and math.isfinite(v)]
    if not finite:
        return lo, hi
    vmin, vmax = min(finite), max(finite)
    span = max(vmax - vmin, 0.02)
    top = min(max(vmax + pad_frac * span, lo + 0.06), 1.02)
    bottom = max(min(vmin - pad_frac * span, lo + 0.02), -0.05)
    return bottom, top


def _fallback(*args: Any, **_kwargs: Any) -> Path:
    """函数入参无法解析时的兜底：把第一个 path 样参数当输出路径。"""
    out = next((a for a in args if isinstance(a, (str, Path))), "_fallback.png")
    return _no_data(out, "参数异常，无法绘图")


def _read_json(path: Path) -> Any:
    """读 JSON，失败返回 None（不抛错）。"""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _load_jsonl(path: Path) -> list[dict]:
    """读 JSONL，逐行容错：坏行跳过，返回 dict 列表。"""
    rows: list[dict] = []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:  # noqa: BLE001 - 单行坏数据不影响整体
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _load_summary_csv(path: Path) -> list[dict]:
    """读 summary.csv，返回原生 Python 类型 dict 列表（无 pandas 时手工兜底）。"""
    path = Path(path)
    if not path.exists():
        return []
    try:
        import pandas as pd

        df = pd.read_csv(path)
        return [
            {str(k): (None if (isinstance(v, float) and math.isnan(v)) else v)
             for k, v in rec.items()}
            for rec in df.to_dict(orient="records")
        ]
    except Exception:  # noqa: BLE001
        pass
    import csv

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            return [dict(r) for r in csv.DictReader(fh)]
    except Exception:  # noqa: BLE001
        return []


def _pairwise_pvalue_matrix(spec: Any) -> dict | None:
    """把显著性检验结果规整成 ``{"methods": [...], "pvalues": [[...]]}``。

    兼容两种契约（前者为手工矩阵，后者为 :class:`lawgate.eval.stats.CompareReport`
    的 ``to_dict`` 列表，见 ``results/<exp>/sig_pvalues.json``）::

        {"methods": ["legalgate", ...], "pvalues": [[1.0, 0.02], [0.02, 1.0]]}
        {"comparisons": [{"method_a": "legalgate", "method_b": "alwaysrag",
                          "p_bootstrap": 0.012, "p_wilcoxon": 0.008}, ...]}
    """
    if spec is None:
        return None
    obj = spec
    if isinstance(spec, (str, Path)):
        obj = _read_json(Path(spec))
    if isinstance(obj, list):
        obj = {"comparisons": obj}
    if not isinstance(obj, dict):
        return None

    methods = obj.get("methods")
    pvals = obj.get("pvalues") or obj.get("p_matrix")
    if isinstance(methods, list) and isinstance(pvals, list) and methods:
        n = len(methods)
        rows: list[list[float | None]] = []
        for i in range(n):
            row: list[float | None] = []
            for j in range(n):
                try:
                    row.append(_as_float(pvals[i][j]))
                except Exception:  # noqa: BLE001
                    row.append(None)
            rows.append(row)
        return {"methods": [str(m) for m in methods], "pvalues": rows}

    comps = obj.get("comparisons")
    if isinstance(comps, list) and comps:
        names: list[str] = []
        for c in comps:
            if not isinstance(c, Mapping):
                continue
            for key in ("method_a", "method_b"):
                v = c.get(key)
                if v is not None and str(v) not in names:
                    names.append(str(v))
        if not names:
            return None
        index = {nm: i for i, nm in enumerate(names)}
        n = len(names)
        rows = [[1.0 if i == j else None for j in range(n)] for i in range(n)]
        for c in comps:
            if not isinstance(c, Mapping):
                continue
            a, b = c.get("method_a"), c.get("method_b")
            if a is None or b is None or str(a) not in index or str(b) not in index:
                continue
            p = _as_float(c.get("p_bootstrap"))
            if p is None:
                p = _as_float(c.get("p_wilcoxon"))
            if p is None:
                p = _as_float(c.get("p"))
            if p is None:
                continue
            i, j = index[str(a)], index[str(b)]
            rows[i][j] = p
            rows[j][i] = p
        if any(rows[i][j] is not None for i in range(n) for j in range(n) if i != j):
            return {"methods": names, "pvalues": rows}
    return None


# ==========================================================================
# 1. E0 信号分布
# ==========================================================================


def _tier_of(auc: float | None) -> str:
    if auc is None:
        return "na"
    if auc >= AUC_GREEN:
        return "green"
    if auc >= AUC_AMBER:
        return "amber"
    return "red"


def _draw_signal_panel(
    ax: "plt.Axes",
    scores: Sequence[float],
    labels: Sequence[int],
    name: str,
    auc: float | None,
    title_prefix: str = "",
    legend: bool = False,
    bins: int = 26,
) -> None:
    """单个信号：need_retrieval=0/1 两条叠加直方图 + 标题里的 AUC 与三段式配色。"""
    scores = _clean_floats(scores)
    labs = [_as_int(x) for x in labels][: len(scores)]
    if len(labs) < len(scores):
        labs = labs + [None] * (len(scores) - len(labs))
    pos = [s for s, l in zip(scores, labs) if l == 1]
    neg = [s for s, l in zip(scores, labs) if l == 0]
    tier = _tier_of(auc)
    color = TIER_COLORS[tier]

    if scores:
        allv = np.asarray(scores, dtype=float)
        lo, hi = float(allv.min()), float(allv.max())
        if hi - lo < 1e-9:
            hi = lo + 1e-3
        edges = np.linspace(lo, hi, bins + 1)
        if neg:
            ax.hist(neg, bins=edges, color="#1f77b4", alpha=0.62, edgecolor="white",
                    linewidth=0.6, label=f"need_retrieval=0 (n={len(neg)})")
        if pos:
            ax.hist(pos, bins=edges, color="#d62728", alpha=0.58, edgecolor="white",
                    linewidth=0.6, label=f"need_retrieval=1 (n={len(pos)})")
        ax.set_xlabel(f"信号取值 {name}")
    else:
        ax.text(0.5, 0.5, NO_DATA_TEXT, ha="center", va="center", fontsize=16,
                color="#b71c1c", weight="bold", transform=ax.transAxes)

    ax.set_ylabel("样本数")
    auc_txt = "AUC = n/a" if auc is None else f"AUC = {auc:.3f}"
    ax.set_title(f"{title_prefix}{name}\n{auc_txt}（{TIER_LABELS[tier]}）",
                 fontsize=10.5, color=color, weight="bold")
    # 顶部色带：绿 / 琥珀 / 红，直观表达三级决策
    ax.add_patch(Rectangle((0.0, 0.94), 1.0, 0.06, transform=ax.transAxes,
                           color=color, alpha=0.22, zorder=1, clip_on=False))
    if legend and (pos or neg):
        ax.legend(loc="upper right", fontsize=8, frameon=True, framealpha=0.9,
                  borderpad=0.4)
    ax.margins(x=0.02, y=0.16)  # 顶部留白，避免 n=/CI 文字被柱顶压住


def _rule_note(aucs: Mapping[str, Any]) -> str:
    """把三段式决策规则连同本图实测结论写成一句话。"""
    ok = [k for k, v in aucs.items() if (_as_float((v or {}).get("auc")) or 0.0) >= AUC_GREEN]
    amber = [k for k, v in aucs.items()
             if AUC_AMBER <= (_as_float((v or {}).get("auc")) or 0.0) < AUC_GREEN]
    bad = [k for k, v in aucs.items() if (_as_float((v or {}).get("auc")) or 0.0) < AUC_AMBER]
    note = (f"三段式门控决策规则（按 AUC）：AUC≥{AUC_GREEN:.2f} 绿色=信号可用；"
            f"{AUC_AMBER:.2f}≤AUC<{AUC_GREEN:.2f} 琥珀色=需谨慎；"
            f"AUC<{AUC_AMBER:.2f} 红色=不可用 → 该信号不单独触发检索。")
    detail = []
    if ok:
        detail.append("本图可用信号: " + ", ".join(sorted(ok)))
    if amber:
        detail.append("需谨慎: " + ", ".join(sorted(amber)))
    if bad:
        detail.append("不可用: " + ", ".join(sorted(bad)))
    return note + ("　|　" + "；".join(detail) if detail else "")


def plot_e0_distributions(
    signals: Mapping[str, Sequence[float]],
    labels: Sequence[int],
    aucs: Mapping[str, Mapping[str, Any]] | None = None,
    out_path: str | Path = "figures/e0/e0_distributions.png",
    provenance: Mapping[str, Any] | None = None,
    buckets: Mapping[str, Sequence[int]] | Mapping[str, Sequence[float]] | None = None,
) -> Path:
    """【E0】三路因果信号的 need_retrieval 分布 + AUC + 分桶 AUC。

    参数
    ----
    signals : ``{信号名: [每条样本的信号值]}``
    labels  : ``[need_retrieval]``，长度与信号一致
    aucs    : ``{"margin": {"auc": 0.71, "n": 216}, ...}``，可含 ``by_bucket``
    buckets : ``{桶名: [每条样本的桶 id]}``（或 ``{桶名: 布尔列表}``），给了就加第二行
    """
    rc_style_defaults()
    try:
        signals = {str(k): list(v) for k, v in dict(signals or {}).items()}
    except Exception:  # noqa: BLE001
        signals = {}
    labels = list(labels or [])
    aucs = {str(k): dict(v or {}) for k, v in dict(aucs or {}).items()}
    sig_names = [k for k in signals if len(_clean_floats(signals[k])) > 0]

    # 分桶行：从 aucs[*]["by_bucket"] 与/或 buckets 入参推导
    bucket_names: list[str] = []
    for spec in aucs.values():
        bb = spec.get("by_bucket")
        if isinstance(bb, Mapping):
            bucket_names.extend(str(k) for k in bb)
    for k in (buckets or {}):
        bucket_names.append(str(k))
    bucket_names = sorted(set(bucket_names))

    if not sig_names:
        return _no_data(out_path, "E0 因果信号分布（need_retrieval）",
                        provenance, "缺少信号分数（signals 为空或全为非数值）")

    n_rows = 2 if bucket_names else 1
    width = max(4.4 * len(sig_names), 7.0)
    fig, axes = plt.subplots(n_rows, len(sig_names), figsize=(width, 4.0 * n_rows + 2.4),
                             dpi=FIG_DPI, squeeze=False)
    fig.suptitle("E0 因果信号与 need_retrieval 的分离度（三路信号 · AUC 分档）",
                 fontsize=14, weight="bold", y=0.995)

    for i, name in enumerate(sig_names):
        spec = aucs.get(name, {})
        auc = _as_float(spec.get("auc"))
        if auc is None:
            auc = _auc_of(labels, signals[name])
        _draw_signal_panel(axes[0][i], signals[name], labels, name, auc,
                           title_prefix="", legend=(i == 0))
        n = _as_int(spec.get("n")) or len(signals[name])
        ci = spec.get("auc_ci95")
        ci_txt = ""
        if isinstance(ci, (list, tuple)) and len(ci) == 2:
            lo, hi = _as_float(ci[0]), _as_float(ci[1])
            if lo is not None and hi is not None:
                ci_txt = f"\n95%CI [{lo:.3f}, {hi:.3f}]"
        axes[0][i].text(0.985, 0.995 if i > 0 else 0.72, f"n={n}{ci_txt}",
                        transform=axes[0][i].transAxes, ha="right", va="top",
                        fontsize=7.4, color="#455a64")

    if bucket_names:
        for i, name in enumerate(sig_names):
            ax = axes[1][i]
            bb = aucs.get(name, {}).get("by_bucket")
            bb = dict(bb) if isinstance(bb, Mapping) else {}
            if not bb and buckets:
                for bname, mask in dict(buckets).items():
                    m = list(mask)[: len(signals[name])]
                    sub_s = [s for s, keep in zip(signals[name], m) if _as_bool(keep) or _as_int(keep) == 1]
                    sub_l = [l for l, keep in zip(labels[: len(m)], m) if _as_bool(keep) or _as_int(keep) == 1]
                    a = _auc_of(sub_l, sub_s)
                    if a is not None:
                        bb[bname] = {"auc": a, "n": len(sub_s)}
            keys = [b for b in bucket_names if b in bb and _as_float((bb[b] or {}).get("auc")) is not None]
            if not keys:
                ax.text(0.5, 0.5, NO_DATA_TEXT, ha="center", va="center", fontsize=14,
                        color="#b71c1c", weight="bold", transform=ax.transAxes)
                ax.set_title(f"{name} 分桶 AUC", fontsize=10.5)
                continue
            vals = [_as_float(bb[k]["auc"]) or 0.0 for k in keys]
            ns = [_as_int((bb[k] or {}).get("n")) or 0 for k in keys]
            colors = [TIER_COLORS[_tier_of(v)] for v in vals]
            bars = ax.bar(range(len(keys)), vals, color=colors, alpha=0.85,
                          edgecolor="#37474f", linewidth=0.7)
            ax.axhline(AUC_GREEN, color=TIER_COLORS["green"], ls="--", lw=1.1,
                       label=f"AUC={AUC_GREEN:.2f} 可用线")
            ax.axhline(AUC_AMBER, color=TIER_COLORS["red"], ls=":", lw=1.1,
                       label=f"AUC={AUC_AMBER:.2f} 下限")
            for rect, v, nn in zip(bars, vals, ns):
                ax.text(rect.get_x() + rect.get_width() / 2, v + 0.012, f"{v:.3f}\nn={nn}",
                        ha="center", va="bottom", fontsize=7.6, color="#263238")
            ax.set_xticks(range(len(keys)))
            ax.set_xticklabels(keys)
            ax.set_ylim(0, min(1.02, max(0.85, max(vals) + 0.18)))
            ax.set_ylabel("AUC")
            ax.set_title(f"{name} 分桶 AUC（按桶复核信号稳定性）", fontsize=10, color="#37474f")
            if i == 0:
                ax.legend(loc="lower left", fontsize=7.5)

    if n_rows == 2 and len(fig.axes) >= 2 * len(sig_names):
        # 分桶柱与上排 x 轴标签之间留白
        fig.subplots_adjust(hspace=0.42)

    note_lines = _wrap_cjk(_rule_note(aucs), 132)
    bottom = _footer_space(note_lines=len(note_lines))
    fig.tight_layout(rect=(0.008, bottom, 0.992, 0.945))
    for k, line in enumerate(reversed(note_lines)):
        fig.text(0.5, 0.034 + 0.022 * k, line, ha="center", va="bottom",
                 fontsize=7.4, color="#37474f")
    provenance_footer(fig, provenance)
    return _save(fig, out_path)


# ==========================================================================
# 3. Pareto：检索率 RR vs 准确率
# ==========================================================================


def _summarize_method_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict]:
    """把 summary.csv 行按方法折叠成 {method, acc, rr, ...}（同方法多 seed 取均值）。"""
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        m = row.get("method")
        if m is None:
            continue
        grouped.setdefault(str(m), []).append(row)
    out: list[dict] = []
    for method, rs in grouped.items():
        accs = [_as_float(r.get("acc")) for r in rs]
        rrs = [_as_float(r.get("rr")) for r in rs]
        accs = [a for a in accs if a is not None]
        rrs = [a for a in rrs if a is not None]
        acc_m, acc_s = _mean_std(accs) if accs else (float("nan"), 0.0)
        rr_m, rr_s = _mean_std(rrs) if rrs else (float("nan"), 0.0)
        rec: dict[str, Any] = {
            "method": method,
            "acc": acc_m,
            "rr": rr_m,
            "acc_std": acc_s,
            "rr_std": rr_s,
            "n_seeds": len(rs),
        }
        for key in ("lac", "tvc", "p50_ms", "p95_ms", "mean_tokens", "n"):
            vals = [_as_float(r.get(key)) for r in rs]
            vals = [v for v in vals if v is not None]
            if vals:
                rec[key] = float(np.mean(vals))
        out.append(rec)
    return out


def plot_pareto(
    rows: Sequence[Mapping[str, Any]],
    out_path: str | Path = "figures/e2/pareto_rr_acc.png",
    provenance: Mapping[str, Any] | None = None,
    curve_method: str = "legalgate",
    tau_scales: Sequence[float] = (0.5, 0.75, 1.0, 1.25),
    **kwargs: Any,
) -> Path:
    """【E2】Pareto 图：x=检索率 RR，y=准确率；曲线方法连成 τ 缩放曲线。

    额外入参
    --------
    pvalue_matrix : 可选，显著性矩阵 JSON 路径或已解析 dict（画在角落）。
    annotate_lines : 默认 True，画 "Always-RAG −1%" 与 "RR ≤ 40% Always-RAG" 参考线。
    """
    rc_style_defaults()
    pval = _pairwise_pvalue_matrix(kwargs.get("pvalue_matrix"))
    annotate = bool(kwargs.get("annotate_lines", True))
    # 单条 summary 记录（有 method/acc/rr）也接受
    if isinstance(rows, Mapping):
        rows = [rows]

    pts: list[dict] = []
    for row in list(rows or []):
        if not isinstance(row, Mapping):
            continue
        if "acc" in row and "rr" in row:
            acc, rr = _as_float(row.get("acc")), _as_float(row.get("rr"))
            if acc is None or rr is None:
                continue
            pts.append(
                {
                    "method": str(row.get("method", "unknown")),
                    "acc": acc,
                    "rr": rr,
                    "acc_std": _as_float(row.get("acc_std")) or 0.0,
                    "rr_std": _as_float(row.get("rr_std")) or 0.0,
                    "tau_scale": _as_float(row.get("tau_scale")),
                }
            )
        else:  # 逐条记录 → 聚合
            pts.extend(_summarize_method_rows([row]))

    pts = [p for p in pts if math.isfinite(p["acc"]) and math.isfinite(p["rr"])]
    if not pts:
        return _no_data(out_path, "E2 Pareto 前沿（检索率 vs 准确率）", provenance,
                        "缺少 acc/rr 结果：需要 results/<exp>/summary.csv")

    # 同一方法多条（τ 缩放点 / 多 seed）→ 取均值当方法点
    by_method: dict[str, list[dict]] = {}
    for p in pts:
        by_method.setdefault(p["method"], []).append(p)

    # 参考基线：优先 Always-RAG（RR≈1），否则取"最贵"的方法
    base_pts = [p for p in pts if p["rr"] >= 0.999]
    if base_pts:
        alw = [p for p in base_pts if p["method"].lower() in ("alwaysrag", "always_rag", "always-rag")]
        base_pool = alw or base_pts
        base = max(base_pool, key=lambda p: p["acc"])
        base_acc, base_rr, base_name = base["acc"], base["rr"], method_label(base["method"])
        base_key = base["method"]
    else:  # 没有 RR≈1 的方法就退化为"最贵"的那个
        top = max(pts, key=lambda p: p["rr"])
        base_acc, base_rr, base_name = top["acc"], top["rr"], method_label(top["method"])
        base_key = top["method"]

    order = sorted(by_method)
    points: list[dict] = []
    for idx, method in enumerate(order):
        group = by_method[method]
        acc_m, _ = _mean_std([g["acc"] for g in group])
        rr_m, _ = _mean_std([g["rr"] for g in group])
        acc_s = float(np.mean([g["acc_std"] for g in group])) if any(g["acc_std"] for g in group) else 0.0
        rr_s = float(np.mean([g["rr_std"] for g in group])) if any(g["rr_std"] for g in group) else 0.0
        points.append({"method": method, "acc": acc_m, "rr": rr_m, "acc_std": acc_s,
                       "rr_std": rr_s, "color": METHOD_COLORS[idx % len(METHOD_COLORS)]})

    curve = sorted([p for p in pts if p["method"] == str(curve_method)], key=lambda p: p["rr"])
    if not curve:
        # 没有 τ 缩放点：就用该方法的均值点单点成线，图仍可读
        curve = [p for p in points if p["method"] == str(curve_method)]

    # 布局：主图留出右侧图例与底部注释区
    prov_lines = 1
    note_lines = _wrap_cjk(f"越靠左上越好：低检索率 + 高准确率。参考基线 {base_name}："
                           f"Acc={base_acc:.3f}、RR={base_rr:.3f}；"
                           f"1% 容忍线 = {base_acc - 0.01:.3f}。τ 缩放曲线为 {method_label(curve_method)} "
                           f"在不同阈值缩放下的工作点。", 118)
    pval_block = None
    if pval:
        rows_txt: list[str] = []
        n_pm = len(pval["methods"])
        header = "配对显著性 p 值（<.01 视为显著）"
        for i, m in enumerate(pval["methods"]):
            cells = []
            for j in range(n_pm):
                v = pval["pvalues"][i][j]
                if v is None:
                    cells.append("--")
                elif float(v) < 0.01:
                    cells.append("<.01")
                else:
                    cells.append(f"{float(v):.3f}")
            rows_txt.append(f"{method_label(m):>12s} " + " ".join(f"{c:>6s}" for c in cells))
        pval_block = header + "\n" + "\n".join(rows_txt)

    n_note_lines = len(note_lines) + (len(pval_block.splitlines()) if pval_block else 0) + prov_lines
    bottom = min(0.34, 0.045 + 0.0225 * n_note_lines)
    fig = plt.figure(figsize=(11.0, 7.0), dpi=FIG_DPI)
    ax = fig.add_axes((0.082, bottom + 0.045, 0.575, 0.985 - 0.055 - bottom - 0.045))
    ax.set_title("E2 Pareto 前沿：检索率 vs 答题准确率（τ 缩放曲线）",
                 fontsize=13, weight="bold", pad=10)

    # y 轴范围：含上界点到 1.0 的留白；准确率都挤在 0.9 附近，留白宁可多一点
    ref_accs = [p["acc"] for p in points]
    y_lo, y_hi = _autoscale_ylim(ref_accs + [base_acc - 0.01], pad_frac=0.30)
    y_hi = min(1.004, y_hi)
    rr_all = [p["rr"] for p in pts]
    x_hi = min(1.06, max(rr_all) * 1.10 + 0.02)

    # 参考线 2：Always-RAG − 1%
    acc_target = base_acc - 0.01
    if annotate:
        ax.axhline(acc_target, color="#c62828", ls=":", lw=1.5, zorder=1)
        ax.annotate(f"{base_name} − 1% = {acc_target:.3f}",
                    xy=(x_hi, acc_target), xytext=(x_hi - 0.006, acc_target - 0.004),
                    fontsize=8.4, color="#c62828", ha="right", va="top", zorder=9)
    # 参考线 1：RR ≤ 40% of Always-RAG
    rr_target = 0.4 * base_rr
    if annotate and base_rr > 0:
        ax.axvspan(0.0, rr_target, color="#2e7d32", alpha=0.075, zorder=0)
        ax.axvline(rr_target, color="#2e7d32", ls="--", lw=1.4, zorder=1)
        ax.annotate(f"RR ≤ 40% of {base_name}（RR≤{rr_target:.3f}）",
                    xy=(rr_target, 0.86), xytext=(rr_target + 0.025, 0.99),
                    textcoords=("data", "axes fraction"), ha="left", va="top",
                    fontsize=8.4, color="#2e7d32", zorder=9,
                    arrowprops={"arrowstyle": "->", "color": "#2e7d32", "lw": 1.0,
                                "connectionstyle": "arc3,rad=-0.2"})

    # τ 缩放曲线（连成线）
    if len(curve) >= 2:
        ax.plot([c["rr"] for c in curve], [c["acc"] for c in curve], "-",
                color="#0d47a1", lw=1.8, alpha=0.75, zorder=3,
                label=f"{method_label(curve_method)} τ 缩放曲线")
    label_scales = list(tau_scales)
    for i, c in enumerate(curve):
        scale = c["tau_scale"]
        if scale is None and len(curve) == len(label_scales):
            scale = label_scales[i]
        if scale is not None:
            ax.annotate(f"τ×{scale:g}", xy=(c["rr"], c["acc"]), xytext=(0, -26),
                        textcoords="offset points", ha="center", va="top", fontsize=7.8,
                        color="#0d47a1", zorder=6)

    # 各方法点（带误差棒，若有 std）
    label_items: list[tuple] = []
    for p in points:
        is_oracle = p["method"] == "oracle"
        ax.errorbar(
            p["rr"], p["acc"],
            xerr=p["rr_std"] if p["rr_std"] > 0 else None,
            yerr=p["acc_std"] if p["acc_std"] > 0 else None,
            fmt="D" if is_oracle else ("o" if p["method"] == str(curve_method) else "s"),
            ms=9 if p["method"] == str(curve_method) else 7.5,
            mfc=p["color"], mec="white", mew=1.1,
            color=p["color"], ecolor=p["color"], elinewidth=1.1, capsize=3.2,
            alpha=0.95, zorder=5, clip_on=False,
            label=method_label(p["method"]) + ("（曲线方法）" if p["method"] == str(curve_method) else ""),
        )
        txt = method_label(p["method"]) + (f"（上界 {p['acc']:.3f}）" if is_oracle else f" {p['acc']:.3f}")
        label_items.append((p["rr"], p["acc"], txt, p["color"], None))

    ax.set_xlabel("检索率 RR（触发检索的样本占比）")
    ax.set_ylabel("答题准确率 Acc")
    ax.set_xlim(0.0, x_hi)
    ax.set_ylim(y_lo, y_hi)
    ax.grid(True, alpha=0.25)
    # 点抽稀：曲线方法只保留均值点标注，避免与 τ 标签挤在一起
    _annotate_collision_free(ax, label_items, gap_frac=0.052, fontsize=8.0, n_lines=1)

    handles, labels = ax.get_legend_handles_labels()
    handles.append(Line2D([0], [0], color="#c62828", ls=":", lw=1.5))
    labels.append(f"{base_name} − 1% 容忍线")
    if annotate and base_rr > 0:
        handles.append(Line2D([0], [0], color="#2e7d32", ls="--", lw=1.4))
        labels.append(f"RR ≤ 40% of {base_name}")
    ax.legend(handles, labels, loc="center left", bbox_to_anchor=(1.015, 0.5),
              frameon=True, framealpha=0.95, fontsize=8.6, title="方法 / 参考线",
              title_fontsize=8.8)

    # 底部注释（折行后自下而上排布），最下面是溯源图注
    yy = 0.016
    for line in reversed(note_lines):
        fig.text(0.014, yy, line, ha="left", va="bottom", fontsize=7.6, color="#37474f")
        yy += 0.0215
    if pval_block:
        fig.text(0.988, yy + 0.004, pval_block, ha="right", va="bottom", fontsize=6.4,
                 color="#37474f",
                 bbox={"boxstyle": "round,pad=0.35", "facecolor": "white",
                       "edgecolor": "#b0bec5", "alpha": 0.95})
    provenance_footer(fig, provenance)
    return _save(fig, out_path)


# ==========================================================================
# 4. 消融实验
# ==========================================================================

#: 四个消融组：A1 去信道B / A2 单一全局 τ / A3 信号开关 / A4 草案长度 k
ABLATION_GROUPS: list[tuple[str, str]] = [
    ("A1", "A1 禁用信道B"),
    ("A2", "A2 单一全局τ"),
    ("A3", "A3 信号开关"),
    ("A4", "A4 草案长度k"),
]


def _ablation_metric(row: Mapping[str, Any], metric: str) -> float | None:
    """从消融行里取指标：优先显式键，其次从 acc/rr 等派生，最后看嵌套 metrics。"""
    key_alias = {
        "lac": ("lac", "lac_rate", "LAC"),
        "tvc": ("tvc", "tvc_rate", "TVC"),
        "rr": ("rr", "retrieval_rate", "retention_rate"),
        "acc": ("acc", "accuracy", "correct_rate"),
    }
    for k in key_alias.get(metric, (metric,)):
        v = _as_float(row.get(k))
        if v is not None:
            return v
    nested = row.get("metrics")
    if isinstance(nested, Mapping):
        for k in key_alias.get(metric, (metric,)):
            v = _as_float(nested.get(k))
            if v is not None:
                return v
    # 由逐条记录派生
    correct = _as_bool(row.get("correct"))
    if correct is not None:
        if metric == "acc":
            return 1.0 if correct else 0.0
        if metric in ("rr", "lac"):
            used = _as_bool(row.get("retrieval_used"))
            if used is None:
                return None
            val = 1.0 if used else 0.0
            return (1.0 - val) if metric == "lac" else val
        if metric == "tvc":
            tvc = _as_int(row.get("tvc"))
            return None if tvc is None else 1.0 if tvc == 1 else 0.0
    return None


def plot_ablation(
    df_rows: Sequence[Mapping[str, Any]],
    out_path: str | Path = "figures/e3/ablation.png",
    provenance: Mapping[str, Any] | None = None,
    metric: str = "lac",
    metrics: Sequence[str] | None = None,
) -> Path:
    """【E3】消融实验分组柱状图（4 组 × 变体，柱上标数值）。

    入参行形如 ``{"group": "A1", "variant": "full", "metric": "lac", "value": 0.31}``
    或 ``{"group": "A2", "arm": "single_tau", "lac": 0.28, "tvc": 0.66, "rr": 0.35}``
    （也接受逐条评测记录，会自动派生指标）。
    """
    rc_style_defaults()
    metric_list = [str(m) for m in (metrics or (metric, "tvc", "rr"))]
    rows = [r for r in (df_rows or []) if isinstance(r, Mapping)]
    if not rows:
        return _no_data(out_path, "E3 消融实验（4 组）", provenance,
                        "缺少消融结果：需要 results/e3/*.jsonl 或 summary.csv")

    # 归一化成 {group: {variant: {metric: value}}}
    table: dict[str, dict[str, dict[str, float]]] = {}
    for row in rows:
        group = str(row.get("group") or row.get("ablation") or row.get("group_id") or "").strip()
        if not group:
            continue
        gkey = group.split()[0].upper()[:2]
        if gkey not in dict(ABLATION_GROUPS):
            continue
        variant = str(row.get("variant") or row.get("arm") or row.get("config") or row.get("method") or "full")
        for m in metric_list:
            val = _as_float(row.get(m)) if row.get(m) is not None else _ablation_metric(row, m)
            if val is not None:
                table.setdefault(gkey, {}).setdefault(variant, {})[m] = val

    if not table:
        return _no_data(out_path, "E3 消融实验（4 组）", provenance,
                        "结果文件中没有 A1–A4 分组字段（group/variant）")

    groups = [g for g, _ in ABLATION_GROUPS if g in table]
    fig, axes = plt.subplots(1, len(metric_list), figsize=(5.8 * len(metric_list), 5.8),
                             dpi=FIG_DPI, squeeze=False)
    fig.suptitle("E3 消融实验：信道B / 全局τ / 信号开关 / 草案长度k 的影响",
                 fontsize=13.5, weight="bold", y=0.985)
    higher_better = {"acc": True, "rr": False, "lac": False, "tvc": True}
    for ax, m in zip(axes[0], metric_list):
        variants: list[str] = []
        for g in groups:
            for v in table[g]:
                if v not in variants:
                    variants.append(v)
        variants = variants[:6] or ["full"]
        n_v = len(variants)
        width = 0.8 / max(1, n_v)
        xs = np.arange(len(groups), dtype=float)
        any_bar = False
        for vi, v in enumerate(variants):
            ys, xs_i = [], []
            for gi, g in enumerate(groups):
                val = table[g].get(v, {}).get(m)
                if val is None:
                    continue
                xs_i.append(xs[gi] + (vi - (n_v - 1) / 2.0) * width)
                ys.append(val)
            if not ys:
                continue
            any_bar = True
            bars = ax.bar(xs_i, ys, width=width * 0.92,
                          color=METHOD_COLORS[vi % len(METHOD_COLORS)], alpha=0.88,
                          edgecolor="#37474f", linewidth=0.7, label=v)
            for rect, val in zip(bars, ys):
                ax.text(rect.get_x() + rect.get_width() / 2,
                        rect.get_height() + 0.012, f"{val:.3f}",
                        ha="center", va="bottom", fontsize=7.4, color="#263238", rotation=0)
        if not any_bar:
            ax.text(0.5, 0.5, NO_DATA_TEXT, ha="center", va="center", fontsize=16,
                    color="#b71c1c", weight="bold", transform=ax.transAxes)
        ax.set_xticks(xs)
        ax.set_xticklabels([dict(ABLATION_GROUPS)[g] for g in groups], fontsize=9)
        arrow = "↑ 越高越好" if higher_better.get(m, True) else "↓ 越低越好"
        ax.set_ylabel(m.upper())
        ax.set_title(f"指标 {m.upper()}（{arrow}）", fontsize=11, color="#37474f")
        vals_all = [vv for g in groups for vv in (table[g].get(v, {}).get(m) for v in variants)
                    if vv is not None]
        ax.set_ylim(0, min(1.05, max(1.0, max(vals_all) * 1.22 + 0.05)) if vals_all else 1.0)
        axh, axl = ax.get_legend_handles_labels()
        if axh:  # 无数据时跳过图例，避免 matplotlib UserWarning
            ax.legend(loc="upper right", fontsize=8, ncol=2 if n_v > 3 else 1)
    note_lines = _wrap_cjk(
        "每组以完整方案（full）为基线；A1=禁用信道B（去掉案由/程序信道），"
        "A2=单一全局阈值 τ（去掉分桶 τ_b），A3=关闭某一因果信号（只留单路信号），"
        "A4=改变草案候选长度 k。柱上数字为对应指标实测值；箭头表示该指标的优化方向。", 128)
    fig.tight_layout(rect=(0.01, max(_footer_space(note_lines=len(note_lines)), 0.115), 0.99, 0.945))
    for k, line in enumerate(reversed(note_lines)):
        fig.text(0.5, 0.034 + 0.022 * k, line, ha="center", va="bottom",
                 fontsize=7.6, color="#37474f")
    provenance_footer(fig, provenance)
    return _save(fig, out_path)


# ==========================================================================
# 5. 多轮对话趋势
# ==========================================================================


def plot_multiturn_trend(
    rows: Sequence[Mapping[str, Any]],
    out_path: str | Path = "figures/e4/multiturn_trend.png",
    provenance: Mapping[str, Any] | None = None,
) -> Path:
    """【E4】多轮趋势：上panel 准确率 Acc vs turn_id(0/1/2)，下panel 检索率 RR + 槽位继承错误率柱。"""
    rc_style_defaults()
    rows = [r for r in (rows or []) if isinstance(r, Mapping)]
    if not rows:
        return _no_data(out_path, "E4 多轮对话趋势（Acc/RR vs turn）", provenance,
                        "缺少多轮结果：需要 results/e4/*.jsonl（含 turn_id 字段）")

    # 逐条记录按 turn 聚合；也接受已聚合行（含 acc/rr/slots_err_rate）
    buckets: dict[int, dict[str, list[float]]] = {}
    agg_rows: dict[int, Mapping[str, Any]] = {}
    for row in rows:
        t = _as_int(row.get("turn_id", row.get("turn")))
        if t is None:
            continue
        agg_keys = {k: _as_float(row.get(k)) for k in ("acc", "rr", "slot_inherit_err_rate", "slots_err_rate")}
        if all(v is None for v in agg_keys.values()):
            cell = buckets.setdefault(t, {"acc": [], "rr": [], "err": []})
            correct = _as_bool(row.get("correct"))
            if correct is not None:
                cell["acc"].append(1.0 if correct else 0.0)
            used = _as_bool(row.get("retrieval_used"))
            if used is not None:
                cell["rr"].append(1.0 if used else 0.0)
            inher = _as_bool(row.get("slots_inherited_ok"))
            if inher is not None:
                cell["err"].append(0.0 if inher else 1.0)
        else:
            agg_rows[t] = row

    turns = sorted(set(list(buckets) + list(agg_rows)))
    if not turns:
        return _no_data(out_path, "E4 多轮对话趋势（Acc/RR vs turn）", provenance,
                        "结果文件里没有可解析的 turn_id 字段")

    accs: list[float | None] = []
    rrs: list[float | None] = []
    errs: list[float | None] = []
    ns: list[int] = []
    for t in turns:
        if t in agg_rows:
            acc = _as_float(agg_rows[t].get("acc"))
            rr = _as_float(agg_rows[t].get("rr"))
            err = _as_float(agg_rows[t].get("slot_inherit_err_rate", agg_rows[t].get("slots_err_rate")))
            n = _as_int(agg_rows[t].get("n")) or 0
        else:
            acc = _mean_std(buckets[t]["acc"])[0] if buckets[t]["acc"] else None
            rr = _mean_std(buckets[t]["rr"])[0] if buckets[t]["rr"] else None
            err = _mean_std(buckets[t]["err"])[0] if buckets[t]["err"] else None
            n = max(len(buckets[t]["acc"]), len(buckets[t]["rr"]))
        accs.append(acc)
        rrs.append(rr)
        errs.append(err)
        ns.append(n)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9.0, 7.0), dpi=FIG_DPI, sharex=True)
    fig.suptitle("E4 多轮对话趋势：准确率 / 检索率 / 槽位继承错误率随轮次变化",
                 fontsize=13, weight="bold", y=0.985)
    xs = np.arange(len(turns))

    finite_acc = [a for a in accs if a is not None]
    if finite_acc:
        ax1.plot(xs, accs, "-o", color="#1f77b4", lw=2.0, ms=8, label="准确率 Acc",
                 markeredgecolor="white", markeredgewidth=1.2)
        ax1.set_ylim(*_autoscale_ylim(finite_acc, pad_frac=0.60))
        ax1.axhline(0.0, color="#9e9e9e", lw=0.6)
        low = 0.0 if min(finite_acc) > 0.5 else ax1.get_ylim()[0]
        low = 0.0 if min(finite_acc) > 0.5 else ax1.get_ylim()[0]
        lo_y, hi_y = ax1.get_ylim()
        for x, a, n in zip(xs, accs, ns):
            if a is None:
                continue
            # 挨着下沿的标注会挡住曲线，统一放到数据点下方
            ax1.annotate(f"{a:.3f}" + (f"  n={n}" if n else ""), xy=(x, a),
                         xytext=(0, 10 if (a - low) < 0.28 * (hi_y - lo_y) else -14),
                         textcoords="offset points", ha="center", va="bottom",
                         fontsize=8.2, color="#0d47a1", zorder=6)
    else:
        ax1.text(0.5, 0.5, NO_DATA_TEXT, ha="center", va="center", fontsize=18,
                 color="#b71c1c", weight="bold", transform=ax1.transAxes)
    ax1.set_ylabel("准确率 Acc")
    ax1.set_title("准确率随轮次的保持情况（检查多轮是否退化）", fontsize=10.5, color="#37474f")
    ax1.legend(loc="lower left", fontsize=9)

    finite_rr = [r for r in rrs if r is not None]
    if finite_rr:
        ax2.plot(xs, rrs, "-s", color="#2ca02c", lw=2.0, ms=8, label="检索率 RR",
                 markeredgecolor="white", markeredgewidth=1.2)
        for x, r in zip(xs, rrs):
            if r is not None:
                ax2.annotate(f"{r:.3f}", xy=(x, r), xytext=(0, 10),
                             textcoords="offset points", ha="center", fontsize=8.2,
                             color="#1b5e20")
    ax2.set_ylabel("检索率 RR", color="#1b5e20")
    ax2.set_xlabel("对话轮次 turn_id")
    ax2.set_title("检索率与槽位继承错误率（柱=继承失败率，右轴）", fontsize=10.5, color="#37474f")
    ax2.set_xticks(xs)
    ax2.set_xticklabels([str(t) for t in turns])
    if finite_rr:
        lo, hi = _autoscale_ylim(finite_rr, pad_frac=0.30)
        ax2.set_ylim(lo, hi)

    ax3 = ax2.twinx()
    err_pairs = [(x, e) for x, e in zip(xs, errs) if e is not None]
    if err_pairs:
        ex = [p[0] for p in err_pairs]
        ev = [p[1] for p in err_pairs]
        bars = ax3.bar(ex, ev, width=0.42, color="#ef6c00", alpha=0.42,
                       edgecolor="#e65100", linewidth=0.8, label="槽位继承错误率")
        for rect, e in zip(bars, ev):
            ax3.text(rect.get_x() + rect.get_width() / 2, rect.get_height(),
                     f"{e:.3f}", ha="center", va="bottom", fontsize=7.8, color="#e65100")
        ax3.set_ylim(0, min(1.0, max(0.12, max(ev) * 1.6)))
        ax3.set_ylabel("槽位继承错误率", color="#e65100")
        ax3.grid(False)
    else:
        ax3.set_yticks([])
        ax3.set_ylabel("槽位继承错误率（无数据）", color="#9e9e9e", fontsize=9)

    h2, l2 = ax2.get_legend_handles_labels()
    h3, l3 = ax3.get_legend_handles_labels()
    if l2 or l3:  # 没有画任何曲线/柱时不建图例（否则 matplotlib 会发 UserWarning）
        ax2.legend(h2 + h3, l2 + l3, loc="lower left", fontsize=8.6)
    note_lines = _wrap_cjk(
        "轮次 0/1/2 指同一会话中第 1/2/3 个问题；准确率与检索率按轮次平均。"
        "槽位继承错误率 = 未正确沿用前轮槽位的问答占比（柱），多轮退化主要来自它。", 112)
    fig.tight_layout(rect=(0.012, 0.128, 0.988, 0.945))
    for k, line in enumerate(reversed(note_lines)):
        fig.text(0.5, 0.034 + 0.022 * k, line, ha="center", va="bottom",
                 fontsize=7.6, color="#37474f")
    provenance_footer(fig, provenance)
    return _save(fig, out_path)


# ==========================================================================
# 6. 陷阱类型 TVC
# ==========================================================================

#: 四种陷阱类型
TRAP_LABELS: dict[str, str] = {
    "T1": "T1 旧法条权\n（废止法源）",
    "T2": "T2 案由错配\n（相似但无关）",
    "T3": "T3 当事人替换\n（角色错位）",
    "T4": "T4 嵌套诱导\n（多跳干扰）",
}


def plot_tvc_by_trap(
    tvc: Mapping[str, Mapping[str, Any]],
    out_path: str | Path = "figures/e5/tvc_by_trap.png",
    provenance: Mapping[str, Any] | None = None,
) -> Path:
    """【E5】按陷阱类型 T1–T4 的堆叠柱状图（按方法堆叠，TVC∈[0,1]，1.0 处虚线参考）。"""
    rc_style_defaults()
    tvc = {str(k): dict(v or {}) for k, v in dict(tvc or {}).items()
           if isinstance(v, Mapping)}
    traps = [t for t in ("T1", "T2", "T3", "T4") if t in tvc]
    if not traps:
        return _no_data(out_path, "E5 各陷阱类型的正确处置率 TVC", provenance,
                        "缺少 results/e5/tvc_by_trap.json 或其中无 T1–T4")

    methods: list[str] = []
    for t in traps:
        for m in tvc[t]:
            if str(m) not in methods:
                methods.append(str(m))
    fig, ax = plt.subplots(figsize=(10.0, 6.2), dpi=FIG_DPI)
    ax.set_title("E5 陷阱鲁棒性：各陷阱类型的正确处置率 TVC（按方法堆叠）",
                 fontsize=13, weight="bold", pad=12)

    xs = np.arange(len(traps), dtype=float)
    width = 0.8 / max(1, len(methods))
    for mi, m in enumerate(methods):
        ys = [_as_float(tvc[t].get(m)) or 0.0 for t in traps]
        pos = xs + (mi - (len(methods) - 1) / 2.0) * width
        bars = ax.bar(pos, ys, width=width * 0.92, bottom=0.0,
                      color=METHOD_COLORS[mi % len(METHOD_COLORS)], alpha=0.9,
                      edgecolor="#37474f", linewidth=0.8, label=method_label(m))
        for rect, v in zip(bars, ys):
            ax.text(rect.get_x() + rect.get_width() / 2,
                    min(0.995, rect.get_height() + 0.012), f"{v:.2f}",
                    ha="center", va="bottom", fontsize=7.8, color="#263238")

    ax.axhline(1.0, color="#c62828", ls="--", lw=1.5, label="TVC = 1.0（完全正确处置）")
    ax.set_xticks(xs)
    ax.set_xticklabels([TRAP_LABELS.get(t, t) for t in traps], fontsize=9.5)
    ax.set_ylabel("正确处置率 TVC")
    ax.set_ylim(0, 1.14)
    # 方法多时把图例放到图内右上（最空的位置），避免与底部注释、x 轴标签争空间
    ax.legend(loc="upper right", fontsize=8.2, ncol=1 if len(methods) <= 5 else 2,
              title="方法 / 参考线", title_fontsize=8.4, framealpha=0.92)
    note_lines = _wrap_cjk(
        "TVC=1 表示对该类陷阱给出正确处置（拒答 / 换法条 / 澄清），柱越接近虚线越鲁棒；"
        "标签数值为该方法在该陷阱类型上的 TVC。T1–T4 为四类典型陷阱，"
        "跨方法对比可看出门控是否真的按陷阱类型分流。", 108)
    fig.tight_layout(rect=(0.01, _footer_space(note_lines=len(note_lines)), 0.99, 0.935))
    for k, line in enumerate(reversed(note_lines)):
        fig.text(0.5, 0.034 + 0.024 * k, line, ha="center", va="bottom",
                 fontsize=7.6, color="#37474f")
    provenance_footer(fig, provenance)
    return _save(fig, out_path)


# ==========================================================================
# 7. 混淆矩阵
# ==========================================================================


def plot_confusion(
    matrix: Sequence[Sequence[Any]],
    labels: Sequence[str],
    subtypes: Sequence[str] | None = None,
    out_path: str | Path = "figures/e6/confusion.png",
    provenance: Mapping[str, Any] | None = None,
    prf: Mapping[str, Any] | None = None,
) -> Path:
    """【E6】答案核验混淆矩阵热力图（格内计数 + 行归一化占比，角落写 P/R/F1）。"""
    rc_style_defaults()
    try:
        arr = np.asarray([[(_as_float(v) or 0.0) for v in row] for row in (matrix or [])],
                         dtype=float)
    except Exception:  # noqa: BLE001
        arr = np.zeros((0, 0))
    labels = [str(x) for x in (labels or [])]
    subtypes = [str(x) for x in (subtypes or [])]

    if arr.size == 0 or arr.shape[0] == 0 or arr.shape[1] == 0:
        return _no_data(out_path, "E6 答案核验混淆矩阵", provenance,
                        "缺少 results/e6/confusion.json 或 matrix 为空")
    if not labels:
        labels = [f"类{i}" for i in range(arr.shape[0])]
    n = int(arr.shape[0])
    tick_labels = [f"{labels[i]}\n（{i}）" if i < len(labels) else f"类{i}" for i in range(n)]
    n_sub = int(arr.shape[1])
    sub_labels = [f"{subtypes[j]}" if j < len(subtypes) else f"S{j + 1}" for j in range(n_sub)]

    row_sums = arr.sum(axis=1, keepdims=True)
    row_norm = np.divide(arr, row_sums, out=np.zeros_like(arr), where=row_sums > 0)

    fig = plt.figure(figsize=(9.4 + 0.22 * n_sub, 7.6), dpi=FIG_DPI)
    ax = fig.add_axes((0.18, 0.26, 0.58, 0.60))
    im = ax.imshow(arr, cmap="Blues", aspect="auto", vmin=0.0,
                   vmax=max(1.0, float(arr.max())))
    ax.set_xticks(range(n_sub))
    ax.set_xticklabels(sub_labels, fontsize=9.5)
    ax.set_yticks(range(n))
    ax.set_yticklabels(tick_labels, fontsize=9.5)
    ax.set_xlabel("预测子类型（V1–V4）")
    ax.set_ylabel("真实标签（人工核验类别）")
    ax.set_title("E6 答案核验混淆矩阵（格内：样本数 / 行归一化占比）",
                 fontsize=12.5, weight="bold", pad=12)
    ax.grid(False)
    span = max(1.0, float(arr.max()))
    for i in range(n):
        for j in range(n_sub):
            cnt = arr[i, j]
            frac = row_norm[i, j]
            txt = f"{int(round(cnt))}\n{frac:.0%}" if row_sums[i, 0] > 0 else f"{int(round(cnt))}\n-"
            ax.text(j, i, txt, ha="center", va="center", fontsize=9.4,
                    color="white" if cnt > 0.62 * span else "#1a237e")
    cax = fig.add_axes((0.785, 0.26, 0.024, 0.60))
    cbar = fig.colorbar(im, cax=cax)
    cbar.set_label("样本数", fontsize=9)

    n_prf_lines = 0
    if prf:
        prf = dict(prf)
        p = _as_float(prf.get("precision"))
        r = _as_float(prf.get("recall"))
        f1 = _as_float(prf.get("f1"))
        lines = [
            "核验指标（正类=非法/需拦截）",
            f"Precision = {p:.4f}" if p is not None else "Precision = n/a",
            f"Recall    = {r:.4f}" if r is not None else "Recall    = n/a",
            f"F1        = {f1:.4f}" if f1 is not None else "F1        = n/a",
        ]
        cm = [("TP", prf.get("tp")), ("FP", prf.get("fp")), ("FN", prf.get("fn")), ("TN", prf.get("tn"))]
        cm_txt = "  ".join(f"{k}={_as_int(v)}" if _as_int(v) is not None else f"{k}=n/a" for k, v in cm)
        lines.append(cm_txt)
        note = prf.get("threshold_note")
        if note:
            lines.extend(_wrap_cjk(f"阈值说明: {note}", 84))
        n_prf_lines = len(lines)
        fig.text(0.02, 0.215, "\n".join(lines), ha="left", va="top", fontsize=7.6,
                 color="#1b5e20",
                 bbox={"boxstyle": "round,pad=0.45", "facecolor": "#f1f8e9",
                       "edgecolor": "#558b2f", "alpha": 0.95})
    note_lines = _wrap_cjk("行=真实标签，列=预测子类型；格内上方为样本数、下方为按行归一化占比（召回视角），"
                           "对角线越深越好；左侧绿框给出正类（非法/需拦截）的 P/R/F1。", 104)
    # 注释区自下而上排布，最下面是溯源图注（两者不重叠）
    for k, line in enumerate(reversed(note_lines)):
        fig.text(0.5, 0.045 + 0.024 * k, line, ha="center", va="bottom",
                 fontsize=7.6, color="#37474f")
    provenance_footer(fig, provenance)
    return _save(fig, out_path)


# ==========================================================================
# 8. 延迟预算
# ==========================================================================


def plot_latency_budget(
    rows: Sequence[Mapping[str, Any]],
    out_path: str | Path = "figures/e7/latency_budget.png",
    provenance: Mapping[str, Any] | None = None,
) -> Path:
    """【E7】各方法 P50 / P95 延迟分组柱状图（对数 y 轴，柱上标 ms 数值）。"""
    rc_style_defaults()
    rows = [r for r in (rows or []) if isinstance(r, Mapping)]
    if not rows:
        return _no_data(out_path, "E7 延迟预算（P50/P95）", provenance,
                        "缺少延迟结果：需要 summary.csv(p50_ms/p95_ms) 或含 latency_ms 的 JSONL")

    lat: dict[str, list[float]] = {}
    agg: dict[str, dict[str, float]] = {}
    for row in rows:
        method = row.get("method")
        if method is None:
            continue
        method = str(method)
        p50 = _as_float(row.get("p50_ms", row.get("latency_p50_ms")))
        p95 = _as_float(row.get("p95_ms", row.get("latency_p95_ms")))
        single = _as_float(row.get("latency_ms"))
        if p50 is not None or p95 is not None:
            agg.setdefault(method, {})
            if p50 is not None:
                agg[method]["p50"] = p50
            if p95 is not None:
                agg[method]["p95"] = p95
        elif single is not None:
            lat.setdefault(method, []).append(single)

    stat: dict[str, tuple[float, float]] = {}
    for method, vals in lat.items():
        if not vals:
            continue
        a = np.asarray(vals, dtype=float)
        stat[method] = (float(np.percentile(a, 50)), float(np.percentile(a, 95)))
    for method, d in agg.items():
        merged = stat.get(method, (float("nan"), float("nan")))
        p50 = d.get("p50", merged[0])
        p95 = d.get("p95", merged[1])
        stat[method] = (p50, p95)

    methods = [m for m in stat if all(math.isfinite(v) for v in stat[m])]
    if not methods:
        return _no_data(out_path, "E7 延迟预算（P50/P95）", provenance,
                        "结果中没有可用的延迟数值（latency_ms / p50_ms / p95_ms）")
    # 稳定排序：按 P50 升序便于阅读，但把 CA-LegalGate 放显眼位置（最右）
    methods.sort(key=lambda m: stat[m][0])
    if "legalgate" in methods:
        methods.remove("legalgate")
        methods.append("legalgate")

    fig, ax = plt.subplots(figsize=(10.2, 6.0), dpi=FIG_DPI)
    ax.set_title("E7 延迟预算：各方法 P50 / P95 端到端延迟（对数刻度）",
                 fontsize=13, weight="bold", pad=12)
    xs = np.arange(len(methods), dtype=float)
    w = 0.36
    p50s = [stat[m][0] for m in methods]
    p95s = [stat[m][1] for m in methods]
    b1 = ax.bar(xs - w / 2, p50s, width=w, color="#42a5f5", alpha=0.92,
                edgecolor="#0d47a1", linewidth=0.8, label="P50 延迟")
    b2 = ax.bar(xs + w / 2, p95s, width=w, color="#ef5350", alpha=0.92,
                edgecolor="#b71c1c", linewidth=0.8, label="P95 延迟")
    for bars in (b1, b2):
        for rect in bars:
            h = rect.get_height()
            ax.text(rect.get_x() + rect.get_width() / 2, h * 1.06, f"{h:,.0f} ms",
                    ha="center", va="bottom", fontsize=7.8, color="#263238")
    ax.set_yscale("log")
    lo = max(1.0, min(min(p50s), min(p95s)) / 1.9)
    hi = max(max(p50s), max(p95s)) * 2.6
    ax.set_ylim(lo, hi)
    ax.set_xticks(xs)
    ax.set_xticklabels([method_label(m) for m in methods], fontsize=9.5, rotation=12, ha="right")
    ax.set_ylabel("延迟 ms（对数刻度）")
    ax.grid(True, which="both", axis="y", alpha=0.25)
    ax.legend(loc="upper left", fontsize=9, title="分位数", title_fontsize=9)
    if "legalgate" in methods:
        i = methods.index("legalgate")
        ax.annotate("本文方法", xy=(i, max(p95s[i], p50s[i]) * 1.35), ha="center",
                    fontsize=8.6, color="#0d47a1", weight="bold")
    note_lines = _wrap_cjk(
        "P95/P50 比值越大说明长尾越重（CPU 推理 + 检索 I/O 的抖动）；对数刻度下等距代表倍数关系。"
        "数值为端到端单条延迟，越小越好；门控的收益正体现在把 Always-RAG 的检索开销压下去。", 120)
    fig.tight_layout(rect=(0.01, _footer_space(note_lines=len(note_lines)), 0.99, 0.945))
    for k, line in enumerate(reversed(note_lines)):
        fig.text(0.5, 0.034 + 0.022 * k, line, ha="center", va="bottom",
                 fontsize=7.6, color="#37474f")
    provenance_footer(fig, provenance)
    return _save(fig, out_path)


# ==========================================================================
# 模块级公开 API
# ==========================================================================

__all__ = [
    "ABLATION_GROUPS",
    "METHOD_LABELS",
    "NO_DATA_TEXT",
    "DEMO_MARK",
    "TRAP_LABELS",
    "plot_ablation",
    "plot_confusion",
    "plot_e0_distributions",
    "plot_latency_budget",
    "plot_multiturn_trend",
    "plot_pareto",
    "plot_tvc_by_trap",
    "provenance_footer",
    "rc_style_defaults",
    "setup_cjk_font",
]


if __name__ == "__main__":  # 自检：字体 + 各图占位（NO DATA）
    print("setup_cjk_font ->", setup_cjk_font())
    print("no-data self-check ->", _no_data("figures/_demo/_selfcheck_nodata.png", "自检"))
