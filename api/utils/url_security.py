import ipaddress
import socket
from urllib.parse import urlparse

from api.constants import OUTBOUND_NETWORK_POLICY

_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")

# Standard SMTP submission and relay ports. Anything else is a network scan.
SMTP_ALLOWED_PORTS = frozenset({25, 465, 587, 2525})


class ServiceHostResolutionError(ValueError):
    """The host could not be resolved; the caller may retry later.

    A ``ValueError`` subclass so existing callers that treat every rejection as
    a bad value keep working, while adapters that distinguish a policy
    rejection from a transient DNS failure can catch this one first.
    """


class UnsafeOutboundURLError(ValueError):
    """A URL may not be fetched because it could reach a private network."""


def validate_public_http_url(url: str) -> str:
    """Reject URLs that could make Bolo reach a private or local service.

    Unlike :func:`validate_user_configured_service_url` this check is always
    on. Scraper-derived website URLs are third-party input, so they must never
    be trusted to point at loopback, LAN or metadata addresses regardless of
    the ``OUTBOUND_NETWORK_POLICY`` the operator configured.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeOutboundURLError("Only http and https URLs can be fetched")
    if parsed.username or parsed.password:
        raise UnsafeOutboundURLError("URLs with credentials cannot be fetched")

    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeOutboundURLError("URL has an invalid port") from exc
    if port is not None and port not in {80, 443}:
        raise UnsafeOutboundURLError("Only standard web ports can be fetched")

    hostname = parsed.hostname.lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise UnsafeOutboundURLError("localhost cannot be fetched")

    for ip in _resolve_hostname_ips(hostname, port):
        if _is_blocked_outbound_ip(ip):
            raise UnsafeOutboundURLError(
                "The URL resolves to a private network address"
            )
    return url.strip()


def validate_user_configured_service_url(
    url: str,
    *,
    field_name: str,
) -> None:
    """Restrict user-configured service URLs when the egress policy is strict.

    Self-hosted deployments commonly point model services at localhost or
    private LAN hosts, so the default policy for ``DEPLOYMENT_MODE=oss`` is
    ``off``. A customer-facing install sets ``OUTBOUND_NETWORK_POLICY=strict``
    so users cannot make Bolo infrastructure connect to private/internal
    network locations.
    """
    if OUTBOUND_NETWORK_POLICY != "strict":
        return

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.hostname:
        raise ValueError(f"{field_name} must be an http, https, ws, or wss URL")

    hostname = parsed.hostname
    if hostname.lower() == "localhost":
        raise ValueError(
            f"{field_name} cannot point to localhost when OUTBOUND_NETWORK_POLICY=strict"
        )

    for ip in _resolve_hostname_ips(hostname, parsed.port):
        if _is_blocked_outbound_ip(ip):
            raise ValueError(
                f"{field_name} must resolve to a public IP address when OUTBOUND_NETWORK_POLICY=strict"
            )


def validate_user_configured_service_host(
    host: str,
    *,
    field_name: str,
    port: int | None = None,
) -> None:
    """Apply the same egress policy to a bare host, such as an SMTP server.

    SMTP settings carry no scheme, so the URL variant cannot be reused. The
    policy is identical: under the strict policy the host must not be localhost
    and must resolve to public addresses only.
    """
    resolve_user_configured_service_host(host, field_name=field_name, port=port)


def resolve_user_configured_service_host(
    host: str,
    *,
    field_name: str,
    port: int | None = None,
) -> str | None:
    """Validate a bare host and return one checked address for it.

    Returns ``None`` when the egress policy is off, where no restriction
    applies and the caller should let the client library resolve the name
    itself. Under the strict policy the returned address is safe to dial
    directly, which lets a caller close the DNS rebinding window between this
    check and the connection.
    """
    if OUTBOUND_NETWORK_POLICY != "strict":
        return None

    hostname = host.strip()
    if hostname.startswith("[") and hostname.endswith("]"):
        hostname = hostname[1:-1]
    if not hostname:
        raise ValueError(f"{field_name} must include a host")

    if hostname.lower() == "localhost":
        raise ValueError(
            f"{field_name} cannot point to localhost when OUTBOUND_NETWORK_POLICY=strict"
        )

    resolved = _resolve_hostname_ips(hostname, port)
    for ip in resolved:
        if _is_blocked_outbound_ip(ip):
            raise ValueError(
                f"{field_name} must resolve to a public IP address when OUTBOUND_NETWORK_POLICY=strict"
            )
    return str(resolved[0])


def validate_user_configured_smtp_port(port: int, *, field_name: str) -> None:
    """Keep user-configured SMTP on standard ports under the strict policy.

    Self-hosted relays legitimately listen on other ports, so installs with the
    policy off keep the unrestricted behaviour.
    """
    if OUTBOUND_NETWORK_POLICY != "strict":
        return

    if port not in SMTP_ALLOWED_PORTS:
        allowed = ", ".join(str(value) for value in sorted(SMTP_ALLOWED_PORTS))
        raise ValueError(
            f"{field_name} must be one of {allowed} when OUTBOUND_NETWORK_POLICY=strict"
        )


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
        raise ServiceHostResolutionError(
            "Could not resolve service URL hostname"
        ) from e

    return [ipaddress.ip_address(addr_info[4][0]) for addr_info in addr_infos]


def _is_blocked_outbound_ip(
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
