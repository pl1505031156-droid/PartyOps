"""冻结更新入口必须在导入业务配置前收敛提权环境。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _module():
    path = Path(__file__).parents[2] / "packaging" / "uos" / "updater_entrypoint.py"
    spec = importlib.util.spec_from_file_location(
        "partyops_updater_entrypoint_test", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # 避免模块末尾导入并执行真实更新器；只执行入口函数定义部分。
    source = path.read_text(encoding="utf-8").split(
        "_prepare_privileged_environment(sys.argv[1:], os.environ)", 1
    )[0]
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


def test_linux_personal_coordinator_retains_only_validated_loopback_context(
    tmp_path: Path,
) -> None:
    module = _module()
    data = tmp_path / "个人数据"
    data.mkdir()
    (data / ".partyops-data-root.json").write_text(
        json.dumps(
            {
                "format_version": 2,
                "product": "PartyOps",
                "app_id": module.APP_ID,
                "scopes": ["personal"],
            }
        ),
        encoding="utf-8",
    )
    environ = {
        "PATH": "/usr/bin",
        "PARTYOPS_MODE": "personal",
        "PARTYOPS_DATA_DIR": str(data),
        "PARTYOPS_PORT": "18775",
        "PARTYOPS_UPDATE_PUBLIC_KEY": "attacker-key",
        "PARTYOPS_DATABASE_URL": "sqlite:///attacker.db",
    }
    module._prepare_privileged_environment(
        ["--linux-personal-run-id", "run-1"], environ
    )
    assert environ["PARTYOPS_DATA_DIR"] == str(data.resolve())
    assert environ["PARTYOPS_BIND_HOST"] == "127.0.0.1"
    assert environ["PARTYOPS_ENVIRONMENT"] == "production"
    assert "PARTYOPS_UPDATE_PUBLIC_KEY" not in environ
    assert "PARTYOPS_DATABASE_URL" not in environ
    assert environ["PATH"] == "/usr/bin"


def test_privileged_package_install_drops_all_user_partyops_environment(
    tmp_path: Path,
) -> None:
    module = _module()
    environ = {
        "PATH": "/usr/bin",
        "PARTYOPS_MODE": "personal",
        "PARTYOPS_DATA_DIR": str(tmp_path),
        "PARTYOPS_PORT": "18775",
        "PARTYOPS_UPDATE_PUBLIC_KEY": "attacker-key",
    }
    module._prepare_privileged_environment(
        ["--install-package", "/tmp/release.partyops-update"], environ
    )
    assert environ == {"PATH": "/usr/bin", "PARTYOPS_ENVIRONMENT": "production"}


def test_pkexec_personal_transaction_rebuilds_context_from_validated_arguments(
    tmp_path: Path,
) -> None:
    module = _module()
    data = tmp_path / "个人事务数据"
    data.mkdir()
    (data / ".partyops-data-root.json").write_text(
        json.dumps(
            {
                "format_version": 2,
                "product": "PartyOps",
                "app_id": module.APP_ID,
                "scopes": ["personal"],
            }
        ),
        encoding="utf-8",
    )
    environ = {
        "PATH": "/usr/bin",
        "PKEXEC_UID": "1000",
        "PARTYOPS_UPDATE_PUBLIC_KEY": "attacker-key",
    }
    module._prepare_privileged_environment(
        [
            "--linux-personal-transaction",
            "run-1",
            "--personal-data-dir",
            str(data),
            "--personal-port",
            "18775",
        ],
        environ,
    )
    assert environ["PARTYOPS_DATA_DIR"] == str(data.resolve())
    assert environ["PARTYOPS_MODE"] == "personal"
    assert environ["PARTYOPS_ENVIRONMENT"] == "production"
    assert "PARTYOPS_UPDATE_PUBLIC_KEY" not in environ
    assert environ["PKEXEC_UID"] == "1000"


def test_windows_system_service_reloads_only_protected_host_configuration(
    monkeypatch, tmp_path: Path
) -> None:
    module = _module()
    program_data = tmp_path / "ProgramData"
    config = program_data / "PartyOps" / "partyops.env"
    data = tmp_path / "自定义主机数据"
    config.parent.mkdir(parents=True)
    data.mkdir()
    (config.parent / "mode.json").write_text(
        json.dumps({"format_version": 1, "mode": "host"}), encoding="utf-8"
    )
    config.write_text(
        "\n".join(
            (
                "PARTYOPS_MODE=host",
                f"PARTYOPS_DATA_DIR='{data}'",
                "PARTYOPS_PORT=18765",
                "PARTYOPS_UPDATE_PUBLIC_KEY=attacker",
                "PARTYOPS_DATABASE_URL=sqlite:///attacker.db",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_windows_program_data", lambda: program_data)
    monkeypatch.setattr(module, "_windows_is_elevated", lambda: True)
    monkeypatch.setattr(module, "_windows_config_is_protected", lambda _path: True)
    monkeypatch.setattr(module, "_windows_data_path_is_protected", lambda _path: True)
    environ = {
        "PATH": "system-path",
        "PARTYOPS_DATA_DIR": str(tmp_path / "attacker"),
        "PARTYOPS_UPDATE_PUBLIC_KEY": "attacker",
    }
    module._prepare_privileged_environment(["--windows-system-service"], environ)
    assert environ["PARTYOPS_MODE"] == "host"
    assert environ["PARTYOPS_DATA_DIR"] == str(data.resolve())
    assert environ["PARTYOPS_PORT"] == "18765"
    assert environ["PARTYOPS_ENVIRONMENT"] == "production"
    assert "PARTYOPS_UPDATE_PUBLIC_KEY" not in environ
    assert "PARTYOPS_DATABASE_URL" not in environ


def test_windows_system_service_fails_closed_for_untrusted_configuration(
    monkeypatch,
) -> None:
    module = _module()
    monkeypatch.setattr(module, "_validated_windows_system_environment", lambda: {})
    with pytest.raises(RuntimeError, match="拒绝启动"):
        module._prepare_privileged_environment(
            ["--windows-system-service"], {"PARTYOPS_DATA_DIR": "C:/attacker"}
        )
