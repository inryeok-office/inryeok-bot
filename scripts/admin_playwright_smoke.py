"""Chromium layout and accessibility smoke for the admin console."""

# ruff: noqa: E501

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

VIEWPORTS = (
    ("desktop-1920", 1920, 1080, 1.0),
    ("desktop-1600", 1600, 900, 1.0),
    ("desktop-1366", 1366, 768, 1.0),
    ("desktop-1280", 1280, 720, 1.0),
    ("desktop-1280-125", 1280, 720, 1.25),
    ("desktop-1280-150", 1280, 720, 1.5),
    ("tablet-1024", 1024, 768, 1.0),
    ("tablet-768", 768, 1024, 1.0),
    ("mobile-430", 430, 932, 1.0),
    ("mobile-390", 390, 844, 1.0),
    ("mobile-360", 360, 800, 1.0),
)
ROUTES = (
    "/admin",
    "/admin/repositories",
    "/admin/repositories/1",
    "/admin/jobs",
    "/admin/jobs/1",
    "/admin/jobs/2",
    "/admin/operations",
    "/admin/settings",
    "/admin/usage",
    "/admin/audit",
)
RAW_DATETIME = re.compile(r"\b\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


def _safe_route(path: str) -> str:
    return path.strip("/").replace("/", "-") or "overview"


def _check_layout(page: object, path: str, viewport_name: str) -> None:
    result = page.evaluate(
        """
        () => {
          const interactive = [...document.querySelectorAll(
            'a:not(.skip-link), button, input:not([type=hidden]), select, textarea, summary'
          )].filter((node) => {
            const style = getComputedStyle(node), rect = node.getBoundingClientRect();
            return !node.closest('details:not([open])') && node.offsetParent !== null && style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
          });
          const boxes = interactive.map((node) => ({node,
            text: (node.textContent || node.getAttribute('aria-label') || '').trim(),
            tag: node.tagName, id: node.id, className: node.className,
            rect: (() => { const r = node.getBoundingClientRect(); return {left:r.left,right:r.right,top:r.top,bottom:r.bottom}; })()
          }));
          const outside = boxes.filter(({rect}) => rect.left < -1 || rect.right > innerWidth + 1).map(({text,rect}) => ({text,rect}));
          const zero = [...document.querySelectorAll('a:not(.skip-link), button, input:not([type=hidden]), select, textarea, summary')].filter((node) => {
            const style = getComputedStyle(node), rect = node.getBoundingClientRect();
            return !node.closest('details:not([open])') && node.offsetParent !== null && style.display !== 'none' && style.visibility !== 'hidden' && (rect.width <= 0 || rect.height <= 0);
          }).map((node) => ({tag:node.tagName, text:(node.textContent || node.getAttribute('aria-label') || '').trim()}));
          const overlaps = [];
          for (let i = 0; i < boxes.length; i += 1) for (let j = i + 1; j < boxes.length; j += 1) {
            if (boxes[i].node.contains(boxes[j].node) || boxes[j].node.contains(boxes[i].node)) continue;
            const a = boxes[i].rect, b = boxes[j].rect;
            const area = Math.max(0, Math.min(a.right,b.right)-Math.max(a.left,b.left)) * Math.max(0, Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top));
            if (area > 20) overlaps.push([`${boxes[i].tag}#${boxes[i].id}.${boxes[i].className}`, `${boxes[j].tag}#${boxes[j].id}.${boxes[j].className}`]);
          }
          const repoNames = [...document.querySelectorAll('.repo-table .repo-name')].map((node) => {
            const style = getComputedStyle(node);
            return {width: node.getBoundingClientRect().width, wordBreak: style.wordBreak, overflowWrap: style.overflowWrap};
          });
          const table = document.querySelector('.repo-table');
          const tableRect = table ? table.getBoundingClientRect() : null;
          return {htmlWidth:document.documentElement.scrollWidth, htmlClientWidth:document.documentElement.clientWidth,
            bodyWidth:document.body.scrollWidth, bodyClientWidth:document.body.clientWidth, outside, zero, overlaps, repoNames, tableRect};
        }
        """
    )
    if (
        result["htmlWidth"] > result["htmlClientWidth"] + 1
        or result["bodyWidth"] > result["bodyClientWidth"] + 1
    ):
        raise RuntimeError(f"horizontal overflow at {viewport_name}: {path}: {result}")
    if result["outside"]:
        raise RuntimeError(
            f"interactive element outside viewport at {viewport_name}: {path}: outside={result['outside'][:4]} table={result['tableRect']} widths={result['htmlWidth']}/{result['htmlClientWidth']}"
        )
    if result["zero"]:
        raise RuntimeError(
            f"zero-sized interactive element at {viewport_name}: {path}: {result['zero'][:5]}"
        )
    if result["overlaps"]:
        raise RuntimeError(
            f"overlapping interactive elements at {viewport_name}: {path}: {result['overlaps'][:3]}"
        )
    for repo in result["repoNames"]:
        if (
            repo["width"] < 100
            or repo["wordBreak"] in {"break-all", "break-word"}
            or repo["overflowWrap"] == "anywhere"
        ):
            raise RuntimeError(
                f"repository name wrapping policy failed at {viewport_name}: {path}: {repo}"
            )
    if RAW_DATETIME.search(page.locator("body").inner_text()):
        raise RuntimeError(f"raw database datetime visible at {viewport_name}: {path}")


def _check_keyboard(page: object, path: str) -> None:
    page.locator("body").click(position={"x": 4, "y": 4})
    count = page.locator("a, button, input:not([type=hidden]), select, textarea, summary").count()
    visited: set[str] = set()
    for _ in range(min(count + 2, 80)):
        page.keyboard.press("Tab")
        active = page.evaluate(
            "() => document.activeElement ? (document.activeElement.outerHTML || document.activeElement.tagName).slice(0, 180) : ''"
        )
        if active:
            visited.add(active)
    if count and not visited:
        raise RuntimeError(f"keyboard navigation did not move focus: {path}")
    first = page.locator("a, button, input:not([type=hidden]), select, textarea, summary").first
    if first.count():
        first.focus()
        outline = page.evaluate(
            "() => { const s=getComputedStyle(document.activeElement); return [s.outlineStyle,s.outlineWidth]; }"
        )
        if outline[0] == "none" or outline[1] == "0px":
            raise RuntimeError(f"focus-visible style missing: {path}")


def _check_axe(page: object, axe_path: Path, path: str) -> None:
    if not page.locator("script[data-axe]").count():
        page.add_script_tag(path=str(axe_path))
        page.evaluate("() => document.body.dataset.axe = 'loaded'")
    result = page.evaluate(
        """
        async () => (await axe.run(document)).violations
          .filter((item) => item.impact === 'critical' || item.impact === 'serious')
          .map((item) => ({id:item.id, impact:item.impact, nodes:item.nodes.map((node) => ({target:node.target, summary:node.failureSummary}))}))
        """
    )
    if result:
        raise RuntimeError(f"axe critical/serious violations at {path}: {result}")


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright test dependency is required", file=sys.stderr)
        return 2
    base_url = os.environ.get("ADMIN_E2E_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
    fixture_mode = os.environ.get("ADMIN_E2E_FIXTURE") == "1"
    artifact_dir = Path(os.environ.get("ADMIN_E2E_ARTIFACT_DIR", "artifacts/admin-e2e"))
    axe_path = Path(os.environ.get("AXE_CORE_PATH", ""))
    if not axe_path.is_file():
        print("AXE_CORE_PATH must point to the isolated axe-core bundle", file=sys.stderr)
        return 2
    artifact_dir.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    failed_requests: list[str] = []
    with sync_playwright() as playwright:
        context_kwargs: dict[str, object] = {"ignore_https_errors": False}
        state = os.environ.get("PLAYWRIGHT_STORAGE_STATE")
        if state:
            context_kwargs["storage_state"] = state
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        page.on(
            "console",
            lambda message: (
                console_errors.append(message.text) if message.type == "error" else None
            ),
        )
        page.on("requestfailed", lambda request: failed_requests.append(request.url))
        page.goto(f"{base_url}/health/live", wait_until="networkidle")
        if not page.locator("body").inner_text().strip():
            raise RuntimeError("live endpoint returned an empty document")
        page.goto(f"{base_url}/admin", wait_until="domcontentloaded")
        if not state and not fixture_mode and "/auth/" not in page.url:
            raise RuntimeError("unauthenticated /admin did not redirect to authentication")
        if state or fixture_mode or "/auth/" not in page.url:
            for viewport_name, width, height, zoom in VIEWPORTS:
                css_width = round(width / zoom)
                css_height = round(height / zoom)
                page.set_viewport_size({"width": css_width, "height": css_height})
                for path in ROUTES:
                    page.goto(f"{base_url}{path}", wait_until="networkidle")
                    page.evaluate("document.documentElement.style.zoom = '1'")
                    page.wait_for_timeout(50)
                    if page.locator("body").count() != 1:
                        raise RuntimeError(f"{path} did not render a document")
                    _check_layout(page, path, viewport_name)
                    _check_axe(page, axe_path, path)
                    page.screenshot(
                        path=str(artifact_dir / f"{_safe_route(path)}-{viewport_name}.png"),
                        full_page=True,
                    )
                _check_keyboard(page, ROUTES[-1])
        context.close()
        browser.close()
    if console_errors:
        raise RuntimeError(f"browser console errors: {console_errors[:5]}")
    if failed_requests:
        raise RuntimeError(f"failed network requests: {failed_requests[:5]}")
    print(
        f"administrator browser smoke passed; screenshots={len(list(artifact_dir.glob('*.png')))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
