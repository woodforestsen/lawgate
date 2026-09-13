# -*- coding: utf-8 -*-
"""内存看门狗（本次会话新增，配合 D21 的长跑实验）。

背景：1.5B fp32 的**每个**模型进程常驻约 6 GB（实测 `WS≈5.9 GB`，权重文件本身
3.09 GB）。长跑期间一旦出现"额外冒出来的 python 进程"（例如误双击
`models/**/*.safetensors`、编辑器预览权重文件 —— 实测出现过
`forrtl: error (200): program aborting due to window-CLOSE event`），
可用内存会被迅速吃掉，**跑了几小时的实验进程会被系统 OOM 掉**。

⚠️ **内存口径（2026-09-11 复测）**：本机物理内存 **32 GB**
（`Win32_OperatingSystem.TotalVisibleMemorySize` = 32373 MB），实测空闲 12.4 GB。
本脚本原按"物理内存约 16 GB"编写（那是当时的读数，见 D19/D24），
现在两个 6 GB 进程可以并存；但**默认阈值仍然保守**——因为真正的瓶颈是 CPU
（两个实验进程互抢 18 核，总产出不会变快，见 D21），内存阈值只是兜底防误启。
按需用 `--min-free-mb` 调。

本脚本只做一件事：每 ``--interval`` 秒检查可用内存；
若低于 ``--min-free-mb``，杀掉**除白名单之外**的最胖 python 进程（一次一个），
并把动作写进 ``docs/mem_watchdog.log``。白名单来自命令行 ``--keep``（pid
数字或 start-time 为 ``HH:MM:SS`` 形式），本脚本自己的 pid 永远在白名单内。

    python scripts/mem_watchdog.py --keep 12496 --min-free-mb 2500 --interval 20
"""
from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

LOG = Path("docs/mem_watchdog.log")


def available_mb() -> int:
    """用 GlobalMemoryStatusEx 读可用物理内存（MB）；失败返回一个大值（不误杀）。"""
    try:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return 1 << 30
        return int(st.ullAvailPhys / (1024 * 1024))
    except Exception:  # noqa: BLE001
        return 1 << 30


def python_procs() -> list[dict]:
    """列出 python 进程（pid / 命令行 / 工作集）。

    用 ``Get-Process`` 而不是 ``Get-CimInstance``：后者的 `-Filter` 引号在
    嵌套调用里极容易被打散（实测直接报 "The string is missing the terminator"），
    而 ``Process.CommandLine`` 在 PowerShell 5.1 上不需要提权。
    """
    ps = ("Get-Process | Where-Object { $_.ProcessName -like '*python*' } | "
          "ForEach-Object { [pscustomobject]@{ pid = $_.Id; "
          "cmd = $_.CommandLine; ws_mb = [int]($_.WorkingSet64/1MB) } } | "
          "ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=60,
                             encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return []
    import json

    txt = (out.stdout or "").strip()
    if not txt:
        return []
    try:
        data = json.loads(txt)
    except Exception:  # noqa: BLE001
        return []
    if isinstance(data, dict):
        data = [data]
    return [{"pid": int(d.get("pid") or 0),
             "cmd": str(d.get("cmd") or ""),
             "ws_mb": int(d.get("ws_mb") or 0)}
            for d in data]


def kill(pid: int) -> str:
    try:
        out = subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                             capture_output=True, text=True, timeout=30,
                             encoding="utf-8", errors="replace")
        return (out.stdout or out.stderr or "").strip()[:200]
    except Exception as exc:  # noqa: BLE001
        return f"taskkill 失败：{exc}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", nargs="*", default=[],
                    help="白名单 pid（必须是本次实验进程）")
    ap.add_argument("--min-free-mb", type=int, default=2500)
    ap.add_argument("--interval", type=int, default=20)
    ap.add_argument("--max-kills", type=int, default=3)
    ap.add_argument("--minutes", type=float, default=0,
                    help="运行时长（分钟）；0 = 一直跑")
    args = ap.parse_args()
    keep = {int(p) for p in args.keep if str(p).isdigit()} | {0}
    keep.add(__import__("os").getpid())

    LOG.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    kills = 0
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"\n=== watchdog 启动 {datetime.now():%Y-%m-%d %H:%M:%S} "
                 f"keep={sorted(keep)} 阈值={args.min_free_mb}MB ===\n")
        while True:
            free = available_mb()
            procs = python_procs()
            big = [p for p in procs if p["pid"] not in keep and p["ws_mb"] > 2000]
            line = (f"[{datetime.now():%H:%M:%S}] free={free}MB "
                    f"python={len(procs)} 可杀大进程={[(p['pid'], p['ws_mb']) for p in big]}")
            if free < args.min_free_mb and big and kills < args.max_kills:
                victim = max(big, key=lambda p: p["ws_mb"])
                msg = kill(victim["pid"])
                kills += 1
                line += (f" → 内存不足，杀掉 pid={victim['pid']} "
                         f"({victim['ws_mb']}MB)：{msg}")
            fh.write(line + "\n")
            fh.flush()
            if args.minutes and (time.time() - t0) > args.minutes * 60:
                fh.write("=== watchdog 到时退出 ===\n")
                return 0
            time.sleep(max(5, args.interval))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
