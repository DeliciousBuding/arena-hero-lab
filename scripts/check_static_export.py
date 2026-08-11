"""Verify that the static export's internal links resolve to real files.

Run this after `pnpm build` (the `apps/leaderboard-web/out/` directory must
exist). It guards against regressions that break GitHub Pages static hosting:

- dangling internal hrefs (wrong `.html` / `index.html` resolution),
- trailing-slash URLs that 404 on plain static hosts (e.g. `/platform/`),
- Next RSC prefetch/flight paths leaking into exported hrefs
  (`__next.<route>.__PAGE__.txt`), which only exist on dev/SSR servers.

The check mirrors the resolution rules of `apps/leaderboard-web/scripts/preview.mjs`
and of GitHub Pages: a route may be a flat `page.html`, a `page/index.html`
directory index, or a basePath root (`/arena-hero-lab` -> `index.html`).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "apps" / "leaderboard-web" / "out"
BASE_PATH = os.environ.get("NEXT_PUBLIC_BASE_PATH", "/arena-hero-lab")

_HREF_RE = re.compile(r'\b(?:href|src)="([^"]+)"')
_EXTERNAL_RE = re.compile(r"^(?:https?:|mailto:|tel:|data:|//)", re.IGNORECASE)
_RSC_PREFETCH_RE = re.compile(r"__next\.[A-Za-z0-9_$.-]+\.__PAGE__\.txt")


def resolve_candidates(route: str) -> list[Path]:
    """Return OUT-relative file paths that could serve `route` on a static host."""
    route = route.rstrip("/")
    if route in ("", "/"):
        return [Path("index.html")]
    rel = route.lstrip("/")
    return [
        Path(rel),
        Path(f"{rel}.html"),
        Path(rel) / "index.html",
    ]


def main() -> int:
    if not OUT.is_dir():
        print(f"Static export not found at {OUT}. Run `pnpm build` first.", file=sys.stderr)
        return 2

    pages = sorted(OUT.rglob("*.html"))
    findings: list[str] = []
    checked = 0
    for page in pages:
        rel_page = page.relative_to(OUT).as_posix()
        text = page.read_text(encoding="utf-8")
        for raw in _HREF_RE.findall(text):
            if _RSC_PREFETCH_RE.search(raw):
                findings.append(f"{rel_page}: RSC prefetch path in href: {raw}")
                continue
            href = raw.strip()
            if not href or href.startswith("#") or _EXTERNAL_RE.match(href):
                continue
            clean = href.split("?", 1)[0].split("#", 1)[0]
            if not clean:
                continue
            if clean.startswith(BASE_PATH):
                route = clean[len(BASE_PATH) :]
                candidates = resolve_candidates(route)
                checked += 1
                if not any((OUT / c).is_file() for c in candidates):
                    tried = ", ".join(c.as_posix() for c in candidates)
                    findings.append(f"{rel_page}: dangling link {raw!r} (tried {tried})")
            elif clean.startswith("/"):
                if not clean.startswith("/_next/"):
                    findings.append(f"{rel_page}: unexpected absolute path without basePath: {raw}")
            else:
                target = (page.parent / clean).resolve()
                checked += 1
                if not target.is_file():
                    findings.append(f"{rel_page}: dangling relative link {raw!r}")

    if findings:
        print(f"Static export integrity check failed ({len(findings)} issue(s)):")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print(
        f"Static export integrity check passed: {len(pages)} pages, "
        f"{checked} internal links resolve."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
