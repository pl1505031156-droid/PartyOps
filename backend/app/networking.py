"""可信局域网地址发现与绑定校验。"""

from __future__ import annotations

import ipaddress
import socket


def discover_lan_addresses() -> list[str]:
    """返回本机可用于办公局域网的私有 IPv4 地址。"""

    candidates: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            candidates.add(info[4][0])
    except OSError:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("192.0.2.1", 9))
            candidates.add(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass
    return sorted(
        address
        for address in candidates
        if address != "127.0.0.1"
        and ipaddress.ip_address(address).is_private
        and not ipaddress.ip_address(address).is_link_local
    )


def validate_bind_host(host: str, production: bool) -> None:
    """生产模式拒绝通配或公网绑定，减少误配置暴露。"""

    if not production:
        return
    if host in {"0.0.0.0", "::"}:
        raise RuntimeError("生产模式必须选择明确的可信局域网 IP，不能绑定全部网卡")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # 固定设备名可用于局域网，实际解析由操作系统负责。
        return
    if not (address.is_private or address.is_loopback):
        raise RuntimeError("生产模式禁止绑定公网 IP")


def validate_transport_security(
    *,
    host: str,
    production: bool,
    tls_enabled: bool,
) -> None:
    """生产环境对外提供局域网服务时必须使用 HTTPS。"""

    if not production or tls_enabled:
        return
    if host in {"127.0.0.1", "::1", "localhost"}:
        # 仅保留冻结运行时的本机健康检查与安装器冒烟入口。
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise RuntimeError("生产模式局域网服务必须启用 HTTPS，禁止明文 HTTP")


def service_url(host: str, port: int) -> str:
    shown_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    return f"http://{shown_host}:{port}"
