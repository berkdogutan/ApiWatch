from __future__ import annotations

import asyncio
import gzip
import json
import time

import httpx
import pytest

from app.config import Settings
from app.models import HealthStatus, Monitor
from app.services import url_validation
from app.services.checker import build_http_client, run_check
from tests.conftest import ChunkedStream, MockAPI, monitor_payload


def make_monitor(**overrides) -> Monitor:
    values = monitor_payload(name="Example", timeout_seconds=2.0)
    values.update(overrides)
    return Monitor(id=1, **values)


async def check(mock_api: MockAPI, settings: Settings, **monitor_overrides):
    async with build_http_client(httpx.MockTransport(mock_api)) as client:
        return await run_check(make_monitor(**monitor_overrides), client, settings)


async def test_successful_check_is_up(mock_api: MockAPI, settings: Settings) -> None:
    outcome = await check(mock_api, settings)
    assert outcome.status == HealthStatus.UP
    assert outcome.success
    assert outcome.status_code == 200
    assert outcome.response_time_ms is not None and outcome.response_time_ms >= 0
    assert outcome.error_category is None
    assert mock_api.requests[0].method == "GET"


async def test_slow_response_is_degraded(mock_api: MockAPI, settings: Settings) -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.06)
        return httpx.Response(200)

    mock_api.handler = slow
    outcome = await check(mock_api, settings, slow_threshold_ms=20)
    assert outcome.status == HealthStatus.DEGRADED
    assert outcome.success  # degraded still counts as a successful response
    assert outcome.response_time_ms >= 20


async def test_unexpected_status_is_down(mock_api: MockAPI, settings: Settings) -> None:
    mock_api.respond(503)
    outcome = await check(mock_api, settings)
    assert outcome.status == HealthStatus.DOWN
    assert outcome.status_code == 503
    assert outcome.error_category == "unexpected_status"
    assert outcome.error_message == "Expected HTTP 200, got 503"


async def test_custom_expected_status(mock_api: MockAPI, settings: Settings) -> None:
    mock_api.respond(204)
    outcome = await check(mock_api, settings, expected_status=204)
    assert outcome.status == HealthStatus.UP


async def test_redirects_are_not_followed(mock_api: MockAPI, settings: Settings) -> None:
    mock_api.respond(302, headers={"Location": "http://169.254.169.254/"})
    outcome = await check(mock_api, settings)
    assert outcome.status == HealthStatus.DOWN
    assert outcome.status_code == 302
    assert len(mock_api.requests) == 1


async def test_timeout_is_down(mock_api: MockAPI, settings: Settings) -> None:
    mock_api.fail(httpx.ReadTimeout("timed out"))
    outcome = await check(mock_api, settings, timeout_seconds=1.5)
    assert outcome.status == HealthStatus.DOWN
    assert outcome.error_category == "timeout"
    assert outcome.error_message == "Request timed out after 1.5s"
    assert outcome.status_code is None


async def test_connection_failure_is_down(mock_api: MockAPI, settings: Settings) -> None:
    mock_api.fail(httpx.ConnectError("[Errno 111] Connection refused"))
    outcome = await check(mock_api, settings)
    assert outcome.status == HealthStatus.DOWN
    assert outcome.error_category == "connection_error"
    assert "Connection refused" in outcome.error_message


async def test_dns_failure_is_down(mock_api: MockAPI, settings: Settings) -> None:
    outcome = await check(mock_api, settings, url="https://no-such-host.example.net/")
    assert outcome.status == HealthStatus.DOWN
    assert outcome.error_category == "dns_error"
    assert mock_api.requests == []  # never attempted


async def test_unsafe_url_is_rejected_at_check_time(mock_api: MockAPI, settings: Settings) -> None:
    # e.g. the DNS record changed to a private address after the monitor was saved
    outcome = await check(mock_api, settings, url="https://internal.example.com/")
    assert outcome.status == HealthStatus.DOWN
    assert outcome.error_category == "invalid_url"
    assert mock_api.requests == []


async def test_json_inspection_extracts_top_level_types(
    mock_api: MockAPI, settings: Settings
) -> None:
    mock_api.respond(
        json={"id": 1, "name": "x", "ok": True, "n": 1.5, "items": [], "meta": {}, "missing": None}
    )
    outcome = await check(mock_api, settings, inspect_json=True)
    assert outcome.json_fields == {
        "id": "number",  # JSON has a single number type
        "name": "string",
        "ok": "boolean",
        "n": "number",
        "items": "array",
        "meta": "object",
        "missing": "null",
    }


