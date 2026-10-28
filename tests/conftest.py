from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.database import init_db, make_engine, make_session_factory
from app.main import create_app
from app.services import url_validation

# The real resolver, kept so its own behaviour can be tested with a fake getaddrinfo.
REAL_RESOLVE_HOST = url_validation.resolve_host

# Fake DNS so tests never touch the network. Keys are the ASCII (IDNA 2008) host names
# that validate_url resolves.
FAKE_DNS = {
    "api.example.com": ["93.184.216.34"],
    "status.example.org": ["93.184.216.35"],
    "internal.example.com": ["10.0.0.5"],
    "rebind.example.com": ["93.184.216.36", "127.0.0.1"],
    "dual.example.com": ["2606:4700::1111", "93.184.216.37"],
    "localhost": ["::1", "127.0.0.1"],
    "metadata.google.internal": ["93.184.216.99"],  # public, so only the name rule blocks it
    "xn--strae-oqa.de": ["81.169.145.78"],  # straße.de; IDNA 2003 would look up strasse.de
}


def fake_resolve(host: str, port: int) -> list[str]:
    if host in FAKE_DNS:
        return FAKE_DNS[host]
    raise OSError(f"[Errno 11001] getaddrinfo failed for {host}")


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """APIWATCH_* variables from the developer's shell must not change test results."""
    for name in list(os.environ):
        if name.upper().startswith("APIWATCH_"):
            monkeypatch.delenv(name)


@pytest.fixture(autouse=True)
def _fake_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(url_validation, "resolve_host", fake_resolve)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if anything tries real DNS or an outbound connection.

    asyncio's create_connection is patched as well: checks connect to IP literals, which
    never go through getaddrinfo.
    """

    def blocked(*args, **kwargs):
        raise RuntimeError("tests must not use the network")

    async def blocked_async(*args, **kwargs):
        raise RuntimeError("tests must not use the network")

    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(asyncio.base_events.BaseEventLoop, "create_connection", blocked_async)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        scheduler_enabled=False,
        allow_localhost=False,
        trusted_hosts=["testserver"],  # the Host header TestClient sends
    )


class MockAPI:
    """Programmable stand-in for remote APIs, used as an httpx transport."""

    def __init__(self) -> None:
        self.handler: Callable[[httpx.Request], httpx.Response] = lambda request: httpx.Response(
            200, json={"status": "ok"}
        )
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        result = self.handler(request)
        if hasattr(result, "__await__"):
            result = await result
        return result

    def respond(self, status_code: int = 200, **kwargs) -> None:
        self.handler = lambda request: httpx.Response(status_code, **kwargs)

    def fail(self, exc: Exception) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise exc

        self.handler = handler


class ChunkedStream(httpx.AsyncByteStream):
    """A response body delivered in chunks like a real network stream, optionally failing
    part-way through."""

    def __init__(self, chunks: list[bytes], error: Exception | None = None) -> None:
        self.chunks = chunks
        self.error = error

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.error is not None:
            raise self.error


@pytest.fixture
def mock_api() -> MockAPI:
    return MockAPI()


@pytest.fixture
def transport(mock_api: MockAPI) -> httpx.MockTransport:
    return httpx.MockTransport(mock_api)


@pytest.fixture
def client(settings: Settings, transport: httpx.MockTransport) -> Iterator[TestClient]:
    app = create_app(settings, transport=transport)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def session_factory(settings: Settings):
    engine = make_engine(settings.database_url)
    init_db(engine)
    yield make_session_factory(engine)
    engine.dispose()


def monitor_payload(**overrides) -> dict:
    payload = {
        "name": "Example API",
        "url": "https://api.example.com/health",
        "method": "GET",
        "expected_status": 200,
        "interval_seconds": 60,
        "timeout_seconds": 5,
        "slow_threshold_ms": 1000,
        "enabled": True,
        "inspect_json": False,
    }
    payload.update(overrides)
    return payload
