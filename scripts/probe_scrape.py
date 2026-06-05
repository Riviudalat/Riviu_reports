"""One-off probe: scrape 1 link with/without resource blocking."""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.async_api import async_playwright

from scraper import (
    BLOCKED_RESOURCE_TYPES,
    USER_AGENTS,
    extract_media_id,
    extract_profile_username,
    parse_channel_name,
    parse_counts,
    scrape_single_link,
    validate_metrics,
)

DEFAULT_URL = "https://www.tiktok.com/@ngc.ngc.i.chill/photo/7646786750978854152"


async def probe_mode(url: str, mode: str, blocked_types: set[str] | None):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=USER_AGENTS[0],
            viewport={"width": 390, "height": 844},
        )

        if blocked_types is not None:

            async def route_handler(route):
                if route.request.resource_type in blocked_types:
                    await route.abort()
                else:
                    await route.continue_()

            await context.route("**/*", route_handler)

        page = await context.new_page()
        started = time.perf_counter()
        data, channel, status = await scrape_single_link(page, url, timeout_ms=60000)
        elapsed = round(time.perf_counter() - started, 2)

        final_url = page.url or url
        content = await page.content()
        media_id = extract_media_id(url) or extract_media_id(final_url)
        profile = extract_profile_username(url) or extract_profile_username(final_url)
        parsed, found = parse_counts(content, media_id=media_id)
        has_universal = "__UNIVERSAL_DATA_FOR_REHYDRATION__" in content
        has_sigi = 'id="SIGI_STATE"' in content
        channel_from_html = parse_channel_name(content, profile)

        await browser.close()

        return {
            "mode": mode,
            "blocked": sorted(blocked_types or []),
            "elapsed_s": elapsed,
            "final_url": final_url,
            "media_id": media_id,
            "profile": profile,
            "status": status,
            "channel_scrape": channel,
            "channel_parse": channel_from_html,
            "metrics": data,
            "parse_found": found,
            "parse_valid": validate_metrics(parsed) if found else False,
            "html_bytes": len(content),
            "has_universal": has_universal,
            "has_sigi": has_sigi,
        }


async def main():
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    out_path = Path(__file__).resolve().parents[1] / "probe_scrape_result.json"
    modes = [
        ("no_block", None),
        ("current", set(BLOCKED_RESOURCE_TYPES)),
        ("images_media_only", {"image", "media"}),
        ("fonts_only", {"font"}),
    ]
    results = []
    for mode_name, blocked in modes:
        try:
            result = await probe_mode(url, mode_name, blocked)
            results.append(result)
        except Exception as exc:
            results.append({"mode": mode_name, "error": str(exc)})

    payload = {"url": url, "results": results}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    ok_modes = [r["mode"] for r in results if r.get("status") == "Success"]
    print(f"Wrote {out_path}")
    print(f"Success modes: {ok_modes or 'NONE'}")


if __name__ == "__main__":
    asyncio.run(main())
