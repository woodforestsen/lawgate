# -*- coding: utf-8 -*-
"""一键重画全部结果图（CA-LegalGate）。

用法
----
* 真实数据（有哪个结果文件就画哪张图，缺输入跳过并给 ASCII 警告）::

      python scripts/plot_all.py --results-dir results --figures-dir figures --exp all
      python scripts/plot_all.py --exp e2

* 自检 / 演示（人工合成小样本，全部图都画到 ``figures/_demo/``，
  每张图的图注都会盖上 "DEMO DATA / NOT REAL RESULTS" 戳记）::

      python scripts/plot_all.py --demo

约定
----
* 控制台输出一律 ASCII（Windows GBK 控制台打印中文会乱码）；
  图内文字用中文（由 ``lawgate.eval.plotting.setup_cjk_font`` 注册系统 CJK 字体）。
* 本脚本只读结果、只写 PNG，不修改任何结果文件。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

HERE = Path(__file__).resolve().parent

# --------------------------------------------------------------------------
# 加载绘图模块：优先正常 import（lawgate/eval/__init__.py 由另一位同学负责），
# 若包尚未成型（缺 __init__.py / 命名冲突）则按文件路径直接加载。
# --------------------------------------------------------------------------
_PLOTTING_PATH = REPO_ROOT / "lawgate" / "eval" / "plotting.py"
_LOAD_ERROR: str | None = None
try:
    from lawgate.eval.plotting import (  # type: ignore
        DEMO_MARK,
        plot_ablation as _plot_ablation,
        plot_confusion as _plot_confusion,
        plot_e0_distributions as _plot_e0,
        plot_latency_budget as _plot_latency,
        plot_multiturn_trend as _plot_multiturn,
        plot_pareto as _plot_pareto,
        plot_tvc_by_trap as _plot_tvc,
        rc_style_defaults,
        setup_cjk_font,
    )
except Exception as _exc:  # noqa: BLE001 - 包未就绪时走文件级加载
    _LOAD_ERROR = f"{type(_exc).__name__}: {_exc}"
    if not _PLOTTING_PATH.exists():
        print("[plot_all] FATAL plotting module not found: %s" % _PLOTTING_PATH)
        raise SystemExit(2)
    _spec = importlib.util.spec_from_file_location("lawgate_plotting_standalone", _PLOTTING_PATH)
    if _spec is None or _spec.loader is None:
        print("[plot_all] FATAL cannot load plotting module from file")
        raise SystemExit(2)
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = _mod
    _spec.loader.exec_module(_mod)
    DEMO_MARK = _mod.DEMO_MARK
    _plot_e0 = _mod.plot_e0_distributions
    _plot_pareto = _mod.plot_pareto
    _plot_ablation = _mod.plot_ablation
    _plot_multiturn = _mod.plot_multiturn_trend
    _plot_tvc = _mod.plot_tvc_by_trap
    _plot_confusion = _mod.plot_confusion
    _plot_latency = _mod.plot_latency_budget
    rc_style_defaults = _mod.rc_style_defaults
    setup_cjk_font = _mod.setup_cjk_font

# --------------------------------------------------------------------------
# 图形清单
# --------------------------------------------------------------------------
#: 每张图需要哪些输入文件（相对 ``results/<exp>/``），缺一个就跳过
FIG_SPECS: list[dict] = [
    {"name": "e0_distributions", "exp": "e0", "kind": "e0",
     "inputs": ["e0_signals.json|signals.json"], "desc": "E0 signal distributions + AUC"},
    {"name": "pareto_rr_acc", "exp": "e2", "kind": "pareto",
     "inputs": ["summary.csv|*.jsonl"], "desc": "E2 Pareto RR vs accuracy"},
    {"name": "ablation", "exp": "e3", "kind": "ablation",
     "inputs": ["ablation.jsonl|*.jsonl"], "desc": "E3 ablation (A1-A4)"},
    {"name": "multiturn_trend", "exp": "e4", "kind": "multiturn",
     "inputs": ["multiturn.json|*.jsonl"], "desc": "E4 multi-turn trend"},
    {"name": "tvc_by_trap", "exp": "e5", "kind": "tvc",
     "inputs": ["tvc_by_trap.json"], "desc": "E5 TVC by trap T1-T4"},
    {"name": "confusion", "exp": "e6", "kind": "confusion",
     "inputs": ["confusion.json"], "desc": "E6 verification confusion matrix"},
    {"name": "latency_budget", "exp": "e7", "kind": "latency",
     "inputs": ["summary.csv|*.jsonl"], "desc": "E7 latency budget P50/P95"},
]


# --------------------------------------------------------------------------
# 结果文件发现与读取
# --------------------------------------------------------------------------


def _read_json(path: Path) -> object | None:
    """读 JSON；失败返回 None（ASCII 警告，不抛错）。"""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        print("  [warn] cannot parse JSON %s (%s)" % (Path(path).name, type(exc).__name__))
        return None


def _read_jsonl(path: Path) -> list[dict]:
    """逐行读 JSONL，坏行跳过。"""
    rows: list[dict] = []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print("  [warn] cannot read %s (%s)" % (Path(path).name, type(exc).__name__))
        return rows
    bad = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:  # noqa: BLE001
            bad += 1
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    if bad:
        print("  [warn] %s: skipped %d malformed line(s)" % (Path(path).name, bad))
    return rows


def _read_csv_rows(path: Path) -> list[dict]:
    """读 summary.csv 为原生类型 dict 列表。"""
    path = Path(path)
    if not path.exists():
        return []
    try:
        import pandas as pd

        df = pd.read_csv(path)
        out: list[dict] = []
        for rec in df.to_dict(orient="records"):
            out.append({str(k): (None if (isinstance(v, float) and math.isnan(v)) else v)
                        for k, v in rec.items()})
        return out
    except Exception:  # noqa: BLE001
        pass
    import csv

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            return [dict(r) for r in csv.DictReader(fh)]
    except Exception as exc:  # noqa: BLE001
        print("  [warn] cannot read CSV %s (%s)" % (path.name, type(exc).__name__))
        return []


def _discover(exp_dir: Path) -> dict:
    """把一个实验目录里能用的输入都收集起来。"""
    exp_dir = Path(exp_dir)
    info: dict = {"dir": exp_dir, "exists": exp_dir.is_dir(), "jsonl": {}, "csv": {}, "json": {}}
    if not info["exists"]:
        return info
    for p in sorted(exp_dir.glob("*.jsonl")):
        info["jsonl"][p.name] = _read_jsonl(p)
    for p in sorted(exp_dir.glob("*.csv")):
        info["csv"][p.name] = _read_csv_rows(p)
    for p in sorted(exp_dir.glob("*.json")):
        obj = _read_json(p)
        if obj is not None:
            info["json"][p.name] = obj
    return info


def _pattern_hits(exp_info: dict, pat: str) -> bool:
    """单个输入模式是否命中（``a.json|b.jsonl`` 用 ``|`` 表示候选之一即可）。"""
    for one in pat.split("|"):
        one = one.strip()
        if one.startswith("*"):
            hit = bool(exp_info["jsonl"] or exp_info["csv"])
        elif one.endswith(".csv"):
            hit = any(k == one or k.endswith("_" + one) for k in exp_info["csv"])
        elif one.endswith(".jsonl"):
            hit = one in exp_info["jsonl"] or bool(exp_info["jsonl"])
        else:
            hit = one in exp_info["json"]
        if hit:
            return True
    return False


def _missing(exp_info: dict, patterns: list[str]) -> list[str]:
    """返回 patterns 中一个都没命中的条目名（用于跳过提示）。"""
    return [pat for pat in patterns if not _pattern_hits(exp_info, pat)]


def _all_rows(exp_info: dict) -> list[dict]:
    """合并该实验目录下所有 JSONL 记录（用于按记录聚合的图）。"""
    rows: list[dict] = []
    for recs in exp_info["jsonl"].values():
        rows.extend(recs)
    return rows


def _all_summary_rows(exp_info: dict) -> list[dict]:
    """合并 summary.csv 行；没有 CSV 就退回逐条记录（绘图模块会自己聚合）。"""
    rows: list[dict] = []
    for name, recs in exp_info["csv"].items():
        if "summary" in name or not rows:
            rows.extend(recs)
    return rows or _all_rows(exp_info)


def _pick_json(exp_info: dict, *names: str) -> object | None:
    """按候选文件名取第一个存在的 JSON。"""
    for n in names:
        if n in exp_info["json"]:
            return exp_info["json"][n]
    return None


# --------------------------------------------------------------------------
# 真实数据 → 绘图
# --------------------------------------------------------------------------


def _draw_real(spec: dict, exp_info: dict, fig_dir: Path, prov: dict, extra: list[str]) -> Path | None:
    """按图类型分派真实数据；返回 None 表示输入不足（调用方记 skipped）。"""
    kind = spec["kind"]
    out = Path(fig_dir) / spec["exp"] / (spec["name"] + ".png")

    if kind == "e0":
        payload = _pick_json(exp_info, "e0_signals.json", "signals.json")
        if not isinstance(payload, dict):
            return None
        signals = payload.get("signals") or {}
        labels = payload.get("labels") or payload.get("need_retrieval") or []
        aucs = payload.get("aucs") or {}
        if isinstance(aucs, dict) and "by_bucket" not in aucs:
            aucs = {str(k): v for k, v in aucs.items()}
        buckets = payload.get("buckets")
        if not signals and isinstance(payload.get("auc"), dict):
            return None
        return _plot_e0(signals, labels, aucs, out, prov, buckets=buckets)

    if kind == "pareto":
        rows = _all_summary_rows(exp_info)
        if not rows:
            return None
        curve_method = "legalgate"
        pv = _pick_json(exp_info, "sig_pvalues.json", "pairwise_pvalues.json")
        return _plot_pareto(rows, out, prov, curve_method=curve_method, pvalue_matrix=pv)

    if kind == "ablation":
        rows = _all_rows(exp_info)
        if not rows:
            return None
        return _plot_ablation(rows, out, prov)

    if kind == "multiturn":
        rows = _all_rows(exp_info)
        agg = _pick_json(exp_info, "multiturn.json", "e4_multiturn.json")
        if isinstance(agg, dict):
            for key in ("rows", "turns", "items"):
                if isinstance(agg.get(key), list):
                    rows = [r for r in agg[key] if isinstance(r, dict)] + rows
                    break
        elif isinstance(agg, list):
            rows = [r for r in agg if isinstance(r, dict)] + rows
        if not rows:
            return None
        return _plot_multiturn(rows, out, prov)

    if kind == "tvc":
        obj = _pick_json(exp_info, "tvc_by_trap.json")
        if not isinstance(obj, dict):
            return None
        if "tvc" in obj and isinstance(obj["tvc"], dict):
            obj = obj["tvc"]
        return _plot_tvc(obj, out, prov)

    if kind == "confusion":
        obj = _pick_json(exp_info, "confusion.json")
        if not isinstance(obj, dict):
            return None
        prf = _pick_json(exp_info, "prf.json")
        return _plot_confusion(
            obj.get("matrix") or [],
            obj.get("labels") or [],
            obj.get("subtypes") or [],
            out,
            prov,
            prf=prf if isinstance(prf, dict) else None,
        )

    if kind == "latency":
        rows = _all_summary_rows(exp_info)
        if not rows:
            return None
        return _plot_latency(rows, out, prov)

    return None


# --------------------------------------------------------------------------
# --demo：合成小样本，覆盖全部图类型
# --------------------------------------------------------------------------
DEMO_SEED = 20240521


def _demo_provenance() -> dict:
    """演示用溯源（尽量取真实运行环境，失败则占位）。"""
    try:
        from lawgate.config import get_settings

        prov = dict(get_settings().provenance())
    except Exception:  # noqa: BLE001
        prov = {"hardware": "unknown", "device": "cpu", "causal_model": "unknown",
                "embed_model": "unknown", "llm_backend": "unknown", "date": "", "version": "unknown"}
    prov["date"] = "DEMO"
    prov["seed"] = DEMO_SEED
    prov["source"] = "synthetic (scripts/plot_all.py --demo)"
    return prov


def _norm(rng: random.Random, mu: float, sigma: float) -> float:
    return rng.gauss(mu, sigma)


def _trunc(rng: random.Random, lo: float = 0.0, hi: float = 1.0) -> float:
    return min(hi, max(lo, rng.random()))


def _bootstrap_auc_ci(labels: list[int], scores: list[float], rng: random.Random,
                      n_boot: int = 400) -> tuple[float, float]:
    """AUC 的 bootstrap 95% 置信区间（纯 numpy，无 sklearn 依赖）。"""
    import numpy as np

    y = np.asarray(labels, dtype=float)
    s = np.asarray(scores, dtype=float)
    n = y.size
    if n == 0:
        return float("nan"), float("nan")
    vals: list[float] = []
    idx_all = np.arange(n)
    for _ in range(n_boot):
        idx = np.asarray([rng.randrange(n) for _ in range(n)])
        yy, ss = y[idx], s[idx]
        if yy.sum() == 0 or yy.sum() == yy.size:
            continue
        order = np.argsort(ss, kind="mergesort")
        ranks = np.empty(ss.size, dtype=float)
        ss_sorted = ss[order]
        i = 0
        while i < ss.size:
            j = i
            while j + 1 < ss.size and ss_sorted[j + 1] == ss_sorted[i]:
                j += 1
            ranks[order[i:j + 1]] = 0.5 * (i + j) + 1.0
            i = j + 1
        n_pos = float(yy.sum())
        n_neg = float(yy.size - n_pos)
        vals.append(float((ranks[yy == 1].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)))
    del idx_all
    if not vals:
        return float("nan"), float("nan")
    arr = np.asarray(vals, dtype=float)
    return float(np.percentile(arr, 2.5)), float(np.percentile(arr, 97.5))


def _demo_items(n: int = 216, rng: random.Random | None = None) -> list[dict]:
    """合成逐条评测记录（字段与真实 JSONL 完全一致，含 need_retrieval 真值）。"""
    rng = rng or random.Random(DEMO_SEED)
    categories = ["provision", "case_reason", "procedure", "temporal"]
    buckets = ["b1", "b2", "b3"]
    methods = ["legalgate", "alwaysrag", "norag", "static_tau"]
    items: list[dict] = []
    for i in range(n):
        need = 1 if rng.random() < 0.55 else 0
        cat = categories[i % len(categories)]
        bucket = buckets[i % len(buckets)]
        # 与 need_retrieval 正相关的信号；margin 分离度最好（AUC 最高）
        margin = min(1.2, max(-0.2, (0.62 if need else 0.26) + _norm(rng, 0, 0.235)))
        entropy = min(1.0, max(0.0, (0.72 if need else 0.46) + _norm(rng, 0, 0.205)))
        low_conf = min(1.0, max(0.0, (0.66 if need else 0.48) + _norm(rng, 0, 0.225)))
        method = methods[i % len(methods)]
        # 方法相关的检索倾向与正确率
        rr = {"legalgate": 0.30, "alwaysrag": 1.0, "norag": 0.0, "static_tau": 0.29}[method]
        p_use = min(1.0, max(0.0, rr + 0.25 * (need - 0.5) * 2))
        used = rng.random() < p_use
        acc_base = {"legalgate": 0.94, "alwaysrag": 0.93, "norag": 0.83, "static_tau": 0.90}[method]
        correct = rng.random() < (acc_base - (0.10 if (need and not used) else 0.0))
        lat = max(30.0, rng.lognormvariate({"legalgate": 6.1, "alwaysrag": 6.9,
                                            "norag": 5.6, "static_tau": 6.0}[method], 0.45))
        items.append(
            {
                "qid": "demo_%05d" % (i + 1),
                "category": cat,
                "bucket": bucket,
                "method": method,
                "seed": i % 3,
                "split": "test",
                "correct": bool(correct),
                "retrieval_used": bool(used),
                "n_retrieval_calls": 1 if used else 0,
                "latency_ms": round(lat, 1),
                "n_tokens": int(_trunc(rng) * 120 + 40),
                "u": round(min(1.0, max(0.0, (0.72 if need else 0.45) + _norm(rng, 0, 0.15))), 4),
                "tau_b": round(0.28 + 0.05 * (i % 3), 4),
                "tau": bucket,
                "channel": "C" if used else "A",
                "tvc": 1 if correct else 0,
                "case_pass": None,
                "invalid_law_cited": bool(rng.random() < 0.04),
                "slots_inherited_ok": None,
                "answer": "[DEMO] 合成的回答文本，仅用于画图自检。",
                # 演示专用真值（真实结果里不直接落盘，这里方便画 E0）
                "_need_retrieval": need,
                "_signal_margin": round(margin, 4),
                "_signal_entropy": round(entropy, 4),
                "_signal_low_conf": round(low_conf, 4),
            }
        )
    return items


def _write_demo_results(demo_results: Path) -> dict:
    """把合成结果写进 results/_demo/，返回给绘图函数用的内存数据。"""
    rng = random.Random(DEMO_SEED)
    demo_results.mkdir(parents=True, exist_ok=True)
    items = _demo_items(216, rng)

    # ---- 逐条记录（E0/E4/E7 都用得到）：分方法写 JSONL
    by_method: dict[str, list[dict]] = {}
    for it in items:
        by_method.setdefault(it["method"], []).append(it)
    for method, recs in by_method.items():
        with (demo_results / f"{method}.jsonl").open("w", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    # 多轮记录：turn_id 0/1/2
    with (demo_results / "e4_multiturn.jsonl").open("w", encoding="utf-8") as fh:
        for i, it in enumerate(items[:120]):
            turn = i % 3
            p_ok = {0: 0.90, 1: 0.86, 2: 0.80}[turn]
            p_rr = {0: 0.34, 1: 0.28, 2: 0.25}[turn]
            p_inh = {0: 0.05, 1: 0.12, 2: 0.21}[turn]
            rec = dict(it)
            rec["qid"] = "demo_turn_%05d" % (i + 1)
            rec["turn_id"] = turn
            rec["method"] = "legalgate"
            rec["correct"] = bool(rng.random() < p_ok)
            rec["retrieval_used"] = bool(rng.random() < p_rr)
            rec["slots_inherited_ok"] = bool(rng.random() > p_inh) if turn > 0 else None
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ---- summary.csv（E2 / E7）
    method_centers = {
        "noRAG": (0.828, 0.000, 0.0, 90.0),
        "static_tau": (0.902, 0.290, 0.0, 78.0),
        "legalgate": (0.931, 0.278, 0.0, 62.0),
        "alwaysrag": (0.940, 1.000, 0.0, 40.0),
        "oracle": (1.000, 1.000, 0.0, 34.0),
    }
    tau_rows: list[dict] = []
    for si, seed in enumerate((0, 1, 2)):
        for method, (acc, rr, lac, tok) in method_centers.items():
            jitter = lambda v, s: round(max(0.0, min(1.0, v + rng.gauss(0, s))), 4)  # noqa: E731
            acc_v = jitter(acc, 0.012)
            rr_v = jitter(rr, 0.02)
            p50 = round(rng.lognormvariate(math.log(780.0 if method != "noRAG" else 420.0), 0.12), 1)
            p95 = round(p50 * rng.uniform(1.6, 2.1), 1)
            tau_rows.append(
                {
                    "method": method,
                    "seed": seed,
                    "split": "test",
                    "n": 216,
                    "acc": acc_v,
                    "rr": rr_v,
                    "lac": jitter(0.22 if method == "legalgate" else 0.30, 0.03),
                    "tvc": jitter(0.93 if method == "legalgate" else 0.86, 0.02),
                    "p50_ms": p50,
                    "p95_ms": p95,
                    "mean_tokens": round(tok + rng.uniform(-4, 4), 1),
                    "pareto_acc_note": "demo",
                }
            )
    # τ 缩放操作点（legalgate 曲线）
    for scale, acc_d, rr_d in ((0.5, -0.035, -0.075), (0.75, -0.016, -0.038),
                               (1.0, 0.0, 0.0), (1.25, 0.007, 0.062)):
        tau_rows.append(
            {
                "method": "legalgate",
                "seed": 0,
                "split": "test",
                "n": 216,
                "acc": round(0.931 + acc_d + rng.gauss(0, 0.004), 4),
                "rr": round(0.278 + rr_d + rng.gauss(0, 0.006), 4),
                "lac": 0.22,
                "tvc": 0.93,
                "p50_ms": 800.0,
                "p95_ms": 1500.0,
                "mean_tokens": 70.0,
                "pareto_acc_note": "tau_scale=%g" % scale,
                "tau_scale": scale,
            }
        )
    cols = ["method", "seed", "split", "n", "acc", "rr", "lac", "tvc",
            "p50_ms", "p95_ms", "mean_tokens", "pareto_acc_note", "tau_scale"]
    with (demo_results / "summary.csv").open("w", encoding="utf-8", newline="") as fh:
        import csv

        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in tau_rows:
            w.writerow({k: r.get(k, "") for k in cols})

    # ---- 显著性 p 值矩阵（E2 角落小表）
    methods = ["noRAG", "static_tau", "legalgate", "alwaysrag", "oracle"]
    pmat = []
    for i, _mi in enumerate(methods):
        row = []
        for j, _mj in enumerate(methods):
            row.append(1.0 if i == j else round(min(0.99, abs(rng.gauss(0.03, 0.03)) + 0.001), 4))
        pmat.append(row)
    (demo_results / "sig_pvalues.json").write_text(
        json.dumps({"methods": methods, "metric": "acc", "pvalues": pmat,
                    "note": DEMO_MARK}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # ---- 消融（E3）
    ab = {
        "A1": {"full_ca": (0.928, 0.274, 0.24, 0.93), "no_channel_b": (0.905, 0.268, 0.31, 0.86),
               "no_gate": (0.940, 1.000, 0.00, 0.84)},
        "A2": {"bucket_tau": (0.931, 0.278, 0.22, 0.93), "single_global_tau": (0.919, 0.291, 0.27, 0.89)},
        "A3": {"all_signals": (0.931, 0.278, 0.22, 0.93), "margin_only": (0.925, 0.264, 0.25, 0.90),
               "entropy_only": (0.908, 0.302, 0.29, 0.85), "u_only": (0.901, 0.255, 0.30, 0.88)},
        "A4": {"k10": (0.908, 0.240, 0.19, 0.88), "k20": (0.931, 0.278, 0.22, 0.93),
               "k40": (0.933, 0.312, 0.26, 0.94)},
    }
    with (demo_results / "ablation.jsonl").open("w", encoding="utf-8") as fh:
        for gi, (group, variants) in enumerate(ab.items()):
            for vi, (variant, (acc, rr, lac, tvc)) in enumerate(variants.items()):
                for seed in (0, 1, 2):
                    fh.write(json.dumps(
                        {
                            "group": group,
                            "variant": variant,
                            "method": "legalgate",
                            "seed": seed,
                            "n": 216,
                            "acc": round(acc + rng.gauss(0, 0.006), 4),
                            "rr": round(rr + rng.gauss(0, 0.008), 4),
                            "lac": round(max(0.0, lac + rng.gauss(0, 0.008)), 4),
                            "tvc": round(min(1.0, tvc + rng.gauss(0, 0.006)), 4),
                            "_group_order": gi,
                            "_variant_order": vi,
                        }, ensure_ascii=False) + "\n")

    # ---- 陷阱 TVC（E5）
    tvc = {
        "T1": {"legalgate": 0.96, "alwaysrag": 0.21, "norag": 0.42, "static_tau": 0.78, "oracle": 1.0},
        "T2": {"legalgate": 0.91, "alwaysrag": 0.34, "norag": 0.38, "static_tau": 0.72, "oracle": 1.0},
        "T3": {"legalgate": 0.88, "alwaysrag": 0.29, "norag": 0.51, "static_tau": 0.69, "oracle": 0.99},
        "T4": {"legalgate": 0.79, "alwaysrag": 0.18, "norag": 0.33, "static_tau": 0.61, "oracle": 0.98},
    }
    (demo_results / "tvc_by_trap.json").write_text(
        json.dumps(tvc, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 混淆矩阵 + PRF（E6）
    labels = ["核验通过", "不存在", "存在但案由不符", "格式非法"]
    subtypes = ["V1", "V2", "V3", "V4"]
    matrix = [
        [88, 2, 1, 1],
        [3, 42, 5, 1],
        [2, 4, 33, 2],
        [1, 1, 2, 29],
    ]
    tp, fp, fn, tn = 95, 2, 3, 14
    precision = tp / (tp + fp)
    recall = tp / (tp + fn)
    f1 = 2 * precision * recall / (precision + recall)
    (demo_results / "confusion.json").write_text(
        json.dumps({"labels": labels, "subtypes": subtypes, "matrix": matrix,
                    "note": DEMO_MARK}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    (demo_results / "prf.json").write_text(
        json.dumps({"precision": round(precision, 4), "recall": round(recall, 4),
                    "f1": round(f1, 4), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
                    "threshold_note": "DEMO: threshold=0.5 on verifier score"},
                   ensure_ascii=False, indent=2),
        encoding="utf-8")
    return {"items": items, "tau_rows": tau_rows}


def run_demo(fig_dir: Path, results_dir: Path, prov: dict) -> list[dict]:
    """合成数据并把每一种图都画到 figures/_demo/。"""
    demo_results = Path(results_dir) / "_demo"
    demo_figs = Path(fig_dir) / "_demo"
    demo_figs.mkdir(parents=True, exist_ok=True)
    print("[demo] synthesizing synthetic inputs under %s" % _rel(demo_results))
    data = _write_demo_results(demo_results)
    items = data["items"]

    rng = random.Random(DEMO_SEED + 7)
    extra = [DEMO_MARK, "synthetic inputs generated by scripts/plot_all.py --demo"]
    a_extra = extra + ["图注中的合成数据不得引用为论文结果"]

    results: list[dict] = []

    # ---- E0：信号分布（真值 need_retrieval 由合成记录携带）
    sigs: dict[str, list[float]] = {"margin": [], "entropy": [], "low_conf": []}
    labs: list[int] = []
    buckets: dict[str, list[int]] = {"b1": [], "b2": [], "b3": []}
    for it in items:
        labs.append(int(it["_need_retrieval"]))
        sigs["margin"].append(float(it["_signal_margin"]))
        sigs["entropy"].append(float(it["_signal_entropy"]))
        sigs["low_conf"].append(float(it["_signal_low_conf"]))
        for b in buckets:
            buckets[b].append(1 if it["bucket"] == b else 0)
    # 用合成数据实算 AUC（含 bootstrap 95% CI），保证图不是凭空的
    auc_payload: dict[str, dict] = {}
    for name, vals in sigs.items():
        from lawgate.eval.plotting import _auc_of  # 内部工具复用

        a = _auc_of(labs, vals) or float("nan")
        lo, hi = _bootstrap_auc_ci(labs, vals, rng, n_boot=300)
        item = {"auc": round(float(a), 4), "n": len(labs),
                "auc_ci95": [round(lo, 4), round(hi, 4)]}
        by_bucket = {}
        for b, mask in buckets.items():
            sub_l = [l for l, m in zip(labs, mask) if m == 1]
            sub_s = [s for s, m in zip(vals, mask) if m == 1]
            sub_a = _auc_of(sub_l, sub_s)
            by_bucket[b] = {"auc": None if sub_a is None else round(float(sub_a), 4),
                            "n": len(sub_l)}
        item["by_bucket"] = by_bucket
        auc_payload[name] = item
    out = _plot_e0(sigs, labs, auc_payload, demo_figs / "e0_distributions.png", prov, buckets=buckets)
    results.append({"name": "e0_distributions", "path": out, "status": "written"})

    # ---- E2：Pareto
    out = _plot_pareto(data["tau_rows"], demo_figs / "pareto_rr_acc.png", prov,
                       curve_method="legalgate",
                       tau_scales=(0.5, 0.75, 1.0, 1.25),
                       pvalue_matrix=demo_results / "sig_pvalues.json")
    results.append({"name": "pareto_rr_acc", "path": out, "status": "written"})

    # ---- E3：消融
    ab_rows = _read_jsonl(demo_results / "ablation.jsonl")
    out = _plot_ablation(ab_rows, demo_figs / "ablation.png", prov,
                         metric="lac", metrics=("lac", "tvc", "rr"))
    results.append({"name": "ablation", "path": out, "status": "written"})

    # ---- E4：多轮趋势
    mt_rows = _read_jsonl(demo_results / "e4_multiturn.jsonl")
    out = _plot_multiturn(mt_rows, demo_figs / "multiturn_trend.png", prov)
    results.append({"name": "multiturn_trend", "path": out, "status": "written"})

    # ---- E5：陷阱 TVC
    tvc = _read_json(demo_results / "tvc_by_trap.json")
    out = _plot_tvc(tvc, demo_figs / "tvc_by_trap.png", prov)
    results.append({"name": "tvc_by_trap", "path": out, "status": "written"})

    # ---- E6：混淆矩阵
    conf = _read_json(demo_results / "confusion.json") or {}
    prf = _read_json(demo_results / "prf.json")
    out = _plot_confusion(conf.get("matrix") or [], conf.get("labels") or [],
                          conf.get("subtypes") or [], demo_figs / "confusion.png", prov,
                          prf=prf if isinstance(prf, dict) else None)
    results.append({"name": "confusion", "path": out, "status": "written"})

    # ---- E7：延迟预算
    out = _plot_latency(data["tau_rows"], demo_figs / "latency_budget.png", prov)
    results.append({"name": "latency_budget", "path": out, "status": "written"})

    # ---- 额外：空输入占位图自检（证明"缺数据不崩"）
    try:
        out = _plot_e0({}, [], {}, demo_figs / "_selfcheck_no_data.png", prov)
        results.append({"name": "_selfcheck_no_data", "path": out, "status": "written"})
    except Exception as exc:  # noqa: BLE001
        print("  [warn] no-data self-check failed: %s" % type(exc).__name__)

    # ---- DEMO 数据清单（可追溯性：合成参数一目了然）
    manifest = {
        "warning": DEMO_MARK,
        "generator": "scripts/plot_all.py --demo",
        "demo_seed": DEMO_SEED,
        "n_items": len(items),
        "n_tau_rows": len(data["tau_rows"]),
        "auc_payload": auc_payload,
        "figures": sorted(p.name for r in results for p in [Path(r["path"])]),
        "note": "所有数值均为合成数据，仅用于验证绘图管线；切勿写入论文或与真实结果混用。",
    }
    (demo_figs / "DEMO_README.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


# --------------------------------------------------------------------------
# 真实数据流程
# --------------------------------------------------------------------------


def run_real(results_dir: Path, fig_dir: Path, exp: str, prov: dict) -> list[dict]:
    """扫描 results/<exp>/ 并按可用输入出图。"""
    results_dir = Path(results_dir)
    if not results_dir.is_dir():
        print("[plot_all] WARN results dir not found: %s" % _rel(results_dir))
        specs = FIG_SPECS if exp == "all" else [s for s in FIG_SPECS if s["exp"] == exp]
        return [{"name": s["name"], "path": None,
                 "status": "skipped(missing inputs: %s)" % ",".join(s["inputs"])} for s in specs]

    if exp == "all":
        specs = list(FIG_SPECS)
    else:
        specs = [s for s in FIG_SPECS if s["exp"] == exp.lower()]
        if not specs:
            print("[plot_all] WARN unknown --exp '%s'; known: %s"
                  % (exp, ",".join(sorted({s["exp"] for s in FIG_SPECS}))))
            return []

    out: list[dict] = []
    for spec in specs:
        exp_dir = results_dir / spec["exp"]
        exp_info = _discover(exp_dir)
        rel_in = ",".join(spec["inputs"])
        if not exp_info["exists"]:
            out.append({"name": spec["name"], "path": None,
                        "status": "skipped(missing inputs: %s)" % rel_in})
            continue
        miss = _missing(exp_info, spec["inputs"])
        if miss:
            out.append({"name": spec["name"], "path": None,
                        "status": "skipped(missing inputs: %s)" % ",".join(miss)})
            continue
        try:
            path = _draw_real(spec, exp_info, fig_dir, prov, [])
        except Exception as exc:  # noqa: BLE001 - 一张图失败不能拖死整批
            print("  [warn] figure '%s' raised %s: %s" % (spec["name"], type(exc).__name__, exc))
            out.append({"name": spec["name"], "path": None,
                        "status": "skipped(error %s)" % type(exc).__name__})
            continue
        if path is None:
            out.append({"name": spec["name"], "path": None,
                        "status": "skipped(missing inputs: %s)" % rel_in})
        else:
            out.append({"name": spec["name"], "path": Path(path), "status": "written"})
    return out


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------


def _rel(path: Path | None) -> str:
    if path is None:
        return "-"
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _print_summary(rows: list[dict], fig_dir: Path) -> None:
    """ASCII 汇总表：figure -> written | skipped(missing inputs)。"""
    print("")
    print("=" * 96)
    print("%-26s %-58s %s" % ("FIGURE", "PATH", "STATUS"))
    print("-" * 96)
    for r in rows:
        size = ""
        p = r.get("path")
        if p and Path(p).exists():
            size = " (%d bytes)" % Path(p).stat().st_size
        print("%-26s %-58s %s%s" % (r["name"], _rel(p if p else None), r["status"], size))
    print("-" * 96)
    written = sum(1 for r in rows if str(r["status"]).startswith("written"))
    skipped = len(rows) - written
    total_bytes = 0
    for r in rows:
        p = r.get("path")
        if p and Path(p).exists():
            total_bytes += Path(p).stat().st_size
    print("TOTAL: %d figure(s) -> written=%d skipped=%d | bytes=%d | out=%s"
          % (len(rows), written, skipped, total_bytes, _rel(fig_dir)))
    print("=" * 96)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Regenerate all CA-LegalGate result figures (ASCII console output).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--results-dir", default="results", help="results root directory")
    ap.add_argument("--figures-dir", default="figures", help="figures output root directory")
    ap.add_argument("--exp", default="all",
                    help="all | <name> (e0,e2,e3,e4,e5,e6,e7); ignored in --demo mode")
    ap.add_argument("--demo", action="store_true",
                    help="render every figure from synthetic inputs into figures/_demo/")
    ap.add_argument("--quiet", action="store_true", help="suppress font diagnostics")
    args = ap.parse_args(argv)

    if _LOAD_ERROR:
        print("[plot_all] note: package import path unavailable, loaded module by file "
              "(%s)" % _LOAD_ERROR.split(":")[0])

    rc_style_defaults()
    font_path = setup_cjk_font(verbose=not args.quiet)
    if font_path is None:
        print("[plot_all] WARN no CJK font file found; Chinese glyphs may be boxes")

    results_dir = Path(args.results_dir)
    fig_dir = Path(args.figures_dir)
    if not results_dir.is_absolute():
        results_dir = REPO_ROOT / results_dir
    if not fig_dir.is_absolute():
        fig_dir = REPO_ROOT / fig_dir
    fig_dir.mkdir(parents=True, exist_ok=True)

    prov = _demo_provenance()
    if args.demo:
        print("[plot_all] DEMO MODE: synthetic inputs -> %s/_demo (figures stamped '%s')"
              % (_rel(fig_dir), DEMO_MARK))
        rows = run_demo(fig_dir, results_dir, prov)
    else:
        print("[plot_all] results=%s figures=%s exp=%s"
              % (_rel(results_dir), _rel(fig_dir), args.exp))
        rows = run_real(results_dir, fig_dir, args.exp, prov)

    _print_summary(rows, fig_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
