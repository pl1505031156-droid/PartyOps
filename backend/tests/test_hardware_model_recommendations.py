"""本机硬件检测和模型分档不上传数据，也不夸大不适配设备。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import hardware_profile
from app.model_catalog import MODEL_CATALOG, recommend_models


def _profile(**overrides) -> dict[str, object]:
    value: dict[str, object] = {
        "platform": "windows",
        "platform_version": "11",
        "architecture": "amd64",
        "cpu_name": "Test CPU",
        "cpu_cores": 16,
        "cpu_flags": ["avx2"],
        "total_memory_mb": 32_768,
        "available_memory_mb": 24_576,
        "reserved_memory_mb": 8_192,
        "model_disk_free_mb": 100_000,
        "gpu_backends": [],
        "gpu_memory_mb": None,
        "gpu_names": [],
        "runtime_backends": ["cpu"],
        "partyops_rss_mb": 512,
        "detected_at": "2026-08-23T00:00:00Z",
        "privacy_notice": "只在本机执行",
    }
    value.update(overrides)
    return value


def test_model_catalog_spans_basic_to_flagship_and_reserves_capacity() -> None:
    assert {item["tier"] for item in MODEL_CATALOG}.issuperset({"基础", "均衡", "专业", "旗舰", "服务器"})
    assert any(item["kind"] == "intent_router" for item in MODEL_CATALOG)
    results = recommend_models(_profile())
    by_id = {item["id"]: item for item in results}
    assert by_id["qwen3-0.6b-gguf"]["status"] == "流畅"
    assert by_id["qwen3-32b-gguf"]["status"] == "不建议"
    assert by_id["qwen3-235b-a22b-gguf"]["delivery"] == "official"
    low_disk = recommend_models(_profile(model_disk_free_mb=500))
    assert all(item["status"] == "不建议" for item in low_disk if item["disk_mb"] >= 512)
    incompatible = recommend_models(_profile(platform="freebsd", architecture="riscv64"))
    assert all(item["status"] == "不建议" for item in incompatible)
    constrained = recommend_models(
        _profile(total_memory_mb=16_384, available_memory_mb=2_200, reserved_memory_mb=2_048)
    )
    assert any(item["status"] == "可用" for item in constrained)
    accelerated = recommend_models(
        _profile(total_memory_mb=65_536, available_memory_mb=49_152, gpu_backends=["cuda"], gpu_memory_mb=24_576)
    )
    accelerated_by_id = {item["id"]: item for item in accelerated}
    assert "硬件加速" in accelerated_by_id["qwen3-14b-gguf"]["reason"]


def test_hardware_endpoints_are_admin_only_and_return_local_profile(
    client: TestClient, admin: dict, monkeypatch
) -> None:
    monkeypatch.setattr("app.routers.ai.collect_hardware_profile", lambda: _profile())
    profile = client.get("/api/v1/ai/hardware-profile")
    assert profile.status_code == 200, profile.text
    assert profile.json()["reserved_memory_mb"] == 8192
    recommendations = client.get("/api/v1/ai/model-recommendations")
    assert recommendations.status_code == 200, recommendations.text
    assert recommendations.json()[0]["status"] in {"流畅", "可用", "不建议"}
    monkeypatch.setattr("app.routers.ai.run_light_benchmark", lambda: {"available": True, "score": 42, "duration_ms": 10, "message": "完成"})
    benchmark = client.post("/api/v1/ai/hardware-profile/benchmark")
    assert benchmark.status_code == 200 and benchmark.json()["score"] == 42


def test_memory_probes_cover_supported_platforms_and_safe_fallbacks(monkeypatch) -> None:
    class Kernel32:
        def __init__(self, succeeds: bool) -> None:
            self.succeeds = succeeds

        def GlobalMemoryStatusEx(self, pointer) -> bool:
            if self.succeeds:
                pointer._obj.total_physical = 16 * 1024**3
                pointer._obj.available_physical = 9 * 1024**3
            return self.succeeds

    monkeypatch.setattr(
        hardware_profile.ctypes,
        "windll",
        SimpleNamespace(kernel32=Kernel32(True)),
        raising=False,
    )
    assert hardware_profile._memory_windows() == (16 * 1024**3, 9 * 1024**3)
    monkeypatch.setattr(
        hardware_profile.ctypes,
        "windll",
        SimpleNamespace(kernel32=Kernel32(False)),
        raising=False,
    )
    assert hardware_profile._memory_windows() == (0, 0)

    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, **kwargs: "MemTotal: 8192 kB\nMemFree: 12 kB\nMemAvailable: 4096 kB\n",
    )
    assert hardware_profile._memory_linux() == (8192 * 1024, 4096 * 1024)
    monkeypatch.setattr(Path, "read_text", lambda self, **kwargs: (_ for _ in ()).throw(OSError()))
    assert hardware_profile._memory_linux() == (0, 0)
    monkeypatch.setattr(Path, "read_text", lambda self, **kwargs: "MemTotal: unknown kB")
    assert hardware_profile._memory_linux() == (0, 0)

    responses = iter(
        [
            SimpleNamespace(stdout=str(16 * 1024**3)),
            SimpleNamespace(stdout="4096"),
            SimpleNamespace(
                stdout=(
                    "Mach Virtual Memory Statistics\n"
                    "Pages free: 100.\nPages inactive: 200.\n"
                    "Pages speculative: 50.\nPages wired down: 10.\n"
                )
            ),
        ]
    )
    monkeypatch.setattr(hardware_profile.subprocess, "run", lambda *args, **kwargs: next(responses))
    assert hardware_profile._memory_macos() == (16 * 1024**3, 350 * 4096)
    monkeypatch.setattr(
        hardware_profile.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError()),
    )
    assert hardware_profile._memory_macos() == (0, 0)

    monkeypatch.setattr(hardware_profile.platform, "system", lambda: "Windows")
    monkeypatch.setattr(hardware_profile, "_memory_windows", lambda: (1, 2))
    assert hardware_profile._memory_bytes() == (1, 2)
    monkeypatch.setattr(hardware_profile.platform, "system", lambda: "Linux")
    monkeypatch.setattr(hardware_profile, "_memory_linux", lambda: (3, 4))
    assert hardware_profile._memory_bytes() == (3, 4)
    monkeypatch.setattr(hardware_profile.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(hardware_profile, "_memory_macos", lambda: (5, 6))
    assert hardware_profile._memory_bytes() == (5, 6)
    monkeypatch.setattr(hardware_profile.platform, "system", lambda: "FreeBSD")
    values = {"SC_PAGE_SIZE": 4096, "SC_PHYS_PAGES": 100, "SC_AVPHYS_PAGES": 25}
    monkeypatch.setattr(hardware_profile.os, "sysconf", lambda key: values[key], raising=False)
    assert hardware_profile._memory_bytes() == (409600, 102400)
    monkeypatch.setattr(
        hardware_profile.os,
        "sysconf",
        lambda key: (_ for _ in ()).throw(ValueError()),
        raising=False,
    )
    assert hardware_profile._memory_bytes() == (0, 0)


def test_cpu_process_and_gpu_probes_degrade_without_breaking_startup(monkeypatch) -> None:
    monkeypatch.setattr(hardware_profile.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, **kwargs: (
            "processor: 0\nflags: fpu avx avx2 sse4_2 fma\n"
            "Features: fp asimd neon\n"
            if str(self).endswith("cpuinfo")
            else "Name: partyops\nVmRSS: 12345 kB\n"
        ),
    )
    monkeypatch.setenv("PROCESSOR_IDENTIFIER", "AVX2 compatible")
    assert set(hardware_profile._cpu_flags()) == {"asimd", "avx", "avx2", "fma", "neon", "sse4_2"}
    assert hardware_profile._process_rss_bytes() == 12345 * 1024
    monkeypatch.setattr(Path, "read_text", lambda self, **kwargs: (_ for _ in ()).throw(OSError()))
    assert hardware_profile._cpu_flags() == ["avx", "avx2"]
    assert hardware_profile._process_rss_bytes() == 0

    class Psapi:
        @staticmethod
        def GetProcessMemoryInfo(process, pointer, size) -> bool:
            pointer._obj.working_set_size = 54321
            return True

    class Kernel32:
        @staticmethod
        def GetCurrentProcess() -> int:
            return 1

    monkeypatch.setattr(hardware_profile.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        hardware_profile.ctypes,
        "windll",
        SimpleNamespace(psapi=Psapi(), kernel32=Kernel32()),
        raising=False,
    )
    assert hardware_profile._process_rss_bytes() == 54321
    monkeypatch.setattr(
        hardware_profile.ctypes,
        "windll",
        SimpleNamespace(psapi=SimpleNamespace(), kernel32=Kernel32()),
        raising=False,
    )
    assert hardware_profile._process_rss_bytes() == 0
    monkeypatch.setattr(hardware_profile.platform, "system", lambda: "Other")
    assert hardware_profile._process_rss_bytes() == 0

    monkeypatch.setattr(hardware_profile.shutil, "which", lambda name: "nvidia-smi")
    monkeypatch.setattr(
        hardware_profile.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="RTX 4090, 24564\n, 1024\n"),
    )
    monkeypatch.setattr(hardware_profile.platform, "system", lambda: "Linux")
    backends, memory, names = hardware_profile._gpu_profile()
    assert backends == ["cuda"] and memory == 24564 and names == ["RTX 4090"]
    monkeypatch.setattr(
        hardware_profile.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError()),
    )
    assert hardware_profile._gpu_profile() == ([], None, [])
    monkeypatch.setattr(hardware_profile.shutil, "which", lambda name: None)
    monkeypatch.setattr(hardware_profile.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(hardware_profile.platform, "machine", lambda: "arm64")
    assert hardware_profile._gpu_profile() == (
        ["metal"],
        None,
        ["Apple 芯片统一内存图形核心"],
    )


@pytest.mark.parametrize(
    ("machine", "expected"),
    [("x86_64", "amd64"), ("aarch64", "arm64"), ("loongarch64", "loongarch64"), ("", "unknown")],
)
def test_collect_hardware_profile_normalizes_architecture_and_capacity(
    monkeypatch, tmp_path: Path, machine: str, expected: str
) -> None:
    monkeypatch.setattr(hardware_profile, "_memory_bytes", lambda: (0, 0))
    monkeypatch.setattr(hardware_profile, "_gpu_profile", lambda: ([], None, []))
    monkeypatch.setattr(hardware_profile, "_cpu_flags", lambda: [])
    monkeypatch.setattr(hardware_profile, "_process_rss_bytes", lambda: 0)
    monkeypatch.setattr(hardware_profile.platform, "machine", lambda: machine)
    monkeypatch.setattr(hardware_profile.platform, "system", lambda: "")
    monkeypatch.setattr(hardware_profile.platform, "release", lambda: "test")
    monkeypatch.setattr(hardware_profile.platform, "processor", lambda: "")
    monkeypatch.delenv("PROCESSOR_IDENTIFIER", raising=False)
    monkeypatch.setattr(
        hardware_profile,
        "get_settings",
        lambda: SimpleNamespace(models_dir=tmp_path),
    )
    monkeypatch.setattr(
        hardware_profile.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(free=10 * 1024**3),
    )
    profile = hardware_profile.collect_hardware_profile()
    assert profile["architecture"] == expected
    assert profile["platform"] == "unknown"
    assert profile["reserved_memory_mb"] == 2048
    assert profile["cpu_name"] == "未知处理器"


def test_light_benchmark_covers_frozen_success_and_failure(monkeypatch) -> None:
    monkeypatch.setattr(hardware_profile.sys, "frozen", True, raising=False)
    assert hardware_profile.run_light_benchmark()["available"] is False
    monkeypatch.setattr(hardware_profile.sys, "frozen", False, raising=False)
    monkeypatch.setattr(
        hardware_profile.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="42\n"),
    )
    assert hardware_profile.run_light_benchmark()["score"] == 42
    monkeypatch.setattr(
        hardware_profile.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="-3\n"),
    )
    assert hardware_profile.run_light_benchmark()["score"] == 0
    monkeypatch.setattr(
        hardware_profile.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError()),
    )
    assert hardware_profile.run_light_benchmark()["available"] is False
