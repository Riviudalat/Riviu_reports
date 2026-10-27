"""Regenerate the platform icons embedded in partner Excel reports (riviu/web/report-icons/).

    uv run python tools/make_report_icons.py

Source: the 256px web icons in riviu/web/static/platform-icons/ (not modified here). Each glyph
is trimmed to its visible pixels, centred on a square canvas so its longer side fills it (both
platforms then look the same size), and downsampled once with Lanczos to
REPORT_ICON_SOURCE_PX. That is 3x the size the report shows (riviu.reports.REPORT_ICON_PX), so
Excel and Google Sheets only shrink it a little and it stays sharp at 100% and when zoomed.
"""

import os
import sys

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from riviu import paths  # noqa: E402
from riviu.reports import REPORT_ICON_PX  # noqa: E402

REPORT_ICON_SOURCE_PX = REPORT_ICON_PX * 3
PLATFORMS = ("tiktok", "threads")


def render(source_path, target_path, size=REPORT_ICON_SOURCE_PX):
    with Image.open(source_path) as image:
        image = image.convert("RGBA")
        glyph = image.crop(image.getchannel("A").getbbox())
    side = max(glyph.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(glyph, ((side - glyph.width) // 2, (side - glyph.height) // 2))
    # Premultiplied resize keeps the transparent edge free of dark/light fringes.
    canvas.convert("RGBa").resize((size, size), Image.LANCZOS).convert("RGBA").save(target_path, optimize=True)


def main():
    os.makedirs(paths.REPORT_ICONS_DIR, exist_ok=True)
    for platform in PLATFORMS:
        target = os.path.join(paths.REPORT_ICONS_DIR, f"{platform}.png")
        render(os.path.join(paths.PLATFORM_ICONS_DIR, f"{platform}.png"), target)
        print(target)


if __name__ == "__main__":
    main()
