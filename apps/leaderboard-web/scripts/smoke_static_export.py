"""Local static-export smoke test (dev tool, not part of CI).

Starts the preview server (scripts/preview.mjs) and verifies with Playwright
that the exported site behaves like a clean GitHub Pages static deployment:
- home and /platform load with HTTP 200 on desktop and mobile viewports,
- /platform/ (trailing slash) also loads,
- no 4xx/5xx responses, no console errors, no page errors, no horizontal
  overflow,
- clicking internal links (platform + an entry page) navigates to real pages,
- hash anchors still scroll (preserves native fragment behavior).

Usage (after `pnpm build`):
    python apps/leaderboard-web/scripts/smoke_static_export.py
Requires: python `playwright` package with chromium installed.
"""

from __future__ import annotations

import subprocess
import sys
import time
import urllib.request
from pathlib import Path

WEB_ROOT = Path(__file__).resolve().parent.parent
BASE_PATH = "/arena-hero-lab"
BASE_URL = f"http://localhost:4173{BASE_PATH}"
PORT = 4173
VIEWPORTS = [("desktop", 1440, 900), ("mobile", 375, 812)]


def wait_for_server(url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError(f"preview server did not become ready at {url}")


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "playwright is not installed; skipping browser smoke test "
            "(run: pip install playwright && playwright install chromium)",
            file=sys.stderr,
        )
        return 0

    server = subprocess.Popen(
        ["node", "scripts/preview.mjs"],
        cwd=str(WEB_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    failures: list[str] = []

    def record(label: str, bad: list[str]) -> None:
        for item in bad:
            failures.append(f"[{label}] {item}")

    try:
        wait_for_server(BASE_URL + "/")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)

            def probe(label: str, path: str, width: int, height: int) -> None:
                page = browser.new_page(viewport={"width": width, "height": height})
                bad: list[str] = []
                page.on(
                    "response",
                    lambda r: bad.append(f"HTTP {r.status} {r.url}")
                    if r.status >= 400
                    else None,
                )
                page.on(
                    "console",
                    lambda m: bad.append(f"console.{m.type}: {m.text}")
                    if m.type in ("error", "warning")
                    else None,
                )
                page.on("pageerror", lambda e: bad.append(f"pageerror: {e}"))
                resp = page.goto(BASE_URL + path, wait_until="load", timeout=30000)
                page.wait_for_timeout(1200)
                if resp is None or resp.status != 200:
                    bad.append(f"status={resp.status if resp else 'ERR'} for {path}")
                overflow = page.evaluate(
                    "document.documentElement.scrollWidth > document.documentElement.clientWidth"
                )
                if overflow:
                    bad.append("horizontal overflow detected")
                record(f"{label} {width}x{height}", bad)
                page.close()

            for name, w, h in VIEWPORTS:
                probe(f"home-{name}", "/", w, h)
                probe(f"platform-{name}", "/platform", w, h)
            probe("platform-slash", "/platform/", 1440, 900)

            # Click navigation: home -> platform detail page.
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            bad: list[str] = []
            page.on(
                "response",
                lambda r: bad.append(f"HTTP {r.status} {r.url}") if r.status >= 400 else None,
            )
            page.on(
                "console",
                lambda m: bad.append(f"console.{m.type}: {m.text}")
                if m.type in ("error", "warning")
                else None,
            )
            page.on("pageerror", lambda e: bad.append(f"pageerror: {e}"))
            page.goto(BASE_URL + "/", wait_until="load", timeout=30000)
            page.get_by_text("查看平台详情").first.click(timeout=10000)
            page.wait_for_load_state("load", timeout=30000)
            page.wait_for_timeout(1000)
            if not page.url.startswith(BASE_URL + "/platform"):
                bad.append(f"click to platform landed on {page.url}")
            record("click-platform", bad)
            page.close()

            # Click navigation: home -> first entry page.
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            bad = []
            page.on(
                "response",
                lambda r: bad.append(f"HTTP {r.status} {r.url}") if r.status >= 400 else None,
            )
            page.on(
                "console",
                lambda m: bad.append(f"console.{m.type}: {m.text}")
                if m.type in ("error", "warning")
                else None,
            )
            page.goto(BASE_URL + "/", wait_until="load", timeout=30000)
            entry = page.locator(f'a[href^="{BASE_PATH}/entry/"]').first
            entry.click(timeout=10000)
            page.wait_for_load_state("load", timeout=30000)
            page.wait_for_timeout(1000)
            if not page.url.startswith(BASE_URL + "/entry/"):
                bad.append(f"click to entry landed on {page.url}")
            record("click-entry", bad)
            page.close()

            # Hash anchor: click 场景 nav item -> scrolls to #scenarios.
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            bad = []
            page.goto(BASE_URL + "/", wait_until="load", timeout=30000)
            page.get_by_role("link", name="场景").first.click(timeout=10000)
            page.wait_for_timeout(800)
            fragment = page.evaluate("location.hash")
            if fragment != "#scenarios":
                bad.append(f"hash anchor did not scroll: fragment={fragment!r}")
            record("hash-anchor", bad)
            page.close()

            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()

    if failures:
        print("Static export smoke test FAILED:")
        for item in failures:
            print(f"- {item}")
        return 1
    print(
        "Static export smoke test passed: home/platform desktop+mobile 200, "
        "0 4xx, 0 console/page errors, no overflow, click nav + hash anchors OK."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
