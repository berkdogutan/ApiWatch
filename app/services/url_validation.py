"""Target URL validation and SSRF protection.

Every URL is validated when a monitor is saved *and* again immediately before each check.
The check then connects to one of the exact addresses validated here (see
``app.services.checker``), so a DNS answer that changes between validation and connection
cannot redirect the request to an internal address.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.config import Settings

ALLOWED_SCHEMES = frozenset({"http", "https"})

# Hostnames that must never be monitored, whatever they resolve to.
BLOCKED_HOSTNAMES = frozenset(
    {"metadata", "metadata.google.internal", "metadata.goog", "instance-data"}
)
LOCALHOST_NAMES = frozenset({"localhost", "localhost.localdomain"})


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

    IPv4-mapped IPv6 addresses are judged by their IPv4 part. Python's ``ipaddress``
    already treats 6to4, Teredo, NAT64 and IPv4-compatible ranges as non-global or
    reserved, so they are blocked.
    """
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return "loopback"
    if ip.is_global and not ip.is_multicast and not ip.is_reserved:
        return "public"
    return "blocked"


def validate_url(url: str, settings: Settings) -> ValidatedURL:
    """Validate a monitor target URL and return its hostname and allowed addresses.

    Rules:
      * scheme must be ``http`` or ``https``; credentials in the URL are rejected;
      * if ``APIWATCH_ALLOWED_TARGET_HOSTS`` is set, the host must match it;
      * cloud metadata hostnames are always rejected;
      * every resolved address must be public. Loopback is accepted only when the
        development-only ``APIWATCH_ALLOW_LOCALHOST`` setting is enabled. Private
        (RFC 1918), link-local (incl. 169.254.169.254), CGNAT, multicast and reserved
        ranges are always rejected.
    """
    if len(url) > 2048:
        raise UnsafeURLError("URL is too long (max 2048 characters)")
    if any(ch.isspace() or ord(ch) < 32 for ch in url):
        raise UnsafeURLError("URL must not contain whitespace or control characters")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise UnsafeURLError(f"Malformed URL: {exc}") from None

    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeURLError("Only http:// and https:// URLs are allowed")
    if parts.username or parts.password:
        raise UnsafeURLError("Credentials in the URL are not allowed")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeURLError("URL must include a hostname")
    if port is None:
        port = 443 if scheme == "https" else 80

    allowlist = settings.allowed_target_hosts
    if allowlist and not _host_matches(host, allowlist):
        raise UnsafeURLError(f"Host '{host}' is not in APIWATCH_ALLOWED_TARGET_HOSTS")
    if host in BLOCKED_HOSTNAMES:
        raise UnsafeURLError(f"Host '{host}' is blocked")

    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        if host in LOCALHOST_NAMES:
            addresses = ["127.0.0.1"]
        else:
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
