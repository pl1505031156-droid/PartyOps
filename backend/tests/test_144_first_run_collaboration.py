"""正式发布前的首次配置与协同入网回归测试。"""

from __future__ import annotations

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
            "agent_version": "1.4.2",
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
