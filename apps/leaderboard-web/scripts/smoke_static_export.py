"""Local static-export smoke test for the rankings-only public site.

Starts the preview server and verifies:
- the ranking home loads on desktop and mobile without browser errors or overflow,
- the removed public platform route returns 404,
- the home contains no Python platform overview or platform navigation link,
- entry-page navigation and native hash anchors still work.

Usage (after `pnpm build`):
    python apps/leaderboard-web/scripts/smoke_static_export.py
"""

from __future__ import annotations

import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

WEB_ROOT = Path(__file__).resolve().parent.parent
BASE_PATH = "/arena-hero-lab"
BASE_URL = f"http://localhost:4173{BASE_PATH}"
VIEWPORTS = [("desktop", 1440, 900), ("mobile", 375, 812)]


def wait_for_server(url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError(f"preview server did not become ready at {url}")


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed; skipping browser smoke test", file=sys.stderr)
        return 0

    server = subprocess.Popen(
        ["node", "scripts/preview.mjs"],
        cwd=str(WEB_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    failures: list[str] = []

    try:
        wait_for_server(BASE_URL + "/")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)

            for name, width, height in VIEWPORTS:
                page = browser.new_page(viewport={"width": width, "height": height})
                errors: list[str] = []
                page.on(
                    "response",
                    lambda response: errors.append(f"HTTP {response.status} {response.url}")
                    if response.status >= 400
                    else None,
                )
                page.on(
                    "console",
                    lambda message: errors.append(f"console.{message.type}: {message.text}")
                    if message.type in ("error", "warning")
                    else None,
                )
                page.on("pageerror", lambda error: errors.append(f"pageerror: {error}"))
                response = page.goto(BASE_URL + "/", wait_until="load", timeout=30000)
                page.wait_for_timeout(800)
                if response is None or response.status != 200:
                    errors.append(f"home status={response.status if response else 'ERR'}")
                body = page.locator("body").inner_text()
                if "Python 新一代平台" in body or "查看平台详情" in body:
                    errors.append("removed Python platform overview is still visible")
                if page.locator('a[href*="/platform"]').count() != 0:
                    errors.append("removed platform navigation link is still present")
                if page.evaluate(
                    "document.documentElement.scrollWidth > document.documentElement.clientWidth"
                ):
                    errors.append("horizontal overflow detected")
                failures.extend(f"[home-{name}] {item}" for item in errors)
                page.close()

            # Removed route must not remain publicly exported.
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            response = page.goto(BASE_URL + "/platform", wait_until="load", timeout=30000)
            if response is None or response.status != 404:
                failures.append(
                    f"[removed-platform] expected 404, got {response.status if response else 'ERR'}"
                )
            page.close()

            # Full-page static navigation to a ranking entry.
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on(
                "response",
                lambda response: errors.append(f"HTTP {response.status} {response.url}")
                if response.status >= 400
                else None,
            )
            page.on("pageerror", lambda error: errors.append(f"pageerror: {error}"))
            page.goto(BASE_URL + "/", wait_until="load", timeout=30000)
            entry = page.locator('a[href*="/entry/"]').first
            href = entry.get_attribute("href")
            entry.click(timeout=10000)
            page.wait_for_load_state("load", timeout=30000)
            if not href or href not in page.url:
                errors.append(f"entry navigation landed on {page.url}, expected {href}")
            failures.extend(f"[click-entry] {item}" for item in errors)
            page.close()

            # Native fragment navigation remains available.
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(BASE_URL + "/", wait_until="load", timeout=30000)
            page.locator('a[href="#methodology"]').first.click(timeout=10000)
            page.wait_for_timeout(300)
            if page.url.split("#", 1)[-1] != "methodology":
                failures.append(f"[hash-anchor] landed on {page.url}")
            page.close()
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)

    if failures:
        print("Static export smoke test failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(
        "Static export smoke test passed: rankings-only home desktop+mobile 200, "
        "platform route removed, 0 browser errors/overflow, entry + hash navigation OK."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
