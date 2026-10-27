#!/usr/bin/env python3
"""system-stats tool: local system diagnostics as a JSON report.

Arguments arrive as JSON in TOOL_ARGS, stdout is
the tool result. Read-only, stdlib only. One requested section prints its
result unwrapped; several print an object keyed by section name. Invalid
arguments print {"error": ...} and exit 1; a failed collector puts "error"
inside its section while the tool still exits 0.
"""

import ctypes
from ctypes import wintypes
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time

SYSTEM = platform.system()  # "Darwin" | "Linux" | "Windows"
DEFAULT_TOP_N = 5
MAX_TOP_N = 100
DEFAULT_BENCH_MB = 64
MAX_BENCH_MB = 1024
CMD_TIMEOUT = 10
CPU_SAMPLE_SECONDS = 1.0


def _run(cmd, encoding=None):
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding=encoding,
        timeout=CMD_TIMEOUT, check=True
    ).stdout


def _powershell_rows(script):
    """Return an array even when PowerShell produces zero or one rows."""
    command = (
        "$ErrorActionPreference = 'Stop'; "
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
        "ConvertTo-Json -InputObject @(& { " + script + " }) -Depth 4 -Compress"
    )
    try:
        out = _run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            encoding="utf-8",
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(e.stderr.strip() or str(e)) from e
    return json.loads(out)


def _windows_api(name, argtypes, restype):
    fn = getattr(ctypes.WinDLL("kernel32", use_last_error=True), name)
    fn.argtypes = argtypes
    fn.restype = restype
    return fn


