# Platform branding in UI and Excel

Local platform icons live under `static/platform-icons`:
- `tiktok.svg` and `threads.svg`: Simple Icons canonical vector paths, CC0 source/trademark note in that directory.
- `tiktok.png` and `threads.png`: transparent256px rasterizations for Excel.

UI uses SVG with adjacent text, decorative empty alt, fixed sizing and white treatment on the selected dark platform button. Icons appear on platform selection, platform-specific source rows, and report dialog context. They load from bundled `/static`, not an external font/image service.

Partner Excel export keeps Riviu logoA1, table headerrow3/data row4, and titles both platforms explicitly. The platform PNG is embedded at H2 for TikTok or I2 for Threads; metadata is merged only across preceding row2 cells. The existing sidecar static directory collection includes both icon formats. Google tab names/headers identify platform; no external IMAGE formula is introduced.

Verification: `tests/test_platform_branding.py` checks PNG payload inside XLSX ZIP, anchor/merge/title/layout contracts and blank Threads shares. Offline Chromium fixture renders at desktop/mobile widths with local SVG. Synthetic example XLSX and illustrative Pillow header previews are in `output/platform-branding-check`; those previews are not actual Excel application rendering. Installer/Excel-desktop acceptance is a separate gate.
