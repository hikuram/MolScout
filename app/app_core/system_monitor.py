"""System resource monitoring and storage admission helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .paths import DATA_DIR

DEFAULT_DISK_MAX_USED_PCT = 90.0
DEFAULT_DISK_MIN_FREE_GB = 5.0


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value


def disk_limits() -> tuple[float, float]:
    max_used_pct = _env_float("MOLSCOUT_DISK_MAX_USED_PCT", DEFAULT_DISK_MAX_USED_PCT)
    min_free_gb = _env_float("MOLSCOUT_DISK_MIN_FREE_GB", DEFAULT_DISK_MIN_FREE_GB)
    max_used_pct = max(0.0, min(max_used_pct, 100.0))
    min_free_gb = max(0.0, min_free_gb)
    return max_used_pct, min_free_gb


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


def disk_snapshot(path: str | Path = "/") -> dict[str, float | str | None]:
    target = str(path)
    try:
        usage = shutil.disk_usage(target)
    except OSError as exc:
        return {
            "path": target,
            "total_gb": None,
            "used_gb": None,
            "free_gb": None,
            "used_pct": None,
            "error": f"{type(exc).__name__}: {exc}",
        }
    gib = 1024 ** 3
    used = usage.total - usage.free
    return {
        "path": target,
        "total_gb": usage.total / gib,
        "used_gb": used / gib,
        "free_gb": usage.free / gib,
        "used_pct": (used / usage.total) * 100 if usage.total else 0.0,
        "error": "",
    }


def evaluate_storage_admission(
    disk: dict[str, float | str | None],
    *,
    max_used_pct: float | None = None,
    min_free_gb: float | None = None,
) -> dict:
    default_max_used_pct, default_min_free_gb = disk_limits()
    max_used_pct = default_max_used_pct if max_used_pct is None else float(max_used_pct)
    min_free_gb = default_min_free_gb if min_free_gb is None else float(min_free_gb)

    used_pct = disk.get("used_pct")
    free_gb = disk.get("free_gb")
    error = str(disk.get("error") or "").strip()
    if error or not isinstance(used_pct, (int, float)) or not isinstance(free_gb, (int, float)):
        return {
            "allowed": False,
            "code": "storage_unavailable",
            "reason": "Storage usage could not be verified. New work is blocked as a safety measure.",
            "max_used_pct": max_used_pct,
            "min_free_gb": min_free_gb,
            "disk": disk,
        }

    reasons: list[str] = []
    if used_pct >= max_used_pct:
        reasons.append(f"disk usage is {used_pct:.1f}% (limit {max_used_pct:.1f}%)")
    if free_gb < min_free_gb:
        reasons.append(f"free space is {free_gb:.1f} GiB (minimum {min_free_gb:.1f} GiB)")

    allowed = not reasons
    return {
        "allowed": allowed,
        "code": "ok" if allowed else "storage_limit",
        "reason": "Storage capacity is available." if allowed else "; ".join(reasons),
        "max_used_pct": max_used_pct,
        "min_free_gb": min_free_gb,
        "disk": disk,
    }


def storage_admission_status(path: str | Path = DATA_DIR) -> dict:
    return evaluate_storage_admission(disk_snapshot(path))


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
    disk = disk_snapshot(DATA_DIR)
    return {
        "cpu_load": cpu_load(),
        "cpu_util_pct": cpu_util_pct(),
        "memory": memory_snapshot(),
        "disk": disk,
        "storage_admission": evaluate_storage_admission(disk),
        "gpus": gpu_snapshot(),
    }