def _windows_cpu_times():
    idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
    get_times = _windows_api(
        "GetSystemTimes", [ctypes.POINTER(wintypes.FILETIME)] * 3, wintypes.BOOL
    )
    if not get_times(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        raise ctypes.WinError(ctypes.get_last_error())

    def ticks(value):
        return (value.dwHighDateTime << 32) | value.dwLowDateTime

    # Kernel time includes idle time.
    return ticks(idle), ticks(kernel) + ticks(user)


def _load_windows():
    idle_before, total_before = _windows_cpu_times()
    start = time.perf_counter()
    time.sleep(CPU_SAMPLE_SECONDS)
    idle_after, total_after = _windows_cpu_times()
    elapsed = time.perf_counter() - start
    total = total_after - total_before
    idle = idle_after - idle_before
    if total <= 0 or idle < 0 or idle > total:
        raise ValueError("invalid Windows CPU time sample")
    return {
        "cpu_pct": round((total - idle) / total * 100, 1),
        "sample_seconds": round(elapsed, 3),
        "cpu_count": os.cpu_count(),
    }


def _section(fn):
    """Run a collector; on failure report the error inside the section."""
    try:
        return fn()
    except Exception as e:
        return {"error": str(e)}


def collect_load():
    if SYSTEM == "Windows":
        return _load_windows()
    l1, l5, l15 = os.getloadavg()
    return {
        "loadavg_1m": round(l1, 2),
        "loadavg_5m": round(l5, 2),
        "loadavg_15m": round(l15, 2),
        "cpu_count": os.cpu_count(),
    }


def _mem_darwin():
    total = int(_run(["sysctl", "-n", "hw.memsize"]).strip())
    out = _run(["vm_stat"])
    page_size = int(re.search(r"page size of (\d+) bytes", out).group(1))
    pages = {
        name: int(count)
        for name, count in re.findall(r"Pages ([\w ]+):\s+(\d+)\.", out)
    }

    def get(*names):
        return sum(pages.get(n, 0) for n in names) * page_size

    return {
        "total_bytes": total,
        "used_bytes": get("active", "wired", "wired down", "occupied by compressor"),
        "available_bytes": get("free", "inactive", "speculative"),
    }


def _mem_linux():
    info = {}
    for line in open("/proc/meminfo"):
        key, rest = line.split(":", 1)
        info[key] = int(rest.strip().split()[0]) * 1024  # kB -> bytes
    return {
        "total_bytes": info["MemTotal"],
        "used_bytes": info["MemTotal"] - info.get("MemAvailable", info["MemFree"]),
        "available_bytes": info.get("MemAvailable", info["MemFree"]),
    }


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _mem_windows():
    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(status)
    get_status = _windows_api(
        "GlobalMemoryStatusEx", [ctypes.POINTER(_MemoryStatusEx)], wintypes.BOOL
    )
    if not get_status(ctypes.byref(status)):
        raise ctypes.WinError(ctypes.get_last_error())
    return {
        "total_bytes": status.ullTotalPhys,
        "used_bytes": status.ullTotalPhys - status.ullAvailPhys,
        "available_bytes": status.ullAvailPhys,
    }


def collect_mem():
    if SYSTEM == "Windows":
        return _mem_windows()
    if SYSTEM == "Darwin":
        return _mem_darwin()
    if SYSTEM == "Linux":
        return _mem_linux()
    return {"error": f"unsupported platform: {SYSTEM}"}


def collect_disk():
    path = os.path.abspath(os.sep) if SYSTEM == "Windows" else "/"
    usage = shutil.disk_usage(path)
    return {
        "path": path,
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "used_pct": round(usage.used / usage.total * 100, 1),
    }


def _uptime_seconds():
    if SYSTEM == "Windows":
        get_ticks = _windows_api("GetTickCount64", [], ctypes.c_ulonglong)
        return get_ticks() / 1000
    if SYSTEM == "Darwin":
        out = _run(["sysctl", "-n", "kern.boottime"])
        return time.time() - float(re.search(r"sec = (\d+)", out).group(1))
    if SYSTEM == "Linux":
        return float(open("/proc/uptime").read().split()[0])
    raise ValueError(f"unsupported platform: {SYSTEM}")


def collect_uptime():
    total = int(_uptime_seconds())
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    return {
        "uptime_seconds": total,
        "uptime_human": f"{days}d {hours}h {rem // 60}m",
    }


def _top_windows(top_n):
    rows = _powershell_rows(
        "Get-CimInstance Win32_PerfFormattedData_PerfProc_Process | "
        "Where-Object { $_.IDProcess -gt 0 } | "
        "Select-Object IDProcess, Name, PercentProcessorTime, WorkingSet"
    )
    total_mem = _mem_windows()["total_bytes"]
    cpu_count = os.cpu_count() or 1
    rows.sort(key=lambda row: (-int(row["PercentProcessorTime"]), int(row["IDProcess"])))
    return {
        "processes": [
            {
                "pid": int(row["IDProcess"]),
                "cpu_pct": round(int(row["PercentProcessorTime"]) / cpu_count, 1),
                "mem_pct": round(int(row["WorkingSet"]) / total_mem * 100, 1),
                "cmd": row["Name"],
            }
            for row in rows[:top_n]
        ]
    }


def collect_top(top_n):
    if SYSTEM == "Windows":
        return _top_windows(top_n)
    if SYSTEM == "Darwin":
        cmd = ["ps", "-Ao", "pid=,pcpu=,pmem=,comm=", "-r"]
    elif SYSTEM == "Linux":
        cmd = ["ps", "-eo", "pid=,pcpu=,pmem=,comm=", "--sort=-pcpu"]
    else:
        return {"error": f"unsupported platform: {SYSTEM}"}
    out = _run(cmd)
    procs = []
    for line in out.splitlines()[:top_n]:
        pid, cpu, mem, comm = line.split(None, 3)
        procs.append(
            {"pid": int(pid), "cpu_pct": float(cpu), "mem_pct": float(mem), "cmd": comm.strip()}
        )
    return {"processes": procs}


def collect_ports():
    if SYSTEM == "Windows":
        rows = _powershell_rows(
            "$names = @{}; "
            "Get-Process | ForEach-Object { $names[[int]$_.Id] = $_.ProcessName }; "
            "Get-NetTCPConnection | Where-Object { $_.State -eq 'Listen' } | "
            "ForEach-Object { "
            "$name = $names[[int]$_.OwningProcess]; "
            "if (-not $name) { $name = 'PID ' + $_.OwningProcess }; "
            "[pscustomobject]@{ port = [int]$_.LocalPort; process = $name } }"
        )
        return {
            "listening": [
                {"port": port, "process": name}
                for port, name in sorted({
                    (int(row["port"]), row["process"]) for row in rows
                })
            ]
        }
    out = _run(["lsof", "+c", "0", "-nP", "-iTCP", "-sTCP:LISTEN"])
    seen = {}
    for line in out.splitlines()[1:]:
        parts = line.split()
        m = re.search(r":(\d+) \(LISTEN\)", line)
        if m:
            seen.setdefault((int(m.group(1)), parts[0]), None)
    return {
        "listening": [
            {"port": port, "process": cmd} for port, cmd in sorted(seen)
        ]
    }


def collect_bench(mb):
    block = os.urandom(1024 * 1024)
    start = time.perf_counter()
    h = hashlib.sha256()
    for _ in range(mb):
        h.update(block)
    elapsed = time.perf_counter() - start
    return {
        "mb_hashed": mb,
        "seconds": round(elapsed, 3),
        "mb_per_sec": round(mb / elapsed, 1),
        "algo": "sha256",
    }


SECTIONS = {
    "load": collect_load,
    "mem": collect_mem,
    "disk": collect_disk,
    "uptime": collect_uptime,
    "top": collect_top,
    "ports": collect_ports,
    "bench": collect_bench,
}


def _int_arg(args, name, default, lo, hi):
    v = args.get(name, default)
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError(f"{name} must be an integer, got {v!r}")
    if not lo <= v <= hi:
        raise ValueError(f"{name} must be {lo}..{hi}, got {v}")
    return v


def main() -> int:
    try:
        args = json.loads(os.environ.get("TOOL_ARGS", "{}"))
        if not isinstance(args, dict):
            raise ValueError("TOOL_ARGS must be a JSON object")
        section = args.get("section", "load,mem,disk")
        if not isinstance(section, str):
            raise ValueError(f"section must be a string, got {section!r}")
        names = list(dict.fromkeys(s.strip() for s in section.split(",") if s.strip()))
        if not names:
            raise ValueError("section must not be empty")
        if "all" in names:
            if len(names) > 1:
                raise ValueError("'all' cannot be combined with other sections")
            names = list(SECTIONS)
        else:
            bad = sorted(set(names) - set(SECTIONS))
            if bad:
                raise ValueError(
                    f"unknown section(s): {', '.join(bad)} "
                    f"(valid: all, {', '.join(SECTIONS)})"
                )
        top_n = _int_arg(args, "top_n", DEFAULT_TOP_N, 1, MAX_TOP_N)
        mb = _int_arg(args, "mb", DEFAULT_BENCH_MB, 1, MAX_BENCH_MB)
    except (ValueError, TypeError) as e:
        print(json.dumps({"error": f"invalid arguments: {e}"}))
        return 1

    report = {}
    for name in names:
        fn = SECTIONS[name]
        if name == "top":
            report[name] = _section(lambda: fn(top_n))
        elif name == "bench":
            report[name] = _section(lambda: fn(mb))
        else:
            report[name] = _section(fn)

    print(json.dumps(report if len(report) > 1 else report[names[0]], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
