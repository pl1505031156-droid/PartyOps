"""macOS 构建链静态门禁；原生 Mach-O/PKG 仍必须在真实 Mac 执行。"""

from __future__ import annotations

import ast
import base64
import hashlib
import importlib.util
import json
import plistlib
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import package_selftest, update_executor

ROOT = Path(__file__).resolve().parents[2]
MACOS = ROOT / "packaging" / "macos"


def test_macos_package_selftest_uses_bundle_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "PartyOps.app" / "Contents" / "MacOS"
    resources = runtime.parent / "Resources"
    runtime.mkdir(parents=True)
    resources.mkdir()
    monkeypatch.setattr(package_selftest.sys, "platform", "darwin")
    assert package_selftest._runtime_contents(runtime) == resources


def test_macos_python_entrypoints_parse_and_use_native_user_paths() -> None:
    for name in ("launcher.py", "launch_agent_entrypoint.py", "updater_entrypoint.py"):
        ast.parse((MACOS / name).read_text(encoding="utf-8"))
    launcher = (MACOS / "launcher.py").read_text(encoding="utf-8")
    agent = (MACOS / "launch_agent_entrypoint.py").read_text(encoding="utf-8")
    assert "Library\" / \"Application Support\" / \"PartyOps" in launcher
    assert "fcntl.flock" in launcher
    assert "wizard.url\").unlink(missing_ok=True)" in launcher
    assert "runtime-launch.log" in launcher
    assert "_wait_for_client_browser" in launcher
    assert "client-browser.url" in launcher
    assert "/api/v1/health" in launcher
    assert "app_version" in launcher and "payload.get(\"mode\")" in launcher
    assert "_consume_reconfigure_request" in launcher
    assert "partyops-client://reconfigure" in launcher
    assert "partyops-client://official-format/" not in launcher
    assert "os.execve" in agent
    assert 'not key.startswith("PARTYOPS_")' in agent
    assert ".partyops-personal-process.json" in agent
    assert "--browser-url-file" in agent
    updater = (MACOS / "updater_entrypoint.py").read_text(encoding="utf-8")
    assert "_controlled_environment" in updater
    assert "PARTYOPS_DATA_DIR" in updater
    assert "--macos-install-package" in updater
    assert "device_update_requested and not device_update_mode" in updater
    client_agent = (ROOT / "backend" / "app" / "client_agent.py").read_text(
        encoding="utf-8"
    )
    assert '[str(helper), "--macos-install-package", str(target)]' in client_agent


