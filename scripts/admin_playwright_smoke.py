"""Read-only administrator browser smoke test.

This intentionally exercises only authentication boundaries and health.  A
signed-in E2E run may provide PLAYWRIGHT_STORAGE_STATE, but no credentials are
read from this script or committed to the repository.
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed; use the HTTP/template fallback", file=sys.stderr)
        return 2

    base_url = os.environ.get("ADMIN_E2E_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    state = os.environ.get("PLAYWRIGHT_STORAGE_STATE")
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
            for path in ("/admin", "/admin/operations", "/admin/jobs"):
                page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
                if page.locator("body").count() != 1:
                    raise RuntimeError(f"{path} did not render a document")
        context.close()
        browser.close()
    print("administrator browser smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
