# Platform branding in UI and Excel

Local platform icons live under `riviu/web/static/platform-icons`:
- `tiktok.svg` and `threads.svg`: Simple Icons canonical vector paths, CC0 source/trademark note in that directory.
- `tiktok.png` and `threads.png`: transparent256px rasterizations for Excel.

UI uses SVG with adjacent text, decorative empty alt, fixed sizing and white treatment on the selected dark platform button. Icons appear on platform selection, platform-specific source rows, and report dialog context. They load from bundled `/static`, not an external font/image service.

Partner Excel export keeps Riviu logoA1, title merged B1:H1, table headerrow3/data row4, and titles both platforms explicitly. Both platforms use the same 8 columns (NGÀY AIR, TÊN KÊNH, LINK AIR, LƯỢT XEM, TIM, BÌNH LUẬN, LƯỢT LƯU or REPOST for Threads, CHIA SẺ) with no status column, so the platform PNG is embedded at H2 and metadata is merged across A2:G2. The sidecar bundles all of `riviu/web` (build_sidecar.py `--add-data`), which includes both icon formats. Google tab names/headers identify platform; no external IMAGE formula is introduced.

Verification: `tests/test_platform_branding.py` checks PNG payload inside XLSX ZIP, anchor/merge/title/layout contracts for both platforms and blank Threads shares. Offline Chromium fixture renders at desktop/mobile widths with local SVG. Synthetic example XLSX files and Pillow header previews were generated as one-off evidence and are not kept in the repository; they were never actual Excel application rendering. Installer/Excel-desktop acceptance is a separate gate.
