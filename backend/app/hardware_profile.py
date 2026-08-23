"""完全在本机执行的硬件画像与受限性能探测。"""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import get_settings


def _memory_windows() -> tuple[int, int]:
    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("memory_load", ctypes.c_ulong),
            ("total_physical", ctypes.c_ulonglong),
            ("available_physical", ctypes.c_ulonglong),
            ("total_page_file", ctypes.c_ulonglong),
            ("available_page_file", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("available_virtual", ctypes.c_ulonglong),
            ("available_extended_virtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return 0, 0
    return int(status.total_physical), int(status.available_physical)


def _memory_linux() -> tuple[int, int]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, _, raw = line.partition(":")
            if key in {"MemTotal", "MemAvailable"}:
                values[key] = int(raw.strip().split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        return 0, 0
    return values.get("MemTotal", 0), values.get("MemAvailable", 0)


def _memory_macos() -> tuple[int, int]:
    try:
        total = int(
            subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout.strip()
        )
        page_size = int(
            subprocess.run(
                ["sysctl", "-n", "hw.pagesize"],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout.strip()
        )
        output = subprocess.run(
            ["vm_stat"], check=True, capture_output=True, text=True, timeout=2
        ).stdout
        pages = 0
        for line in output.splitlines():
            if line.startswith(("Pages free", "Pages inactive", "Pages speculative")):
                pages += int(line.rsplit(":", 1)[1].strip().rstrip("."))
        return total, pages * page_size
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return 0, 0


def _memory_bytes() -> tuple[int, int]:
    system = platform.system().lower()
    if system == "windows":
        return _memory_windows()
    if system == "linux":
        return _memory_linux()
    if system == "darwin":
        return _memory_macos()
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        total = page_size * os.sysconf("SC_PHYS_PAGES")
        available = page_size * os.sysconf("SC_AVPHYS_PAGES")
        return int(total), int(available)
    except (AttributeError, OSError, ValueError):
        return 0, 0


def _cpu_flags() -> list[str]:
    flags: set[str] = set()
    if platform.system().lower() == "linux":
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
                if line.lower().startswith(("flags", "features")):
                    flags.update(line.partition(":")[2].strip().lower().split())
        except OSError:
            pass
    identifier = os.environ.get("PROCESSOR_IDENTIFIER", "").lower()
    for flag in ("avx2", "avx", "sse4_2", "neon"):
        if flag in identifier:
            flags.add(flag)
    return sorted(flags.intersection({"avx", "avx2", "sse4_2", "neon", "asimd", "fma"}))


def _process_rss_bytes() -> int:
    if platform.system().lower() == "windows":
        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("page_fault_count", ctypes.c_ulong),
                ("peak_working_set_size", ctypes.c_size_t),
                ("working_set_size", ctypes.c_size_t),
                ("quota_peak_paged_pool_usage", ctypes.c_size_t),
                ("quota_paged_pool_usage", ctypes.c_size_t),
                ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
                ("quota_non_paged_pool_usage", ctypes.c_size_t),
                ("pagefile_usage", ctypes.c_size_t),
                ("peak_pagefile_usage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        try:
            if ctypes.windll.psapi.GetProcessMemoryInfo(
                ctypes.windll.kernel32.GetCurrentProcess(),
                ctypes.byref(counters),
                counters.cb,
            ):
                return int(counters.working_set_size)
        except (AttributeError, OSError):
            return 0
    if platform.system().lower() == "linux":
        try:
            for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
        except (OSError, ValueError, IndexError):
            return 0
    return 0


def _gpu_profile() -> tuple[list[str], int | None, list[str]]:
    backends: list[str] = []
    names: list[str] = []
    memory_mb: int | None = None
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        try:
            output = subprocess.run(
                [
                    nvidia_smi,
                    "--query-gpu=name,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout
            values: list[int] = []
            for line in output.splitlines():
                name, _, raw_memory = line.rpartition(",")
                if name.strip():
                    names.append(name.strip()[:120])
                values.append(int(raw_memory.strip()))
            if values:
                backends.append("cuda")
                memory_mb = max(values)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    if platform.system().lower() == "darwin" and platform.machine().lower() in {"arm64", "aarch64"}:
        backends.append("metal")
        names.append("Apple 芯片统一内存图形核心")
    return backends, memory_mb, names


def collect_hardware_profile() -> dict[str, object]:
    total, available = _memory_bytes()
    total_mb = total // 1024**2
    available_mb = available // 1024**2
    reserve_mb = max(2_048, int(total_mb * 0.25)) if total_mb else 2_048
    settings = get_settings()
    disk = shutil.disk_usage(settings.models_dir)
    gpu_backends, gpu_memory_mb, gpu_names = _gpu_profile()
    machine = platform.machine().lower()
    architecture = "amd64" if machine in {"amd64", "x86_64"} else "arm64" if machine in {"arm64", "aarch64"} else machine or "unknown"
    runtime_backends = ["cpu"]
    runtime_backends.extend(gpu_backends)
    return {
        "platform": platform.system().lower() or "unknown",
        "platform_version": platform.release(),
        "architecture": architecture,
        "cpu_name": (platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER") or "未知处理器")[:160],
        "cpu_cores": max(1, os.cpu_count() or 1),
        "cpu_flags": _cpu_flags(),
        "total_memory_mb": int(total_mb),
        "available_memory_mb": int(available_mb),
        "reserved_memory_mb": reserve_mb,
        "model_disk_free_mb": int(disk.free // 1024**2),
        "gpu_backends": gpu_backends,
        "gpu_memory_mb": gpu_memory_mb,
        "gpu_names": gpu_names,
        "runtime_backends": sorted(set(runtime_backends)),
        "partyops_rss_mb": int(_process_rss_bytes() // 1024**2),
        "detected_at": datetime.now(timezone.utc),
        "privacy_notice": "检测只在本机执行，不上传硬件信息、文件名、提示词或业务数据。",
    }


def run_light_benchmark() -> dict[str, object]:
    """三秒内完成的 CPU 哈希探测；不在冻结应用中启动解释器副本。"""

    if getattr(sys, "frozen", False):
        return {
            "available": False,
            "score": 0,
            "duration_ms": 0,
            "message": "当前冻结运行时未启用隔离性能测试；硬件容量判断仍然有效。",
        }
    script = (
        "import hashlib,time;d=b'PartyOps-local-benchmark'*4096;"
        "s=time.perf_counter();n=0;"
        "\nwhile time.perf_counter()-s<0.75: hashlib.sha256(d).digest();n+=1"
        "\nprint(n)"
    )
    started = time.perf_counter()
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-c", script],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
            env={**os.environ, "PYTHONUTF8": "1"},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        score = max(0, int(result.stdout.strip()))
        return {
            "available": True,
            "score": score,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "message": "隔离性能测试完成；结果只用于本机模型档位建议。",
        }
    except (OSError, ValueError, subprocess.SubprocessError):
        return {
            "available": False,
            "score": 0,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "message": "性能测试未完成，系统已保留容量检测结果。",
        }
