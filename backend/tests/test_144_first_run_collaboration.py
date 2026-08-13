"""正式发布前的首次配置与协同入网回归测试。"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.networking import enrollment_service_url
from app.routers import fleet
from app import setup_wizard


def test_enrollment_rejects_loopback_advertised_host(
    client,
    admin: dict,
    monkeypatch,
) -> None:
    """协同机绝不能收到只指向自身的回环地址。"""

    monkeypatch.setattr(
        fleet,
        "discover_lan_addresses",
        lambda: ["192.168.36.18"],
        raising=False,
    )
    response = client.post(
        "/api/v1/admin/devices/enrollments",
        json={"name": "组织委员电脑", "advertised_host": "127.0.0.1"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "ENROLLMENT_HOST_INVALID"
    assert "advertised_host" in response.json()["fields"]


def test_enrollment_uses_selected_lan_address(
    client,
    admin: dict,
    monkeypatch,
) -> None:
    """主机管理员选择的可信局域网地址必须原样交给协同机。"""

    monkeypatch.setattr(
        fleet,
        "discover_lan_addresses",
        lambda: ["10.23.8.16", "192.168.36.18"],
        raising=False,
    )
    response = client.post(
        "/api/v1/admin/devices/enrollments",
        json={"name": "档案室电脑", "advertised_host": "192.168.36.18"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["host_url"] == "http://192.168.36.18:18765"


def test_enrollment_requires_choice_when_multiple_lan_addresses_exist(
) -> None:
    """多网卡机器不能猜测地址，避免把虚拟网卡地址发给协同机。"""

    with pytest.raises(LookupError):
        enrollment_service_url(
            requested_host=None,
            configured_host="127.0.0.1",
            configured_port=18765,
            request_base_url="http://127.0.0.1:18765",
            lan_candidates=["10.23.8.16", "192.168.36.18"],
            tls_enabled=False,
        )


def test_enrollment_status_confirms_agent_completed_pairing(
    client,
    admin: dict,
    monkeypatch,
) -> None:
    """主机界面应以真实入网结果确认完成，不能让新手凭感觉判断。"""

    monkeypatch.setattr(
        fleet,
        "discover_lan_addresses",
        lambda: ["192.168.36.18"],
    )
    created = client.post(
        "/api/v1/admin/devices/enrollments",
        json={"name": "党员活动室电脑", "advertised_host": "192.168.36.18"},
    )
    assert created.status_code == 201, created.text
    enrollment_id = created.json()["id"]

    pending = client.get(f"/api/v1/admin/devices/enrollments/{enrollment_id}/status")
    assert pending.status_code == 200, pending.text
    assert pending.json()["status"] == "pending"

    enrolled = client.post(
        "/api/v1/devices/enroll",
        json={
            "code": created.json()["code"],
            "name": "党员活动室电脑",
            "architecture": "amd64",
            "platform": "windows",
            "agent_version": "1.4.3",
            "local_username": "partyops-user",
        },
    )
    assert enrolled.status_code == 201, enrolled.text

    completed = client.get(f"/api/v1/admin/devices/enrollments/{enrollment_id}/status")
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "enrolled"
    assert completed.json()["device_id"] == enrolled.json()["device_id"]


def test_first_run_wizard_prioritizes_reachable_role_based_setup(monkeypatch) -> None:
    """首次配置必须先选角色，且局域网地址优先于只能本机访问的地址。"""

    monkeypatch.setattr(
        setup_wizard,
        "discover_lan_addresses",
        lambda: ["192.168.36.18"],
    )
    page = setup_wizard.render_page("csrf-token")

    assert "第一步 · 这台电脑做什么" in page
    assert 'data-role="host"' in page
    assert 'data-role="client"' in page
    assert 'value="192.168.36.18" selected' in page
    assert page.index('value="192.168.36.18"') < page.index('value="127.0.0.1"')
    assert "先测试主机连接" in page
    assert 'id="client-submit"' in page and "disabled" in page


def test_client_first_run_rejects_loopback_host(monkeypatch) -> None:
    """协同机填写 127.0.0.1 等于连接自己，正式环境应在联网前阻止。"""

    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    with pytest.raises(ValueError, match="协同机不能使用回环地址"):
        setup_wizard.resolve_host_url("http://127.0.0.1:18765")


def test_host_first_run_keeps_admin_creation_inside_setup() -> None:
    """主机配置结束前必须创建首位管理员，不能把第二套向导丢到今日工作台。"""

    page = setup_wizard.render_admin_setup_page(
        "csrf-token",
        "https://192.168.36.18:18765",
    )
    assert "首次配置最后一步" in page
    assert "创建管理员并进入登录页" in page
    assert 'name="mode" value="bootstrap_admin"' in page
    assert "我已掌握" not in page


def test_first_admin_bootstrap_uses_local_loopback_channel(monkeypatch) -> None:
    """管理员密码只应发送给刚启动的本机服务，不经过办公网地址。"""

    captured: dict[str, object] = {}

    class Response:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def open_request(request, **kwargs):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["context"] = kwargs.get("context")
        return Response()

    monkeypatch.setattr(setup_wizard.urllib.request, "urlopen", open_request)
    setup_wizard.bootstrap_first_admin(
        "https://192.168.36.18:18765",
        username="Admin_01",
        display_name="首位管理员",
        password="PartyOps@2026",
    )

    assert captured["url"] == "https://127.0.0.1:18765/api/v1/bootstrap/host"
    assert captured["body"] == {
        "username": "admin_01",
        "display_name": "首位管理员",
        "password": "PartyOps@2026",
    }
    assert captured["context"] is not None


@pytest.mark.skipif(setup_wizard.os.name != "nt", reason="仅验证 Windows UAC 角色隔离")
def test_windows_host_role_uses_uac_helper_for_protected_config(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """日常桌面账号选择主机时只为系统配置请求一次 UAC。"""

    program_data = tmp_path / "ProgramData"
    local_config = tmp_path / "LocalConfig"
    helper = tmp_path / "PartyOpsWizard.exe"
    helper.write_bytes(b"test")
    elevated_calls: list[list[str]] = []
    cleared: list[bool] = []

    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    monkeypatch.setenv("PROGRAMDATA", str(program_data))
    monkeypatch.setattr(setup_wizard, "config_root", lambda: local_config)
    monkeypatch.setattr(
        setup_wizard,
        "discover_lan_addresses",
        lambda: ["192.168.36.18"],
    )
    monkeypatch.setattr(setup_wizard, "windows_is_admin", lambda: False)
    monkeypatch.setattr(setup_wizard, "_executable", lambda _name: helper)
    monkeypatch.setattr(
        setup_wizard,
        "clear_windows_client_autostart",
        lambda: cleared.append(True),
    )

    def run_elevated(command, **_kwargs):
        elevated_calls.append(command)
        config = program_data / "PartyOps" / "partyops.env"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("PARTYOPS_MODE=host\n", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(setup_wizard.subprocess, "run", run_elevated)
    config_path = setup_wizard.configure_host_config(
        "192.168.36.18",
        18765,
        tmp_path / "业务 数据",
    )

    assert config_path == program_data / "PartyOps" / "partyops.env"
    assert "-Verb RunAs" in elevated_calls[0][4]
    encoded_data_dir = elevated_calls[0][-1]
    assert base64.b64decode(encoded_data_dir).decode("utf-8").endswith("业务 数据")
    assert json.loads((local_config / "mode.json").read_text(encoding="utf-8"))[
        "mode"
    ] == "host"
    assert cleared == [True]


def test_enablement_center_uses_real_state_and_role_specific_steps(
    client,
    admin: dict,
    staff: dict,
) -> None:
    """上手中心的完成状态必须来自业务事实，且普通用户不出现管理任务。"""

    admin_status = client.get("/api/v1/me/enablement")
    assert admin_status.status_code == 200, admin_status.text
    admin_body = admin_status.json()
    assert admin_body["persona"] == "host_admin"
    admin_steps = {item["key"]: item for item in admin_body["steps"]}
    assert admin_steps["account"]["complete"] is True
    assert admin_steps["network"]["complete"] is False
    assert admin_steps["backup"]["route"] == "/settings/backups"

    try:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "staff", "password": "PartyOps@2026"},
        )
        assert login.status_code == 200, login.text
        staff_status = client.get("/api/v1/me/enablement")
        assert staff_status.status_code == 200, staff_status.text
        staff_body = staff_status.json()
        assert staff_body["persona"] == "host_staff"
        assert "backup" not in {item["key"] for item in staff_body["steps"]}
        assert all(
            not item["route"].startswith("/settings")
            for item in staff_body["steps"]
        )
    finally:
        restored = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "PartyOps@2026"},
        )
        assert restored.status_code == 200, restored.text
