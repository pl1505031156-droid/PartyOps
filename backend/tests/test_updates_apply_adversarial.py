"""专业更新编排的权限、幂等、异构终端与个人模式分支。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.enums import UpdateStatus
from app.models import DeviceCommand, UpdatePackage, UpdateRun
from app.problems import ProblemException
from app.routers import fleet, updates
from app.schemas import UpdateApplyRequest


class _Rows:
    def __init__(self, values) -> None:
        self.values = list(values)

    def all(self):
        return list(self.values)


class _Db:
    def __init__(self, package=None, *, scalar_values=None, devices=None) -> None:
        self.package = package
        self.scalar_values = list(scalar_values or [])
        self.devices = list(devices or [])
        self.added: list[object] = []
        self.commits = 0

    def get(self, model, identity):
        if (
            model is UpdatePackage
            and self.package is not None
            and identity == self.package.id
        ):
            return self.package
        return None

    def scalar(self, _query):
        return self.scalar_values.pop(0) if self.scalar_values else None

    def scalars(self, _query):
        return _Rows(self.devices)

    def add(self, value) -> None:
        self.added.append(value)

    def flush(self) -> None:
        for index, value in enumerate(self.added):
            if isinstance(value, UpdateRun) and not value.id:
                value.id = f"run-{index}"

    def commit(self) -> None:
        self.commits += 1


def _package(tmp_path: Path, *, status=UpdateStatus.VALIDATED, signature=True):
    payload = tmp_path / "signed.partyops-update"
    payload.write_bytes(b"signed-package")
    return SimpleNamespace(
        id="package-1",
        filename=payload.name,
        version="1.4.3-rc.4",
        manifest={"online_download": {"source": "official-online-catalog"}},
        sha256=updates._sha256_path(payload),
        signature_valid=signature,
        status=status,
    )


def _configure(monkeypatch, tmp_path: Path, *, mode: str = "host") -> None:
    monkeypatch.setattr(
        updates,
        "get_settings",
        lambda: SimpleNamespace(updates_dir=tmp_path, data_dir=tmp_path, mode=mode),
    )
    monkeypatch.setattr(updates, "_ensure_free_space", lambda *_args: None)
    monkeypatch.setattr(updates, "write_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updates, "emit_event", lambda *_args, **_kwargs: None)


def _request(host: str | None = "127.0.0.1"):
    return SimpleNamespace(client=SimpleNamespace(host=host) if host else None)


@pytest.mark.parametrize(
    ("package_factory", "expected"),
    [
        (lambda _path: None, "UPDATE_NOT_READY"),
        (lambda path: _package(path, status=UpdateStatus.FAILED), "UPDATE_NOT_READY"),
        (lambda path: _package(path, signature=False), "UPDATE_SIGNATURE_INVALID"),
    ],
)
def test_apply_update_rejects_unready_or_unsigned_package(
    monkeypatch,
    tmp_path: Path,
    package_factory,
    expected: str,
) -> None:
    _configure(monkeypatch, tmp_path)
    package = package_factory(tmp_path)
    with pytest.raises(ProblemException) as caught:
        updates.apply_update(
            "package-1",
            UpdateApplyRequest(include_host=True),
            _request(),
            SimpleNamespace(id="admin"),
            _Db(package),
        )
    assert caught.value.code == expected


def test_apply_update_rejects_missing_file_active_run_unknown_or_empty_target(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch, tmp_path)
    package = _package(tmp_path)
    (tmp_path / package.filename).unlink()
    with pytest.raises(ProblemException) as missing:
        updates.apply_update(
            package.id,
            UpdateApplyRequest(),
            _request(),
            SimpleNamespace(id="admin"),
            _Db(package),
        )
    assert missing.value.code == "UPDATE_FILE_MISSING"

    package = _package(tmp_path)
    active = SimpleNamespace(id="active-run")
    with pytest.raises(ProblemException) as running:
        updates.apply_update(
            package.id,
            UpdateApplyRequest(include_host=True),
            _request(),
            SimpleNamespace(id="admin"),
            _Db(package, scalar_values=[active]),
        )
    assert running.value.code == "UPDATE_ALREADY_RUNNING"

    with pytest.raises(ProblemException) as unknown:
        updates.apply_update(
            package.id,
            UpdateApplyRequest(include_host=False, target_device_ids=["missing"]),
            _request(),
            SimpleNamespace(id="admin"),
            _Db(package, scalar_values=[None], devices=[]),
        )
    assert unknown.value.code == "DEVICE_NOT_FOUND"

    with pytest.raises(ProblemException) as empty:
        updates.apply_update(
            package.id,
            UpdateApplyRequest(include_host=False),
            _request(),
            SimpleNamespace(id="admin"),
            _Db(package, scalar_values=[None]),
        )
    assert empty.value.code == "UPDATE_TARGET_REQUIRED"


def test_apply_update_direct_device_is_idempotent_and_uses_official_intent(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch, tmp_path)
    package = _package(tmp_path)
    device = SimpleNamespace(id="device-linux-arm64")
    db = _Db(package, scalar_values=[None, None], devices=[device])
    runs = updates.apply_update(
        package.id,
        UpdateApplyRequest(include_host=False, target_device_ids=[device.id]),
        _request(None),
        SimpleNamespace(id="admin"),
        db,
    )
    assert len(runs) == 1 and runs[0].message == "等待设备上线后升级"
    command = next(item for item in db.added if isinstance(item, DeviceCommand))
    assert command.payload["official_online"] is True
    assert db.commits == 1

    existing = SimpleNamespace(
        id="existing-run",
        package_id=package.id,
        target_device_id=device.id,
    )
    reused = updates.apply_update(
        package.id,
        UpdateApplyRequest(include_host=False, target_device_ids=[device.id]),
        _request(),
        SimpleNamespace(id="admin"),
        _Db(package, scalar_values=[None, existing], devices=[device]),
    )
    assert reused == [existing]


def test_apply_update_host_queues_all_devices_and_personal_starts_one_uac(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch, tmp_path)
    package = _package(tmp_path)
    devices = [SimpleNamespace(id="windows"), SimpleNamespace(id="linux-arm64")]
    db = _Db(package, scalar_values=[None, None, None], devices=devices)
    runs = updates.apply_update(
        package.id,
        UpdateApplyRequest(include_host=True, target_device_ids=["ignored"]),
        _request(),
        SimpleNamespace(id="admin"),
        db,
    )
    assert runs[0].target_device_id is None
    assert {run.target_device_id for run in runs[1:]} == {"windows", "linux-arm64"}
    assert all(run.message == "等待主机升级和健康检查完成" for run in runs[1:])
    assert not any(isinstance(item, DeviceCommand) for item in db.added)
    assert package.status == UpdateStatus.APPLYING

    _configure(monkeypatch, tmp_path, mode="personal")
    package = _package(tmp_path)
    backups: list[bool] = []
    launched: list[str] = []
    monkeypatch.setattr(
        updates, "create_pre_upgrade_backup", lambda: backups.append(True)
    )

    class _Thread:
        def __init__(self, *, target, args, name, daemon):
            assert target is updates.launch_windows_personal_update
            assert name == "partyops-personal-update-uac" and daemon is True
            self.run_id = args[0]

        def start(self):
            launched.append(self.run_id)

    monkeypatch.setattr(updates.threading, "Thread", _Thread)
    personal_runs = updates.apply_update(
        package.id,
        UpdateApplyRequest(include_host=True),
        _request(),
        SimpleNamespace(id="admin"),
        _Db(package, scalar_values=[None], devices=devices),
    )
    assert backups == [True]
    assert personal_runs[0].message == "已完成升级前备份，等待 Windows 管理员确认"
    assert launched == [personal_runs[0].id]


def test_linux_personal_update_uses_polkit_coordinator_after_backup(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Linux 个人模式由独立协调器接管，不能留下无人消费的 APPLYING。"""

    _configure(monkeypatch, tmp_path, mode="personal")
    monkeypatch.setattr(updates, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(updates.sys, "platform", "linux")
    backups: list[bool] = []
    launched: list[str] = []
    monkeypatch.setattr(
        updates, "create_pre_upgrade_backup", lambda: backups.append(True)
    )

    class _Thread:
        def __init__(self, *, target, args, name, daemon):
            assert target is updates.launch_linux_personal_update
            assert name == "partyops-personal-update-polkit" and daemon is True
            self.run_id = args[0]

        def start(self):
            launched.append(self.run_id)

    monkeypatch.setattr(updates.threading, "Thread", _Thread)
    package = _package(tmp_path)
    db = _Db(package, scalar_values=[None])
    runs = updates.apply_update(
        package.id,
        UpdateApplyRequest(include_host=True),
        _request(),
        SimpleNamespace(id="admin"),
        db,
    )

    assert backups == [True]
    assert runs[0].status == UpdateStatus.APPLYING
    assert launched == [runs[0].id]
    assert db.commits == 1


def test_device_update_download_requires_registered_package_and_existing_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        fleet, "authenticated_device", lambda *_args: SimpleNamespace(id="device-1")
    )
    monkeypatch.setattr(
        updates,
        "get_settings",
        lambda: SimpleNamespace(updates_dir=tmp_path),
    )

    with pytest.raises(ProblemException) as absent:
        updates.download_device_update("missing.partyops-update", "token", _Db())
    assert absent.value.code == "UPDATE_NOT_FOUND"

    package = SimpleNamespace(filename="device.partyops-update")
    db = _Db(scalar_values=[package])
    with pytest.raises(ProblemException) as missing_file:
        updates.download_device_update(package.filename, "token", db)
    assert missing_file.value.code == "UPDATE_FILE_MISSING"

    (tmp_path / package.filename).write_bytes(b"verified")
    response = updates.download_device_update(
        package.filename,
        "token",
        _Db(scalar_values=[package]),
    )
    assert Path(response.path) == tmp_path / package.filename
