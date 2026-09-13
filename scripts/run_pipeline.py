# -*- coding: utf-8 -*-
"""一键跑完全部实验（顺序执行，单进程 14 线程）。

用法（作为后台任务运行最合适）：
    python scripts/run_pipeline.py --max-tokens 80 --threads 14

执行顺序：
  1. dev 基线（neverrag / alwaysrag）→ 供桶级校准
  2. 桶级阈值校准 → configs/thresholds.json
  3. E1 主对比（6 方法 × test_e1）
  4. E1 汇总 / Pareto / 核心断言 / 统计检验
  5. E5 时效性陷阱（temporal_trap 全量，4 方法）
  6. E6 案号核验（纯通道 B，秒级）
  7. E3 多轮（test_e4）
  8. E2 消融（A1 关通道B / A2 单全局τ / A3 信号切换 / A4 草稿长度）
  9. 全部图表（scripts/plot_all.py）

每一步都包 try/except：单步失败不阻断后续步骤，失败原因写入日志与
docs/pipeline_steps.json，避免"部分失败被当成全部成功"。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
# 强制父子进程都用 UTF-8 编码 stdout/stderr（手册 D20 配套修复）：
#   * 父进程自己的 print 在 Windows GBK 控制台会把模型输出里的
#     \ufffd 替换字符报 UnicodeEncodeError；
#   * 子进程（run_exp / calibrate / eN_*) 若没强制 UTF-8，其中文 print
#     同样会在 GBK 控制台崩。这里统一显式设 UTF-8 + PYTHONUTF8，避免"部分
#     失败被当成全部成功"。
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 父进程自身的 stdout/stderr 也显式用 UTF-8（errors="replace" 兜底，
# 即便某个字节不是 UTF-8 也不会崩）。
import io as _io  # noqa: E402
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass

PY = sys.executable
STEPS: list[dict] = []

# ---------------------------------------------------------------------------
# D20.2 单实例锁（docs/pipeline.lock）：
#   2026-09-11 实测事故：两个 pipeline 实例并行跑（10:22 与 10:24 各一个），
#   各自加载一份 0.5B 模型 + 14 线程，互相抢占 18 逻辑核与 chroma/gen_cache，
#   dev-alwaysrag 25 分钟只推进 30 条（正常吞吐 ~17s/条），且并发改写
#   docs/pipeline_steps.json 造成状态互相覆盖。
#   处置：启动时以 O_CREAT|O_EXCL 抢占锁文件；锁被活进程持有则拒绝启动
#   （exit 2）；锁的持有进程已死则收尸（unlink）后重试。正常退出经
#   atexit 释放。强制 kill 留下的陈旧锁在下次启动时自动回收。
# ---------------------------------------------------------------------------
import atexit as _atexit  # noqa: E402

LOCK_FILE = Path("docs/pipeline.lock")


def _pid_alive(pid: int, created: float | None = None) -> bool:
    """PID 是否由**本流水线**持有（不会被 PID 复用骗过）。

    2026-09-11 实测事故：死掉的流水线（pid 15836）留下的锁文件被一个
    新起的编辑器进程复用了同一个 PID，于是"锁还活着"→ 新实例 exit 2 拒绝启动，
    表面症状是"流水线一启动就退出、日志里什么都没有"。
    处置：锁文件额外记录进程**创建时间**，两者都对上才算锁活着；
    进程存在但创建时间不同 ⇒ PID 已被复用 ⇒ 陈旧锁，直接回收。
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        try:
            h = ctypes.windll.kernel32.OpenProcess(0x0400, False, pid)
        except Exception:
            return False
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        if created is None:
            return True
        ct = _proc_create_time(pid)
        if ct is None:
            return True                          # 查不到 → 保守认为活着
        return abs(ct - float(created)) < 2.0
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                              # 存在但不可查 → 保守认为活着
    return True