async def test_invalid_json_is_reported(mock_api: MockAPI, settings: Settings) -> None:
    mock_api.respond(200, content=b"<html>not json</html>")
    outcome = await check(mock_api, settings, inspect_json=True)
    assert outcome.status == HealthStatus.UP  # health follows HTTP status + latency rules
    assert outcome.error_category == "invalid_json"
    assert outcome.json_fields is None


async def test_response_size_limit(mock_api: MockAPI, settings: Settings) -> None:
    mock_api.respond(200, content=b'{"a": "' + b"x" * 5000 + b'"}')
    small = settings.model_copy(update={"max_response_bytes": 1000})
    outcome = await check(mock_api, small, inspect_json=True)
    assert outcome.error_category == "response_too_large"
    assert outcome.json_fields is None


@pytest.mark.parametrize("method", ["GET", "HEAD"])
async def test_http_method_is_used(mock_api: MockAPI, settings: Settings, method: str) -> None:
    await check(mock_api, settings, method=method)
    assert mock_api.requests[0].method == method
    assert "authorization" not in {k.lower() for k in mock_api.requests[0].headers}


async def test_request_connects_to_the_validated_address(
    mock_api: MockAPI, settings: Settings
) -> None:
    await check(mock_api, settings, url="https://api.example.com/v1/health?verbose=1")
    request = mock_api.requests[0]
    # Connects to the IP that passed validation, never re-resolving the name...
    assert request.url.host == "93.184.216.34"
    assert request.url.raw_path == b"/v1/health?verbose=1"
    # ...while HTTP and TLS still see the original hostname.
    assert request.headers["host"] == "api.example.com"
    assert request.extensions["sni_hostname"] == "api.example.com"


async def test_plain_http_keeps_explicit_port(mock_api: MockAPI, settings: Settings) -> None:
    await check(mock_api, settings, url="http://api.example.com:8080/ping")
    request = mock_api.requests[0]
    assert (request.url.host, request.url.port) == ("93.184.216.34", 8080)
    assert request.headers["host"] == "api.example.com:8080"
    assert "sni_hostname" not in request.extensions


