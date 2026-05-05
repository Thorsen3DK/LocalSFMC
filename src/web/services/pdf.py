"""
PDF rendering service — converts HTML to PDF using headless Chromium via Playwright.
"""

from playwright.sync_api import sync_playwright


def html_to_pdf(html_content: str) -> bytes:
    """Convert HTML to PDF using headless Chromium via Playwright."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        try:
            page.set_content(html_content, wait_until="load", timeout=15000)
            page.evaluate(
                "() => Promise.race(["
                "document.fonts.ready,"
                "new Promise(r => setTimeout(r, 5000))"
                "])"
            )
        except Exception:
            pass
        height = page.evaluate("() => document.documentElement.scrollHeight")
        pdf_bytes = page.pdf(
            width="8.5in",
            height=f"{height + 20}px",
            print_background=True,
        )
        browser.close()
    return pdf_bytes