def _proc_create_time(pid: int) -> float | None:
    """进程创建时间（Unix 秒）；拿不到返回 None。"""
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        k32 = ctypes.windll.kernel32

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD),
                        ("dwHighDateTime", wintypes.DWORD)]

        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return None
        try:
            c, e, k, u = (FILETIME(), FILETIME(), FILETIME(), FILETIME())
            ok = k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e),
                                     ctypes.byref(k), ctypes.byref(u))
            if not ok:
                return None
            ticks = (c.dwHighDateTime << 32) | c.dwLowDateTime
            return ticks / 1e7 - 11644473600.0    # FILETIME → Unix 秒
        finally:
            k32.CloseHandle(h)
    except Exception:  # noqa: BLE001
        return None


def _acquire_lock() -> None:
    while True:
        try:
            with open(LOCK_FILE, "x", encoding="utf-8") as fh:
                fh.write(json.dumps({"pid": os.getpid(),
                                     "created": _proc_create_time(os.getpid()),
                                     "ts": time.time(),
                                     "cmd": sys.argv}, ensure_ascii=False))
            _atexit.register(lambda: LOCK_FILE.unlink(missing_ok=True))
            return
        except FileExistsError:
            created = None
            try:
                rec = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
                pid = int(rec.get("pid", -1))
                created = rec.get("created")
            except Exception:
                pid = -1
            if _pid_alive(pid, created):
                print(f"[lock] 另一个 pipeline 实例正在运行（pid {pid}）：{LOCK_FILE}")
                print("      为避免双实例互抢 CPU/缓存，本实例拒绝启动（exit 2）。"
                      "确认另一个可以停掉后再重启；切勿手删活锁。", flush=True)
                raise SystemExit(2)
            print(f"[lock] 回收陈旧锁（pid {pid} 已不在运行或 PID 已被复用）",
                  flush=True)
            try:
                LOCK_FILE.unlink()
            except FileNotFoundError:
                pass
            continue


_acquire_lock()


