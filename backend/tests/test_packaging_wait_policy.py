"""UOS 安装器慢启动等待策略的回归测试。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_portable_smoke_waits_for_slow_uos_startup() -> None:
    script = (ROOT / "packaging" / "uos" / "build-portable.sh").read_text(
        encoding="utf-8"
    )

    assert 'PARTYOPS_SMOKE_TIMEOUT_SECONDS:-180' in script
    assert "SMOKE_DEADLINE" in script
    assert 'kill -0 "$PID"' in script
    assert "portable-smoke-failure-$ARCH.log" in script
    assert "seq 1 30" not in script


def test_installed_service_waits_for_slow_uos_startup() -> None:
    script = (ROOT / "packaging" / "uos" / "build-and-install.sh").read_text(
        encoding="utf-8"
    )

    assert 'PARTYOPS_HEALTH_TIMEOUT_SECONDS:-180' in script
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
    configured_host = (
        ROOT / "packaging" / "uos" / "build-and-install.sh"
    ).read_text(encoding="utf-8")

    assert "systemctl is-enabled --quiet partyops-updater.service" in one_click
    assert "systemctl is-active --quiet partyops-updater.service" in one_click
    assert "journalctl -u partyops-updater -n 80" in one_click
    assert "systemctl enable --now partyops-updater.service" in configured_host
    assert "systemctl is-active --quiet partyops-updater.service" in configured_host
    assert "journalctl -u partyops-updater -n 80" in configured_host


def test_debian_upgrade_recovers_only_verified_stubborn_partyops_processes() -> None:
    script = (ROOT / "packaging" / "uos" / "build-deb.sh").read_text(
        encoding="utf-8"
    )

    assert "is_partyops_process()" in script
    assert '"/proc/$pid/exe"' in script
    assert '"/proc/$pid/stat"' in script
    assert 'kill -TERM "$pid"' in script
    assert 'kill -KILL "$pid"' in script
    assert "拒绝终止身份不匹配的进程" in script


def test_runtime_and_stop_script_have_bounded_graceful_shutdown() -> None:
    main = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    stop = (ROOT / "packaging" / "uos" / "stop.sh").read_text(encoding="utf-8")

    assert '"timeout_graceful_shutdown": 15' in main
    assert "is_partyops_process()" in stop
    assert 'kill -TERM "$PID"' in stop
    assert 'kill -KILL "$PID"' in stop
    assert '"/proc/$pid/stat"' in stop


def test_legacy_host_config_is_migrated_to_tls_agent_port() -> None:
    start = (ROOT / "packaging" / "uos" / "start.sh").read_text(encoding="utf-8")
    deb = (ROOT / "packaging" / "uos" / "build-deb.sh").read_text(
        encoding="utf-8"
    )

    for script in (start, deb):
        assert "migrate_legacy_host_config()" in script
        assert "PARTYOPS_AGENT_PORT" in script
        assert "PARTYOPS_TLS_ENABLED=true" in script
        assert "旧版主机配置已迁移" in script


def test_agent_listener_failure_stops_partial_host_startup() -> None:
    main = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")

    assert "agent_server.started" in main
    assert "agent_thread.is_alive()" in main
    assert "设备安全端口启动失败" in main


def test_optional_local_ai_dependencies_do_not_block_base_uos_install() -> None:
    """本地模型组件缺失时，基础离线安装必须仍可完成。"""

    base_requirements = (ROOT / "backend" / "requirements.txt").read_text(
        encoding="utf-8"
    )
    portable = (ROOT / "packaging" / "uos" / "build-portable.sh").read_text(
        encoding="utf-8"
    )
    local_ai_requirements = (
        ROOT / "backend" / "requirements-local-ai.txt"
    ).read_text(encoding="utf-8")

    for package in ("numpy", "onnxruntime", "tokenizers"):
        assert f"{package}==" not in base_requirements
        assert f"{package}==" in local_ai_requirements
    assert 'if [[ "$LOCAL_EMBEDDING_AVAILABLE" == "1" ]]' in portable
    assert 'if [[ "$LOCAL_LLM_AVAILABLE" == "1" ]]' in portable
    assert 'requirements-local-ai.txt' in portable
    assert 'validate-uos-wheelhouse.py' in portable
    assert "本地语义离线依赖闭包不完整" in portable


def test_windows_installer_defers_host_privileges_until_role_selection() -> None:
    """协同机安装后不能自动启动主机服务、更新服务或开放入站端口。"""

    installer = (ROOT / "packaging" / "windows" / "PartyOps.iss").read_text(
        encoding="utf-8"
    )
    host_service = (
        ROOT / "packaging" / "windows" / "windows_service.py"
    ).read_text(encoding="utf-8")

    assert installer.count("--startup manual") == 2
    assert "--startup auto" not in installer
    assert "start PartyOpsUpdateService" not in installer
    assert "runasoriginaluser" in installer
    assert 'ValueName: "PartyOpsAgent"' not in installer
    assert "advfirewall firewall add rule" not in installer
    assert "sdset PartyOpsHost" in installer
    assert "remoteip=LocalSubnet" in host_service
    assert '["sc.exe", "start", "PartyOpsUpdateService"]' in host_service
    assert "prepare_host_runtime(environment, executable)" in host_service


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
    assert installer.index("function PrepareToInstall") < installer.index(
        "procedure CurStepChanged"
    )
