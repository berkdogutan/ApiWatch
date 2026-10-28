"""Asynchronous HTTP health check engine."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import time
import zlib
from dataclasses import dataclass
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Any

import httpx

from app import __version__
from app.config import Settings
from app.models import HealthStatus, Monitor
from app.services.url_validation import (
    RESOLVER_POOL,
    DNSResolutionError,
    UnsafeURLError,
    ValidatedURL,
    validate_url,
)

USER_AGENT = f"APIWatch/{__version__}"
MAX_ERROR_LENGTH = 300
MAX_CONCURRENT_CHECKS = 20


@dataclass
class CheckOutcome:
    status: HealthStatus
    response_time_ms: float | None = None
    status_code: int | None = None
    error_category: str | None = None
    error_message: str | None = None
    # Top-level JSON key -> type name. Values are never kept.
    json_fields: dict[str, str] | None = None

    @property
    def success(self) -> bool:
        return self.status != HealthStatus.DOWN


def build_http_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """Create the shared outbound client.

    * ``follow_redirects=False`` - a redirect to an internal address would bypass URL checks.
    * ``trust_env=False`` - ignore proxy variables and ``.netrc`` credentials.
    * no cookies - responses never set cookies for later checks (requests go to pinned IPs,
      so a stored cookie could even reach another hostname on the same address).
    * ``Accept-Encoding: identity`` - bodies are read raw, so the size limit applies to
      what is actually received.
    * no keep-alive - connections are pooled per IP address, so a reused connection could
      carry a request for a different hostname; it also makes every response time include
      connection setup consistently.
    """
    return httpx.AsyncClient(
        transport=transport,
        follow_redirects=False,
        trust_env=False,
        cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
        limits=httpx.Limits(max_connections=MAX_CONCURRENT_CHECKS, max_keepalive_connections=0),
    )


def pinned_request(url: str, address: str) -> tuple[httpx.URL, dict[str, str], dict[str, str]]:
    """Return (url, headers, extensions) that connect to ``address`` directly.

    The original hostname is kept for the ``Host`` header and, for HTTPS, for TLS SNI and
    certificate verification, so the request behaves as if DNS had returned ``address``.
    """
    original = httpx.URL(url)
    try:
        ipaddress.ip_address(original.host)
        return original, {}, {}  # already an IP literal; nothing to pin
    except ValueError:
        pass
    headers = {"Host": original.netloc.decode("ascii")}
    extensions = {}
    if original.scheme == "https":
        extensions["sni_hostname"] = original.raw_host.decode("ascii")
    return original.copy_with(host=address), headers, extensions


def sanitize_error(message: str) -> str:
    """Collapse whitespace and truncate so error text is safe to store and display."""
    cleaned = " ".join(message.split())
    if len(cleaned) > MAX_ERROR_LENGTH:
        cleaned = cleaned[: MAX_ERROR_LENGTH - 3] + "..."
    return cleaned


def describe_http_error(exc: httpx.HTTPError) -> str:
    """A short description of a failed request.

    Protocol and decoding errors can quote raw response bytes (h11 includes them in its
    messages), so they get a fixed text: response content is never stored.
    """
    if isinstance(exc, httpx.RemoteProtocolError):
        return "The server sent an invalid or incomplete HTTP response"
    if isinstance(exc, httpx.DecodingError):
        return "The response could not be decoded"
    return sanitize_error(f"{type(exc).__name__}: {exc}")


def json_type_name(value: Any) -> str:
    """JSON type of ``value``. JSON has one number type, so 20 and 19.99 are both
    'number' and a value change never shows up as a type change."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def classify_status(status_code: int, elapsed_ms: float, monitor: Monitor) -> HealthStatus:
    if status_code != monitor.expected_status:
        return HealthStatus.DOWN
    if elapsed_ms > monitor.slow_threshold_ms:
        return HealthStatus.DEGRADED
    return HealthStatus.UP


