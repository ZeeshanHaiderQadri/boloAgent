"""ICE policy and server configuration shared by every WebRTC signalling path.

Kept out of the route modules so the workflow signalling endpoint and the
copilot voice endpoint resolve candidates and TURN servers the same way.
"""

import ipaddress
import os
from enum import Enum
from typing import List, Optional

from aiortc import RTCIceServer
from loguru import logger

from api.constants import ENABLE_COTURN, ENVIRONMENT, FORCE_TURN_RELAY, SERVER_IP
from api.enums import Environment
from api.routes.turn_credentials import (
    TURN_HOST,
    TURN_PORT,
    TURN_SECRET,
    generate_turn_credentials,
)


class NonRelayFilterPolicy(Enum):
    """What to filter from non-relay ICE candidates. Relay candidates always pass."""

    NONE = "none"  # filter nothing — pass all candidates
    PRIVATE = "private"  # filter non-relay candidates with private/CGNAT IPs
    PUBLIC = "public"  # filter non-relay candidates that are NOT private/CGNAT
    ALL = "all"  # filter all non-relay candidates (relay-only mode)


def is_cgnat_ip(ip_str: str) -> bool:
    """Return True for CGNAT addresses (100.64.0.0/10) specifically — the
    range Tailscale and similar overlay networks use. Distinct from the
    broader is_local_or_cgnat_ip(): a CGNAT-addressed server (e.g. Tailscale-
    only, no public IP) commonly has no route to the public internet at all,
    whereas a plain RFC1918 LAN server usually still has normal NAT'd
    internet access — the two need different inbound-candidate handling.
    """

    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False

    return ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10")


def is_local_or_cgnat_ip(ip_str: str) -> bool:
    """Return True for RFC1918, loopback, link-local, and CGNAT addresses."""

    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False

    return ip.is_private or ip.is_loopback or ip.is_link_local or is_cgnat_ip(ip_str)


def resolve_ice_filter_policies(
    environment: str,
    force_turn_relay: bool,
    server_ip: str,
) -> tuple[NonRelayFilterPolicy, NonRelayFilterPolicy]:
    """Resolve outbound and inbound non-relay filtering for this deployment."""

    private_lan_deployment = (
        environment != Environment.LOCAL.value and is_local_or_cgnat_ip(server_ip)
    )

    if force_turn_relay:
        # Relay-only diagnostics stay explicit. On private LAN deployments we
        # must still accept inbound private candidates for relay<->host pairs.
        outbound_policy = NonRelayFilterPolicy.ALL
        if private_lan_deployment:
            # A CGNAT-addressed server (e.g. Tailscale-only, no public IP)
            # commonly has no route to the public internet at all, so a
            # genuinely public inbound candidate can never be reachable once
            # we've committed to relay-only — e.g. a client-side STUN entry
            # leaking a server-reflexive public-IP candidate despite
            # iceTransportPolicy: 'relay'. Drop those instead of wasting the
            # ICE timeout on a doomed direct-to-internet check.
            #
            # A plain RFC1918 LAN server usually still has normal NAT'd
            # internet access, so a public candidate there might genuinely
            # succeed — keep accepting everything inbound as before.
            inbound_policy = (
                NonRelayFilterPolicy.PUBLIC
                if is_cgnat_ip(server_ip)
                else NonRelayFilterPolicy.NONE
            )
        else:
            inbound_policy = NonRelayFilterPolicy.PRIVATE
        return outbound_policy, inbound_policy

    if environment == Environment.LOCAL.value or private_lan_deployment:
        return NonRelayFilterPolicy.NONE, NonRelayFilterPolicy.NONE

    # Public remote deployment: drop private-IP host candidates to avoid
    # coturn denied-peer-ip errors against Docker bridge and LAN interfaces.
    return NonRelayFilterPolicy.PRIVATE, NonRelayFilterPolicy.PRIVATE


ICE_OUTBOUND_POLICY, ICE_INBOUND_POLICY = resolve_ice_filter_policies(
    ENVIRONMENT,
    FORCE_TURN_RELAY,
    SERVER_IP,
)


def is_private_ip_candidate(candidate_str: str) -> bool:
    """Check if ICE candidate contains a private IP address or CGNAT IP Address.

    Parses the candidate string to extract the IP address and checks if it's private.
    This is used to filter out host candidates with private IPs in non-local environments,
    preventing TURN relay errors when coturn blocks private IP ranges or CGNAT IP Addresses.

    Args:
        candidate_str: ICE candidate string, e.g.,
            "candidate:123 1 udp 2122260223 192.168.50.24 63603 typ host ..."

    Returns:
        True if the candidate contains a private IP, False otherwise.
    """
    try:
        parts = candidate_str.split()
        # Find "typ" and get the IP which is 2 positions before it
        if "typ" in parts:
            typ_index = parts.index("typ")
            ip_str = parts[typ_index - 2]
            return is_local_or_cgnat_ip(ip_str)
    except (ValueError, IndexError):
        pass
    return False


