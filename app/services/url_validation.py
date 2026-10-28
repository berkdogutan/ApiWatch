"""Target URL validation and SSRF protection.

Every URL is validated when a monitor is saved *and* again immediately before each check.
The check then connects to one of the exact addresses validated here (see
``app.services.checker``), so a DNS answer that changes between validation and connection
cannot redirect the request to an internal address.

URLs are parsed with httpx's parser, the same one that sends the request, so validation and
request always agree on the host (including its IDNA 2008 ASCII form) and anything httpx
cannot request is rejected when the monitor is saved.
"""

from __future__ import annotations

import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import httpx

from app.config import Settings

ALLOWED_SCHEMES = frozenset({"http", "https"})

# Threads for validate_url's DNS lookups. A lookup that outlives its caller's deadline keeps
# its thread busy, so lookups get a generous pool of their own instead of competing with
# other work in the event loop's default executor.
RESOLVER_POOL = ThreadPoolExecutor(max_workers=80, thread_name_prefix="apiwatch-dns")

# Hostnames that must never be monitored, whatever they resolve to.
BLOCKED_HOSTNAMES = frozenset(
    {"metadata", "metadata.google.internal", "metadata.goog", "instance-data"}
)

# IPv6 ranges that embed or translate IPv4 addresses. Blocked explicitly because whether
# ``ipaddress`` treats them as global depends on the Python patch release
# (6to4 only became non-global in 3.12.4).
BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "2002::/16",  # 6to4
        "2001::/32",  # Teredo
        "64:ff9b::/96",  # NAT64, well-known prefix
        "64:ff9b:1::/48",  # NAT64, local-use prefix
        "::/96",  # IPv4-compatible (deprecated)
    )
)


class UnsafeURLError(ValueError):
    """The URL is malformed or points at a disallowed destination."""


class DNSResolutionError(UnsafeURLError):
    """The hostname could not be resolved."""


@dataclass(frozen=True)
class ValidatedURL:
    host: str
    # Every address the host resolved to, all of them allowed, IPv4 first.
    addresses: tuple[str, ...]


def resolve_host(host: str, port: int) -> list[str]:
    """Resolve ``host`` to IP addresses, keeping the resolver's preference order."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def _host_matches(host: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern.startswith("*."):
            if host.endswith(pattern[1:]) and host != pattern[2:]:
                return True
        elif host == pattern:
            return True
    return False


def _classify(address: str) -> str:
    """Return 'public', 'loopback' or 'blocked' for an IP address string.

    IPv4-mapped IPv6 addresses are judged by their IPv4 part; other IPv6 ranges that
    embed IPv4 addresses (6to4, Teredo, NAT64, IPv4-compatible) are always blocked.
    """
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return "loopback"
    if any(ip in network for network in BLOCKED_NETWORKS):
        return "blocked"
    if ip.is_global and not ip.is_multicast and not ip.is_reserved:
        return "public"
    return "blocked"


def validate_url(url: str, settings: Settings) -> ValidatedURL:
    """Validate a monitor target URL and return its hostname and allowed addresses.

    Rules:
      * the URL must be one httpx can request; the scheme must be ``http`` or ``https``;
        credentials in the URL are rejected;
      * if ``APIWATCH_ALLOWED_TARGET_HOSTS`` is set, the host must match it;
      * cloud metadata hostnames are always rejected;
      * every resolved address must be public. Loopback is accepted only when the
        development-only ``APIWATCH_ALLOW_LOCALHOST`` setting is enabled. Private
        (RFC 1918), link-local (incl. 169.254.169.254), CGNAT, multicast, reserved and
        IPv4-embedding IPv6 ranges are always rejected.
    """
    if len(url) > 2048:
        raise UnsafeURLError("URL is too long (max 2048 characters)")
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        raise UnsafeURLError("URL must not contain whitespace or control characters")
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL as exc:
        raise UnsafeURLError(f"Malformed URL: {exc}") from None

    scheme = parsed.scheme
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeURLError("Only http:// and https:// URLs are allowed")
    if parsed.userinfo:
        raise UnsafeURLError("Credentials in the URL are not allowed")
    # The ASCII (IDNA 2008) form: exactly what the request sends as Host and TLS SNI.
    host = parsed.raw_host.decode("ascii").rstrip(".").lower()
    if not host:
        raise UnsafeURLError("URL must include a hostname")
    port = parsed.port
    if port is None:
        port = 443 if scheme == "https" else 80
    elif not 1 <= port <= 65535:
        raise UnsafeURLError(f"Invalid port {port}")

    allowlist = settings.allowed_target_hosts
    if allowlist and not _host_matches(host, allowlist):
        raise UnsafeURLError(f"Host '{host}' is not in APIWATCH_ALLOWED_TARGET_HOSTS")
    if host in BLOCKED_HOSTNAMES:
        raise UnsafeURLError(f"Host '{host}' is blocked")

    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            addresses = resolve_host(host, port)
        except (OSError, UnicodeError):
            raise DNSResolutionError(f"Could not resolve host '{host}'") from None
    if not addresses:
        raise DNSResolutionError(f"Could not resolve host '{host}'")

    for address in addresses:
        kind = _classify(address)
        if kind == "loopback":
            if not settings.allow_localhost:
                raise UnsafeURLError(
                    "Localhost targets are disabled. For local development only, set "
                    "APIWATCH_ALLOW_LOCALHOST=true"
                )
        elif kind == "blocked":
            raise UnsafeURLError(
                f"Host '{host}' resolves to a private or reserved address ({address})"
            )
    # Prefer IPv4 (most widely reachable); otherwise keep the resolver's order.
    ordered = sorted(addresses, key=lambda a: ":" in a)
    return ValidatedURL(host=host, addresses=tuple(ordered))
