"""Anonymous network reads of a shared Google Sheet: its title and its .xlsx export."""

import html
import re
import urllib.request

from riviu.workbook.google_registry import parse_google_spreadsheet_id
from riviu.workbook.text import clean_text


def fetch_google_spreadsheet_title(source_url):
    spreadsheet_id = parse_google_spreadsheet_id(source_url)
    view_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
    request = urllib.request.Request(view_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            page_html = response.read().decode("utf-8", errors="ignore")
    except Exception:
        return f"Google Sheet {spreadsheet_id[:12]}"

    for pattern in (
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
        r"<title[^>]*>([^<]+)</title>",
    ):
        match = re.search(pattern, page_html, flags=re.IGNORECASE)
        if not match:
            continue
        title = clean_text(html.unescape(match.group(1)))
        title = re.sub(r"\s*-\s*Google Sheets\s*$", "", title, flags=re.IGNORECASE)
        title = re.sub(r"\s*-\s*Google Trang tính\s*$", "", title, flags=re.IGNORECASE)
        if title:
            return title
    return f"Google Sheet {spreadsheet_id[:12]}"


def google_sheet_export_url(source_url):
    spreadsheet_id = parse_google_spreadsheet_id(source_url)
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=xlsx"


def download_google_sheet(source_url, destination_path):
    export_url = google_sheet_export_url(source_url)
    request = urllib.request.Request(export_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        content = response.read()
    with open(destination_path, "wb") as file_obj:
        file_obj.write(content)
    return destination_path
