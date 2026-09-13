# -*- coding: utf-8 -*-
"""统一实验框架（手册 S6.1）。

    python scripts/run_exp.py --exp e1 --method legalgate --seed 0 --split test

设计要点
--------
1. **算力现实**：本机 CPU-only，单条 128-token 生成约 10–15 秒。因此：
   * 一切生成走 ``lawgate.cache.GenCache``（内容寻址，跨方法复用）；
   * 支持 ``--shard i --nshards n`` 把测试集切片，用多个独立进程并行跑，
     **不用 multiprocessing 的 IPC**（受沙箱限制，进程间管道不可用），
     由 PowerShell 起多个 python 进程即可；
   * ``--limit`` 支持先小样本试跑，测出真实吞吐再决定全量预算。
2. **seed 的现实含义**：贪心解码下生成是确定性的，seed 只影响数据划分与
   统计重采样。故 ``--seed`` 仍完整实现，但 E1 主对比固定用 seed 0 生成、
   在统计层用 3 个 bootstrap seed，并在报告中说明（docs/deviations.md D8）。
3. 结果按 ``{method}_seed{seed}_{split}.jsonl`` 落盘；已存在即拒绝覆盖。
"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from lawgate.channel.b_structured import ChannelB
from lawgate.channel.c_semantic import Retriever
from lawgate.channel.llm_base import BaseLLM, get_llm
from lawgate.config import get_settings
from lawgate.eval import io as eio
from lawgate.eval.baselines import build_methods, count_tokens
from lawgate.eval.metrics import aggregate, score_item
from lawgate.gate.calibrate import load_taus
from lawgate.knowledge.seed_cases import CAUSE_ACTIONS
from lawgate.router import LegalGateRouter, RouterOptions


@dataclass
class RunContext:
    """一次实验运行的全部共享资源。

    并发说明：本机沙箱禁止外部进程编排（Wait-Process 被拒）与命名管道，
    故并行只能走**线程**。torch 在算子计算期间释放 GIL，因此多线程同时跑
    ``generate`` 能真实加速。但 SQLite 连接与 chromadb 客户端不是线程安全的，
    因此：
      * ``ChannelB`` / ``LegalGateRouter`` 按线程各建一份（线程局部）；
      * ``Retriever`` 的检索加锁串行（检索 ~50ms，相对生成 10s 可忽略）；
      * ``GenCache`` 内部已是线程局部连接 + 写锁，无需改动。
    """

    llm: BaseLLM
    retriever: Retriever
    channel_b: ChannelB
    router: LegalGateRouter
    # 门控草稿来源链（D30）：回答模型换成 API 后，u 可能来自另一条通道
    draft: object | None = None
    invalid_laws: set[str] = field(default_factory=set)
    case_causes: list[str] = field(default_factory=lambda: list(CAUSE_ACTIONS))
    methods: dict = field(default_factory=dict)
    taus: dict = field(default_factory=dict)
    options: RouterOptions | None = None
    db_path: str | None = None
    workers: int = 1
    _tls: object = field(default_factory=threading.local, repr=False)
    _lock: object = field(default_factory=threading.Lock, repr=False)

    # ------------------------------------------------------------ 线程局部
    def thread_bundle(self):
        """返回当前线程专属的 (channel_b, router)；主线程沿用传入实例。"""
        tls = self._tls
        if not hasattr(tls, "bundle"):
            if threading.current_thread() is threading.main_thread():
                tls.bundle = (self.channel_b, self.router)
            else:
                B = ChannelB(self.db_path)
                r = LegalGateRouter(self.llm, self.retriever, B,
                                    taus=self.taus or load_taus(),
                                    options=self.options, db_path=self.db_path,
                                    draft_llm=self.draft)
                tls.bundle = (B, r)
        return tls.bundle

    def method(self, name: str, common: dict):
        """取（按线程隔离的）方法实例。"""
        tls = self._tls
        cache = getattr(tls, "methods", None)
        if cache is None:
            cache = {}
            tls.methods = cache
        if name not in cache:
            B, router = self.thread_bundle()
            cache[name] = build_methods(self.llm, self.retriever, router,
                                        **common)[name]
        return cache[name]

    def score_ctx(self, item: dict, out: dict) -> dict:
        tr = out.get("trace") or {}
        return {
            "invalid_laws": self.invalid_laws,
            "case_causes": self.case_causes,
            "case_passed": tr.get("case_verify", {}).get("passed")
            if isinstance(tr.get("case_verify"), dict) else None,
            "slots_inherited": tr.get("slots_inherited") or [],
        }

    def close_thread_resources(self) -> None:
        tls = self._tls
        b = getattr(tls, "bundle", None)
        if b and b[0] is not self.channel_b:
            try:
                b[0].close()
            except Exception:  # noqa: BLE001
                pass


def load_invalid_laws(db_path: str | None = None) -> set[str]:
    import sqlite3

    s = get_settings()
    conn = sqlite3.connect(db_path or s.db_path)
    try:
        rows = conn.execute(
            """SELECT DISTINCT law_short FROM legal_provisions
               WHERE validity_status IN ('已废止','已修订','部分失效')""").fetchall()
        laws = {r[0] for r in rows}
        # 沿革表里被废止/替代但仍可能被引用的法名
        rows2 = conn.execute(
            """SELECT DISTINCT from_law FROM law_lifecycle
               WHERE relation IN ('废止','替代','修订')""").fetchall()
        laws |= {r[0] for r in rows2}
    finally:
        conn.close()
    return laws


def build_context(method_names: list[str] | None = None,
                  tau_single: float = 0.10,
                  taus_path: str = "configs/thresholds.json",
                  llm: BaseLLM | None = None,
                  with_retriever: bool = True,
                  options: RouterOptions | None = None,
                  threads: int | None = None,
                  workers: int = 1,
                  db_path: str | None = None,
                  draft: object | None = None) -> RunContext:
    s = get_settings()
    llm = llm or get_llm(threads=threads)
    ret = Retriever() if with_retriever else None
    B = ChannelB(db_path)
    if draft is None:
        from lawgate.channel.draft_source import build_draft_source

        draft = build_draft_source(llm, s, threads=threads)
    router = LegalGateRouter(llm, ret, B, taus=taus_path,
                             options=options, db_path=db_path, draft_llm=draft)
    return RunContext(llm=llm, retriever=ret, channel_b=B, router=router,
                      draft=draft,
                      invalid_laws=load_invalid_laws(db_path),
                      taus=dict(router.taus), options=options,
                      db_path=db_path, workers=workers)


def get_method(ctx: RunContext, name: str, **common):
    if not ctx.methods:
        ctx.methods = build_methods(ctx.llm, ctx.retriever, ctx.router, **common)
    if name not in ctx.methods:
        raise KeyError(f"未知方法 {name}；可用 {list(ctx.methods)}")
    return ctx.methods[name]


def shard_items(items: list[dict], shard: int = 0, nshards: int = 1) -> list[dict]:
    if nshards <= 1:
        return items
    return [it for i, it in enumerate(items) if i % nshards == shard]


def run_method(ctx: RunContext, method_name: str, items: list[dict],
               seed: int, split: str, max_tokens: int = 128,
               top_k: int = 8, rerank: bool = True, k_draft: int = 20,
               signal: str = "margin", tau_single: float = 0.10,
               progress_every: int = 0,
               tau_scale: float = 1.0,
               workers: int | None = None) -> list[dict]:
    """跑一个方法，返回已判分记录列表。

    ``workers > 1`` 时用线程池并行处理条目（torch 计算期释放 GIL，故有效加速）。
    """
    common = dict(max_tokens=max_tokens, top_k=top_k, rerank=rerank,
                  k_draft=k_draft, signal=signal, tau_single=tau_single,
                  tau_scale=tau_scale)
    nw = max(int(workers or ctx.workers or 1), 1)
    records: list[dict] = [None] * len(items)  # type: ignore[list-item]
    t_start = time.time()
    done = [0]
    count_lock = threading.Lock()

    def work(i: int, item: dict) -> None:
        t0 = time.time()
        method = ctx.method(method_name, common)
        try:
            out = method.query(item)
        except Exception as exc:  # noqa: BLE001
            out = {"answer": f"[ERROR] {type(exc).__name__}: {exc}",
                   "trace": {"channel": "error", "n_retrieval_calls": 0},
                   "n_tokens": 0}
        dt = (time.time() - t0) * 1000
        tr = out.get("trace") or {}
        sc = score_item(out.get("answer", ""), item, ctx.score_ctx(item, out))
        records[i] = {
            "qid": item.get("qid"), "category": item.get("category"),
            "bucket": tr.get("bucket") or item.get("bucket"),
            "method": method_name, "seed": seed, "split": split,
            "turn_id": item.get("turn_id", 0), "group_id": item.get("group_id"),
            "gold_pass": item.get("gold_pass"),
            "correct": sc.correct,
            "retrieval_used": int(tr.get("n_retrieval_calls", 0)) > 0,
            "n_retrieval_calls": int(tr.get("n_retrieval_calls", 0)),
            "latency_ms": round(dt, 2),
            "n_tokens": int(out.get("n_tokens") or
                            count_tokens(ctx.llm, out.get("answer", ""))),
            "u": tr.get("u"), "tau_b": tr.get("tau_b"), "tau": tr.get("tau"),
            "channel": tr.get("channel"),
            "gate_source": tr.get("gate_source"),
            "router_mode": tr.get("router_mode"),
            "tvc": sc.tvc, "case_pass": sc.case_pass,
            "invalid_law_cited": sc.invalid_law_cited,
            "invalid_laws": sc.invalid_laws,
            "cited_golden": sc.cited_golden, "key_recall": sc.key_recall,
            "refusal": sc.refusal, "uncertain": sc.uncertain,
            "answer_len": sc.answer_len,
            "slots_inherited_ok": sc.slots_inherited_ok,
            "score_detail": sc.score_detail,
            "answer": out.get("answer", ""),
            "trace": tr,
        }
        with count_lock:
            done[0] += 1
            if progress_every and done[0] % progress_every == 0:
                el = time.time() - t_start
                print(f"    [{method_name}] {done[0]}/{len(items)} 用时 {el:.0f}s "
                      f"({el / done[0]:.2f}s/条) 预计总 "
                      f"{el / done[0] * len(items):.0f}s", flush=True)

    if nw <= 1:
        for i, item in enumerate(items):
            work(i, item)
    else:
        with ThreadPoolExecutor(max_workers=nw,
                               thread_name_prefix=f"lg-{method_name}") as ex:
            futs = [ex.submit(work, i, it) for i, it in enumerate(items)]
            for f in futs:
                f.result()
        ctx.close_thread_resources()
    return [r for r in records if r is not None]


def run_experiment(exp: str, method_name: str, seed: int, split: str,
                   out_dir: str | None = None, limit: int | None = None,
                   shard: int = 0, nshards: int = 1, overwrite: bool = False,
                   tag: str = "", save_answer: bool = True,
                   max_tokens: int = 128, top_k: int = 8, rerank: bool = True,
                   k_draft: int = 20, signal: str = "margin",
                   tau_single: float = 0.10, tau_scale: float = 1.0,
                   options: RouterOptions | None = None,
                   threads: int | None = None,
                   workers: int = 1,
                   progress_every: int = 0) -> tuple[list[dict], Path]:
    s = get_settings()
    out_dir = Path(out_dir or (Path(s.results_dir) / exp))
    items = eio.load_split(split)
    items = shard_items(items, shard, nshards)
    if limit:
        items = items[:limit]

    # LegalGate 的生成参数走 router options，必须与其它基线一致，否则对比不公平
    if options is None:
        options = RouterOptions(max_new_tokens=max_tokens, top_k=top_k,
                                rerank=rerank, k_draft=k_draft, signal=signal,
                                tau_scale=tau_scale)
    ctx = build_context(taus_path="configs/thresholds.json", options=options,
                        threads=threads, workers=workers)
    recs = run_method(ctx, method_name, items, seed=seed, split=split,
                      max_tokens=max_tokens, top_k=top_k, rerank=rerank,
                      k_draft=k_draft, signal=signal, tau_single=tau_single,
                      progress_every=progress_every, tau_scale=tau_scale,
                      workers=workers)
    if not save_answer:
        for r in recs:
            r.pop("answer", None)
    # 分片运行时不写主文件，避免互相覆盖
    if nshards > 1:
        p = out_dir / "shards" / f"{method_name}{('_' + tag) if tag else ''}" \
            f"_seed{seed}_{split}_part{shard}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        eio.write_jsonl(recs, p, overwrite=True)
    else:
        p = eio.result_path(out_dir, method_name, seed, split, tag)
        eio.write_jsonl(recs, p, overwrite=overwrite)
    summary = aggregate(recs)
    summary.update({"method": method_name, "seed": seed, "split": split,
                    "exp": exp, "tag": tag,
                    "shard": f"{shard}/{nshards}",
                    "elapsed_s": round(sum(r["latency_ms"] for r in recs) / 1000, 1)})
    eio.write_json(summary, out_dir / f"summary_{method_name}"
                   f"{('_' + tag) if tag else ''}_seed{seed}_{split}"
                   f"{'' if nshards == 1 else f'_part{shard}'}.json")
    return recs, p


def merge_shards(out_dir: str | Path, method: str, seed: int, split: str,
                 tag: str = "") -> Path | None:
    """把分片结果合并为正式结果文件。"""
    out_dir = Path(out_dir)
    sd = out_dir / "shards"
    if not sd.exists():
        return None
    parts = sorted(sd.glob(f"{method}{('_' + tag) if tag else ''}"
                           f"_seed{seed}_{split}_part*.jsonl"))
    if not parts:
        return None
    recs: list[dict] = []
    for p in parts:
        recs += eio.load_results(p)
    recs.sort(key=lambda r: str(r.get("qid")))
    dst = eio.result_path(out_dir, method, seed, split, tag)
    eio.write_jsonl(recs, dst, overwrite=True)
    summary = aggregate(recs)
    summary.update({"method": method, "seed": seed, "split": split,
                    "tag": tag, "n_shards": len(parts),
                    "merged_from": [p.name for p in parts]})
    eio.write_json(summary, out_dir / f"summary_{method}"
                   f"{('_' + tag) if tag else ''}_seed{seed}_{split}.json")
    return dst


def collect_summaries(out_dir: str | Path, exp: str | None = None) -> list[dict]:
    """读取目录下所有 summary_*.json（跳过分片），返回按方法/seed 排序的列表。"""
    out_dir = Path(out_dir)
    rows = []
    for p in sorted(out_dir.glob("summary_*.json")):
        if "_part" in p.stem:
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if exp:
            d.setdefault("exp", exp)
        d["_file"] = p.name
        rows.append(d)
    return rows


def per_item_vectors(records: list[dict]) -> tuple[list[str], list[int]]:
    """按 qid 排序返回 (qids, correct 向量)，供配对统计使用。"""
    recs = sorted(records, key=lambda r: str(r.get("qid")))
    return [str(r.get("qid")) for r in recs], [int(bool(r.get("correct"))) for r in recs]


def load_method_records(out_dir: str | Path, method: str, seed: int,
                        split: str, tag: str = "") -> list[dict]:
    return eio.load_results(eio.result_path(out_dir, method, seed, split, tag))