def test_macos_build_is_native_strict_signed_and_notarized() -> None:
    build = (MACOS / "build-pkg.sh").read_text(encoding="utf-8")
    validation = (MACOS / "validate-bundle.sh").read_text(encoding="utf-8")
    spec = (MACOS / "partyops.spec").read_text(encoding="utf-8")
    runbook = (MACOS / "README.md").read_text(encoding="utf-8")
    assert "MACOS_NATIVE_BUILD_REQUIRED" in build
    assert "MACOS_BUILD_ARCH_MISMATCH" in build
    assert "UNSIGNED-DO-NOT-PUBLISH" in build
    assert "codesign --deep" not in build
    assert "notarytool submit" in build
    assert "stapler staple" in build and "stapler validate" in build
    assert 'stapler staple "$APP"' in build
    assert 'spctl --assess --type execute' in build
    assert 'data.get("status") == "Accepted"' in build
    assert "pkgutil --check-signature" in build
    assert "/opt/homebrew|/usr/local|/Users/" in validation
    assert "MACOS_ARCH_MISMATCH" in validation
    assert 'bundle_identifier="cn.partyops.desktop"' in spec
    assert '"CFBundleExecutable": "partyops-desktop"' in spec
    assert '"CFBundleVersion": "1.4.5.3"' in spec
    assert '"LSMinimumSystemVersion": "11.0"' in spec
    assert "target_arch=target_arch" in spec
    assert 'name="partyops-updater"' in spec
    assert 'name="partyops-desktop"' in spec
    # 桌面入口与核心主程序不能只靠大小写区分；普通 APFS 默认不区分
    # 大小写，会把 PartyOps 与 partyops 当成同一文件。
    assert 'name="PartyOps",\n    target_arch=target_arch' not in spec
    assert "required=(partyops-desktop partyops-desktop-bin partyops " in validation
    assert "MACOS_CASEFOLD_NAME_COLLISION" in validation
    assert "MACOS_BUNDLE_EXECUTABLE_INVALID" in validation
    # PyInstaller 对 datas 的重排位置不是运行时安全契约；根公钥必须在
    # BUNDLE 完成后显式安装到 Apple 约定的 Resources 目录。
    assert "update-public-key.txt" not in spec
    assert (
        'UPDATE_PUBLIC_KEY_TARGET="$APP/Contents/Resources/update-public-key.txt"'
        in build
    )
    assert '/usr/bin/install -m 0644 "$UPDATE_PUBLIC_KEY_SOURCE"' in build
    assert '/usr/bin/cmp -s "$UPDATE_PUBLIC_KEY_SOURCE" "$UPDATE_PUBLIC_KEY_TARGET"' in build
    assert 'macho-candidates-adhoc.bin' in build
    assert 'done <"$MACHO_CANDIDATE_LIST"' in build
    assert 'BUNDLE_EXECUTABLE="$APP/Contents/MacOS/partyops-desktop"' in build
    assert 'PYTHON_DESKTOP="$APP/Contents/MacOS/partyops-desktop-bin"' in build
    assert 'xcrun clang -arch "$TARGET_ARCH"' in build
    wrapper = (MACOS / "launcher-wrapper.c").read_text(encoding="utf-8")
    assert "status=launchservices-entered" in wrapper
    assert 'execv(target, child_argv)' in wrapper
    assert 'partyops_log_path("launch-stderr.log"' in wrapper
    assert "sanitize_child_environment" in wrapper
    assert "_PYI_PARENT_PROCESS_LEVEL" in wrapper
    assert 'setenv("PYINSTALLER_RESET_ENVIRONMENT", "1", 1)' in wrapper
    assert "DYLD_LIBRARY_PATH" in wrapper
    assert "status=desktop-stderr-tail" in wrapper
    assert "chdir(directory)" in wrapper
    assert "status=desktop-child-started" in wrapper
    assert "status=desktop-child-exited" in wrapper
    assert "status=desktop-child-signaled" in wrapper
    assert "waitpid(child, &child_status, 0)" in wrapper
    assert '"CFBundleURLSchemes": ["partyops-client"]' in spec
    assert 'find "$APP/Contents" -type f ! -path "$BUNDLE_EXECUTABLE" -print0' in build
    assert build.count('"$BUNDLE_EXECUTABLE"') == 4
    assert build.index('done <"$MACHO_CANDIDATE_LIST"') < build.index(
        'codesign --force --timestamp --options runtime \\\n    --sign "$PARTYOPS_MACOS_APPLICATION_IDENTITY" "$BUNDLE_EXECUTABLE"'
    )
    assert build.rindex('done <"$MACHO_CANDIDATE_LIST"') < build.index(
        'codesign --force --options runtime --sign - "$BUNDLE_EXECUTABLE"'
    )
    assert 'PAYLOAD_ROOT="$BUILD_ROOT/pkg-root"' in build
    assert "export MACOSX_DEPLOYMENT_TARGET='11.0'" in build
    assert 'PAYLOAD_ARCHIVE="$PAYLOAD_INSTALLER_DIR/PartyOps.app.zip"' in build
    assert '/usr/bin/ditto -c -k --sequesterRsrc --keepParent' in build
    assert '/usr/bin/ditto -x -k "$PAYLOAD_ARCHIVE" "$roundtrip"' in build
    assert '/bin/chmod -R a+rX,go-w "$APP"' in build
    assert "MACOS_APP_PERMISSIONS_PRIVATE" in build
    assert 'pkgbuild --root "$PAYLOAD_ROOT"' in build
    assert "--component-plist" not in build
    assert 'pkgbuild --component "$APP"' not in build
    assert '--scripts "$PKG_SCRIPTS"' in build
    preinstall = (MACOS / "pkg-scripts" / "preinstall").read_text(encoding="utf-8")
    postinstall = (MACOS / "pkg-scripts" / "postinstall").read_text(encoding="utf-8")
    assert "MACOS_EXISTING_APP_UNSAFE" in preinstall
    assert "MACOS_EXISTING_APP_CONFLICT" in preinstall
    assert "/usr/bin/find \"$APP\" -depth -delete" not in preinstall
    assert "Application Support" not in preinstall
    assert "MACOS_INSTALL_TRANSACTION_FAILED" in postinstall
    assert "MACOS_STAGED_APP_INVALID" in postinstall
    assert "MACOS_APP_EXECUTABLE_INVALID" in postinstall
    assert "MACOS_APP_SIGNATURE_INVALID" in postinstall
    assert 'BACKUP_APP="$BACKUP_ROOT/PartyOps.app"' in postinstall
    assert '/usr/bin/ditto -x -k "$ARCHIVE" "$STAGE_ROOT"' in postinstall
    assert '/usr/sbin/chown -R root:wheel "$STAGED_APP"' in postinstall
    assert '/bin/chmod -R a+rX,go-w "$STAGED_APP"' in postinstall
    assert '/usr/bin/codesign --verify --deep --strict "$candidate"' in postinstall
    assert "Application Support/PartyOps/Installer" in postinstall
    assert '"$OCR_RUNTIME/bin/tesseract" "$APP/Contents/MacOS/tesseract"' in build
    assert '"$OCR_RUNTIME/tessdata" "$APP/Contents/Resources/ocr/tessdata"' in build
    assert 'Contents/Resources/ocr/tessdata/chi_sim.traineddata' in validation
    assert 'runtime.parent / "Resources" / "ocr"' in (
        ROOT / "packaging" / "uos" / "entrypoint.py"
    ).read_text(encoding="utf-8")
    package_selftest = (ROOT / "backend" / "app" / "package_selftest.py").read_text(
        encoding="utf-8"
    )
    app_main = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    assert 'resources = runtime.parent / "Resources"' in package_selftest
    assert 'parent.parent / "Resources"' in app_main
    assert '"$LLAMA_RUNTIME/llama-server" "$APP/Contents/MacOS/llama-server"' in build
    assert "PARTYOPS_MACOS_OFFICE_RUNTIME" in build
    assert "MACOS_OFFICE_RUNTIME_MISSING" in build
    assert "MACOS_OFFICE_RUNTIME_ARCH_MISMATCH" in build
    assert "MACOS_OFFICE_RUNTIME_SYMLINK_INVALID" in build
    assert '"$OFFICE_RUNTIME" "$APP/Contents/Resources/office-runtime"' in build
    assert "PARTYOPS_MACOS_OCR_RUNTIME" not in spec
    assert "PARTYOPS_MACOS_LLAMA_RUNTIME" not in spec
    assert "PARTYOPS_MACOS_OFFICE_RUNTIME" not in spec
    assert '(str(ocr_runtime), "ocr")' not in spec
    assert '(str(llama_runtime), ".")' not in spec
    update_key = ROOT / "packaging" / "uos" / "update-public-key.txt"
    assert update_key.is_file()
    update_key_raw = base64.b64decode(
        update_key.read_text(encoding="ascii").strip(), validate=True
    )
    assert len(update_key_raw) == 32
    assert hashlib.sha256(update_key_raw).hexdigest() == (
        "7d9d69a006ab26add736a16d0f9eb4f3667343c63da18d7672daf5d6fa2de2a3"
    )
    assert "独立 onefile" in spec
    assert "MACOS_UPDATE_TRUST_ROOT_MISSING" in validation
    assert "--ownership recommended" in build
    assert "不使用 Docker" in runbook
    assert "UNSIGNED-DO-NOT-PUBLISH" in runbook
    assert "公开测试候选升级为稳定版的必要条件" in runbook


