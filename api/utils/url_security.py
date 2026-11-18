import ipaddress
import socket
from urllib.parse import urlparse

from api.constants import DEPLOYMENT_MODE

_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")

# Standard SMTP submission and relay ports. Anything else is a network scan.
SMTP_ALLOWED_PORTS = frozenset({25, 465, 587, 2525})


def validate_user_configured_service_url(
    url: str,
    *,
    field_name: str,
) -> None:
    """Restrict user-configured service URLs in hosted deployments.

    OSS deployments commonly point model services at localhost or private LAN
    hosts. SaaS deployments must not allow users to make Bolo infrastructure
    connect to private/internal network locations.
    """
    if DEPLOYMENT_MODE == "oss":
        return

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.hostname:
        raise ValueError(f"{field_name} must be an http, https, ws, or wss URL")

    hostname = parsed.hostname
    if hostname.lower() == "localhost":
        raise ValueError(f"{field_name} cannot point to localhost in SaaS mode")

    for ip in _resolve_hostname_ips(hostname, parsed.port):
        if _is_blocked_saas_service_ip(ip):
            raise ValueError(
                f"{field_name} must resolve to a public IP address in SaaS mode"
            )


def validate_user_configured_service_host(
    host: str,
    *,
    field_name: str,
    port: int | None = None,
) -> None:
    """Apply the same egress policy to a bare host, such as an SMTP server.

    SMTP settings carry no scheme, so the URL variant cannot be reused. The
    policy is identical: in SaaS mode the host must not be localhost and must
    resolve to public addresses only. DNS can change between a settings check
    and a connection, so callers should re-run this immediately before
    connecting.
    """
    if DEPLOYMENT_MODE == "oss":
        return

    hostname = host.strip()
    if hostname.startswith("[") and hostname.endswith("]"):
        hostname = hostname[1:-1]
    if not hostname:
        raise ValueError(f"{field_name} must include a host")

    if hostname.lower() == "localhost":
        raise ValueError(f"{field_name} cannot point to localhost in SaaS mode")

    for ip in _resolve_hostname_ips(hostname, port):
        if _is_blocked_saas_service_ip(ip):
            raise ValueError(
                f"{field_name} must resolve to a public IP address in SaaS mode"
            )


def validate_user_configured_smtp_port(port: int, *, field_name: str) -> None:
    """Keep user-configured SMTP on standard ports in SaaS deployments.

    Self-hosted relays legitimately listen on other ports, so OSS deployments
    keep the unrestricted behaviour.
    """
    if DEPLOYMENT_MODE == "oss":
        return

    if port not in SMTP_ALLOWED_PORTS:
        allowed = ", ".join(str(value) for value in sorted(SMTP_ALLOWED_PORTS))
        raise ValueError(f"{field_name} must be one of {allowed} in SaaS mode")


def _resolve_hostname_ips(
    hostname: str, port: int | None
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        return [ipaddress.ip_address(hostname)]
    except ValueError:
        pass

    try:
        addr_infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ValueError("Could not resolve service URL hostname") from e

    return [ipaddress.ip_address(addr_info[4][0]) for addr_info in addr_infos]


def _is_blocked_saas_service_ip(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or (ip.version == 4 and ip in _CGNAT_NETWORK)
    )
