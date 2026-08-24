"""可信局域网地址发现与绑定校验。"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit


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


def validate_bind_host(
    host: str,
    production: bool,
    *,
    advertised_host: str | None = None,
) -> None:
    """生产模式只允许可信局域网监听。

    Windows 主机为了让本机首次配置始终可以走 127.0.0.1，可显式监听
    0.0.0.0；此时必须同时提供可信的局域网展示地址，安装器还会把入站
    防火墙限制在专用网络和 LocalSubnet。
    """

    if not production:
        return
    if host in {"0.0.0.0", "::"}:  # nosec B104 - 仅配合可信展示地址和防火墙使用。
        advertised = (advertised_host or "").strip()
        if not advertised or advertised in {"0.0.0.0", "::"}:  # nosec B104 - 这里只拒绝通配展示值，未绑定网络接口。
            raise RuntimeError("通配监听必须同时配置明确的可信局域网展示地址")
        try:
            address = ipaddress.ip_address(advertised)
        except ValueError:
            return
        if address.is_link_local:
            raise RuntimeError("协同公布地址不能使用链路本地地址")
        if address.is_loopback or address.is_unspecified or address.is_multicast:
            raise RuntimeError("协同公布地址必须是其他电脑可达的明确私网地址")
        if not address.is_private:
            raise RuntimeError("协同公布地址不得直接使用公网 IP")
        return
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # 固定设备名可用于局域网，实际解析由操作系统负责。
        return
    if not (address.is_private or address.is_loopback):
        raise RuntimeError("生产模式禁止绑定公网 IP")


def validate_advertise_host(host: str) -> None:
    """拒绝不能由其他电脑访问的协同公布地址。"""

    normalized = host.strip().lower().rstrip(".")
    if not normalized or normalized in {"localhost", "0.0.0.0", "::"}:  # nosec B104 - 此处只校验展示值。
        raise RuntimeError("协同公布地址不能使用 localhost 或通配地址")
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        # 固定局域网 DNS 名称由管理员控制；协议和路径在调用方拒绝。
        if normalized.endswith(".localhost"):
            raise RuntimeError("协同公布地址不能使用仅本机解析的名称")
        return
    if address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast:
        raise RuntimeError("协同公布地址必须是其他电脑可达的明确私网地址或局域网主机名")
    if not address.is_private:
        raise RuntimeError("协同公布地址不得直接使用公网 IP")


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


def service_url(host: str, port: int, *, tls_enabled: bool = False) -> str:
    """返回浏览器和诊断接口应展示的主服务地址。"""

    shown_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host  # nosec B104 - 通配值只映射为回环展示地址。
    try:
        if isinstance(ipaddress.ip_address(shown_host), ipaddress.IPv6Address):
            shown_host = f"[{shown_host}]"
    except ValueError:
        pass
    scheme = "https" if tls_enabled else "http"
    return f"{scheme}://{shown_host}:{port}"


def enrollment_service_url(
    *,
    requested_host: str | None,
    configured_host: str,
    configured_port: int,
    request_base_url: str,
    lan_candidates: list[str],
    tls_enabled: bool,
) -> str:
    """选择协同机实际可达的主机地址，绝不把回环或通配地址发给别的电脑。"""

    candidates = sorted(dict.fromkeys(lan_candidates))
    allowed = set(candidates)
    if configured_host not in {"0.0.0.0", "::", "localhost"}:  # nosec B104 - 通配值不会加入可下发地址。
        try:
            configured_address = ipaddress.ip_address(configured_host)
        except ValueError:
            configured_address = None
        if configured_address is None or (
            configured_address.is_private
            and not configured_address.is_loopback
            and not configured_address.is_link_local
        ):
            allowed.add(configured_host)

    selected = (requested_host or "").strip()
    if selected:
        if selected not in allowed:
            raise ValueError("所选地址不是本机当前可用的可信局域网地址")
    elif configured_host in allowed:
        selected = configured_host
    else:
        request_host = urlsplit(request_base_url).hostname or ""
        if request_host in allowed:
            selected = request_host
        elif len(candidates) == 1:
            selected = candidates[0]
        else:
            raise LookupError("请明确选择协同电脑能够访问的主机局域网地址")

    return service_url(selected, configured_port, tls_enabled=tls_enabled)
