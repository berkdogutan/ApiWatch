"""Deterministic mock API used for local demos and manual testing.

Run with:  uv run python -m demo.mock_api      (serves http://127.0.0.1:8001)
"""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI, Response

SLOW_DELAY_SECONDS = float(os.getenv("MOCK_SLOW_DELAY_SECONDS", "1.5"))

app = FastAPI(title="APIWatch demo mock API", version="1.0")


@app.get("/demo/healthy")
@app.head("/demo/healthy")
async def healthy() -> dict:
    return {"status": "ok"}


@app.get("/demo/slow")
@app.head("/demo/slow")
async def slow() -> dict:
    await asyncio.sleep(SLOW_DELAY_SECONDS)
    return {"status": "ok", "delay_seconds": SLOW_DELAY_SECONDS}


@app.get("/demo/error")
@app.head("/demo/error")
async def error() -> Response:
    return Response(
        content='{"error": "internal server error"}',
        status_code=500,
        media_type="application/json",
    )


@app.get("/demo/json")
async def json_v1() -> dict:
    return {
        "id": 42,
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "active": True,
        "balance": 120.5,
        "tags": ["admin", "beta"],
    }


@app.get("/demo/json-changed")
async def json_v2() -> dict:
    # vs /demo/json: "email" removed, "id" integer -> string, "created_at" added.
    return {
        "id": "42",
        "name": "Ada Lovelace",
        "active": True,
        "balance": 120.5,
        "tags": ["admin", "beta"],
        "created_at": "2026-01-01T00:00:00Z",
    }


def main() -> None:
    import uvicorn

    port = int(os.getenv("MOCK_API_PORT", "8001"))
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