async def test_falls_back_to_next_validated_address(mock_api: MockAPI, settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "93.184.216.37":
            raise httpx.ConnectError("[Errno 113] No route to host")
        return httpx.Response(200)

    mock_api.handler = handler
    outcome = await check(mock_api, settings, url="https://dual.example.com/")
    assert outcome.status == HealthStatus.UP
    assert [r.url.host for r in mock_api.requests] == ["93.184.216.37", "2606:4700::1111"]


async def test_all_addresses_failing_is_connection_error(
    mock_api: MockAPI, settings: Settings
) -> None:
    mock_api.fail(httpx.ConnectError("[Errno 111] Connection refused"))
    outcome = await check(mock_api, settings, url="https://dual.example.com/")
    assert outcome.error_category == "connection_error"
    assert len(mock_api.requests) == 2


async def test_timeout_is_a_deadline_for_the_whole_check(
    mock_api: MockAPI, settings: Settings
) -> None:
    async def trickle(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(2)  # e.g. a server sending bytes slowly but steadily
        return httpx.Response(200)

    mock_api.handler = trickle
    outcome = await check(mock_api, settings, timeout_seconds=0.2)
    assert outcome.status == HealthStatus.DOWN
    assert outcome.error_category == "timeout"
    assert outcome.response_time_ms < 1500


async def test_slow_dns_counts_towards_the_timeout(
    mock_api: MockAPI, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def slow_resolve(host: str, port: int) -> list[str]:
        time.sleep(0.5)
        return ["93.184.216.34"]

    monkeypatch.setattr(url_validation, "resolve_host", slow_resolve)
    outcome = await check(mock_api, settings, timeout_seconds=0.1)
    assert outcome.error_category == "dns_error"
    assert "timed out" in outcome.error_message
    assert mock_api.requests == []


async def test_cookies_are_never_stored_or_sent(mock_api: MockAPI, settings: Settings) -> None:
    # Requests go to pinned IPs; a stored cookie could reach another hostname on that IP.
    mock_api.respond(200, headers={"Set-Cookie": "session=SECRET; Path=/"})
    async with build_http_client(httpx.MockTransport(mock_api)) as client:
        await run_check(make_monitor(), client, settings)
        await run_check(make_monitor(url="https://status.example.org/"), client, settings)
        assert len(client.cookies.jar) == 0
    assert "cookie" not in mock_api.requests[1].headers
    assert mock_api.requests[0].headers["accept-encoding"] == "identity"


async def test_falls_back_after_connect_timeout(mock_api: MockAPI, settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "93.184.216.37":
            raise httpx.ConnectTimeout("timed out")  # e.g. a firewall dropping SYNs
        return httpx.Response(200)

    mock_api.handler = handler
    outcome = await check(mock_api, settings, url="https://dual.example.com/", timeout_seconds=2)
    assert outcome.status == HealthStatus.UP
    assert [r.url.host for r in mock_api.requests] == ["93.184.216.37", "2606:4700::1111"]
    # The connect timeout is shared between the two addresses.
    assert mock_api.requests[0].extensions["timeout"]["connect"] == 1.0


async def test_deeply_nested_json_is_a_warning(mock_api: MockAPI, settings: Settings) -> None:
    mock_api.respond(200, content=b"[" * 100_000 + b"]" * 100_000)
    outcome = await check(mock_api, settings, inspect_json=True)
    assert outcome.status == HealthStatus.UP
    assert outcome.error_category == "invalid_json"
    assert "nested too deeply" in outcome.error_message


async def test_compressed_body_is_inflated_within_the_limit(
    mock_api: MockAPI, settings: Settings
) -> None:
    bomb = gzip.compress(b"0" * (5 * 1024 * 1024))  # ~5 KB on the wire, 5 MiB inflated
    mock_api.handler = lambda request: httpx.Response(
        200, headers={"Content-Encoding": "gzip"}, stream=ChunkedStream([bomb])
    )
    outcome = await check(mock_api, settings, inspect_json=True)
    assert outcome.status == HealthStatus.UP
    assert outcome.error_category == "response_too_large"

    small = gzip.compress(json.dumps({"id": 1}).encode())
    mock_api.handler = lambda request: httpx.Response(
        200, headers={"Content-Encoding": "gzip"}, stream=ChunkedStream([small])
    )
    outcome = await check(mock_api, settings, inspect_json=True)
    assert outcome.json_fields == {"id": "number"}


async def test_body_read_error_does_not_change_health(
    mock_api: MockAPI, settings: Settings
) -> None:
    mock_api.handler = lambda request: httpx.Response(
        200,
        stream=ChunkedStream(
            [b'{"id": 1, '], httpx.RemoteProtocolError("peer closed connection early")
        ),
    )
    outcome = await check(mock_api, settings, inspect_json=True)
    assert (outcome.status, outcome.status_code) == (HealthStatus.UP, 200)
    assert outcome.response_time_ms is not None
    assert outcome.error_category == "invalid_json"


async def test_protocol_errors_never_store_response_content(
    mock_api: MockAPI, settings: Settings
) -> None:
    leaked = 'illegal status line: bytearray(b\'{"token": "tok_live_9f8e"}\')'
    mock_api.fail(httpx.RemoteProtocolError(leaked))
    outcome = await check(mock_api, settings)
    assert outcome.error_category == "request_error"
    assert "tok_live" not in outcome.error_message


@pytest.mark.parametrize("url", ["https://api.example.com/x\x7f", "https://i❤.ws/"])
async def test_urls_the_client_cannot_request_are_reported(
    mock_api: MockAPI, settings: Settings, url: str
) -> None:
    outcome = await check(mock_api, settings, url=url)
    assert outcome.status == HealthStatus.DOWN
    assert outcome.error_category == "invalid_url"
    assert mock_api.requests == []


async def test_network_guard_blocks_real_connections(settings: Settings) -> None:
    # Without a mock transport the check would connect to 93.184.216.34 for real.
    async with build_http_client() as client:
        # anyio may wrap the guard's RuntimeError in an ExceptionGroup.
        with pytest.raises((RuntimeError, ExceptionGroup)) as raised:
            await run_check(make_monitor(), client, settings)
    assert "must not use the network" in repr(raised.value)
