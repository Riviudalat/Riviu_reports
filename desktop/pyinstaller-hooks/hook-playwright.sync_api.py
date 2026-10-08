"""Replace Playwright's own sync_api hook, which collects every installed browser.

The app only imports playwright.async_api, but hook-playwright.async_api.py lists
every Playwright submodule as a hidden import, so this hook runs too. The async
hook already collects the filtered Playwright data files.
"""

datas = []
