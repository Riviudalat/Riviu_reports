"""PyInstaller entry script for the riviu-server sidecar (see build_sidecar.py).

It sits outside the package so the frozen app imports riviu.desktop_server as
a package module, exactly as source mode does.

`riviu-server --self-check` launches the bundled headless Chromium once and
exits 0 on success; CI runs it on every built sidecar (desktop/smoke_sidecar.py).
"""

import sys


def self_check() -> int:
    import asyncio
    import os

    from playwright.async_api import async_playwright

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

    async def run() -> str:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content("<p id='ok'>ok</p>")
                assert await page.text_content("#ok") == "ok"
                return browser.version
            finally:
                await browser.close()

    print(f"Bundled Chromium OK: {asyncio.run(run())}", flush=True)
    return 0


if __name__ == "__main__":
    if "--self-check" in sys.argv[1:]:
        sys.exit(self_check())
    from riviu.desktop_server import main

    main()
