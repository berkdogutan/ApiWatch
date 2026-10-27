from __future__ import annotations

import socket

import pytest

from app.config import Settings
from app.services.url_validation import DNSResolutionError, UnsafeURLError, validate_url
from tests.conftest import REAL_RESOLVE_HOST


def make_settings(**kwargs) -> Settings:
    return Settings(_env_file=None, **kwargs)


def test_accepts_public_https_url() -> None:
    target = validate_url("https://api.example.com/v1/health", make_settings())
    assert target.host == "api.example.com"
    assert target.addresses == ("93.184.216.34",)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://api.example.com/file",
        "file:///etc/passwd",
        "gopher://api.example.com",
        "javascript:alert(1)",
        "api.example.com/health",
    ],
)
def test_rejects_non_http_schemes(url: str) -> None:
    with pytest.raises(UnsafeURLError, match="http"):
        validate_url(url, make_settings())


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.1/",
        "http://192.168.1.10/",
        "http://172.16.5.4/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata (link-local)
        "http://100.64.0.1/",  # carrier-grade NAT
        "http://0.0.0.0/",
        "http://[fe80::1]/",
        "http://[fd00::1]/",  # unique local IPv6
        "http://[::ffff:10.0.0.1]/",  # IPv4-mapped IPv6
        "http://[2002:a00:1::1]/",  # 6to4 wrapping 10.0.0.1
        "http://[64:ff9b::a00:1]/",  # NAT64 wrapping 10.0.0.1
        "http://[2001::1]/",  # Teredo
        "http://[::a00:1]/",  # IPv4-compatible IPv6
        "http://metadata.google.internal/",
        "http://internal.example.com/",  # public name resolving to a private IP
    ],
)
def test_blocks_private_and_internal_targets(url: str) -> None:
    with pytest.raises(UnsafeURLError):
        validate_url(url, make_settings(allow_localhost=True))


def test_blocks_name_with_any_private_address() -> None:
    # One of its addresses is loopback, so the whole host is rejected.
    with pytest.raises(UnsafeURLError, match="Localhost targets are disabled"):
        validate_url("https://rebind.example.com/", make_settings())


@pytest.mark.parametrize(
    "url", ["http://localhost:8001/demo", "http://127.0.0.1/", "http://[::1]/"]
)
def test_localhost_requires_development_flag(url: str) -> None:
    with pytest.raises(UnsafeURLError, match="APIWATCH_ALLOW_LOCALHOST"):
        validate_url(url, make_settings())
    assert validate_url(url, make_settings(allow_localhost=True)).addresses


def test_localhost_name_is_not_resolved() -> None:
    # "localhost" maps straight to 127.0.0.1 instead of trusting the hosts file / DNS.
    target = validate_url("http://localhost:8001/", make_settings(allow_localhost=True))
    assert target.addresses == ("127.0.0.1",)


def test_rejects_credentials_in_url() -> None:
    with pytest.raises(UnsafeURLError, match="Credentials"):
        validate_url("https://user:secret@api.example.com/", make_settings())


def test_target_host_allowlist() -> None:
    settings = make_settings(allowed_target_hosts="api.example.com,*.example.org")
    assert validate_url("https://api.example.com/", settings)
    assert validate_url("https://status.example.org/", settings)
    with pytest.raises(UnsafeURLError, match="ALLOWED_TARGET_HOSTS"):
        validate_url("https://other.example.net/", settings)
    with pytest.raises(UnsafeURLError, match="ALLOWED_TARGET_HOSTS"):
        validate_url("https://example.org/", settings)  # "*.example.org" excludes the apex


def test_allowlist_does_not_bypass_address_checks() -> None:
    settings = make_settings(allowed_target_hosts="internal.example.com")
    with pytest.raises(UnsafeURLError, match="private or reserved"):
        validate_url("https://internal.example.com/", settings)


def test_ipv4_addresses_are_tried_first() -> None:
    target = validate_url("https://dual.example.com/", make_settings())
    assert target.addresses == ("93.184.216.37", "2606:4700::1111")


def test_unresolvable_host_is_dns_error() -> None:
    with pytest.raises(DNSResolutionError):
        validate_url("https://does-not-exist.invalid/", make_settings())


def test_resolver_keeps_order_and_removes_duplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(host, port, type=0):
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700::1111", port, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700::1111", port, 0, 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert REAL_RESOLVE_HOST("api.example.com", 443) == ["2606:4700::1111", "93.184.216.34"]
