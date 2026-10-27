"""Capture README screenshots from a *running* APIWatch instance.

Prerequisites: APIWatch on http://127.0.0.1:8000 with demo monitors (see README Quick start).

    uv run --with playwright python -m playwright install chromium
    uv run --with playwright python docs/capture_screenshots.py [--base-url URL]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

OUT = Path(__file__).parent / "screenshots"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    OUT.mkdir(parents=True, exist_ok=True)

    monitors = httpx.get(f"{base}/api/monitors").json()
    by_name = {m["name"]: m["id"] for m in monitors}
    json_id = by_name.get("Demo JSON API", monitors[0]["id"])

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=1,
            locale="en-US",
            color_scheme="dark",
        )

        page.goto(base + "/")
        page.wait_for_selector("#monitor-rows tr[data-id]")
        page.wait_for_timeout(800)
        page.screenshot(path=OUT / "dashboard.png", full_page=True)

        page.click("button[data-action=add-monitor] >> nth=0")
        page.fill("#f-name", "Payments API")
        page.fill("#f-url", "https://api.example.com/health")
        page.wait_for_timeout(300)
        page.screenshot(path=OUT / "add-monitor.png")
        page.keyboard.press("Escape")

        page.goto(f"{base}/monitors/{json_id}")
        page.wait_for_selector("#history-rows tr")
        page.wait_for_timeout(800)
        page.screenshot(path=OUT / "monitor-details.png", full_page=True)

        mobile = browser.new_page(
            viewport={"width": 390, "height": 844},
            device_scale_factor=2,
            locale="en-US",
            color_scheme="dark",
        )
        mobile.goto(base + "/")
        mobile.wait_for_selector("#monitor-rows tr[data-id]")
        mobile.wait_for_timeout(800)
        mobile.screenshot(path=OUT / "dashboard-mobile.png")
        browser.close()

    for file in sorted(OUT.glob("*.png")):
        print(f"saved {file.relative_to(OUT.parent.parent)}")


if __name__ == "__main__":
    main()
