"""rc.9 个人模式端口与进程归属的跨平台分支门禁。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import setup_wizard
from app.setup_wizard import HostStartupError


def run_result(stdout: str = "") -> SimpleNamespace:
    return SimpleNamespace(stdout=stdout, stderr="", returncode=0)


def test_listener_pid_windows_filters_every_untrusted_shape(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_wizard.os, "name", "nt")
    monkeypatch.setattr(setup_wizard.sys, "platform", "win32")
    output = "\n".join(
        [
            "short",
            "UDP 127.0.0.1:18775 x x 1",
            "TCP 127.0.0.1:18775 x ESTABLISHED 2",
            "TCP 127.0.0.1:19999 x LISTENING 3",
            "TCP 192.168.1.2:18775 x LISTENING 4",
            "TCP 127.0.0.1:18775 x LISTENING 5",
        ]
    )
    monkeypatch.setattr(setup_wizard.subprocess, "run", lambda *_a, **_k: run_result(output))
    assert setup_wizard._listener_pid_for_loopback_port(18775) == 5

    output += "\nTCP 0.0.0.0:18775 x LISTENING 6"
    monkeypatch.setattr(setup_wizard.subprocess, "run", lambda *_a, **_k: run_result(output))
    assert setup_wizard._listener_pid_for_loopback_port(18775) is None

    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda *_a, **_k: run_result("TCP 127.0.0.1:18775 x LISTENING bad"),
    )
    assert setup_wizard._listener_pid_for_loopback_port(18775) is None


def test_listener_pid_darwin_linux_and_command_failure(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_wizard.os, "name", "posix")
    monkeypatch.setattr(setup_wizard.sys, "platform", "darwin")
    monkeypatch.setattr(
        setup_wizard.subprocess, "run", lambda *_a, **_k: run_result("17 noise")
    )
    assert setup_wizard._listener_pid_for_loopback_port(18775) == 17
    monkeypatch.setattr(
        setup_wizard.subprocess, "run", lambda *_a, **_k: run_result("17 18")
    )
    assert setup_wizard._listener_pid_for_loopback_port(18775) is None

    monkeypatch.setattr(setup_wizard.sys, "platform", "linux")
    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda *_a, **_k: run_result('users:(("partyops",pid=21,fd=7))'),
    )
    assert setup_wizard._listener_pid_for_loopback_port(18775) == 21
    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda *_a, **_k: run_result("pid=21 pid=22"),
    )
    assert setup_wizard._listener_pid_for_loopback_port(18775) is None
    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("missing")),
    )
    assert setup_wizard._listener_pid_for_loopback_port(18775) is None


def test_process_executable_matches_darwin_and_linux_matrix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    expected = tmp_path / "partyops"
    expected.write_bytes(b"exe")
    assert not setup_wizard._process_executable_matches(0, expected)

    import ctypes

    monkeypatch.setattr(setup_wizard.sys, "platform", "darwin")
    monkeypatch.setattr(ctypes, "CDLL", lambda *_a, **_k: (_ for _ in ()).throw(OSError()))
    assert not setup_wizard._process_executable_matches(10, expected)

    class ProcPidPath:
        argtypes = None
        restype = None

        def __init__(self, value: bytes, length: int):
            self.value = value
            self.length = length

        def __call__(self, _pid, buffer, _size):
            if self.length > 0:
                buffer.value = self.value
            return self.length

    class LibProc:
        def __init__(self, proc):
            self.proc_pidpath = proc

    monkeypatch.setattr(ctypes, "CDLL", lambda *_a, **_k: LibProc(ProcPidPath(b"", 0)))
    assert not setup_wizard._process_executable_matches(10, expected)
    encoded = os.fsencode(str(expected.resolve()))
    monkeypatch.setattr(
        ctypes, "CDLL", lambda *_a, **_k: LibProc(ProcPidPath(encoded, len(encoded)))
    )
    assert setup_wizard._process_executable_matches(10, expected)

    native_path = type(tmp_path)
    monkeypatch.setattr(setup_wizard.sys, "platform", "linux")
    monkeypatch.setattr(setup_wizard.os, "name", "posix")

    def matching_path(value):
        if str(value).startswith("/proc/"):
            return SimpleNamespace(resolve=lambda: expected.resolve())
        return native_path(value)

    monkeypatch.setattr(setup_wizard, "Path", matching_path)
    assert setup_wizard._process_executable_matches(10, expected)

    def broken_path(value):
        if str(value).startswith("/proc/"):
            return SimpleNamespace(resolve=lambda: (_ for _ in ()).throw(OSError()))
        return native_path(value)

    monkeypatch.setattr(setup_wizard, "Path", broken_path)
    assert not setup_wizard._process_executable_matches(10, expected)


def write_marker(data_dir: Path, executable: Path, pid: object = 42) -> Path:
    marker = setup_wizard._personal_process_marker(data_dir)
    marker.write_text(
        json.dumps({"pid": pid, "executable": str(executable)}), encoding="utf-8"
    )
    return marker


def test_personal_process_owned_marker_matrix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "partyops"
    executable.write_bytes(b"exe")
    monkeypatch.setattr(setup_wizard, "_executable", lambda _name: executable)
    assert not setup_wizard._personal_process_is_owned(tmp_path)

    marker = setup_wizard._personal_process_marker(tmp_path)
    marker.write_text("broken", encoding="utf-8")
    assert not setup_wizard._personal_process_is_owned(tmp_path)
    write_marker(tmp_path, tmp_path / "other")
    assert not setup_wizard._personal_process_is_owned(tmp_path)

    write_marker(tmp_path, executable)
    monkeypatch.setattr(setup_wizard, "_process_executable_matches", lambda *_a: False)
    assert not setup_wizard._personal_process_is_owned(tmp_path)
    monkeypatch.setattr(setup_wizard, "_process_executable_matches", lambda *_a: True)
    assert setup_wizard._personal_process_is_owned(tmp_path)


def test_recover_legacy_personal_marker_requires_three_proofs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "partyops"
    executable.write_bytes(b"exe")
    monkeypatch.setattr(setup_wizard, "_executable", lambda _name: executable)
    monkeypatch.setattr(setup_wizard, "_listener_pid_for_loopback_port", lambda _p: None)
    assert not setup_wizard._recover_legacy_personal_process_marker(tmp_path, 18775)

    monkeypatch.setattr(setup_wizard, "_listener_pid_for_loopback_port", lambda _p: 42)
    assert not setup_wizard._recover_legacy_personal_process_marker(tmp_path, 18775)
    lock = tmp_path / ".partyops-instance.lock"
    lock.write_text("41", encoding="ascii")
    assert not setup_wizard._recover_legacy_personal_process_marker(tmp_path, 18775)
    lock.write_text("42", encoding="ascii")
    monkeypatch.setattr(setup_wizard, "_process_executable_matches", lambda *_a: False)
    assert not setup_wizard._recover_legacy_personal_process_marker(tmp_path, 18775)

    recorded: list[int] = []
    monkeypatch.setattr(setup_wizard, "_process_executable_matches", lambda *_a: True)
    monkeypatch.setattr(
        setup_wizard, "_record_personal_pid", lambda _data, pid: recorded.append(pid)
    )
    assert setup_wizard._recover_legacy_personal_process_marker(tmp_path, 18775)
    assert recorded == [42]


def test_stop_personal_process_missing_corrupt_and_identity_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "partyops"
    executable.write_bytes(b"exe")
    monkeypatch.setattr(setup_wizard, "_executable", lambda _name: executable)
    monkeypatch.setattr(
        setup_wizard.socket,
        "create_connection",
        lambda *_a, **_k: (_ for _ in ()).throw(ConnectionRefusedError()),
    )
    assert not setup_wizard._stop_personal_process_for_data_migration(tmp_path, 18775)

    class Connected:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(setup_wizard.socket, "create_connection", lambda *_a, **_k: Connected())
    with pytest.raises(ValueError, match="缺少受控进程标记"):
        setup_wizard._stop_personal_process_for_data_migration(tmp_path, 18775)

    marker = setup_wizard._personal_process_marker(tmp_path)
    marker.write_text("broken", encoding="utf-8")
    with pytest.raises(ValueError, match="标记损坏"):
        setup_wizard._stop_personal_process_for_data_migration(tmp_path, 18775)

    write_marker(tmp_path, tmp_path / "other")
    assert not setup_wizard._stop_personal_process_for_data_migration(tmp_path, 18775)
    assert not marker.exists()


def test_stop_personal_process_exit_escalation_and_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "partyops"
    executable.write_bytes(b"exe")
    monkeypatch.setattr(setup_wizard, "_executable", lambda _name: executable)

    write_marker(tmp_path, executable)
    monkeypatch.setattr(setup_wizard, "_process_executable_matches", lambda *_a: True)
    monkeypatch.setattr(
        setup_wizard.os,
        "kill",
        lambda *_a: (_ for _ in ()).throw(ProcessLookupError()),
    )
    assert not setup_wizard._stop_personal_process_for_data_migration(tmp_path, 18775)

    write_marker(tmp_path, executable)
    states = iter([True, True, False, False, False])
    monkeypatch.setattr(
        setup_wizard, "_process_executable_matches", lambda *_a: next(states)
    )
    monkeypatch.setattr(setup_wizard.os, "kill", lambda *_a: None)
    monkeypatch.setattr(setup_wizard.time, "sleep", lambda *_a: None)
    assert setup_wizard._stop_personal_process_for_data_migration(tmp_path, 18775)

    write_marker(tmp_path, executable)
    states = iter([True, True, False])
    monkeypatch.setattr(
        setup_wizard, "_process_executable_matches", lambda *_a: next(states)
    )
    ticks = iter([0.0, 21.0])
    monkeypatch.setattr(setup_wizard.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(setup_wizard, "Path", type(tmp_path))
    monkeypatch.setattr(setup_wizard.os, "name", "posix")
    monkeypatch.setattr(setup_wizard.signal, "SIGKILL", 9, raising=False)
    killed: list[int] = []
    monkeypatch.setattr(setup_wizard.os, "kill", lambda _pid, sig: killed.append(sig))
    assert setup_wizard._stop_personal_process_for_data_migration(tmp_path, 18775)
    assert len(killed) == 2

    write_marker(tmp_path, executable)
    monkeypatch.setattr(setup_wizard, "_process_executable_matches", lambda *_a: True)
    ticks = iter([0.0, 21.0])
    monkeypatch.setattr(setup_wizard.time, "monotonic", lambda: next(ticks))
    with pytest.raises(ValueError, match="未能安全停止"):
        setup_wizard._stop_personal_process_for_data_migration(tmp_path, 18775)


def test_record_port_selection_and_atomic_rewrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "partyops"
    executable.write_bytes(b"exe")
    monkeypatch.setattr(setup_wizard, "_executable", lambda _name: executable)
    setup_wizard._record_personal_pid(tmp_path, 0)
    assert not setup_wizard._personal_process_marker(tmp_path).exists()
    setup_wizard._record_personal_process(tmp_path, None)
    setup_wizard._record_personal_process(tmp_path, SimpleNamespace(pid=0))
    setup_wizard._record_personal_process(tmp_path, SimpleNamespace(pid=42))
    assert json.loads(setup_wizard._personal_process_marker(tmp_path).read_text())["pid"] == 42

    monkeypatch.setattr(
        setup_wizard,
        "_loopback_port_available",
        lambda port: port == 18777,
    )
    assert setup_wizard._select_alternative_personal_port(18775) == 18777
    monkeypatch.setattr(setup_wizard, "_loopback_port_available", lambda _p: False)
    with pytest.raises(HostStartupError):
        setup_wizard._select_alternative_personal_port(18775)

    missing = tmp_path / "missing.env"
    with pytest.raises(ValueError, match="受控普通文件"):
        setup_wizard._rewrite_personal_port(missing, 18779)
    config = tmp_path / "personal.env"
    config.write_text(
        "# keep\nPARTYOPS_PORT=18775\nPARTYOPS_BOOTSTRAP_TOKEN=secret\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        setup_wizard,
        "load_host_environment",
        lambda path: {"content": path.read_text(encoding="utf-8")},
    )
    result = setup_wizard._rewrite_personal_port(config, 18779)
    assert "PARTYOPS_PORT=18779" in result["content"]
    assert "PARTYOPS_AGENT_PORT=18780" in result["content"]
    assert "PARTYOPS_BOOTSTRAP_TOKEN=secret" in result["content"]


def test_loopback_port_probe_success_and_failure(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    class Probe:
        def __init__(self, fail: bool):
            self.fail = fail
            self.options: list[tuple] = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def setsockopt(self, *args):
            self.options.append(args)

        def bind(self, _address):
            if self.fail:
                raise OSError("busy")

    success = Probe(False)
    monkeypatch.setattr(setup_wizard.socket, "socket", lambda *_a: success)
    assert setup_wizard._loopback_port_available(18775)
    monkeypatch.setattr(setup_wizard.os, "name", "posix")
    posix_success = Probe(False)
    monkeypatch.setattr(setup_wizard.socket, "socket", lambda *_a: posix_success)
    assert setup_wizard._loopback_port_available(18775)
    assert posix_success.options == []
    failure = Probe(True)
    monkeypatch.setattr(setup_wizard.socket, "socket", lambda *_a: failure)
    assert not setup_wizard._loopback_port_available(18775)
