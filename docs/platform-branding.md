# Platform branding in UI and Excel

Local platform icons live under `riviu/web/static/platform-icons`:
- `tiktok.svg` and `threads.svg`: Simple Icons canonical vector paths, CC0 source/trademark note in that directory.
- `tiktok.png` and `threads.png`: transparent 256px rasterizations of the SVGs.

Partner Excel reports embed separate renditions from `riviu/web/report-icons/` (`tiktok.png`, `threads.png`), generated from those 256px PNGs by `tools/make_report_icons.py`: the glyph is trimmed to its visible pixels and centred on a square canvas so its longer side fills it (both platforms show the same glyph height), then downsampled once with Lanczos to 72x72, three times the 24px the report shows, so Excel and Google Sheets stay sharp at 100% and when zoomed. Rerun the tool if the web icons change.

UI uses SVG with adjacent text, decorative empty alt, fixed sizing and white treatment on the selected dark platform button. Icons appear on platform selection, platform-specific source rows, and report dialog context. They load from bundled `/static`, not an external font/image service.

Partner Excel export keeps Riviu logo A1, title merged B1:H1, table header row 3/data row 4, and titles both platforms explicitly. Both platforms use the same 8 columns (NGÀY AIR, TÊN KÊNH, LINK AIR, LƯỢT XEM, TIM, BÌNH LUẬN, LƯỢT LƯU or REPOST for Threads, CHIA SẺ) with no status column, so the platform icon is embedded at H2 and metadata is merged across A2:G2.

Image sizing (`riviu/reports.py`): the column widths are set before the images are anchored, because `add_centered_image_to_cell` centres each image from the cell's final width and height. The logo is scaled (aspect kept) to `REPORT_LOGO_MAX_HEIGHT_PX` = 40px high in row 1 (42pt = 56px) and centred in column A (width 14 = 103px). The platform icon is `REPORT_ICON_PX` = 24px square, centred in H2 (row 26pt = 34px), and the A2 metadata text is vertically centred to line up with it. Google tab names/headers identify platform; no external IMAGE formula is introduced.

Verification: `tests/test_platform_branding.py` checks the report-icon PNG payload inside the XLSX ZIP, that the logo and icon are centred in their cells with the expected size and aspect ratio, the square 3x report-icon renditions, the merge/title/layout contracts for both platforms and blank Threads shares. Offline Chromium fixture renders at desktop/mobile widths with local SVG. Synthetic example XLSX files and Pillow header previews were generated as one-off evidence and are not kept in the repository; they were never actual Excel application rendering. Installer/Excel-desktop acceptance is a separate gate.
