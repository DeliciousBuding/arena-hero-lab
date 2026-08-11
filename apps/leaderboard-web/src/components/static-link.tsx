import type { AnchorHTMLAttributes, ReactNode } from "react";

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? "/arena-hero-lab";

/**
 * Resolve an app-relative path to a basePath-aware href for a static export.
 * External URLs and bare hash anchors pass through unchanged; "/" maps to the
 * base path root (GitHub Pages serves index.html there).
 */
export function staticHref(href: string): string {
  if (/^(?:https?:|mailto:|tel:)/i.test(href) || href.startsWith("#")) {
    return href;
  }
  if (href === "/") {
    return BASE_PATH;
  }
  return `${BASE_PATH}${href}`;
}

/**
 * Static-export internal navigation anchor.
 *
 * Next <Link> drives the client-side router and issues RSC prefetch/flight
 * requests (`__next.<route>.__PAGE__.txt`), which 404 on plain static hosts
 * such as GitHub Pages and local file servers. A static export does not need
 * the flight cache, so we navigate with a basePath-aware plain <a>: the
 * browser performs a normal full-page navigation and never requests RSC
 * payloads. Hash anchors keep native scroll-to-fragment behavior.
 */
export function StaticLink({
  href,
  children,
  ...rest
}: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string; children?: ReactNode }) {
  return (
    <a href={staticHref(href)} {...rest}>
      {children}
    </a>
  );
}