def _child_env() -> dict:
    """子进程环境：显式 UTF-8 + 保留父进程其它环境变量。"""
    import os as _os
    env = dict(_os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def run(cmd: list[str], label: str, stream=None) -> bool:
    """跑一个子步骤。

    ``stream``（``--inherit-stdio``）：
      子进程直接继承父进程的 stdout/stderr（父进程已把二者都写到
      ``docs/pipeline.log`` 的文件句柄）。**为什么不默认用 capture_output**：
      在受限文件沙箱（DSH workspace-write）下，程序无法打开命名管道，
      ``subprocess.run(..., capture_output=True)`` 会让子进程以
      ``PermissionError/EPERM`` 直接失败（实测 exit=1，日志里看不到任何子进程输出）。
      继承式 stdio 完全不经过管道，因此在这种沙箱下可正常运行；
      代价是失去"只保留输出尾部 6000 字符"的截断。
    """
    head = f"\n{'=' * 78}\n[{label}] {' '.join(map(str, cmd))}\n{'=' * 78}"
    print(head, flush=True)
    if stream is not None:
        stream.write(head + "\n")
        stream.flush()
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=str(Path(__file__).resolve().parents[1]),
                           capture_output=stream is None, text=True,
                           encoding="utf-8", errors="replace", timeout=36000,
                           env=_child_env(), stdout=stream, stderr=stream,
                           )
        if stream is None:
            out = (p.stdout or "") + (p.stderr or "")
            # 父进程 print 到 stdout 前，把 \ufffd 替换成可读标记，避免 GBK
            # 控制台报 UnicodeEncodeError（即便已经强制 UTF-8，仍然兜底）。
            print(out.replace("\ufffd", "?")[-6000:], flush=True)
        ok = p.returncode == 0
        err = None if ok else f"exit={p.returncode}"
    except Exception as exc:  # noqa: BLE001
        ok, err = False, f"{type(exc).__name__}: {exc}"
        msg = f"[{label}] 失败：{err}"
        print(msg, flush=True)
        if stream is not None:
            stream.write(msg + "\n")
    STEPS.append({"step": label, "cmd": cmd, "ok": ok, "error": err,
                  "seconds": round(time.time() - t0, 1)})
    tail = f"[{label}] {'OK' if ok else 'FAIL'} {time.time() - t0:.0f}s"
    print(tail, flush=True)
    if stream is not None:
        stream.write(tail + "\n")
        stream.flush()
    Path("docs/pipeline_steps.json").write_text(
        json.dumps(STEPS, ensure_ascii=False, indent=2), encoding="utf-8")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-tokens", type=int, default=80)
    ap.add_argument("--threads", type=int, default=14)
    ap.add_argument("--skip-dev", action="store_true",
                    help="跳过 dev 基线与校准（已跑过时）")
    ap.add_argument("--skip-e1", action="store_true")
    ap.add_argument("--only", nargs="*", default=None,
                    help="只跑指定步骤：dev calib e1 e5 e6 e3 e2 plots")
    ap.add_argument("--inherit-stdio", action="store_true",
                    help="子进程继承 stdout/stderr（受限文件沙箱下必须开；见 run() 文档）")
    ap.add_argument("--log", default="docs/pipeline.log",
                    help="--inherit-stdio 时父进程与全部子进程的输出落盘位置")
    ap.add_argument("--workers", type=int, default=1,
                    help="单个实验进程内的并发条目数（线程池）。CPU 实测 workers=2 "
                         "把有效吞吐从 24 s/条 提到 ~17 s/条；内存每进程约 6 GB，"
                         "故**不要**用多进程并行（见 D21）")
    ap.add_argument("--shards", type=int, default=1,
                    help="把每个方法切成 N 个分片顺序跑再合并（D21 抗中断："
                         "run_exp 只在方法跑完时落盘，不切分片则中断即丢整方法）")
    ap.add_argument("--e1-split", default="test_e1",
                    help="E1 主对比（含 τ 缩放工作点与 e1_plot 汇总）所用划分。"
                         "默认 test_e1（D19 的 300 条子集）；D33：API 后端解除"
                         "算力约束后可跑手册原设定的 test 全量（944 条）")
    args = ap.parse_args()
    mt, th = args.max_tokens, args.threads
    e1s = args.e1_split
    nw, ns = args.workers, max(1, args.shards)
    only = set(args.only) if args.only else None
    # 受限沙箱下无法给 capture_output 建管道 → 统一改成继承式 stdio，
    # 父/子输出全部追加到 docs/pipeline.log（ERR 也重定向到同一文件）。
    stream = None
    if args.inherit_stdio:
        Path(args.log).parent.mkdir(parents=True, exist_ok=True)
        stream = open(args.log, "a", encoding="utf-8", errors="replace")
        # 父进程自己的 print 也写进同一个文件（子进程通过 stdout=stream 继承）。
        try:
            sys.stdout = stream
            sys.stderr = stream
        except Exception:  # noqa: BLE001
            pass

    def want(name: str) -> bool:
        return only is None or name in only

    def _run(cmd: list[str], label: str) -> bool:
        return run(cmd, label, stream=stream)

    def _exp(exp: str, method: str, extra: list[str], label: str,
             split: str = "test_e1") -> bool:
        """跑一个 (方法 × 划分)：分片顺序执行后合并，带 workers 并发。

        为什么要分片（D21）：``run_exp.py`` 只在**整个方法跑完**时才写结果文件，
        所以中断（进程被杀、内存不足、harness 重启）会丢掉该方法已算的全部条目。
        切成 N 片后每片独立落盘 ``results/<exp>/shards/...part<i>.jsonl``，
        中断只会丢当前那一片；重跑时同一分片直接覆盖，已完成的片秒过
        （生成本身还有内容寻址缓存兜底）。
        """
        base = [PY, "scripts/run_exp.py", "--exp", exp, "--method", method,
                "--seed", "0", "--split", split,
                "--max-tokens", str(mt), "--threads", str(th)] + extra
        if nw > 1:
            base += ["--workers", str(nw)]
        if ns <= 1:
            return _run(base + ["--progress-every", "20"], label)
        ok = True
        for i in range(ns):
            ok = _run(base + ["--shard", str(i), "--nshards", str(ns),
                              "--progress-every", "20"],
                      f"{label}-shard{i + 1}of{ns}") and ok
        if not ok:
            # 有分片失败时仍然合并已成功的片：部分结果 > 没有结果，
            # 合并后的 acc/RR 覆盖的真实条目数会体现在 n 上。
            _run(base + ["--merge"], f"{label}-merge")
            return False
        return _run(base + ["--merge"], f"{label}-merge")

    # ---- 0. 子集（幂等）----
    if want("dev") or want("e1"):
        _run([PY, "scripts/make_subsets.py"], "subsets")

    # ---- 1. dev 基线 ----
    if want("dev") and not args.skip_dev:
        for m in ("neverrag", "alwaysrag"):
            _run([PY, "scripts/run_exp.py", "--exp", "dev_baseline", "--method", m,
                  "--seed", "0", "--split", "dev_calib", "--overwrite",
                  "--max-tokens", str(mt), "--threads", str(th),
                  "--progress-every", "40"], f"dev-{m}")

    # ---- 2. 校准 ----
    if want("calib") and not args.skip_dev:
        # D20.1：hybrid/complexity 模式的 τ 网格上界必须 ≥ 1.0（复杂度评分值域
        # 为 [0,1]），否则 b4（中位 0.500）与 b3（恒 1.000）的 τ 被网格上界夹住，
        # 校准退化为"网格下界"。手册 S3.6 的 GRID=0.01..0.50 仅适用 margin
        # 信号；hybrid 默认路由必须 --grid-max 1.0。
        _run([PY, "scripts/calibrate.py", "--split", "dev_calib",
              "--grid-max", "1.0"], "calibrate")

    # ---- 3. E1 主对比 ----
    # 注意：**不加 --overwrite**。生成走内容寻址缓存，重复条目 0 计算；
    # 去掉 overwrite 后，中断重跑只会补齐缺的部分（D21 后的 1.5B 重跑尤其需要）。
    if want("e1") and not args.skip_e1:
        for m in ("neverrag", "alwaysrag", "targ", "complexity", "legal_llm",
                  "legalgate"):
            _exp("e1", m, [], f"e1-{m}", split=e1s)
        # τ 缩放工作点（Pareto 曲线）
        for scale, name in ((0.5, "e1_tau0.5"), (0.75, "e1_tau0.75"),
                            (1.25, "e1_tau1.25")):
            _exp(name, "legalgate", ["--tau-scale", str(scale)], f"e1-tau{scale}",
                 split=e1s)
        # --tau-scaled-dir 必须传：e1_plot 只在给了它时才把 results/e1_tau* 读进来
        # 拼成 Pareto 曲线（内部用 `Path("results").glob("e1_tau*")`，所以这里给 results）。
        _run([PY, "scripts/e1_plot.py", "--split", e1s,
              "--tau-scaled-dir", "results"], "e1-summary")

    def _sharded(base: list[str], label: str) -> bool:
        """按 --shards 顺序切片跑一个脚本，再 --merge（合并 + 出图/分析）。

        E5/E3/E2 与 E1 同理（D21）：这些脚本内部也是"整方法跑完才落盘"，
        不切片则中断即丢整轮。``--merge`` 在这三个脚本里都同时承担
        "合并分片 + 跑分析/出图"，故成功与部分成功都要调一次。
        """
        if ns <= 1:
            return _run(base, label)
        ok = True
        for i in range(ns):
            ok = _run(base + ["--shard", str(i), "--nshards", str(ns)],
                      f"{label}-shard{i + 1}of{ns}") and ok
        # 有片失败也合并：部分结果 > 没有结果，n 会如实反映覆盖到的条目数
        _run(base + ["--merge"], f"{label}-merge")
        return ok

    # ---- 5. E5 时效性 ----
    if want("e5"):
        _sharded([PY, "scripts/e5_temporal.py", "--max-tokens", str(mt),
                  "--threads", str(th)], "e5")

    # ---- 6. E6 案号核验 ----
    if want("e6"):
        _run([PY, "scripts/e6_case_verify.py"], "e6")

    # ---- 7. 图表（**必须在 E3/E2 之前**，见下）----
    # plot_all 的 FIG_SPECS 是按**图目录**读 results/<图目录>/，与结果目录错位一格
    # （D23）：ablation 图归 figures/e3/，输入按 `results/e3/*.jsonl` 找。
    # 而 results/e3/ 正是 **E3 多轮**的逐条结果目录 —— E3 一跑完，
    # `_all_rows()` 就会把多轮记录喂给 `_plot_ablation()`，把 e2_ablation.py
    # 辛苦画出的 figures/e3/ablation.png **覆盖成一张用错数据画的图**
    # （2026-09-11 用哨兵文件实测确认：ablation.png 被重写成 36 KB 的 PNG）。
    # 处置：出图排在 E3/E2 之前 —— 此时 results/e3 与 results/e4 尚不存在，
    # plot_all 对这两张图只会记 skipped、不落盘（`_draw_real` 返回 None），
    # 随后由 e2_ablation.py / e3_multiturn.py 写出正确版本的图。
    if want("plots"):
        _run([PY, "scripts/plot_all.py", "--exp", "all"], "plots")

    # ---- 8. E3 多轮 ----
    if want("e3"):
        _sharded([PY, "scripts/e3_multiturn.py", "--split-e4",
                  "--max-tokens", str(mt), "--threads", str(th)], "e3")

    # ---- 9. E2 消融 ----
    # D24：手册 S6.6 原设定是在 test 全量（944 条）上跑 3 组共 10 个变体
    # （≈9440 条生成，CPU-only 下约 40 机时）。本机预算下改在 test_e1
    # （300 条，与 E1 主对比同一子集）上跑，使消融与主对比可直接对照；
    # --max-tokens 必须显式传，否则 e2_ablation 会退回自身默认值而与 E1 口径不一致。
    #
    # 与 E1 同样的理由（D21）：一组 A3 就是 6 个变体 × 300 条，一口气跑完要
    # 7 小时以上，中途被杀会丢整组。故这里也按 --shards 切片，每片独立落盘
    # results/e2_<g>/shards/，最后统一 --merge。
    # D33：A4（草稿长度 k=8..64）恢复进流水线——手册 S6.6 本就要求 4 组；
    # 当年因 CPU 预算被砍，API 后端下答案几乎全部命中内容缓存（k≥8 时 u 不变、
    # 路由不变），主要成本只剩本机草稿生成，补全后 E2 才完整。
    if want("e2"):
        for g in ("A1", "A2", "A3", "A4"):
            _sharded([PY, "scripts/e2_ablation.py", "--group", g,
                      "--split", "test_e1", "--max-tokens", str(mt),
                      "--threads", str(th)], f"e2-{g}")

    n_ok = sum(1 for s in STEPS if s["ok"])
    print(f"\n{'=' * 78}\n步骤完成 {n_ok}/{len(STEPS)}\n{'=' * 78}")
    for s in STEPS:
        print(f"  {'OK  ' if s['ok'] else 'FAIL'} {s['step']:16s} {s['seconds']:8.1f}s "
              f"{s['error'] or ''}")
    if stream is not None:
        stream.flush()
        stream.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