def test_macos_reconfigure_marker_is_short_lived_and_single_use(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LaunchServices 不透传 URL 时，桌面入口仍只能消费一次有效短期请求。"""

    module_spec = importlib.util.spec_from_file_location(
        "partyops_macos_launcher_test",
        MACOS / "launcher.py",
    )
    assert module_spec and module_spec.loader
    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace())
    launcher = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(launcher)
    config = tmp_path / "Config"
    config.mkdir()
    monkeypatch.setattr(launcher, "_config_root", lambda: config)
    monkeypatch.setattr(launcher.time, "time", lambda: 1_700_000_000)
    marker = config / "reconfigure-request.json"
    marker.write_text(
        '{"format_version":1,"requested_at":1700000000,"expires_at":1700000120}',
        encoding="utf-8",
    )

    assert launcher._consume_reconfigure_request() is True
    assert not marker.exists()
    assert launcher._consume_reconfigure_request() is False

    marker.write_text(
        '{"format_version":1,"requested_at":1699999000,"expires_at":1699999120}',
        encoding="utf-8",
    )
    assert launcher._consume_reconfigure_request() is False
    assert not marker.exists()


def test_macos_unsigned_candidate_and_remote_native_builder_are_explicit() -> None:
    build = (MACOS / "build-pkg.sh").read_text(encoding="utf-8")
    runtimes = (MACOS / "build-native-runtimes.sh").read_text(encoding="utf-8")
    validation = (MACOS / "validate-bundle.sh").read_text(encoding="utf-8")
    workflow = (
        ROOT / ".github" / "workflows" / "build-macos-1.4.5-rc.6.yml"
    ).read_text(encoding="utf-8")
    office = (ROOT / "scripts" / "prepare-libreoffice-macos.sh").read_text(
        encoding="utf-8"
    )
    package_selftest = (ROOT / "backend" / "app" / "package_selftest.py").read_text(
        encoding="utf-8"
    )
    readme = (MACOS / "README.md").read_text(encoding="utf-8")

    assert "--unsigned-candidate" in build
    assert "setup.py build_static bdist_wheel" in build
    assert 'sqlite.sqlite_version != "3.51.3"' in build
    assert "MACOS_SQLITE_FTS5_MISSING" in build
    assert "resolve_locked_source" in build
    assert "sqlite-amalgamation-3510300.zip" in build
    assert "acb1e6f5d832484bf6d32b681e858c38add8b2acdfd42ac5df24b8afb46552b4" in build
    assert "pysqlite3-0.5.4.tar.gz" in build
    assert "fbc69bfdc0cb43a5badd5403b126d5151371b5037e0397ba9802bb440c5b0021" in build
    assert "MACOS_SQLITE_ARCHIVE_UNSAFE" in build
    assert "validate-portable-tar.py" in build
    assert '"code_signature": "ad-hoc"' in build
    assert '"notarized": False' in build
    assert '"real_device_validation": False' in build
    assert "OUTPUT_HASH=\"$(shasum -a 256" in build
    assert '"$(basename "$OUTPUT")"' in build
    assert "MACOS_RUNTIME_NATIVE_REQUIRED" in runtimes
    assert "MACOS_RUNTIME_SOURCE_HASH_MISMATCH" in runtimes
    assert "assert_thin_architecture" in runtimes
    assert "assert_system_dependencies_only" in runtimes
    # libpng 在 Apple 平台默认生成 framework；必须显式关闭，避免 OCR
    # 运行时在用户电脑上依赖构建机路径中的 png.framework。
    assert "-DPNG_FRAMEWORK=OFF" in runtimes
    # OCR 必须能在无 Homebrew 的用户电脑上读取 JPG/TIFF 扫描件；源码、
    # 哈希、静态构建参数和真实格式探针全部进入同一构建契约。
    assert "LIBJPEG_TURBO_VERSION='3.1.3'" in runtimes
    assert "075920b826834ac4ddf97661cc73491047855859affd671d52079c6867c1c6c0" in runtimes
    assert "LIBTIFF_VERSION='4.7.1'" in runtimes
    assert "f698d94f3103da8ca7438d84e0344e453fe0ba3b7486e04c5bf7a9a3fabe9b69" in runtimes
    static_targets = (MACOS / "ocr-static-targets.cmake").read_text(
        encoding="utf-8"
    )
    assert runtimes.count(
        '-DCMAKE_PROJECT_INCLUDE_BEFORE="$SCRIPT_DIR/ocr-static-targets.cmake"'
    ) == 2
    assert 'export PARTYOPS_OCR_PREFIX="$PREFIX"' in runtimes
    assert "-DSTRICT_CONF=ON" in runtimes
    assert "add_library(CMath::CMath INTERFACE IMPORTED GLOBAL)" in static_targets
    assert "INTERFACE_LINK_LIBRARIES m" in static_targets
    assert "if(NOT _partyops_ocr_try_compile AND NOT TARGET CMath::CMath)" in (
        static_targets
    )
    assert "if(_partyops_ocr_try_compile)\n" not in static_targets
    assert static_targets.count("if(NOT _partyops_ocr_try_compile") == 2
    assert "PARTYOPS_OCR_PREFIX is required for the OCR build" in static_targets
    assert "CMAKE_TRY_COMPILE_PLATFORM_VARIABLES CMAKE_PROJECT_INCLUDE_BEFORE" in (
        static_targets
    )
    assert r"CMakeScratch[/\\\\]TryCompile-" in static_targets
    for imported_target, archive_name in (
        ("ZLIB::ZLIB", "libz.a"),
        ("PNG::PNG", "libpng16.a"),
        ("JPEG::JPEG", "libjpeg.a"),
        ("TIFF::TIFF", "libtiff.a"),
    ):
        assert f"_partyops_import_ocr_archive({imported_target} {archive_name})" in (
            static_targets
        )
    assert "-DENABLE_JPEG=ON -DENABLE_TIFF=ON" in runtimes
    assert "-DWITH_JPEG8=ON" in runtimes
    assert "-DDISABLE_TIFF=OFF" in runtimes
    assert 'export PKG_CONFIG_LIBDIR="$PREFIX/lib/pkgconfig"' in runtimes
    assert "-DCMAKE_IGNORE_PREFIX_PATH='/usr/local;/opt/homebrew'" in runtimes
    assert '-DJPEG_INCLUDE_DIR="$PREFIX/include"' in runtimes
    assert '-DJPEG_LIBRARY="$PREFIX/lib/libjpeg.a"' in runtimes
    assert runtimes.count('"${LOCKED_OCR_BASE_FIND_FLAGS[@]}"') == 2
    assert runtimes.count('"${LOCKED_OCR_FIND_FLAGS[@]}"') == 2
    assert "MACOS_OCR_FORMAT_SELFTEST_FAILED" in runtimes
    assert "for image_format in jpeg tiff" in runtimes
    assert "chi_sim.traineddata" in runtimes
    assert "llama-server" in runtimes
    # 动态依赖门禁必须读取真实 LC_LOAD/LC_RPATH；otool -L 同时包含
    # LC_ID_DYLIB，会把上游框架自己的安装名误判成外部依赖。
    assert '/usr/bin/otool -l "$candidate"' in validation
    assert "LC_(LOAD|LOAD_WEAK|REEXPORT|LAZY_LOAD|LOAD_UPWARD)_DYLIB" in validation
    assert 'bad_dependency="$candidate -> $external_dependency"' in validation

    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow and "pull_request:" not in workflow
    assert "contents: read" in workflow
    assert "macos-15-intel" in workflow and "macos-15" in workflow
    assert "BUILD-UNSIGNED-145-RC6" in workflow
    assert "ref: ${{ github.sha }}" in workflow
    assert "建立真实 0023/0025 覆盖升级基线" in workflow
    assert "MACOS_0023_UPGRADE_FAILED" in workflow
    assert "MACOS_0025_UPGRADE_FAILED" in workflow
    assert "test \"$revision\" = '0026'" in workflow
    assert "rc4-native-upgrade-admin" in workflow
    assert "PartyOps-pre-upgrade-*.partyops-backup" in workflow
    assert "verify_backup" in workflow
    assert "scripts/prepare-libreoffice-macos.sh" in workflow
    assert "PARTYOPS_MACOS_OFFICE_RUNTIME" in workflow
    assert "office-${{ matrix.architecture }}" in workflow
    assert 'file -b "$RUNTIME/program/soffice"' in office
    assert 'program/soffice.bin' not in office
    assert 'file -b "$OFFICE_RUNTIME/program/soffice"' in build
    assert 'test -f "$office/program/soffice.bin"' not in workflow
    assert 'lipo -archs "$office/program/soffice"' in workflow
    assert "不使用 Linux 的 `program/soffice.bin` 布局" in readme
    assert "macOS 11" in workflow
    assert "sudo /usr/sbin/installer" in workflow
    assert workflow.count('sudo /usr/sbin/installer -pkg "$package" -target /') == 1
    assert workflow.count("install_package") == 3
    assert "/var/log/install.log" in workflow
    assert 'cd "$(dirname "$package")"' in workflow
    assert '"$(basename "$package").sha256"' in workflow
    assert "actions/cache@0057852bfaa89a56745cba8c7296529d2fc39830" in workflow
    assert "MACOS_INSTALLED_APP_MISSING" in workflow
    assert 'plutil -extract CFBundleIdentifier raw' in workflow
    assert 'plutil -extract CFBundleExecutable raw' in workflow
    assert "= 'partyops-desktop'" in workflow
    assert 'Contents/MacOS/PartyOps' not in workflow
    assert '/usr/bin/open -na "$app" --args --launch-services-self-test' in workflow
    assert "status=desktop-child-exited child_pid=.* exit_code=0" in workflow
    assert "MACOS_LAUNCHSERVICES_SELFTEST_FAILED" in workflow
    assert "_PYI_PARENT_PROCESS_LEVEL=9" in workflow
    assert "PYTHONHOME='/tmp/forged-python-home'" in workflow
    assert "MACOSX_DEPLOYMENT_TARGET: '11.0'" in workflow
    assert "MACOS_DEPLOYMENT_TARGET_TOO_NEW" in validation
    assert '${deployment_target} (发布基线为 11.0)' in validation
    assert "$deployment_target（" not in validation
    runtime_lock = (MACOS / "requirements-runtime.txt").read_text(encoding="utf-8")
    assert "numpy==1.26.4" in runtime_lock
    assert "onnxruntime==1.19.2" in runtime_lock
    assert "sha256:d863e8acdc7232d705d49e41087e10b274c42f09e259016a46f32c34e06dc4fd" in runtime_lock
    assert '--requirement "$SCRIPT_DIR/requirements-runtime.txt"' in build
    assert "--require-hashes" in build and "--no-deps" in build
    assert "MACOS_OPENSSL_LEGACY_PROVIDER_REFERENCED" in build
    assert "MACOS_OPENSSL_LEGACY_PROVIDER_REMAINED" in build
    assert "/bin/rm -f \"$LEGACY_OPENSSL_PROVIDER\"" in build
    assert "OPENSSL_VERSION='3.5.7'" in build
    assert "a8c0d28a529ca480f9f36cf5792e2cd21984552a3c8e4aa11a24aa31aeac98e8" in build
    assert "OPENSSL_STATIC=1 OPENSSL_DIR=\"$OPENSSL_PREFIX\"" in build
    assert "--no-binary-package cryptography" in build
    assert "MACOS_CRYPTOGRAPHY_OPENSSL_MISMATCH" in build
    assert "MACOS_CRYPTOGRAPHY_DYNAMIC_OPENSSL" in build
    assert 'SOURCE_COMMIT="$(git -C "$ROOT" rev-parse HEAD)"' in build
    assert 'WORKFLOW_COMMIT="${GITHUB_SHA:-$SOURCE_COMMIT}"' in build
    assert '"workflow_commit": workflow_commit' in build
    assert "gh release" not in workflow
    action_lines = [
        line.strip() for line in workflow.splitlines() if line.strip().startswith("uses:")
    ]
    assert action_lines
    assert all(len(line.rsplit("@", 1)[-1]) == 40 for line in action_lines)

    assert "VERSION='26.2.5.2'" in office
    assert "c99fb4fe574437fc4cb820a4ca15271bca325920861f7139858b36d7f9df78ad" in office
    assert "e26180298685274b54aa7fe6e1101c65465a372f457a6748ebd642720811db36" in office
    assert "downloadarchive.documentfoundation.org" in office
    assert "hdiutil attach -readonly -nobrowse" in office
    assert "/bin/ln -s MacOS \"$RUNTIME/program\"" in office
    assert 'codesign --verify --deep --strict --verbose=2 "$SOURCE_APP"' in office
    assert "subprocess.run(" not in office
    assert '"--headless",' in package_selftest
    assert '"--version",' in package_selftest
    assert '"-env:UserInstallation=' in package_selftest
    assert "--preserve-metadata=entitlements" in build
    assert "--preserve-metadata=entitlements,flags" in build
    assert build.count('$APP/Contents/Resources/office-runtime/') >= 2
    # 未签名的新增 Mach-O 在首次校验时 codesign --display 会返回非零；
    # 校验器必须把“未签名”当成允许状态，而不是让 pipefail 静默中止。
    assert 'done <"$SCAN_LIST"' in validation
    assert "done < <(/usr/bin/find" not in validation
    assert "/usr/bin/awk -F= '/TeamIdentifier=/" in validation
    assert "|| true" in validation
    assert "MACOS_WIZARD_SELFTEST_FAILED" in validation
    assert "MACOS_PACKAGE_SELFTEST_FAILED" in validation
    assert "PARTYOPS_WIZARD_SELFTEST_REPORT" in validation


def test_macos_wizard_gui_selftest_writes_auditable_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无控制台 GUI 入口也必须留下成功或失败原因，不能只返回退出码 1。"""

    module_spec = importlib.util.spec_from_file_location(
        "partyops_wizard_selftest", ROOT / "packaging" / "uos" / "wizard_entrypoint.py"
    )
    assert module_spec and module_spec.loader
    wizard = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(wizard)
    report = tmp_path / "wizard-selftest.json"
    monkeypatch.setenv("PARTYOPS_WIZARD_SELFTEST_REPORT", str(report))

    class FakeRoot:
        def __init__(self) -> None:
            self.tk = SimpleNamespace(call=lambda *_args: "8.6.14")

        def withdraw(self) -> None:
            return None

        def update_idletasks(self) -> None:
            return None

        def destroy(self) -> None:
            return None

    monkeypatch.setitem(sys.modules, "tkinter", SimpleNamespace(Tk=FakeRoot))
    assert wizard._frozen_gui_self_test() == 0
    assert json.loads(report.read_text(encoding="utf-8")) == {
        "passed": True,
        "tcl_tk": "8.6.14",
    }

    def fail_tk() -> None:
        raise RuntimeError("Tk 框架无法加载")

    monkeypatch.setitem(sys.modules, "tkinter", SimpleNamespace(Tk=fail_tk))
    assert wizard._frozen_gui_self_test() == 2
    failure = json.loads(report.read_text(encoding="utf-8"))
    assert failure["passed"] is False
    assert failure["code"] == "PACKAGE_WIZARD_GUI_SELFTEST_FAILED"
    assert failure["error"] == "Tk 框架无法加载"


@pytest.mark.parametrize("target_revision", ["0023", "0025"])
def test_macos_upgrade_fixture_builds_both_real_baselines(
    tmp_path: Path, target_revision: str
) -> None:
    data_root = tmp_path / f"upgrade-{target_revision}"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "create-0023-upgrade-fixture.py"),
            "--repo-root",
            str(ROOT),
            "--data-root",
            str(data_root),
            "--target-revision",
            target_revision,
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    payload = json.loads(completed.stdout)
    assert payload["schema_revision"] == target_revision
    with sqlite3.connect(data_root / "partyops.db") as database:
        tables = {
            row[0]
            for row in database.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert ("timezone_migration_audits" in tables) is (target_revision == "0025")
    assert "ai_orchestration_sessions" not in tables


def test_macos_is_present_in_platform_release_contracts() -> None:
    scripts = {
        name: (ROOT / "scripts" / name).read_text(encoding="utf-8")
        for name in (
            "build-platform-update-packages.py",
            "generate-update-catalog.py",
            "generate-release-bundle-manifest.py",
            "validate-partyops-update.py",
        )
    }
    for content in scripts.values():
        assert "macos" in content
        assert "arm64" in content
        assert "amd64" in content or "x86_64" in content

    script = ROOT / "scripts" / "generate-update-catalog.py"
    spec = importlib.util.spec_from_file_location("macos_catalog_contract", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert ("macos", "amd64") in module.TARGETS
    assert ("macos", "arm64") in module.TARGETS

    update_route = (ROOT / "backend" / "app" / "routers" / "updates.py").read_text(
        encoding="utf-8"
    )
    assert "if personal_update or macos_local_update:" in update_route


def test_macos_bundle_version_rejects_links_and_invalid_plist(tmp_path: Path) -> None:
    app = tmp_path / "PartyOps.app"
    info = app / "Contents" / "Info.plist"
    info.parent.mkdir(parents=True)
    info.write_bytes(
        plistlib.dumps({"CFBundleShortVersionString": "1.4.3-rc.8"})
    )
    assert update_executor._macos_bundle_version(app) == "1.4.3-rc.8"
    info.write_text("not a plist", encoding="utf-8")
    assert update_executor._macos_bundle_version(app) == ""
    target = tmp_path / "real.app"
    target.mkdir()
    link = tmp_path / "linked.app"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("当前测试环境不允许创建目录链接")
    assert update_executor._macos_bundle_version(link) == ""


def test_macos_trust_requires_codesign_and_gatekeeper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = tmp_path / "PartyOps.app"
    app.mkdir()
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "accepted", "")

    monkeypatch.setattr(update_executor, "_run", fake_run)
    assert update_executor._macos_application_is_trusted(app) is True
    assert calls[0][:3] == ["/usr/bin/codesign", "--verify", "--strict"]
    assert calls[1][:4] == ["/usr/sbin/spctl", "--assess", "--type", "execute"]

    monkeypatch.setattr(
        update_executor,
        "_run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 1, "", "denied"),
    )
    assert update_executor._macos_application_is_trusted(app) is False


def test_macos_production_trust_root_is_loaded_only_from_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = tmp_path / "PartyOps.app" / "Contents" / "MacOS"
    runtime.mkdir(parents=True)
    resources = runtime.parent / "Resources"
    resources.mkdir()
    public_key = resources / "update-public-key.txt"
    public_key.write_text("A" * 44, encoding="utf-8")
    public_key.chmod(0o600)
    monkeypatch.setattr(update_executor.sys, "platform", "darwin")
    monkeypatch.setattr(update_executor.sys, "executable", str(runtime / "partyops-updater"))
    monkeypatch.setattr(
        update_executor,
        "get_settings",
        lambda: type("Settings", (), {"environment": "production", "update_public_key": "forged"})(),
    )
    assert update_executor._trusted_public_key() == "A" * 44


def test_macos_privileged_installer_passes_path_as_osascript_argument(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    package = tmp_path / "PartyOps update 'quoted'.pkg"
    package.write_bytes(b"pkg")
    observed: list[str] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.extend(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(update_executor, "_run", fake_run)
    assert update_executor._run_macos_privileged_installer(package) is True
    assert observed[:2] == ["/usr/bin/osascript", "-e"]
    assert "quoted form of packagePath" in observed[2]
    assert observed[-1] == str(package.resolve())
    assert str(package.resolve()) not in observed[2]


def test_launch_macos_update_missing_helper_fails_without_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    messages: list[dict[str, object]] = []
    monkeypatch.setattr(update_executor.sys, "platform", "darwin")
    monkeypatch.setattr(update_executor.sys, "executable", str(tmp_path / "partyops"))
    monkeypatch.setattr(
        update_executor,
        "_set_run",
        lambda _run_id, **values: messages.append(values),
    )
    assert update_executor.launch_macos_update("a" * 32) is False
    assert messages and "更新助手缺失" in str(messages[0]["message"])


def test_execute_macos_update_rejects_non_macos_before_database_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(update_executor.sys, "platform", "win32")
    monkeypatch.setattr(
        update_executor.db_runtime,
        "session_factory",
        lambda: pytest.fail("非 macOS 不应访问数据库"),
    )
    assert update_executor.execute_macos_update("b" * 32) is False
    assert update_executor.install_macos_device_package(Path("missing.partyops-update")) is False
