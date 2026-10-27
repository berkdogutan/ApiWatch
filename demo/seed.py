"""Create sample monitors pointing at the local mock API.

Uses the APIWatch REST API so the running scheduler picks the monitors up immediately.

    uv run python -m demo.seed [--base-url http://127.0.0.1:8000] [--mock-url http://127.0.0.1:8001]

Requires APIWatch to run with APIWATCH_ALLOW_LOCALHOST=true (development only).
"""

from __future__ import annotations

import argparse
import sys

import httpx


def sample_monitors(mock_url: str) -> list[dict]:
    return [
        {"name": "Demo Healthy API", "url": f"{mock_url}/demo/healthy", "slow_threshold_ms": 500},
        {"name": "Demo Slow API", "url": f"{mock_url}/demo/slow", "slow_threshold_ms": 800},
        {"name": "Demo Error API", "url": f"{mock_url}/demo/error"},
        {
            "name": "Demo JSON API",
            "url": f"{mock_url}/demo/json",
            "inspect_json": True,
            "slow_threshold_ms": 500,
        },
        {
            "name": "Demo Healthy (HEAD)",
            "url": f"{mock_url}/demo/healthy",
            "method": "HEAD",
            "interval_seconds": 120,
            "slow_threshold_ms": 500,
        },
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--mock-url", default="http://127.0.0.1:8001")
    parser.add_argument("--interval", type=int, default=30, help="check interval in seconds")
    args = parser.parse_args(argv)

    try:
        with httpx.Client(base_url=args.base_url, timeout=15) as client:
            existing = {m["name"] for m in client.get("/api/monitors").raise_for_status().json()}
            for monitor in sample_monitors(args.mock_url.rstrip("/")):
                monitor.setdefault("interval_seconds", args.interval)
                if monitor["name"] in existing:
                    print(f"= {monitor['name']} already exists")
                    continue
                response = client.post("/api/monitors", json=monitor)
                if response.status_code == 201:
                    print(f"+ created {monitor['name']} -> {monitor['url']}")
                else:
                    print(f"! {monitor['name']}: HTTP {response.status_code} {response.text}")
                    if "ALLOW_LOCALHOST" in response.text:
                        print("  Start APIWatch with APIWATCH_ALLOW_LOCALHOST=true for the demo.")
                    return 1
    except httpx.HTTPError as exc:
        print(f"Could not reach APIWatch at {args.base_url}: {exc}", file=sys.stderr)
        return 1
    print(f"Done. Open {args.base_url} to watch the dashboard.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
