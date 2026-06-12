"""System resource monitoring helpers."""

from __future__ import annotations

import os
import shutil
import subprocess


def read_proc_meminfo() -> dict[str, float]:
    data = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                key, value = line.split(":", 1)
                parts = value.strip().split()
                if parts:
                    data[key] = float(parts[0])
    except OSError:
        return {}
    return data


def cpu_load() -> float | None:
    try:
        with open("/proc/loadavg", "r", encoding="utf-8") as handle:
            return float(handle.read().split()[0])
    except OSError:
        return None


def cpu_util_pct() -> float | None:
    load = cpu_load()
    cpu_count = os.cpu_count() or 1
    if load is None or cpu_count <= 0:
        return None
    return max(0.0, min((load / cpu_count) * 100.0, 100.0))


def memory_snapshot() -> dict[str, float | None]:
    info = read_proc_meminfo()
    total = info.get("MemTotal")
    available = info.get("MemAvailable")
    if total is None or available is None:
        return {"total_gb": None, "used_gb": None, "used_pct": None}
    used = total - available
    return {
        "total_gb": total / 1024 / 1024,
        "used_gb": used / 1024 / 1024,
        "used_pct": (used / total) * 100,
    }


def disk_snapshot(path: str = "/") -> dict[str, float]:
    usage = shutil.disk_usage(path)
    return {
        "total_gb": usage.total / 1024 / 1024 / 1024,
        "used_gb": (usage.total - usage.free) / 1024 / 1024 / 1024,
        "used_pct": ((usage.total - usage.free) / usage.total) * 100 if usage.total else 0.0,
    }


def gpu_snapshot() -> list[dict]:
    command = [
        "nvidia-smi",
        "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu",
        "--format=csv,noheader,nounits",
    ]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return []
    if proc.returncode != 0:
        return []
    rows = []
    for line in proc.stdout.strip().splitlines():
        parts = [item.strip() for item in line.split(",")]
        if len(parts) != 6:
            continue
        rows.append(
            {
                "index": parts[0],
                "name": parts[1],
                "util_pct": float(parts[2]),
                "mem_used_mb": float(parts[3]),
                "mem_total_mb": float(parts[4]),
                "temp_c": float(parts[5]),
            }
        )
    return rows


def system_snapshot() -> dict:
    return {
        "cpu_load": cpu_load(),
        "cpu_util_pct": cpu_util_pct(),
        "memory": memory_snapshot(),
        "disk": disk_snapshot("/workspace"),
        "gpus": gpu_snapshot(),
    }
