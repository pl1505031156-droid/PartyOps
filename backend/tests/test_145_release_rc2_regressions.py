"""1.4.3-rc.2 安装、启动与离线打包根因回归。"""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from app import setup_wizard
from app import windows_host_status
from app.windows_host_status import CHILD_EXITED, write_service_status


class _HealthResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return b'{"status":"ok"}'


def test_windows_service_shared_probe_marks_loopback_health(monkeypatch) -> None:
    """监督服务重启后应自行确认 ready，不能永久停在 child_running。"""

    requested: list[str] = []

    def open_health(request, **_kwargs):
        requested.append(request.full_url)
        return _HealthResponse()

    monkeypatch.setattr(windows_host_status.urllib.request, "urlopen", open_health)
    healthy, detail = windows_host_status.probe_loopback_health(18765, tls=True)

    assert healthy is True
    assert detail == ""
    assert requested == ["https://127.0.0.1:18765/api/v1/health"]


def test_windows_service_source_promotes_child_to_ready() -> None:
    service = (
        Path(__file__).parents[2] / "packaging" / "windows" / "windows_service.py"
    ).read_text(encoding="utf-8")

    assert "probe_loopback_health(port, tls=tls)" in service
    assert 'stage="ready"' in service
    assert 'stage="health_timeout"' in service
    assert "code=HEALTH_TIMEOUT" in service


def test_lxml_security_version_is_explicitly_locked() -> None:
    """文档依赖的宽松传递约束不能把候选包重新解析到已知漏洞版本。"""

    root = Path(__file__).parents[2]
    requirements = (root / "backend" / "requirements.txt").read_text(encoding="utf-8")
    project = (root / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    wheelhouses = list((root / "vendor" / "wheels").glob("*/lxml-*.whl"))

    assert "lxml==6.1.1" in requirements
    assert '"lxml==6.1.1"' in project
    assert len(wheelhouses) == 2
    assert all("lxml-6.1.1-" in path.name for path in wheelhouses)


def test_health_probe_uses_loopback_but_returns_advertised_url(monkeypatch, tmp_path) -> None:
    requested: list[str] = []

    def open_health(request, **_kwargs):
        requested.append(request.full_url)
        return _HealthResponse()

    monkeypatch.setattr(setup_wizard.urllib.request, "urlopen", open_health)
    url = setup_wizard.wait_for_host_health(
        "192.168.100.40",
        18765,
        tls=True,
        timeout=5,
        data_dir=tmp_path,
    )
    assert requested == ["https://127.0.0.1:18765/api/v1/health"]
    assert url == "https://192.168.100.40:18765"
    assert json.loads((tmp_path / "logs" / "partyops-host-status.json").read_text(encoding="utf-8"))["stage"] == "ready"


def test_health_probe_fails_fast_on_service_terminal_status(monkeypatch, tmp_path) -> None:
    write_service_status(
        tmp_path,
        stage="child_exited",
        code=CHILD_EXITED,
        detail="主进程退出码 1",
    )
    monkeypatch.setattr(
        setup_wizard.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不应继续访问端口")),
    )
    with pytest.raises(setup_wizard.HostStartupError, match=r"\[CHILD_EXITED\]"):
        setup_wizard.wait_for_host_health(
            "192.168.100.40",
            18765,
            tls=True,
            timeout=180,
            data_dir=tmp_path,
        )


def test_data_directory_migration_copies_and_keeps_source(tmp_path) -> None:
    source = tmp_path / "旧 数据"
    target = tmp_path / "新 数据"
    source.mkdir()
    target.mkdir()
    (source / "attachments").mkdir()
    (source / "attachments" / "材料.txt").write_text("原始材料", encoding="utf-8")
    database = source / "partyops.db"
    import sqlite3

    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE sample(value TEXT)")
    connection.execute("INSERT INTO sample VALUES ('ok')")
    connection.commit()
    connection.close()

    setup_wizard.migrate_windows_data_dir(source, target)

    assert (target / "attachments" / "材料.txt").read_text(encoding="utf-8") == "原始材料"
    assert database.is_file()
    assert (target / "partyops.db").is_file()


def _write_wheel(path: Path, *, name: str, version: str) -> None:
    metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n\n"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{name}-{version}.dist-info/METADATA", metadata)


