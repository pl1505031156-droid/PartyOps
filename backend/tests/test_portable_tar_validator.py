"""Linux 便携载荷进入原生包前的 TAR 攻击面回归。"""

from __future__ import annotations

import io
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "scripts" / "validate-portable-tar.py"


def tar_bytes(
    entries: list[tuple[str, bytes, str, int]], *, include_root: bool = True
) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        if include_root:
            root = tarfile.TarInfo("PartyOps")
            root.type = tarfile.DIRTYPE
            root.mode = 0o755
            archive.addfile(root)
        for name, payload, kind, mode in entries:
            info = tarfile.TarInfo(name)
            info.mode = mode
            if kind == "file":
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                archive.addfile(info)
            else:
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
    return output.getvalue()


def run_validator(payload: bytes, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), *arguments],
        input=payload,
        capture_output=True,
        check=False,
    )


def test_portable_tar_accepts_only_bounded_regular_tree() -> None:
    accepted = run_validator(
        tar_bytes([("PartyOps/bin/partyops", b"runtime", "file", 0o755)])
    )
    assert accepted.returncode == 0, accepted.stderr.decode("utf-8")


def test_source_tar_may_omit_explicit_root_but_cannot_escape_it() -> None:
    """部分官方源码包不写顶层目录成员，文件仍必须全在该根下。"""

    payload = tar_bytes(
        [("PartyOps/src/main.c", b"int main(void) { return 0; }", "file", 0o644)],
        include_root=False,
    )
    strict = run_validator(payload)
    assert strict.returncode == 2
    assert b"PORTABLE_TAR_ROOT_MISSING" in strict.stderr
    accepted = run_validator(payload, "--allow-implicit-root")
    assert accepted.returncode == 0, accepted.stderr.decode("utf-8")


@pytest.mark.parametrize(
    ("entries", "code"),
    [
        ([("../escape", b"x", "file", 0o644)], "PORTABLE_TAR_PATH_INVALID"),
        ([("Other/file", b"x", "file", 0o644)], "PORTABLE_TAR_PATH_INVALID"),
        ([("PartyOps//file", b"x", "file", 0o644)], "PORTABLE_TAR_PATH_INVALID"),
        ([("PartyOps/link", b"", "symlink", 0o777)], "PORTABLE_TAR_SPECIAL_FILE"),
        ([("PartyOps/tool", b"x", "file", 0o4755)], "PORTABLE_TAR_MODE_INVALID"),
        (
            [
                ("PartyOps/Extra", b"one", "file", 0o644),
                ("PartyOps/extra", b"two", "file", 0o644),
            ],
            "PORTABLE_TAR_DUPLICATE",
        ),
    ],
)
def test_portable_tar_rejects_adversarial_members(entries, code) -> None:
    rejected = run_validator(tar_bytes(entries))
    assert rejected.returncode == 2
    assert code.encode("utf-8") in rejected.stderr


def test_portable_tar_enforces_member_and_expanded_limits() -> None:
    payload = tar_bytes([("PartyOps/value", b"payload", "file", 0o644)])
    assert run_validator(payload, "--max-members", "1").returncode == 2
    assert run_validator(payload, "--max-bytes", "1").returncode == 2
