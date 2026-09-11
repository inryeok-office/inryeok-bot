"""Read-only administrator browser smoke test.

This intentionally exercises only authentication boundaries and health.  A
signed-in E2E run may provide PLAYWRIGHT_STORAGE_STATE, but no credentials are
read from this script or committed to the repository.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed; use the HTTP/template fallback", file=sys.stderr)
        return 2

    base_url = os.environ.get("ADMIN_E2E_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    state = os.environ.get("PLAYWRIGHT_STORAGE_STATE")
    artifact_dir = os.environ.get("ADMIN_E2E_ARTIFACT_DIR")
    viewports = ((1280, 720), (1366, 768), (1440, 900), (768, 1024), (390, 844))
    routes = (
        "/admin",
        "/admin/repositories",
        "/admin/jobs",
        "/admin/usage",
        "/admin/operations",
        "/admin/audit",
        "/admin/settings",
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context_args = {"storage_state": state} if state else {}
        context = browser.new_context(**context_args)
        page = context.new_page()
        page.goto(f"{base_url}/health/live", wait_until="domcontentloaded")
        if page.locator("body").inner_text().strip() == "":
            raise RuntimeError("live endpoint returned an empty document")
        page.goto(f"{base_url}/admin", wait_until="domcontentloaded")
        if not state and "/auth/" not in page.url:
            raise RuntimeError("unauthenticated /admin did not redirect to authentication")
        if state:
            output_path = Path(artifact_dir) if artifact_dir else None
            if output_path:
                output_path.mkdir(parents=True, exist_ok=True)
            for width, height in viewports:
                page.set_viewport_size({"width": width, "height": height})
                for path in routes:
                    page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
                    if page.locator("body").count() != 1:
                        raise RuntimeError(f"{path} did not render a document")
                    overflow = page.evaluate(
                        "document.documentElement.scrollWidth > "
                        "document.documentElement.clientWidth"
                    )
                    if overflow:
                        raise RuntimeError(f"horizontal overflow at {width}x{height}: {path}")
                    if output_path:
                        safe_name = path.strip("/").replace("/", "-") or "overview"
                        page.screenshot(
                            path=str(output_path / f"{safe_name}-{width}x{height}.png"),
                            full_page=True,
                        )
        context.close()
        browser.close()
    print("administrator browser smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