def test_uos_validator_reports_both_duplicate_cryptography_files(tmp_path) -> None:
    wheelhouse = tmp_path / "wheels"
    wheelhouse.mkdir()
    first = wheelhouse / "cryptography-49.0.0-py3-none-any.whl"
    second = wheelhouse / "cryptography-50.0.0-py3-none-any.whl"
    _write_wheel(first, name="cryptography", version="49.0.0")
    _write_wheel(second, name="cryptography", version="50.0.0")
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("cryptography==50.0.0\n", encoding="utf-8")
    script = Path(__file__).resolve().parents[2] / "scripts" / "validate-uos-wheelhouse.py"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--architecture",
            "arm64",
            "--wheelhouse",
            str(wheelhouse),
            "--requirements",
            str(requirements),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 2
    assert "离线目录包含重复包：cryptography" in result.stderr
    assert first.name in result.stderr and second.name in result.stderr


def test_frontend_closure_validator_rejects_missing_chunk(tmp_path) -> None:
    root = tmp_path / "client"
    assets = root / "assets"
    assets.mkdir(parents=True)
    (root / "index.html").write_text('<script src="/assets/main.js"></script>', encoding="utf-8")
    (assets / "main.js").write_text('import("./missing.js")', encoding="utf-8")
    for index in range(9):
        (assets / f"extra-{index}.txt").write_text("x", encoding="utf-8")
    script = Path(__file__).resolve().parents[2] / "scripts" / "validate-frontend-dist.py"

    result = subprocess.run(
        [sys.executable, str(script), str(root)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "missing.js" in result.stderr


def test_frontend_closure_validator_ignores_runtime_template(tmp_path) -> None:
    root = tmp_path / "client"
    assets = root / "assets"
    assets.mkdir(parents=True)
    (root / "index.html").write_text(
        '<script src="/assets/main.js"></script>', encoding="utf-8"
    )
    (assets / "main.js").write_text(
        'const path = "assets/${name}"; export { path };', encoding="utf-8"
    )
    for index in range(9):
        (assets / f"extra-{index}.txt").write_text("x", encoding="utf-8")
    script = Path(__file__).resolve().parents[2] / "scripts" / "validate-frontend-dist.py"

    result = subprocess.run(
        [sys.executable, str(script), str(root)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_uos_build_kit_normalizes_archive_metadata() -> None:
    """同一源码重复构建必须得到相同 ZIP，不能嵌入当前时间。"""

    script = (
        Path(__file__).resolve().parents[2] / "scripts" / "package-uos-build-kit.ps1"
    ).read_text(encoding="utf-8")

    assert "[DateTimeOffset]::Now" not in script
    assert '"发布基线日期：2026-08-13（内容采用可复现构建，不嵌入构建机当前时间）"' in script
    assert "$entry.LastWriteTime = [DateTimeOffset]$normalizedTime" in script


def test_uos_single_zip_verifies_all_inputs_before_install() -> None:
    """UOS 普通用户只下载一个 ZIP，安装入口自动完成前后两段验证。"""

    root = Path(__file__).resolve().parents[2]
    package_script = (root / "scripts" / "package-uos-build-kit.ps1").read_text(
        encoding="utf-8"
    )
    installer = (root / "packaging" / "uos" / "one-click-install.sh").read_text(
        encoding="utf-8"
    )

    assert 'Join-Path $staging "BUILD-KIT-SHA256SUMS"' in package_script
    assert 'sha256sum -c "BUILD-KIT-SHA256SUMS"' in installer
    assert 'run_stage "0/5 自动校验 ZIP 内全部安装输入" verify_build_kit' in installer
    assert 'run_stage "核验安装、应用入口和系统内更新助手" verify_installed_package' in installer


def test_windows_package_includes_installer_icon_before_inno_compile() -> None:
    """Inno 引用的品牌图标必须进入组装目录，不能到最后一步才报缺文件。"""

    root = Path(__file__).resolve().parents[2]
    package_script = (
        root / "packaging" / "windows" / "package-windows.ps1"
    ).read_text(encoding="utf-8")

    assert 'Join-Path $repoRoot "packaging\\windows\\partyops.ico"' in package_script
    assert 'Join-Path $bundleRoot "partyops.ico"' in package_script
