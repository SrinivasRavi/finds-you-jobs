"""Resume markdown → PDF via headless Chromium.

Carried from the maintainer's prior repository
(`sidecar/modules/applier_redesign/pdf.py` — same CSS, same page setup) and
adapted to async Playwright so the apply op renders inside its own event
loop. PDF-first upload is the observed-ATS regression knowledge the applier
redesign keeps (`docs/internal/applier.md` §2).
"""

from __future__ import annotations

from typing import Any

from markdown_it import MarkdownIt

_CSS = """
  body { font-family: -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif;
         font-size: 10.5pt; line-height: 1.45; color: #111; margin: 0; }
  h1 { font-size: 17pt; margin: 0 0 2pt; }
  h2 { font-size: 12pt; margin: 12pt 0 4pt; border-bottom: 1px solid #999;
       padding-bottom: 2pt; }
  h3 { font-size: 11pt; margin: 8pt 0 2pt; }
  p { margin: 4pt 0; }
  ul { margin: 4pt 0; padding-left: 16pt; }
  li { margin: 2pt 0; }
  a { color: #111; text-decoration: none; }
"""


async def render_resume_pdf_async(pw: Any, markdown_text: str, out_path: str) -> None:
    """Render with the caller's already-started async_playwright handle."""
    body = MarkdownIt("commonmark").render(markdown_text)
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{_CSS}</style></head><body>{body}</body></html>"
    )
    browser = await pw.chromium.launch(headless=True)
    try:
        page = await browser.new_page()
        await page.set_content(html, wait_until="load")
        await page.pdf(
            path=out_path,
            format="A4",
            print_background=True,
            margin={
                "top": "14mm",
                "bottom": "14mm",
                "left": "13mm",
                "right": "13mm",
            },
        )
    finally:
        await browser.close()


class PdfRenderError(RuntimeError):
    """Chromium missing / render failure, with a user-facing message."""


def render_resume_pdf(markdown_text: str, out_path: str) -> None:
    """Sync render for worker-thread callers (the `/api/export/pdf` route).

    Same pipeline/CSS as the async path; sync Playwright refuses to start
    inside a running asyncio loop, so callers must run this in a thread."""
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    body = MarkdownIt("commonmark").render(markdown_text)
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{_CSS}</style></head><body>{body}</body></html>"
    )
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content(html, wait_until="load")
                page.pdf(
                    path=out_path,
                    format="A4",
                    print_background=True,
                    margin={
                        "top": "14mm",
                        "bottom": "14mm",
                        "left": "13mm",
                        "right": "13mm",
                    },
                )
            finally:
                browser.close()
    except PlaywrightError as e:
        raise PdfRenderError(f"could not render the PDF: {e}") from e