async def run_check(
    monitor: Monitor, client: httpx.AsyncClient, settings: Settings
) -> CheckOutcome:
    """Perform one check of ``monitor``. Never raises for network/target problems.

    ``monitor.timeout_seconds`` is a deadline for the whole check: DNS lookup, connecting,
    and reading the response.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + monitor.timeout_seconds
    try:
        async with asyncio.timeout_at(deadline):
            target = await loop.run_in_executor(RESOLVER_POOL, validate_url, monitor.url, settings)
    except TimeoutError:
        return CheckOutcome(
            HealthStatus.DOWN,
            error_category="dns_error",
            error_message=f"DNS lookup timed out after {monitor.timeout_seconds:g}s",
        )
    except DNSResolutionError as exc:
        return CheckOutcome(HealthStatus.DOWN, error_category="dns_error", error_message=str(exc))
    except UnsafeURLError as exc:
        return CheckOutcome(
            HealthStatus.DOWN, error_category="invalid_url", error_message=sanitize_error(str(exc))
        )

    read_body = monitor.inspect_json and monitor.method == "GET"
    start = time.perf_counter()
    try:
        async with asyncio.timeout_at(deadline):
            status_code, body, body_problem = await _fetch(
                client, monitor, target, read_body, settings.max_response_bytes
            )
    except (TimeoutError, httpx.TimeoutException):
        return CheckOutcome(
            HealthStatus.DOWN,
            response_time_ms=round((time.perf_counter() - start) * 1000, 2),
            error_category="timeout",
            error_message=f"Request timed out after {monitor.timeout_seconds:g}s",
        )
    except httpx.ConnectError as exc:
        return CheckOutcome(
            HealthStatus.DOWN,
            error_category="connection_error",
            error_message=sanitize_error(f"Connection failed: {exc}".rstrip(": ")),
        )
    except httpx.HTTPError as exc:
        return CheckOutcome(
            HealthStatus.DOWN,
            error_category="request_error",
            error_message=describe_http_error(exc),
        )
    except httpx.InvalidURL as exc:  # validate_url uses the same parser; defence in depth
        return CheckOutcome(
            HealthStatus.DOWN, error_category="invalid_url", error_message=sanitize_error(str(exc))
        )
    elapsed_ms = (time.perf_counter() - start) * 1000

    outcome = CheckOutcome(
        status=classify_status(status_code, elapsed_ms, monitor),
        response_time_ms=round(elapsed_ms, 2),
        status_code=status_code,
    )
    if outcome.status == HealthStatus.DOWN:
        outcome.error_category = "unexpected_status"
        outcome.error_message = f"Expected HTTP {monitor.expected_status}, got {status_code}"
        return outcome

    if read_body:
        _inspect_json(outcome, body, body_problem, settings.max_response_bytes)
    return outcome


async def _fetch(
    client: httpx.AsyncClient,
    monitor: Monitor,
    target: ValidatedURL,
    read_body: bool,
    limit: int,
) -> tuple[int, bytes | None, str | None]:
    """Send the request to the validated addresses in order until one accepts the
    connection. Returns (status code, body or None, body problem or None).

    The connect timeout is shared between the addresses, so an address that silently
    drops connections still leaves time to try the next one.
    """
    timeout = httpx.Timeout(
        monitor.timeout_seconds, connect=monitor.timeout_seconds / len(target.addresses)
    )
    last_error: httpx.ConnectError | httpx.ConnectTimeout | None = None
    for address in target.addresses:
        url, headers, extensions = pinned_request(monitor.url, address)
        try:
            async with client.stream(
                monitor.method, url, headers=headers, extensions=extensions, timeout=timeout
            ) as response:
                if not read_body:
                    return response.status_code, None, None
                body, problem = await _read_body(response, limit)
                return response.status_code, body, problem
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            last_error = exc
    raise last_error or httpx.ConnectError("No address to connect to")


async def _read_body(response: httpx.Response, limit: int) -> tuple[bytes | None, str | None]:
    """Read at most ``limit`` bytes of the body for JSON inspection.

    Errors while reading the body only affect the inspection (the status code already
    arrived); timeouts still propagate because the deadline covers the whole check.
    """
    if response.is_stream_consumed:  # built in memory (e.g. by a mock transport), not streamed
        body = response.content
        return (body, None) if len(body) <= limit else (None, "response_too_large")
    raw = bytearray()
    try:
        async for chunk in response.aiter_raw():
            raw.extend(chunk)
            if len(raw) > limit:
                return None, "response_too_large"
    except httpx.TimeoutException:
        raise
    except httpx.HTTPError:
        return None, "body_unreadable"
    return _decode(bytes(raw), response.headers.get("content-encoding", ""), limit)


def _decode(raw: bytes, content_encoding: str, limit: int) -> tuple[bytes | None, str | None]:
    """Undo gzip/deflate that a server applied despite ``Accept-Encoding: identity``,
    never producing more than ``limit`` bytes."""
    encoding = content_encoding.strip().lower()
    if encoding in ("", "identity"):
        return raw, None
    if encoding not in ("gzip", "x-gzip", "deflate"):
        return None, "body_unreadable"
    inflater = zlib.decompressobj(zlib.MAX_WBITS | 32)  # accepts gzip and zlib headers
    try:
        data = inflater.decompress(raw, limit + 1)
    except zlib.error:
        return None, "body_unreadable"
    if len(data) > limit or inflater.unconsumed_tail:
        return None, "response_too_large"
    return data, None


def _inspect_json(
    outcome: CheckOutcome, body: bytes | None, problem: str | None, limit: int
) -> None:
    """Extract the top-level key/type map. Problems are recorded as warnings only:
    the health status still follows the HTTP status and latency rules."""
    if problem == "response_too_large":
        outcome.error_category = "response_too_large"
        outcome.error_message = f"Response exceeded {limit} bytes; JSON inspection skipped"
        return
    if problem is not None or body is None:
        outcome.error_category = "invalid_json"
        outcome.error_message = "Response body could not be read; JSON inspection skipped"
        return
    try:
        data = json.loads(body)
    except RecursionError:
        outcome.error_category = "invalid_json"
        outcome.error_message = "JSON is nested too deeply to inspect"
        return
    except (ValueError, UnicodeDecodeError):
        outcome.error_category = "invalid_json"
        outcome.error_message = "Response body is not valid JSON"
        return
    if not isinstance(data, dict):
        outcome.error_category = "json_not_object"
        outcome.error_message = (
            f"JSON response is a {json_type_name(data)}, not an object; inspection skipped"
        )
        return
    outcome.json_fields = {str(key): json_type_name(value) for key, value in data.items()}
