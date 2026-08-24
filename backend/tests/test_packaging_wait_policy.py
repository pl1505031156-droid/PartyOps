"""UOS 安装器慢启动等待策略的回归测试。"""

import hashlib
import os
from pathlib import Path
import struct
import subprocess
from uuid import uuid4
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL 安装路径回归")
def test_windows_custom_install_path_preflight_accepts_user_parent(
    tmp_path: Path,
) -> None:
    """可写的中文/空格父目录不能在目标 ACL 收敛前阻断自定义安装。"""

    validator = ROOT / "packaging" / "windows" / "validate-install-path.ps1"
    parent = tmp_path / f"用户可写 父目录 {uuid4().hex}"
    parent.mkdir()
    acl_result = subprocess.run(
        [
            "icacls.exe",
            str(parent),
            "/inheritance:r",
            "/grant:r",
            "*S-1-5-18:(OI)(CI)F",
            "*S-1-5-32-544:(OI)(CI)F",
            "*S-1-5-11:(OI)(CI)M",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert acl_result.returncode == 0, acl_result.stderr
    target = parent / "党建智办 PartyOps"
    diagnostic = tmp_path / "安装目录诊断.txt"
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(validator),
            "-Path",
            str(target),
            "-DiagnosticFile",
            str(diagnostic),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert diagnostic.read_text(encoding="utf-8").startswith("[INSTALL_DIR_OK]")


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL 安装路径回归")
def test_windows_custom_install_path_final_acl_still_rejects_untrusted_writer(
    tmp_path: Path,
) -> None:
    """自由选择父目录不能取消最终服务目录的写权限门禁。"""

    validator = ROOT / "packaging" / "windows" / "validate-install-path.ps1"
    target = tmp_path / "自定义 PartyOps"
    target.mkdir()
    acl_result = subprocess.run(
        [
            "icacls.exe",
            str(target),
            "/inheritance:r",
            "/grant:r",
            "*S-1-5-18:(OI)(CI)F",
            "*S-1-5-32-544:(OI)(CI)F",
            "*S-1-5-11:(OI)(CI)M",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert acl_result.returncode == 0, acl_result.stderr
    diagnostic = tmp_path / "最终目录诊断.txt"
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(validator),
            "-Path",
            str(target),
            "-VerifyTargetAcl",
            "-DiagnosticFile",
            str(diagnostic),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )

    assert result.returncode == 6
    assert diagnostic.read_text(encoding="utf-8").startswith(
        "[INSTALL_DIR_EXISTING_ACL_UNSAFE]"
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL 安装路径回归")
def test_windows_custom_install_path_can_be_secured_under_writable_parent(
    tmp_path: Path,
) -> None:
    """安装器必须自动保护最终目录，而不是因父目录通用 ACL 拒绝自定义路径。"""

    validator = ROOT / "packaging" / "windows" / "validate-install-path.ps1"
    parent = tmp_path / f"普通用户可写安装盘 {uuid4().hex}"
    parent.mkdir()
    acl_result = subprocess.run(
        [
            "icacls.exe",
            str(parent),
            "/inheritance:r",
            "/grant:r",
            "*S-1-5-18:(OI)(CI)F",
            "*S-1-5-32-544:(OI)(CI)F",
            "*S-1-5-11:(OI)(CI)M",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert acl_result.returncode == 0, acl_result.stderr
    target = parent / "PartyOps 自定义程序目录"
    diagnostic = tmp_path / "自定义目录完整流程.txt"

    def validate(*extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(validator),
                "-Path",
                str(target),
                "-DiagnosticFile",
                str(diagnostic),
                *extra,
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )

    assert validate().returncode == 0
    target.mkdir()
    secure_result = subprocess.run(
        [
            "icacls.exe",
            str(target),
            "/inheritance:r",
            "/grant:r",
            "*S-1-5-18:(OI)(CI)F",
            "*S-1-5-32-544:(OI)(CI)F",
            "*S-1-5-32-545:(OI)(CI)RX",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert secure_result.returncode == 0, secure_result.stderr
    verified = validate("-VerifyTargetAcl")
    assert verified.returncode == 0, verified.stderr
    assert diagnostic.read_text(encoding="utf-8").startswith("[INSTALL_DIR_OK]")


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL 安装路径回归")
def test_windows_installer_acl_normalization_keeps_payload_readable(
    tmp_path: Path,
) -> None:
    """目录继承标记不能递归写成令载荷不可读的空文件 ACL。"""

    target = tmp_path / f"自定义 程序目录 {uuid4().hex}"
    nested = target / "_internal" / "frontend"
    nested.mkdir(parents=True)
    payload = target / "PartyOpsService.exe"
    nested_payload = nested / "index.html"
    payload.write_bytes(b"MZ-partyops-acl-regression")
    nested_payload.write_text("PartyOps", encoding="utf-8")

    root_acl = subprocess.run(
        [
            "icacls.exe",
            str(target),
            "/inheritance:r",
            "/grant:r",
            "*S-1-5-18:(OI)(CI)F",
            "*S-1-5-32-544:(OI)(CI)F",
            "*S-1-5-32-545:(OI)(CI)RX",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert root_acl.returncode == 0, root_acl.stderr
    tree_acl = subprocess.run(
        [
            "icacls.exe",
            str(target / "*"),
            "/reset",
            "/T",
            "/C",
            "/Q",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert tree_acl.returncode == 0, tree_acl.stderr

    assert payload.read_bytes() == b"MZ-partyops-acl-regression"
    assert nested_payload.read_text(encoding="utf-8") == "PartyOps"
    payload_acl = subprocess.run(
        ["icacls.exe", str(payload)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout
    assert "(I)(RX)" in payload_acl


def test_windows_build_tool_versions_are_resolver_compatible() -> None:
    """发布构建依赖不能固定到 PyInstaller 明确排除的 pefile 版本。"""

    requirements = (
        ROOT / "packaging" / "windows" / "requirements-build.txt"
    ).read_text(encoding="utf-8")

    assert "pyinstaller==6.16.0" in requirements.lower()
    assert "pefile==2023.2.7" in requirements.lower()
    assert "pefile==2024.8.26" not in requirements.lower()


def test_portable_smoke_waits_for_slow_uos_startup() -> None:
    script = (ROOT / "packaging" / "uos" / "build-portable.sh").read_text(
        encoding="utf-8"
    )

    assert "PARTYOPS_SMOKE_TIMEOUT_SECONDS:-180" in script
    assert "SMOKE_DEADLINE" in script
    assert 'kill -0 "$PID"' in script
    assert "portable-smoke-failure-$ARCH.log" in script
    assert "seq 1 30" not in script


def test_portable_build_isolates_architecture_specific_pyinstaller_outputs() -> None:
    """双架构构建不能共享输出，也不能在不保存 POSIX 权限的盘上冻结。"""

    script = (ROOT / "packaging" / "uos" / "build-portable.sh").read_text(
        encoding="utf-8"
    )

    assert 'BUILD_PARENT="${PARTYOPS_BUILD_BASE:-$ROOT/.build-uos}"' in script
    assert 'stat -c \'%a\' "$MODE_PROBE"' in script
    assert 'stat -c \'%a\' "$MODE_PROBE/file"' in script
    assert 'BUILD_PARENT="${TMPDIR:-/tmp}/partyops-build"' in script
    assert 'BUILD="$(mktemp -d "$BUILD_PARENT/portable.XXXXXX")"' in script
    assert '--distpath "$PYI_DIST" --workpath "$PYI_WORK"' in script
    assert 'cp -a "$PYI_DIST/PartyOps/." "$RUNTIME/"' in script
    assert 'cp "$PYI_DIST/partyops-client" "$PYI_DIST/partyops-wizard"' not in script
    assert '"$ROOT/dist/PartyOps/."' not in script
    assert '"$PYTHON_BIN" "$ROOT/scripts/verify-version-consistency.py"' in script
    assert 'python3 "$ROOT/scripts/verify-version-consistency.py"' not in script

    native = (ROOT / "packaging" / "linux" / "build-native.sh").read_text(
        encoding="utf-8"
    )
    assert '"$PYTHON_BIN" "$ROOT/scripts/verify-version-consistency.py"' in native
    assert '"$PYTHON_BIN" "$ROOT/scripts/validate-uos-wheelhouse.py"' in native
    assert 'PACKAGING_WHEELS=("$WHEELHOUSE"/packaging-*.whl)' in native
    assert 'PYTHONPATH="${PACKAGING_WHEELS[0]}' in native
    assert 'python3 "$ROOT/scripts/verify-version-consistency.py"' not in native


def test_linux_auxiliary_entrypoints_share_onedir_runtime() -> None:
    """辅助入口不得以单文件模式向 /tmp 解包可执行共享库。"""

    spec = (ROOT / "packaging" / "uos" / "partyops.spec").read_text(
        encoding="utf-8"
    )
    portable = (ROOT / "packaging" / "uos" / "build-portable.sh").read_text(
        encoding="utf-8"
    )
    selftest = (
        ROOT / "packaging" / "linux" / "post-install-selftest.sh"
    ).read_text(encoding="utf-8")
    wizard = (ROOT / "packaging" / "uos" / "wizard_entrypoint.py").read_text(
        encoding="utf-8"
    )

    assert spec.count("exclude_binaries=True") == 4
    collect = spec[spec.rindex("host_collect = COLLECT(") :]
    for entrypoint in ("host_exe", "client_exe", "wizard_exe", "updater_exe"):
        assert entrypoint in collect
    assert "client_analysis.binaries" in collect
    assert "wizard_analysis.binaries" in collect
    assert "updater_analysis.binaries" in collect
    assert '"$RUNTIME/partyops-wizard" --runtime-layout-self-test' in portable
    assert '"$RUNTIME/partyops-wizard" --runtime-layout-self-test' in selftest
    native_runtime = (
        ROOT / "scripts" / "test-native-package-runtime.sh"
    ).read_text(encoding="utf-8")
    assert '"$RUNTIME/partyops-wizard" --runtime-layout-self-test' in native_runtime
    assert "partyops partyops-client partyops-wizard partyops-updater" in native_runtime
    assert "runtime_root != expected_root" in wizard
    assert 'runtime_root.rglob("*.so*")' in wizard


def test_installed_service_waits_for_slow_uos_startup() -> None:
    script = (ROOT / "packaging" / "uos" / "build-and-install.sh").read_text(
        encoding="utf-8"
    )

    assert "PARTYOPS_HEALTH_TIMEOUT_SECONDS:-180" in script
    assert "HEALTH_DEADLINE" in script
    assert "journalctl -u partyops -n 120" in script
    assert "seq 1 30" not in script


def test_one_click_installer_can_force_rebuild_same_version_hotfix() -> None:
    script = (ROOT / "packaging" / "uos" / "one-click-install.sh").read_text(
        encoding="utf-8"
    )

    assert "--rebuild)" in script
    assert 'FORCE_REBUILD="${PARTYOPS_FORCE_REBUILD:-0}"' in script
    assert '[[ -f "$DEB" && "$FORCE_REBUILD" != "1" ]]' in script
    assert "从当前修复源码重新生成" in script


def test_installers_verify_system_update_helper_before_success() -> None:
    """推广安装不能在更新助手失效时误报成功。"""

    one_click = (ROOT / "packaging" / "uos" / "one-click-install.sh").read_text(
        encoding="utf-8"
    )
    configured_host = (ROOT / "packaging" / "uos" / "build-and-install.sh").read_text(
        encoding="utf-8"
    )

    assert "systemctl is-enabled --quiet partyops-updater.service" in one_click
    assert "systemctl is-active --quiet partyops-updater.service" in one_click
    assert "journalctl -u partyops-updater -n 80" in one_click
    assert "systemctl enable --now partyops-updater.service" in configured_host
    assert "systemctl is-active --quiet partyops-updater.service" in configured_host
    assert "journalctl -u partyops-updater -n 80" in configured_host


def test_debian_upgrade_recovers_only_verified_stubborn_partyops_processes() -> None:
    script = (ROOT / "packaging" / "linux" / "pre-install-stop.sh").read_text(
        encoding="utf-8"
    )
    selftest = (ROOT / "packaging" / "linux" / "post-install-selftest.sh").read_text(
        encoding="utf-8"
    )

    assert "is_partyops_process()" in script
    assert '"/proc/$pid/exe"' in script
    assert '"/proc/$pid/stat"' in script
    assert 'kill -TERM "$pid"' in script
    assert 'kill -KILL "$pid"' in script
    assert "身份不明时宁可中止安装也不误杀" in script
    for lifecycle_script in (script, selftest):
        assert '"${PARTYOPS_IN_APP_UPDATE:-0}" = "1"' in lifecycle_script
        assert (
            "systemctl stop partyops.service partyops-updater.service"
            in lifecycle_script
        )
        assert "systemctl stop partyops.service" in lifecycle_script


def test_runtime_and_stop_script_have_bounded_graceful_shutdown() -> None:
    main = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    stop = (ROOT / "packaging" / "uos" / "stop.sh").read_text(encoding="utf-8")
    start = (ROOT / "packaging" / "uos" / "start.sh").read_text(encoding="utf-8")
    desktop = (ROOT / "packaging" / "uos" / "desktop-launcher.sh").read_text(
        encoding="utf-8"
    )

    assert '"timeout_graceful_shutdown": 15' in main
    assert '"access_log": False' in main
    assert "is_partyops_process()" in stop
    assert 'kill -TERM "$PID"' in stop
    assert 'kill -KILL "$PID"' in stop
    assert '"/proc/$pid/stat"' in stop
    assert "rotate_launcher_log" in start
    assert "5242880" in start
    assert "CONFIG_INVALID" in start
    assert "CONFIG_INVALID" in desktop
    assert "startup-diagnostic.txt" in desktop
    assert "PARTYOPS_DESKTOP_HEALTH_TIMEOUT_SECONDS:-180" in desktop
    assert "runtime_pid_alive" in desktop
    assert "[PID_FILE_MISSING]" in desktop
    assert "[CHILD_EXITED]" in desktop
    assert "[HEALTH_TIMEOUT]" in desktop
    assert "RUNTIME_VERSION_MISMATCH" in desktop
    assert 'EXPECTED_VERSION <"$APP_ROOT/VERSION"' in desktop
    assert "systemctl is-active" in desktop
    assert ': >>"$LAUNCH_LOG"' in desktop
    assert "CONFIG_DIR_UNAVAILABLE" in desktop
    assert "START_COMMAND_FAILED" in desktop
    assert "LAUNCH_LOCK_TIMEOUT" not in desktop
    assert "flock -w 190 9" not in desktop
    assert "flock -n 9" in desktop
    assert "LAUNCH_IN_PROGRESS" in desktop
    assert "WIZARD_PAGE_TIMEOUT" in desktop
    assert 'kill -TERM "$pid"' in desktop
    assert 'kill -KILL "$pid"' in desktop
    assert '9>&- &' in desktop
    assert "while ((attempt < 360))" in desktop
    assert 'printf \'%s\\n\' "$APP_VERSION" >"$RUNTIME/VERSION"' in (
        ROOT / "packaging" / "uos" / "build-portable.sh"
    ).read_text(encoding="utf-8")


def test_legacy_host_config_is_migrated_to_tls_agent_port() -> None:
    start = (ROOT / "packaging" / "uos" / "start.sh").read_text(encoding="utf-8")
    deb = (ROOT / "packaging" / "linux" / "post-install-configure.sh").read_text(
        encoding="utf-8"
    )

    for script in (start, deb):
        assert "migrate_legacy_host_config()" in script
        assert "PARTYOPS_AGENT_PORT" in script
        assert "PARTYOPS_BIND_HOST" in script
        assert "PARTYOPS_ADVERTISE_HOST" in script
        assert "bind_host=0.0.0.0" in script
        assert "PARTYOPS_TLS_ENABLED=true" in script
        assert "旧版主机配置已迁移" in script
    assert 'if [[ "$CONFIG" != "$PERSONAL_CONFIG" ]]; then' in start
    assert "个人模式刻意使用本机 HTTP" in start
    assert "/home/*/.config/partyops/partyops.env" not in deb
    assert "/data/home/*/.config/partyops/partyops.env" not in deb
    assert "! -perm /022" in deb


def test_native_linux_packages_embed_upgrade_and_selftest_lifecycle() -> None:
    """DEB/RPM 必须复用同一套停服、迁移与安装后自检脚本。"""

    build = (ROOT / "packaging" / "linux" / "build-native.sh").read_text(
        encoding="utf-8"
    )

    assert "pre-install-stop.sh" in build
    assert "post-install-configure.sh" in build
    assert "post-install-selftest.sh" in build
    assert "post-install-services.sh" in build
    assert "post-install-transaction.sh" in build
    assert (
        'cp "$ROOT/packaging/linux/pre-install-stop.sh" "$PKG/DEBIAN/preinst"' in build
    )
    assert "%pre" in build
    assert "%post" in build
    assert "/opt/partyops/post-install-transaction.sh $ARCH rpm" in build
    assert "post-install-transaction.sh %s deb" in build
    assert "sed 's/\\r$//'" in build
    assert "桌面入口换行规范化失败" in build
    assert 'DEB_VERSION="1.4.5~rc.2"' in build
    assert "Version: $DEB_VERSION" in build
    assert "systemd, util-linux, coreutils, iproute2" in build
    assert "systemd, util-linux, coreutils, iproute" in build
    assert "License: GPL-3.0-or-later AND AGPL-3.0-only" in build
    transaction = (
        ROOT / "packaging" / "linux" / "post-install-transaction.sh"
    ).read_text(encoding="utf-8")
    services = (
        ROOT / "packaging" / "linux" / "post-install-services.sh"
    ).read_text(encoding="utf-8")
    assert "post-install-selftest.sh" in transaction
    assert transaction.index("post-install-selftest.sh") < transaction.index(
        "post-install-services.sh"
    )
    assert "PACKAGE_UPDATER_START_FAILED" in services
    assert "PACKAGE_HOST_RESTART_FAILED" in services
    assert "partyops-package-install.log" in services
    assert "journalctl -u partyops-updater" in services
    assert "dpkg --configure -a" in services
    assert "重新安装当前 RPM" in services
    assert (
        "systemctl enable --now partyops-updater.service >/dev/null 2>&1 || true"
        not in services
    )
    assert services.index("systemctl restart --no-block partyops.service") < services.index(
        'rm -f -- "$RESTART_MARKER"'
    )
    rpm_preun = build.split("%preun", 1)[1].split("%postun", 1)[0]
    assert 'if [ "\\$1" -eq 0 ]; then' in rpm_preun
    assert "systemctl stop partyops.service partyops-updater.service" in rpm_preun

    one_click = (ROOT / "packaging" / "uos" / "one-click-install.sh").read_text(
        encoding="utf-8"
    )
    assert 'VERSION="${PARTYOPS_VERSION:-1.4.5-rc.2}"' in one_click
    assert 'PACKAGE_VERSION="${PARTYOPS_PACKAGE_VERSION:-1.4.5~rc.2}"' in one_click
    assert 'DEB="$ARTIFACTS/PartyOps_${VERSION}_linux_${ARCH}.deb"' in one_click
    assert '[[ "$installed_version" == "$PACKAGE_VERSION" ]]' in one_click
    assert 'chown -R "$CURRENT_USER' not in one_click

    acceptance = (ROOT / "packaging" / "uos" / "target-acceptance.sh").read_text(
        encoding="utf-8"
    )
    assert 'PACKAGE_VERSION="${PARTYOPS_PACKAGE_VERSION:-1.4.5~rc.2}"' in acceptance
    assert 'test "$INSTALLED_VERSION" = "$PACKAGE_VERSION"' in acceptance
    assert "LD_LIBRARY_PATH=/opt/partyops/ocr/lib" in acceptance

    shortcut = (ROOT / "packaging" / "uos" / "install-desktop-shortcut.sh").read_text(
        encoding="utf-8"
    )
    assert 'run_as_target_user install -d -m 0755 "$DESKTOP_DIR"' in shortcut
    assert (
        'run_as_target_user install -m 0755 \\\n  "$DESKTOP_ENTRY" "$DESKTOP_DIR/党建智办.desktop"'
        in shortcut
    )
    assert 'install -d -o "$TARGET_USER"' not in shortcut

    selftest = (ROOT / "packaging" / "linux" / "post-install-selftest.sh").read_text(
        encoding="utf-8"
    )
    assert "mktemp -d /run/partyops-package-selftest." in selftest
    assert "mktemp -d /var/lib/partyops/" not in selftest
    assert selftest.count("runuser -u partyops -- env") == 3
    assert "tail -n 120" in selftest
    assert "SYSTEMD_VERIFY_LOG" in selftest
    assert "partyops-file.desktop" in selftest
    assert "partyops-desktop-file-validate.log" in selftest
    assert "--desktop-server-self-test" in selftest
    assert "PACKAGE_WIZARD_SERVER_INVALID" in selftest
    assert "systemd 服务定义与当前麒麟/UOS版本不兼容" in selftest

    host_unit = (ROOT / "packaging" / "uos" / "partyops.service").read_text(
        encoding="utf-8"
    )
    updater_unit = (
        ROOT / "packaging" / "uos" / "partyops-updater.service"
    ).read_text(encoding="utf-8")
    for unit in (host_unit, updater_unit):
        assert "StartLimitInterval=300" in unit
        assert "StartLimitIntervalSec" not in unit

    desktop_launcher = (
        ROOT / "packaging" / "uos" / "desktop-launcher.sh"
    ).read_text(encoding="utf-8")
    assert "rotate_desktop_log" in desktop_launcher
    assert "5242880" in desktop_launcher

    ca_helper = (ROOT / "packaging" / "uos" / "install-internal-ca.sh").read_text(
        encoding="utf-8"
    )
    assert "mktemp /run/partyops-ca." in ca_helper
    assert 'SOURCE="$SNAPSHOT"' in ca_helper
    assert "不是自签名证书" in ca_helper
    assert 'CANONICAL_HOME="$(readlink -f -- "$DESKTOP_HOME"' in ca_helper
    assert 'DESKTOP_HOME="$CANONICAL_HOME"' in ca_helper
    assert 'run_as_desktop_user install -d -m 0700 "$NSS_DIR"' in ca_helper
    assert 'run_as_desktop_user install -d -m 0700 "$MARKER_DIR"' in ca_helper
    assert 'install -d -o "$DESKTOP_UID"' not in ca_helper
    assert 'chown "$DESKTOP_UID:$DESKTOP_GID" "$MARKER_TEMP"' not in ca_helper
    assert 'openssl verify -CAfile "$SOURCE" "$SOURCE"' in ca_helper
    assert 'TARGET_BACKUP="$(mktemp /run/partyops-ca-backup.' in ca_helper
    assert "TRUST_CHANGED=1" in ca_helper
    assert "TRUST_COMMITTED=1" in ca_helper
    assert "PartyOps CA 回滚后系统证书索引刷新失败" in ca_helper


def test_windows_privileged_updater_uses_system_log_and_sanitized_environment() -> None:
    service = (ROOT / "packaging" / "windows" / "windows_updater_service.py").read_text(
        encoding="utf-8"
    )
    assert 'if not key.startswith("PARTYOPS_")' in service
    assert '"--windows-system-service"' in service
    assert "load_host_environment" not in service
    assert '/ "PartyOps-System"' in service
    assert 'data_dir / "logs" / "partyops-updater-service.log"' not in service

    host_service = (ROOT / "packaging" / "windows" / "windows_service.py").read_text(
        encoding="utf-8"
    )
    assert "existing_code in TERMINAL_CODES" in host_service
    assert "code = existing_code" in host_service


def test_agent_listener_failure_stops_partial_host_startup() -> None:
    main = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")

    assert "agent_server.started" in main
    assert "agent_thread.is_alive()" in main
    assert "设备安全端口启动失败" in main


def test_strict_local_ai_dependencies_block_incomplete_linux_build() -> None:
    """rc.3 不允许缺少任一架构智能运行时后降级生成基础包。"""

    base_requirements = (ROOT / "backend" / "requirements.txt").read_text(
        encoding="utf-8"
    )
    portable = (ROOT / "packaging" / "uos" / "build-portable.sh").read_text(
        encoding="utf-8"
    )
    local_ai_requirements = (ROOT / "backend" / "requirements-local-ai.txt").read_text(
        encoding="utf-8"
    )

    for package in ("numpy", "onnxruntime", "tokenizers"):
        assert f"{package}==" not in base_requirements
        assert f"{package}==" in local_ai_requirements
    assert 'if [[ "$LOCAL_EMBEDDING_AVAILABLE" == "1" ]]' in portable
    assert 'if [[ "$LOCAL_LLM_AVAILABLE" == "1" ]]' in portable
    assert "requirements-local-ai.txt" in portable
    assert "validate-uos-wheelhouse.py" in portable
    assert "本地语义离线轮子" in portable
    assert "严格模式拒绝构建" in portable
    assert "REQUIRE_LOCAL_AI_RUNTIME=1" in portable


def test_onnxruntime_freezing_does_not_import_runtime_during_collection() -> None:
    """交叉冻结只能静态收集 ONNX 文件，不能在构建机执行 CPU 探测。"""

    spec = (ROOT / "packaging" / "uos" / "partyops.spec").read_text(
        encoding="utf-8"
    )

    assert 'for package in ("numpy", "tokenizers")' in spec
    assert 'collect_data_files("onnxruntime", include_py_files=True)' in spec
    assert 'collect_dynamic_libs("onnxruntime")' in spec
    assert 'collect_all("onnxruntime")' not in spec


def test_linux_freeze_rejects_python_without_shared_runtime() -> None:
    """PyInstaller 需要共享 libpython，必须在耗时构建前给出中文诊断。"""

    portable = (ROOT / "packaging" / "uos" / "build-portable.sh").read_text(
        encoding="utf-8"
    )

    assert 'sysconfig.get_config_var("Py_ENABLE_SHARED")' in portable
    assert 'sysconfig.get_config_var("LDLIBRARY")' in portable
    assert 'candidate = Path(sys.base_prefix) / "lib" / library' in portable
    assert "发布冻结要求带共享 libpython 的 Python 3.11" in portable


def test_llama_runtime_is_rebuilt_for_glibc_217_without_openssl() -> None:
    """国产 Linux 包不能复用要求新 glibc/OpenSSL 的 Ubuntu 预编译运行时。"""

    script = (ROOT / "packaging" / "linux" / "build-llama-runtime.sh").read_text(
        encoding="utf-8"
    )

    assert '[[ "$GLIBC_VERSION" == 2.17 ]]' in script
    assert "-DLLAMA_OPENSSL=OFF" in script
    assert "-static-libstdc++ -static-libgcc" in script
    assert "-DGGML_NATIVE=OFF" in script
    assert "partyops-llama-git-metadata" in script
    assert "PARTYOPS_LLAMA_BUILD_BASE" in script
    assert "rev-parse --short HEAD" in script
    assert '-DGIT_EXECUTABLE="$FAKE_GIT"' in script
    assert "readelf --version-info" in script
    assert "GLIBC_2.17" in script
    assert 'llama-server" --version' in script
    assert "validate-portable-tar.py" in script


def test_windows_installer_defers_host_privileges_until_role_selection() -> None:
    """协同机安装后不能自动启动主机服务、更新服务或开放入站端口。"""

    installer = (ROOT / "packaging" / "windows" / "PartyOps.iss").read_text(
        encoding="utf-8"
    )
    host_service = (ROOT / "packaging" / "windows" / "windows_service.py").read_text(
        encoding="utf-8"
    )

    assert "Result := '--startup manual '" in installer
    assert "Result := '--startup auto '" in installer
    assert "HostServiceStartup + ServiceInstallAction('PartyOpsHost')" in installer
    assert (
        "UpdateServiceStartup + ServiceInstallAction('PartyOpsUpdateService')"
        in installer
    )
    assert "ConfiguredHostModeBeforeInstall" in installer
    assert (
        "(ConfiguredHostModeBeforeInstall and not UpdateServiceExistedBeforeInstall)"
        in installer
    )
    assert "not InAppServiceUpdate then" in installer
    assert "if UpdateServiceRunningBeforeInstall then" in installer
    assert "runasoriginaluser" in installer
    assert 'ValueName: "PartyOpsAgent"' not in installer
    assert "advfirewall firewall add rule" not in installer
    assert "sdset PartyOpsHost" in installer
    assert "remoteip=LocalSubnet" in host_service
    assert '["sc.exe", "start", "PartyOpsUpdateService"]' in host_service
    assert "updater.returncode not in {0, 1056}" in host_service
    assert "PartyOps 更新服务未能启动" in host_service
    assert "prepare_host_runtime(environment, executable)" in host_service
    assert "assert_windows_service_data_path_security" in host_service
    assert "normalize_windows_service_data_path_security(data_dir)" in host_service
    assert "已安全升级旧版自定义数据目录权限" in host_service
    assert "verify_target=True" in host_service
    assert "InAppServiceUpdate" in installer
    assert "{param:INAPPUPDATE|0}" in installer
    assert "PartyOpsUpdater.exe,PartyOpsUpdaterService.exe" in installer
    assert installer.count("restartreplace") >= 2


def test_windows_installer_is_chinese_branded_and_preserves_custom_paths() -> None:
    """安装器须保留程序/数据自定义目录，并在覆盖文件前安全停服。"""

    installer = (ROOT / "packaging" / "windows" / "PartyOps.iss").read_text(
        encoding="utf-8"
    )

    assert 'Name: "chinesesimp"' in installer
    assert "ChineseSimplified.isl" in installer
    assert "欢迎使用党建智办 PartyOps 安装向导" in installer
    assert "UsePreviousAppDir=yes" in installer
    assert "LoadStringsFromFile(" in installer
    assert "SaveStringsToUTF8File(" in installer
    assert "function LoadConfiguredDataDir" in installer
    assert "PartyOps\\partyops.env" in installer
    assert installer.index("LoadConfiguredDataDir(PreviousDataDir)") < installer.index(
        "install-data-dir.txt"
    )
    assert "install-data-dir.txt" in installer
    assert "function PrepareToInstall" in installer
    assert "--wait=45 stop" in installer
    assert "UPGRADE_SERVICE_STOP_FAILED" in installer
    assert "请先卸载损坏的旧版本" not in installer
    assert "function QueryOwnedServiceExecutable" in installer
    assert "GetShortName" in installer
    assert "WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall" in installer
    assert "ExtractServiceExecutablePath(ImagePath, ServiceExecutable)" in installer
    assert "function StopOwnedServiceThroughScm" in installer
    assert "LEGACY_SERVICE_CONFLICT" in installer
    assert "LEGACY_SERVICE_STOP_FAILED" in installer
    assert "'stop ' + ServiceName" in installer
    assert "MarkServiceOwnership('PartyOpsHost', 'PartyOpsService.exe')" in installer
    assert (
        "MarkServiceOwnership('PartyOpsUpdateService', 'PartyOpsUpdaterService.exe')"
        in installer
    )
    assert installer.index("function PrepareToInstall") < installer.index(
        "procedure CurStepChanged"
    )
    assert (
        'Source: "{#SourcePath}\\validate-install-path.ps1"; Flags: dontcopy'
        in installer
    )
    assert "function ValidateAndSecureInstallDirectory" in installer
    assert "INSTALL_DIR_REPARSE_POINT" in installer
    assert "INSTALL_DIR_NOT_PARTYOPS" in installer
    assert "INSTALL_DIR_ACL_DENIED" in installer
    assert "INSTALL_DIR_ACL_UNSAFE" in installer
    assert "ReadInstallPathDiagnostic" in installer
    assert "function RunInstallPathValidator" in installer
    assert "ExecAndLogOutput(" in installer
    install_path_flow = installer.split(
        "function ValidateAndSecureInstallDirectory", maxsplit=1
    )[1].split("function PrepareToInstall", maxsplit=1)[0]
    assert "Exec(PowerShell" not in install_path_flow
    assert install_path_flow.count("RunInstallPathValidator(") == 3
    assert "partyops-install-path-diagnostic.txt" in installer
    assert "*S-1-5-18:(OI)(CI)F" in installer
    assert "*S-1-5-32-544:(OI)(CI)F" in installer
    assert "*S-1-5-32-545:(OI)(CI)RX" in installer
    assert "AddQuotes(AddBackslash(AppDir) + '*') + ' /reset /T /C /Q'" in installer
    assert "INSTALL_DIR_TREE_ACL_DENIED" in installer
    assert "INSTALL_DIR_TREE_ACL_VERIFY_FAILED" in installer
    assert "INSTALL_DIR_INTEGRITY_DENIED" in installer
    assert " /setintegritylevel (OI)(CI)H /T /C /Q" in installer
    assert "VersionInfoVersion=1.4.5.2" in installer
    assert "MinVersion=10.0" in installer
    assert "MinVersion=6.1sp1" in installer
    assert "此安装包仅支持 Windows 10/11" in installer
    assert "windows7_amd64" in installer
    assert "windows7_x86" in installer
    assert '[UninstallDelete]' in installer
    assert 'Type: dirifempty; Name: "{app}"' in installer
    assert "*S-1-5-32-545:(OI)(CI)RX /T /C /Q" not in installer
    assert "*S-1-5-18:(OI)(CI)F *S-1-5-32-544:(OI)(CI)F /T /C /Q" not in installer
    assert "AddQuotes(AddBackslash(ControlRoot) + '*') + ' /reset /T /C /Q'" in installer
    assert "AddQuotes(AddBackslash(TransactionRoot) + '*') + ' /reset /T /C /Q'" in installer
    assert "function UninstallDataActionParameter" in installer
    assert "if UninstallSilent or (DataAction <> '') then" in installer
    assert "if DataAction = 'delete' then" in installer
    assert "Choice := IDNO" in installer
    assert "UNINSTALL_DATAACTION_INVALID" in installer
    assert "function IsConfiguredHostMode" in installer
    assert "function LoadConfiguredMode" in installer
    assert "function IsConfiguredPersonalMode" in installer
    assert "ConfiguredPersonalModeBeforeInstall := IsConfiguredPersonalMode" in installer
    assert "(not ConfiguredPersonalModeBeforeInstall) and IsConfiguredHostMode" in installer
    assert "function HostServiceStartupArgument" in installer
    assert "if ConfiguredPersonalMode then" in installer
    assert "(not ConfiguredPersonalModeBeforeInstall) and" in installer
    assert "Existed, ConfiguredHostMode: Boolean" in installer
    assert "else if ConfiguredHostMode then" in installer
    assert (
        "ConfiguredHostModeBeforeInstall and not HostServiceExistedBeforeInstall"
        in installer
    )
    assert (
        "ConfiguredHostModeBeforeInstall and not UpdateServiceExistedBeforeInstall"
        in installer
    )
    disabled_branch = installer.index("else if StartType = 4 then")
    legacy_manual_repair = installer.index("else if ConfiguredHostMode then")
    assert disabled_branch < legacy_manual_repair
    assert installer.index(
        "Result := ValidateAndSecureInstallDirectory"
    ) < installer.index("Result := StopServiceBeforeUpgrade(")

    validator_path = ROOT / "packaging" / "windows" / "validate-install-path.ps1"
    validator_bytes = validator_path.read_bytes()
    assert validator_bytes.startswith(b"\xef\xbb\xbf")
    validator = validator_path.read_text(encoding="utf-8-sig")
    assert "[IO.DriveType]::Fixed" in validator
    assert "[IO.FileAttributes]::ReparsePoint" in validator
    assert 'Join-Path $fullPath "PartyOps.exe"' in validator
    for build_script in ("build-windows.ps1", "package-windows.ps1"):
        build_text = (ROOT / "packaging" / "windows" / build_script).read_text(
            encoding="utf-8"
        )
        assert "WindowsPowerShell\\v1.0\\powershell.exe" in build_text
        assert "-File $installPathValidator" in build_text
        assert "安装路径-$validatorProbeId\\中文 空格" in build_text
    assert "DeleteSubdirectoriesAndFiles" in validator
    assert "FileSystemRights]::Delete" in validator
    assert "FileSystemRights]::WriteData" in validator
    assert "FileSystemRights]::AppendData" in validator
    assert "FileSystemRights]::ChangePermissions" in validator
    assert "INSTALL_DIR_EXISTING_ACL_UNSAFE" in validator
    assert "Assert-SecureDirectoryAcl $fullPath -ForProgramDirectory" in validator
    assert "Assert-SecureDirectoryAcl" in validator
    assert "S-1-5-32-544" in validator
    assert "INSTALL_DIR_PARENT_UNSAFE" not in validator
    assert "INSTALL_DIR_PARENT_ACL_UNSAFE" not in validator
    assert "WindowsIdentity]::GetCurrent" in validator
    assert 'DiagnosticFile = ""' in validator
    assert "安装位置由用户自由选择" in validator
    assert "祖先目录的通用 ACL" in validator
    assert installer.count("ErrorMessage := ValidateAndSecureInstallDirectory") == 1


def test_frozen_windows_wizard_has_real_gui_self_test_gate() -> None:
    entrypoint = (ROOT / "packaging" / "uos" / "wizard_entrypoint.py").read_text(
        encoding="utf-8"
    )
    build = (ROOT / "packaging" / "windows" / "build-windows.ps1").read_text(
        encoding="utf-8"
    )
    assert 'sys.argv[1:] == ["--self-test"]' in entrypoint
    assert "root = tkinter.Tk()" in entrypoint
    assert 'PartyOpsWizard.exe\") --self-test' in build
    assert "setuptools._vendor.backports.tarfile" in build
    assert '$hiddenModules += @("backports", "backports.tarfile")' in build
    assert "if ($requiresBackportsTarfile)" in build
    assert '$ErrorActionPreference = "Continue"' in build
    assert "$ErrorActionPreference = $previousEap" in build
    assert "安装成功后向导/启动器立即退出" in build
    for required in (
        "_tkinter.pyd",
        "tcl86t.dll",
        "tk86t.dll",
        "_tcl_data\\init.tcl",
        "_tk_data\\tk.tcl",
    ):
        assert required in build


def test_windows_installer_runs_target_machine_startup_selftest_before_services() -> None:
    entrypoint = (ROOT / "packaging" / "uos" / "entrypoint.py").read_text(
        encoding="utf-8"
    )
    installer = (ROOT / "packaging" / "windows" / "PartyOps.iss").read_text(
        encoding="utf-8"
    )

    assert 'sys.argv[1:] == ["--startup-self-test"]' in entrypoint
    assert 'sys.argv[1:] == ["--startup-self-test-child"]' in entrypoint
    assert "procedure RunRuntimeStartupSelfTest" in installer
    assert "ExecAndLogOutput" in installer
    assert "--startup-self-test" in installer
    assert "PACKAGE_RUNTIME_STARTUP_SELFTEST_FAILED" in installer
    post_install = installer[installer.index("procedure CurStepChanged") :]
    assert post_install.index("RunRuntimeStartupSelfTest;") < post_install.index(
        "ProtectSystemControlDirectories;"
    )


def test_win7_uses_verified_sdk_ucrt_instead_of_build_host_system_dlls() -> None:
    build = (ROOT / "packaging" / "windows" / "build-windows.ps1").read_text(
        encoding="utf-8"
    )
    validator = (ROOT / "scripts" / "validate-win7-pe.py").read_text(
        encoding="utf-8"
    )
    assert "ucrt-10.0.19041.0-$ucrtArchitecture" in build
    assert 'Where-Object { $_.Name -like "api-ms-win-*.dll"' in build
    assert "ucrt-source.json" in build
    assert 'for directory in (root, root / "_internal")' in validator
    assert "is_verified_ucrt_forwarder" in validator
    assert "10.0.19041.0" in validator
    assert '"api-ms-win-core-path-"' in validator
    assert '#define PartyOpsLegacy' in (
        ROOT / "packaging" / "windows" / "PartyOps-Win7-x64.iss"
    ).read_text(encoding="utf-8")
    assert '#define PartyOpsLegacy' in (
        ROOT / "packaging" / "windows" / "PartyOps-Win7-x86.iss"
    ).read_text(encoding="utf-8")


def test_linux_wizard_freeze_includes_tcl_runtime_and_entrypoint_smoke() -> None:
    """数据目录选择器依赖 Tcl/Tk，不能只验证主机服务就发布。"""

    script = (ROOT / "packaging" / "uos" / "build-portable.sh").read_text(
        encoding="utf-8"
    )

    assert "PYTHON_BASE_LIB" in script
    assert 'LD_LIBRARY_PATH="$PYTHON_BASE_LIB' in script
    assert "partyops-client partyops-wizard partyops-updater" in script
    assert '"$RUNTIME/$entrypoint" --help' in script
    assert "冻结入口自检失败" in script


def test_linux_native_install_moves_slow_runtime_health_check_out_of_package_transaction() -> None:
    transaction = (ROOT / "packaging" / "linux" / "post-install-transaction.sh").read_text(encoding="utf-8")
    selftest = (ROOT / "packaging" / "linux" / "post-install-selftest.sh").read_text(encoding="utf-8")
    verifier = (ROOT / "packaging" / "linux" / "post-install-verify.sh").read_text(encoding="utf-8")
    service = (ROOT / "packaging" / "linux" / "partyops-install-verify.service").read_text(encoding="utf-8")
    build = (ROOT / "packaging" / "linux" / "build-native.sh").read_text(encoding="utf-8")

    assert 'post-install-selftest.sh" "$EXPECTED_ARCH" quick' in transaction
    assert "systemctl start --no-block partyops-install-verify.service" in transaction
    assert 'if [ "$MODE" = "quick" ]' in selftest
    assert 'post-install-selftest.sh" "" full' in verifier
    assert "install-verification.json" in verifier
    assert "ExecStart=/opt/partyops/post-install-verify.sh" in service
    assert "TimeoutStartSec=240" in service
    assert "partyops-install-verify.service" in build


def test_linux_bundle_only_includes_current_user_documents() -> None:
    """发布后生成的验收/哈希记录不能反向封入制品形成循环或残留旧版本。"""

    script = (ROOT / "packaging" / "uos" / "build-portable.sh").read_text(
        encoding="utf-8"
    )

    assert 'cp -a "$ROOT/docs" "$RUNTIME/"' not in script
    assert '"release-notes-v1.4.5-rc.2.md"' in script
    for document in (
        "user-guide.md",
        "deployment.md",
        "upgrade-1.4.3.md",
        "installation-checklist.md",
        "backup-restore.md",
        "operations-runbook.md",
    ):
        assert f'"{document}"' in script
    assert "README.md CHANGELOG.md LICENSE THIRD_PARTY_NOTICES.md" in script
    for release_evidence in (
        "artifact-manifest-1.4.3.md",
        "acceptance-1.4.3.md",
        "release-readiness-1.4.3.md",
    ):
        assert release_evidence not in script


def test_linux_portable_archive_dereferences_runtime_symlinks() -> None:
    """原生包只接受普通文件，便携归档必须展开 PyInstaller 共享库链接。"""

    script = (ROOT / "packaging" / "uos" / "build-portable.sh").read_text(
        encoding="utf-8"
    )

    assert (
        'tar --dereference --hard-dereference -cf - -C "$BUILD" PartyOps'
        in script
    )
    assert (
        '"$PYTHON_BIN" "$ROOT/scripts/validate-portable-tar.py"'
        in script
    )
    assert "validate-portable-tar.py" in (
        ROOT / "packaging" / "linux" / "build-native.sh"
    ).read_text(encoding="utf-8")


def test_linux_portable_release_extensions_disable_debug_location_views() -> None:
    """manylinux2014 的旧汇编器不得收到 GCC 11 的调试定位扩展。"""

    script = (ROOT / "packaging" / "uos" / "build-portable.sh").read_text(
        encoding="utf-8"
    )

    assert 'export CFLAGS="${CFLAGS:-} -O3 -g0 -fPIC"' in script
    assert 'export CXXFLAGS="${CXXFLAGS:-} -O3 -g0 -fPIC"' in script


def test_linux_native_release_requires_embedded_update_trust_key() -> None:
    """正式 DEB/RPM 不得在缺少应用内更新信任根时继续封装。"""

    script = (ROOT / "packaging" / "linux" / "build-native.sh").read_text(
        encoding="utf-8"
    )

    assert '[[ -s "$PKG/opt/partyops/update-public-key.txt" ]]' in script
    assert "拒绝生成无法应用内升级的正式包" in script


def test_linux_rpm_rollback_seed_keeps_current_release_generation() -> None:
    """首次 RPM 回滚不能伪装成旧 rc，否则健康门禁会再次拒绝运行时。"""

    script = (ROOT / "packaging" / "linux" / "build-native.sh").read_text(
        encoding="utf-8"
    )

    assert 'RPM_RELEASE="0.rc.2.1"' in script
    assert 'SEED_RELEASE="0.rc.2.0"' in script


def test_linux_native_build_uses_a_posix_permission_build_root() -> None:
    """DrvFS 不保存 chmod 时，DEB/RPM 暂存树必须自动转入 Linux 本地盘。"""

    script = (ROOT / "packaging" / "linux" / "build-native.sh").read_text(
        encoding="utf-8"
    )

    assert 'BUILD_PARENT="${PARTYOPS_NATIVE_BUILD_BASE:-$ROOT/.build-linux}"' in script
    assert 'stat -c \'%a\' "$MODE_PROBE"' in script
    assert 'stat -c \'%a\' "$MODE_PROBE/file"' in script
    assert 'BUILD_PARENT="${TMPDIR:-/tmp}/partyops-native-build"' in script
    assert 'BUILD="$(mktemp -d "$BUILD_PARENT/native.XXXXXX")"' in script
    assert 'chmod 0644 \\' in script
    assert '"$PKG/lib/systemd/system/partyops.service"' in script
    assert '"$PKG/usr/share/polkit-1/actions/cn.partyops.update.policy"' in script


def test_arm64_native_package_is_host_wrapped_then_chroot_tested() -> None:
    """ARM 运行根无需塞入宿主包工具，封装和架构内动态门禁必须分离。"""

    script = (ROOT / "scripts" / "build-linux-arm64-chroot.sh").read_text(
        encoding="utf-8"
    )
    assert "HOST_PYTHON_BIN=" in script
    assert 'if [[ "$ACTION" == deb ]]' in script
    assert "PARTYOPS_ALLOW_CROSS_PACKAGE=1" in script
    assert "bash packaging/linux/build-native.sh deb" in script
    assert "rpm) bash packaging/linux/build-native.sh rpm" in script
    assert "test-native-package-runtime.sh" in script
    assert "deb|rpm) bash packaging/linux/build-native.sh '$ACTION'" not in script


def test_linux_native_checksum_sidecar_uses_only_the_artifact_filename() -> None:
    """发布校验文件不得泄露构建机绝对路径，且必须可跨目录复用。"""

    script = (ROOT / "packaging" / "linux" / "build-native.sh").read_text(
        encoding="utf-8"
    )

    assert 'cd "$(dirname "$OUTPUT")"' in script
    assert 'output_name="$(basename "$OUTPUT")"' in script
    assert 'sha256sum "$output_name" >"$output_name.sha256"' in script
    assert 'sha256sum "$OUTPUT" >"$OUTPUT.sha256"' not in script


def test_linux_deb_old_builder_checks_ownership_before_compat_build() -> None:
    """glibc 2.17 构建机的旧 dpkg-deb 仅在 root:root 载荷上降级参数。"""

    script = (ROOT / "packaging" / "linux" / "build-native.sh").read_text(
        encoding="utf-8"
    )

    assert "dpkg-deb --help" in script
    assert "! -uid 0 -o ! -gid 0" in script
    assert "旧版 dpkg-deb 环境中的载荷并非全部 root:root" in script


def test_linux_native_packages_preserve_frozen_runtime_and_verify_identity() -> None:
    """RPM 不得改写冻结载荷，DEB/RPM 输出必须回读版本与架构。"""

    script = (ROOT / "packaging" / "linux" / "build-native.sh").read_text(
        encoding="utf-8"
    )

    assert "%global __os_install_post %{nil}" in script
    assert "dpkg-deb --field" in script
    assert "rpm -qp --queryformat" in script
    assert "元数据与冻结版本/架构不一致" in script
    assert 'find "$PKG/opt/partyops" -type f -exec chmod 0644 {} +' in script
    assert 'find "$PKG/opt/partyops" -type f -perm /111 -print0' in script
    assert "原生包共享库被错误标记为可执行文件" in script


def test_portable_builder_uses_an_executable_allowlist() -> None:
    """共享库和静态资源不得以可执行权限进入便携或原生安装包。"""

    script = (ROOT / "packaging" / "uos" / "build-portable.sh").read_text(
        encoding="utf-8"
    )

    assert 'find "$RUNTIME" -type f -exec chmod 0644 {} +' in script
    assert 'find "$RUNTIME" -type f -perm /111 -print0' in script
    assert "共享库被错误标记为可执行文件" in script
    assert script.index('find "$RUNTIME" -type f -exec chmod 0644') < script.index(
        'chmod 0755 "$RUNTIME/partyops"'
    )


def test_linux_services_cap_restart_storms() -> None:
    """运行库被系统策略拦截时不得无限重启并持续弹出安全中心提示。"""

    for name in ("partyops.service", "partyops-updater.service"):
        unit = (ROOT / "packaging" / "uos" / name).read_text(encoding="utf-8")
        assert "StartLimitBurst=3" in unit
        assert "Restart=on-failure" in unit
        assert "RestartSec=10" in unit
        assert "Restart=always" not in unit


def test_linux_ocr_uses_locked_glibc217_runtime_not_build_host() -> None:
    """Linux 制品必须封入固定 OCR，不能复用构建机的过时系统版本。"""

    portable = (ROOT / "packaging" / "uos" / "build-portable.sh").read_text(
        encoding="utf-8"
    )
    builder = (ROOT / "packaging" / "linux" / "build-tesseract-runtime.sh").read_text(
        encoding="utf-8"
    )
    environment = (
        ROOT / "packaging" / "uos" / "ensure-build-environment.sh"
    ).read_text(encoding="utf-8")
    configured_host = (ROOT / "packaging" / "uos" / "build-and-install.sh").read_text(
        encoding="utf-8"
    )

    assert 'OCR_ARCHIVE="$OCR_RUNTIME/tesseract-runtime.tar.gz"' in portable
    assert "validate-portable-tar.py" in portable
    assert "--expected-root tesseract-5.5.3" in portable
    assert "command -v tesseract" not in portable
    assert "/usr/share/tesseract" not in portable
    assert "^tesseract 5\\.5\\.3" in portable
    assert "chi_sim" in portable and "eng" in portable

    assert "TESSERACT_VERSION=5.5.3" in builder
    assert '[[ "$GLIBC_VERSION" == 2.17 ]]' in builder
    assert "readelf --version-info" in builder
    assert "GLIBC_2.17" in builder
    assert "-static-libstdc++ -static-libgcc" in builder
    assert "DISABLED_LEGACY_ENGINE=ON" in builder
    assert "ENABLE_NATIVE=OFF" in builder
    assert "validate-portable-tar.py" in builder
    assert "ocr-smoke.pgm" in builder
    assert "Tesseract OCR 识别链路自检通过" in builder
    assert '[[ "$OCR_SMOKE_OUTPUT" == TEST ]]' in builder
    assert "partyops-ocr-archive." in builder
    assert "-type d -exec chmod 0755" in builder
    assert "-type f -exec chmod 0644" in builder
    for script in (environment, configured_host):
        assert "tesseract-ocr" not in script
        assert "command -v tesseract" not in script
        assert "tesseract --list-langs" not in script


def test_windows_ocr_uses_locked_minimal_runtime_and_runs_during_freeze() -> None:
    """Windows 制品必须封入固定中英文 OCR，且不能夹带卸载器或训练工具。"""

    helper = (ROOT / "packaging" / "windows" / "prepare-ocr-runtime.ps1").read_text(
        encoding="utf-8"
    )
    builder = (ROOT / "packaging" / "windows" / "build-windows.ps1").read_text(
        encoding="utf-8"
    )
    packager = (ROOT / "packaging" / "windows" / "package-windows.ps1").read_text(
        encoding="utf-8"
    )

    assert "57825338CEAA141C617F66D2A2210B6BEF396436FFC83D242595E5F5F33BF462" in helper
    assert "8174F4646283567AEF49490393D95F3D89265E7B584FA3D95CF64F7795B90CC5" in helper
    assert "tesseract v5\\.5\\.3" in helper
    assert "tesseract 5\\.5\\.2" in helper
    assert '$env:PARTYOPS_LEGACY_ARCH -eq "x86"' in helper
    assert "chi_sim.traineddata" in helper and "eng.traineddata" in helper
    assert "tesseract-uninstall.exe" in helper and "lstmtraining.exe" in helper
    assert "Expand-VerifiedPartyOpsOcrRuntime" in builder
    assert "Expand-VerifiedPartyOpsOcrRuntime" in packager


def test_windows7_x86_ocr_archive_is_locked_native_and_minimal() -> None:
    """Win7 x86 只能封入静态 x86 引擎，不能复用 amd64 OCR DLL。"""

    archive_path = (
        ROOT
        / "vendor"
        / "windows"
        / "ocr"
        / "tesseract-5.5.2-windows7-x86.zip"
    )
    assert hashlib.sha256(archive_path.read_bytes()).hexdigest() == (
        "8174f4646283567aef49490393d95f3d89265e7b584fa3d95cf64f7795b90cc5"
    )
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        executable = archive.read("bin/tesseract.exe")
    pe_offset = struct.unpack_from("<I", executable, 0x3C)[0]
    machine = struct.unpack_from("<H", executable, pe_offset + 4)[0]
    assert executable[:2] == b"MZ" and machine == 0x014C
    assert not any(name.lower().endswith(".dll") for name in names)
    assert {
        "tessdata/chi_sim.traineddata",
        "tessdata/eng.traineddata",
        "tessdata/osd.traineddata",
        "SOURCE.json",
    } <= names


def test_linux_native_packaging_accepts_only_explicit_validated_cross_payload() -> None:
    """ARM 自检载荷可在 x86_64 封装，但必须显式授权并复核 ELF 架构。"""

    native = (ROOT / "packaging" / "linux" / "build-native.sh").read_text(
        encoding="utf-8"
    )

    assert "PARTYOPS_ALLOW_CROSS_PACKAGE" in native
    assert "EXPECTED_PAYLOAD_PATTERN='x86-64'" in native
    assert "EXPECTED_PAYLOAD_PATTERN='ARM aarch64'" in native
    assert 'file "$PKG/opt/partyops/partyops"' in native
    assert native.count('--target "$RPM_ARCH"') == 2
    assert "tar --zstd" not in native
    assert 'zstd -dc -- "$PORTABLE_COPY"' in native