def _keep_candidate(candidate_str: str, policy: NonRelayFilterPolicy) -> bool:
    """Return True if this ICE candidate should be kept under the given policy.

    Relay candidates always pass — a relay with a private IP (LAN TURN server)
    must never be dropped regardless of policy.
    """
    if " typ relay" in candidate_str:
        return True
    if policy == NonRelayFilterPolicy.NONE:
        return True
    if policy == NonRelayFilterPolicy.ALL:
        return False
    if policy == NonRelayFilterPolicy.PUBLIC:
        # PUBLIC: drop non-relay candidates that are NOT private/CGNAT
        return is_private_ip_candidate(candidate_str)
    # PRIVATE: drop non-relay candidates with private/CGNAT IPs
    return not is_private_ip_candidate(candidate_str)


def filter_outbound_sdp(sdp: str) -> str:
    """Strip ICE candidates from an outbound answer SDP based on ICE_OUTBOUND_POLICY."""
    if ICE_OUTBOUND_POLICY == NonRelayFilterPolicy.NONE:
        return sdp

    lines = sdp.split("\r\n")
    filtered: List[str] = []
    dropped = 0
    kept_relay = 0
    for line in lines:
        if line.startswith("a=candidate:"):
            candidate_str = line[2:]
            if not _keep_candidate(candidate_str, ICE_OUTBOUND_POLICY):
                dropped += 1
                continue
            if " typ relay" in candidate_str:
                kept_relay += 1
        filtered.append(line)

    if ICE_OUTBOUND_POLICY == NonRelayFilterPolicy.ALL:
        if kept_relay == 0:
            logger.warning(
                "FORCE_TURN_RELAY is on but the answer SDP has no relay candidates "
                f"(dropped {dropped} non-relay). TURN may be unreachable; "
                "the connection will fail."
            )
        else:
            logger.info(
                f"FORCE_TURN_RELAY: kept {kept_relay} relay candidates, "
                f"dropped {dropped} non-relay"
            )

    return "\r\n".join(filtered)


def get_ice_servers(user_id: Optional[str] = None) -> List[RTCIceServer]:
    """Build ICE servers configuration including TURN if configured.

    Args:
        user_id: Optional user ID for generating time-limited TURN credentials.
                 If provided and TURN_SECRET is configured, uses TURN REST API.

    Returns:
        List of RTCIceServer configurations for WebRTC peer connection.
    """
    # A `stun:` entry can only yield srflx, never relay, so it cannot help a
    # relay-only connection — it only gathers a public IP that
    # filter_outbound_sdp() strips back out. Matches the client-side skip.
    servers: List[RTCIceServer] = (
        [] if FORCE_TURN_RELAY else [RTCIceServer(urls="stun:stun.l.google.com:19302")]
    )

    # Check if TURN is configured. ENABLE_COTURN is the deployment's declared
    # answer to "is there a TURN server?" — the same flag /health advertises to
    # browsers — so the server side must respect it too, or it would try to
    # relay through a TURN server the deployment says it doesn't have.
    if not ENABLE_COTURN or not TURN_HOST:
        if FORCE_TURN_RELAY:
            # Fail loudly rather than silently degrading to STUN: relay-only was
            # requested precisely because direct connectivity is known not to
            # work, so an empty server list producing zero candidates is the
            # honest outcome — but it needs to be diagnosable.
            logger.error(
                "FORCE_TURN_RELAY is on but no TURN server is configured "
                f"(ENABLE_COTURN={ENABLE_COTURN}, TURN_HOST={TURN_HOST!r}). "
                "Relay-only connections cannot succeed until TURN is configured."
            )
        return servers

    # Use time-limited credentials if TURN_SECRET is configured (recommended)
    if TURN_SECRET and user_id:
        try:
            credentials = generate_turn_credentials(user_id)
            servers.append(
                RTCIceServer(
                    urls=credentials["uris"],
                    username=credentials["username"],
                    credential=credentials["password"],
                )
            )
            logger.info(
                f"TURN server configured with time-limited credentials, TTL: {credentials['ttl']}s"
            )
            return servers
        except Exception as e:
            logger.error(f"Failed to generate TURN credentials: {e}")

    # Fallback to static credentials (legacy mode - not recommended for production)
    turn_username = os.getenv("TURN_USERNAME")
    turn_password = os.getenv("TURN_PASSWORD")

    if turn_username and turn_password:
        servers.append(
            RTCIceServer(
                urls=[
                    f"turn:{TURN_HOST}:{TURN_PORT}",
                    f"turn:{TURN_HOST}:{TURN_PORT}?transport=tcp",
                ],
                username=turn_username,
                credential=turn_password,
            )
        )
        logger.warning(
            "TURN server configured with static credentials (consider using TURN_SECRET for time-limited auth)"
        )

    return servers
