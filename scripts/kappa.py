# -*- coding: utf-8 -*-
"""标注者间一致性（Cohen's kappa）评估脚本 —— manual S4.3。

对齐两个标注员的投票文件（JSONL，每行
``{"qid":..., "need_retrieval":bool, "golden_provisions":[...]}``），报告：

1. ``need_retrieval`` 的 Cohen's kappa；
2. ``golden_provisions`` 的 Cohen's kappa（法条集合先规范化成排序后的
   ``"law#article|law#article"`` 字符串，再做一致性比较）。

判定：kappa >= 0.7 → ``PASS``；否则 ``FAIL → 重新对齐标注规范``（退出码 1）。
同时列出所有分歧 qid。

==============================================================================
关于 ``--simulate`` 的**必须披露**事项
==============================================================================
本执行环境**没有任何人工标注员**。``--simulate`` 用规则 + 随机噪声从已构建的
``data/benchmark/all.jsonl`` 生成两份**人工伪造的**标注文件，仅用于证明
kappa 流水线可运行、可复现。因此：

    * 仿真模式产出的 kappa **不是**标注者间一致性证据，
      **不得**写进论文/手册充当"kappa 对齐已通过"；
    * 报告里会用醒目标题区分 "SIMULATED (NOT HUMAN)" 与 "HUMAN"；
    * 真人双标完成后，用 ``--a votes_a.jsonl --b votes_b.jsonl``
      重新运行同一脚本即可得到真实 kappa。

用法::

    python scripts/kappa.py --a A.jsonl --b B.jsonl
    python scripts/kappa.py --simulate --noise 0.12
    python scripts/kappa.py --simulate --noise 0.12 --n 300 --report data/benchmark/kappa_report.md
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from lawgate.config import get_settings                      # noqa: E402
from lawgate.eval.benchmark import read_jsonl, write_jsonl   # noqa: E402

KAPPA_THRESHOLD = 0.7


# ---------------------------------------------------------------- 指标

def cohens_kappa(a: Sequence[Any], b: Sequence[Any]) -> dict[str, Any]:
    """Cohen's kappa（支持布尔或任意可哈希离散标签）。

    返回 ``{'kappa','po','pe','n','labels'}``；当 pe == 1（完全常数标注、
    期望一致率饱和）时 kappa 记为 ``None``，由调用方按"无信息量"处理。
    """
    if len(a) != len(b):
        raise ValueError(f"标注长度不一致：{len(a)} vs {len(b)}")
    n = len(a)
    if n == 0:
        return {"kappa": None, "po": None, "pe": None, "n": 0, "labels": []}
    labels = sorted({*a, *b}, key=str)
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pe = 0.0
    for lab in labels:
        pa = sum(1 for x in a if x == lab) / n
        pb = sum(1 for y in b if y == lab) / n
        pe += pa * pb
    kappa = None if abs(1.0 - pe) < 1e-12 else (po - pe) / (1.0 - pe)
    return {"kappa": kappa, "po": po, "pe": pe, "n": n,
            "labels": [str(x) for x in labels]}


def canon_provisions(provs: Any) -> str:
    """法条金标规范化：去重、按 (law_short, article_no) 排序后拼成字符串。"""
    if not provs:
        return "<empty>"
    keys: set[tuple[str, int]] = set()
    for p in provs:
        if isinstance(p, dict):
            law = str(p.get("law_short", ""))
            art = p.get("article_no", 0)
            try:
                art_i = int(art)
            except (TypeError, ValueError):
                art_i = 0
            keys.add((law, art_i))
        else:  # 允许纯字符串形式
            keys.add((str(p), 0))
    return "|".join(f"{law}#{art}" for law, art in sorted(keys))


def kappa_report(votes_a: list[dict[str, Any]], votes_b: list[dict[str, Any]]) -> dict[str, Any]:
    """对齐两份投票并按 qid 计算两项 kappa + 分歧清单。"""
    map_a = {v["qid"]: v for v in votes_a}
    map_b = {v["qid"]: v for v in votes_b}
    common = sorted(set(map_a) & set(map_b))
    only_a = sorted(set(map_a) - set(map_b))
    only_b = sorted(set(map_b) - set(map_a))

    a_nr, b_nr, a_gp, b_gp = [], [], [], []
    disagree_nr: list[str] = []
    disagree_gp: list[str] = []
    for qid in common:
        va, vb = map_a[qid], map_b[qid]
        na = bool(va.get("need_retrieval"))
        nb = bool(vb.get("need_retrieval"))
        ga = canon_provisions(va.get("golden_provisions"))
        gb = canon_provisions(vb.get("golden_provisions"))
        a_nr.append(na)
        b_nr.append(nb)
        a_gp.append(ga)
        b_gp.append(gb)
        if na != nb:
            disagree_nr.append(qid)
        if ga != gb:
            disagree_gp.append(qid)

    return {
        "n_common": len(common),
        "only_a": only_a,
        "only_b": only_b,
        "need_retrieval": cohens_kappa(a_nr, b_nr),
        "golden_provisions": cohens_kappa(a_gp, b_gp),
        "disagree_need_retrieval": disagree_nr,
        "disagree_golden_provisions": disagree_gp,
    }


# ---------------------------------------------------------------- 模拟标注

def simulate_votes(bench_path: Path, noise: float, n: int, seed: int,
                   ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """从评测集生成两份**模拟**标注（A 为"金标"，B 注入噪声）。

    * ``need_retrieval``：以 ``noise`` 概率翻转（独立伯努利）；
    * ``golden_provisions``：以 ``noise`` 概率发生集合级扰动
      （删掉一个法条，或追加一个同法相邻条号）。

    **这不是人类标注，也不是真实一致性证据。**
    """
    items = read_jsonl(bench_path)
    if not items:
        raise FileNotFoundError(f"评测集为空或不存在：{bench_path}")
    rng = random.Random(seed)
    if n and n < len(items):
        pool = sorted(items, key=lambda it: it["qid"])
        rng.shuffle(pool)
        pool = sorted(pool[:n], key=lambda it: it["qid"])
    else:
        pool = items
    # 便于"追加相邻条号"扰动：收集同法已有条号
    by_law: dict[str, list[int]] = {}
    for it in items:
        for gp in it.get("golden_provisions") or []:
            by_law.setdefault(str(gp.get("law_short")), []).append(int(gp.get("article_no", 0)))

    votes_a: list[dict[str, Any]] = []
    votes_b: list[dict[str, Any]] = []
    for it in pool:
        nr = bool(it["need_retrieval"])
        gps = [{"law_short": g["law_short"], "article_no": g["article_no"]}
               for g in (it.get("golden_provisions") or [])]
        votes_a.append({"qid": it["qid"], "need_retrieval": nr, "golden_provisions": gps})

        nr_b = nr
        if rng.random() < noise:
            nr_b = not nr
        gps_b = list(gps)
        if rng.random() < noise:
            if gps_b and rng.random() < 0.6:
                gps_b.pop(rng.randrange(len(gps_b)))          # 漏标
            else:
                law = (gps_b[0]["law_short"] if gps_b
                       else rng.choice(sorted(by_law) or ["民法典"]))
                pool_arts = by_law.get(law) or [1]
                gps_b.append({"law_short": law,
                              "article_no": int(rng.choice(pool_arts))})  # 多标
        votes_b.append({"qid": it["qid"], "need_retrieval": nr_b,
                        "golden_provisions": gps_b})
    return votes_a, votes_b


# ---------------------------------------------------------------- 报告

def _fmt_kappa(k: float | None) -> str:
    return "N/A" if k is None else f"{k:.4f}"


def render_markdown(rep: dict[str, Any], *, source: str, noise: float | None,
                    votes_a_path: str, votes_b_path: str,
                    simulated: bool) -> str:
    """渲染中文报告；仿真模式必须显式标注来源。"""
    k_nr = rep["need_retrieval"]["kappa"]
    k_gp = rep["golden_provisions"]["kappa"]

    def verdict(k: float | None) -> str:
        if k is None:
            return "N/A（标注为常数，无信息量）"
        return "PASS" if k >= KAPPA_THRESHOLD else "FAIL → 重新对齐标注规范"

    banner = ("# ⚠️ 警告：本报告的 kappa 来自【模拟标注员】，"
              "**不是**真实标注者间一致性证据" if simulated
              else "# 标注者间一致性报告（真实标注文件）")
    src_line = (f"**数据来源：SIMULATED (NOT HUMAN)** —— 由脚本按噪声率 "
                f"{noise} 从 `{source}` 程序化生成两份假投票，"
                f"仅用于验证 kappa 流水线可运行。"
                if simulated else
                f"**数据来源：HUMAN** —— 两个独立标注员的真实投票文件")

    lines = [
        banner, "",
        src_line, "",
        "## 判定阈值",
        "",
        f"Cohen's kappa >= {KAPPA_THRESHOLD} 记为 PASS，否则 FAIL → 重新对齐标注规范。",
        "",
        "## 输入",
        "",
        f"- 标注员 A 投票文件：`{votes_a_path}`",
        f"- 标注员 B 投票文件：`{votes_b_path}`",
        f"- 共同 qid 数：{rep['n_common']}",
        f"- 仅 A 有：{len(rep['only_a'])} 个" + (f"（{rep['only_a'][:10]}）" if rep["only_a"] else ""),
        f"- 仅 B 有：{len(rep['only_b'])} 个" + (f"（{rep['only_b'][:10]}）" if rep["only_b"] else ""),
        "",
        "## 结果",
        "",
        "| 指标 | Cohen's kappa | Po | Pe | n | 判定 |",
        "|---|---|---|---|---|---|",
        f"| need_retrieval | {_fmt_kappa(k_nr)} | {rep['need_retrieval']['po']:.4f} "
        f"| {rep['need_retrieval']['pe']:.4f} | {rep['need_retrieval']['n']} | {verdict(k_nr)} |",
        f"| golden_provisions（集合规范化后） | {_fmt_kappa(k_gp)} "
        f"| {rep['golden_provisions']['po']:.4f} | {rep['golden_provisions']['pe']:.4f} "
        f"| {rep['golden_provisions']['n']} | {verdict(k_gp)} |",
        "",
        "> 说明：`golden_provisions` 一致性先按 `law_short#article_no` 规范化、去重、排序，"
        "再拼成字符串比较——顺序与重复不计入分歧。",
        "",
        "### 如何解读这些数字（尤其是仿真模式）",
        "",
        f"- 仿真噪声率 = {noise}：B 的 `need_retrieval` 以该概率翻转，"
        f"`golden_provisions` 以该概率被删/增一个条目，因此 kappa 会随噪声率单调下降；",
        "- kappa 对**类别不平衡**敏感：本评测集 `need_retrieval` 的边际分布偏斜，"
        "期望一致率 Pe 较高，同样的观测一致率 Po 会得到更低的 kappa；"
        "若把两类样本配平，" f"噪声率 {noise} 下的 kappa 会明显低于本表数值；",
        "- `golden_provisions` 的 Pe 很低（本项目金标条文组合高度分散），"
        "因此其 kappa 偏高属于指标性质，不等于标注质量一定更高；",
        "- 结论：**仿真数值不具备任何标注质量含义**，只用于证明脚本可运行、可复现。",
        "",
        "## 分歧清单",
        "",
        f"- `need_retrieval` 分歧 {len(rep['disagree_need_retrieval'])} 个："
        + (", ".join(f"`{q}`" for q in rep["disagree_need_retrieval"][:60]) or "无"),
        f"- `golden_provisions` 分歧 {len(rep['disagree_golden_provisions'])} 个："
        + (", ".join(f"`{q}`" for q in rep["disagree_golden_provisions"][:60]) or "无"),
        "",
        "## 结论口径（必须照抄进论文，禁止改写）",
        "",
    ]
    if simulated:
        lines += [
            "- 本报告中的 kappa 来自**程序化模拟标注员**，只证明 kappa 流水线可运行、"
            "结果可复现，",
            "- **不得**表述为「标注者间一致性已达到 kappa=X」；",
            "- **不得**作为「人工标注已完成」的证据；",
            "- 申报前必须由两名真人独立标注，并执行：",
            "  `python scripts/kappa.py --a votes_A.jsonl --b votes_B.jsonl`",
            "  以真实投票文件重新生成本报告（届时报告标题会自动变为 HUMAN 版本）。",
        ]
    else:
        lines += [
            "- 本报告的 kappa 来自两名独立标注员的真实投票文件，可作为人工标注一致性证据；",
            "- 仍需在论文中披露标注规范、分歧仲裁规则与仲裁比例。",
        ]
    lines += [
        "",
        "## 环境口径",
        "",
        f"- 阈值：{KAPPA_THRESHOLD}",
        f"- 仿真噪声率：{noise if simulated else 'N/A'}",
        "- 随机种子：见命令行（默认 42）",
        "- 复现命令（仿真）：`python scripts/kappa.py --simulate --noise 0.12`",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    """命令行入口。stdout 只输出 ASCII。"""
    ap = argparse.ArgumentParser(
        description="Cohen's kappa 标注一致性评估（manual S4.3）")
    ap.add_argument("--a", dest="path_a", default=None, help="标注员 A 投票 JSONL")
    ap.add_argument("--b", dest="path_b", default=None, help="标注员 B 投票 JSONL")
    ap.add_argument("--simulate", action="store_true",
                    help="从评测集生成两份**模拟**投票（非人类标注）")
    ap.add_argument("--noise", type=float, default=0.12, help="模拟噪声率")
    ap.add_argument("--n", type=int, default=300, help="模拟抽样条数（0=全部）")
    ap.add_argument("--seed", type=int, default=42, help="模拟随机种子")
    ap.add_argument("--bench", default="data/benchmark/all.jsonl", help="评测集路径")
    ap.add_argument("--report", default="data/benchmark/kappa_report.md", help="报告输出路径")
    ap.add_argument("--votes-dir", default="data/benchmark/simulated_annotators",
                    help="模拟投票文件输出目录")
    args = ap.parse_args(argv)

    settings = get_settings()
    root = settings.root

    def _abs(p: str) -> Path:
        pp = Path(p)
        return pp if pp.is_absolute() else root / pp

    if args.simulate:
        bench = _abs(args.bench)
        votes_dir = _abs(args.votes_dir)
        votes_dir.mkdir(parents=True, exist_ok=True)
        votes_a, votes_b = simulate_votes(bench, noise=args.noise, n=args.n,
                                         seed=args.seed)
        pa = votes_dir / f"simulated_annotator_A_noise{args.noise:.2f}.jsonl"
        pb = votes_dir / f"simulated_annotator_B_noise{args.noise:.2f}.jsonl"
        write_jsonl(pa, votes_a)
        write_jsonl(pb, votes_b)
        rep = kappa_report(votes_a, votes_b)
        simulated = True
        source = bench.as_posix()
        a_path, b_path, noise = pa.as_posix(), pb.as_posix(), args.noise
    else:
        if not args.path_a or not args.path_b:
            ap.error("非仿真模式必须同时提供 --a 与 --b 投票文件")
        a_path, b_path = str(_abs(args.path_a)), str(_abs(args.path_b))
        votes_a = read_jsonl(Path(a_path))
        votes_b = read_jsonl(Path(b_path))
        rep = kappa_report(votes_a, votes_b)
        simulated = False
        source = a_path
        noise = None

    report = _abs(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_markdown(
        rep, source=source, noise=noise, votes_a_path=a_path, votes_b_path=b_path,
        simulated=simulated), encoding="utf-8")

    k_nr = rep["need_retrieval"]["kappa"]
    k_gp = rep["golden_provisions"]["kappa"]
    print("MODE", "SIMULATED (NOT HUMAN)" if simulated else "HUMAN VOTES")
    print("N_COMMON", rep["n_common"])
    print("NEED_RETRIEVAL_KAPPA", "N/A" if k_nr is None else "%.4f" % k_nr)
    print("NEED_RETRIEVAL_PO", "%.4f" % rep["need_retrieval"]["po"])
    print("GOLDEN_PROVISIONS_KAPPA", "N/A" if k_gp is None else "%.4f" % k_gp)
    print("GOLDEN_PROVISIONS_PO", "%.4f" % rep["golden_provisions"]["po"])
    print("DISAGREE_NEED_RETRIEVAL", len(rep["disagree_need_retrieval"]))
    print("DISAGREE_GOLDEN_PROVISIONS", len(rep["disagree_golden_provisions"]))
    ok = (k_nr is not None and k_nr >= KAPPA_THRESHOLD
          and k_gp is not None and k_gp >= KAPPA_THRESHOLD)
    print("VERDICT", "PASS" if ok else "FAIL_ALIGN_ANNOTATION_SPEC")
    if simulated:
        print("WARNING simulated annotators: kappa is NOT inter-annotator evidence")
    print("REPORT", report.as_posix())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
