"""Public Threads post checks, kept separate from TikTok's scraper."""

import asyncio
import json
import os
import re
import tempfile
import time
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urlparse

import openpyxl
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

from workbook_utils import (
    THREADS_SCAN_STATUS_HEADER,
    clean_text,
    format_display_datetime,
    is_threads_link,
    normalize_header,
    normalize_threads_url,
    workbook_data_sheet_names,
    worksheet_find_link_column_index,
    worksheet_partner_column_indexes,
    worksheet_row_partners,
)


METRICS = {
    "views": "LƯỢT XEM",
    "likes": "TIM",
    "comments": "BÌNH LUẬN",
    "reposts": "REPOST",
    "shares": "CHIA SẺ",
}
ACTION_TITLES = {"likes": "Like", "comments": "Comment", "reposts": "Repost", "shares": "Share"}
POST_PATH = re.compile(r"^/@([^/]+)/post/([A-Za-z0-9_-]+)/?$", re.IGNORECASE)
SHARE_PATH = re.compile(r"^/share/[A-Za-z0-9_-]+/?$", re.IGNORECASE)


class _JSONScripts(HTMLParser):
    """Read data scripts without treating captions or HTML text as JSON."""
    def __init__(self):
        super().__init__()
        self.collecting = False
        self.parts = []
        self.documents = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.collecting = dict(attrs).get("type", "").lower() == "application/json"
            self.parts = []

    def handle_data(self, data):
        if self.collecting:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.collecting:
            self.collecting = False
            try:
                self.documents.append(json.loads("".join(self.parts)))
            except (ValueError, RecursionError):
                pass


def _embedded_post_views(content):
    parser = _JSONScripts()
    parser.feed(content)
    pending = list(parser.documents)
    views = set()
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            pending.extend(value for value in node.values() if isinstance(value, (dict, list)))
        elif isinstance(node, list):
            # This typed module is the logged-out permalink's view counter.
            # Only inspect its direct payload; never search caption strings or
            # unrelated posts that happen to have a similarly named field.
            if len(node) >= 3 and node[0] == "BarcelonaLoggedOutExpansionGating" and isinstance(node[2], dict):
                value = node[2].get("view_counts")
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    views.add(value)
            pending.extend(value for value in node if isinstance(value, (dict, list)))
    return next(iter(views)) if len(views) == 1 else None


def post_identity(url):
    normalized = normalize_threads_url(url)
    if not is_threads_link(normalized):
        return None
    match = POST_PATH.fullmatch(urlparse(normalized).path)
    return (match.group(1).casefold(), match.group(2)) if match else None


def same_threads_post(requested_url, final_url):
    requested = post_identity(requested_url)
    final = post_identity(final_url)
    if requested is not None:
        return requested == final
    return bool(is_threads_link(requested_url) and SHARE_PATH.fullmatch(urlparse(normalize_threads_url(requested_url)).path) and final)


def empty_metrics():
    return {key: None for key in METRICS}


def parse_action_count(value):
    text = clean_text(value).replace("\xa0", "").replace(",", "").strip()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([KMB]?)", text, re.IGNORECASE)
    if not match:
        return None
    scale = {"": 1, "K": 1000, "M": 1000000, "B": 1000000000}[match.group(2).upper()]
    return int(float(match.group(1)) * scale)


def parse_threads_http(content, requested_url, final_url):
    result = {"channel": "", "metrics": empty_metrics(), "error": ""}
    if not same_threads_post(requested_url, final_url):
        result["error"] = "Bài Threads chuyển hướng sang URL khác"
        return result
    result["channel"] = post_identity(final_url)[0]
    if re.search(r"This content isn't available|This content isn.t available|invalid_post", content, re.IGNORECASE):
        result["error"] = "Bài Threads không công khai hoặc không còn tồn tại"
        return result
    result["metrics"]["views"] = _embedded_post_views(content)
    return result


def result_status(result):
    if result.get("error"):
        return f"Error: {result['error']}"
    missing = [label for key, label in METRICS.items() if result["metrics"].get(key) is None]
    if not missing:
        return "Success"
    if len(missing) == len(METRICS):
        return "Ẩn số liệu: Threads không trả chỉ số"
    return f"Partial: thiếu {', '.join(missing)}"


def merge_results(first, second):
    merged = {"channel": first.get("channel") or second.get("channel", ""), "metrics": dict(first["metrics"]), "error": ""}
    for key, value in second["metrics"].items():
        # Browser is a fallback for missing values, not a reason to replace
        # a value already extracted from the HTTP post response.
        if value is not None and merged["metrics"].get(key) is None:
            merged["metrics"][key] = value
    if not any(value is not None for value in merged["metrics"].values()):
        merged["error"] = second.get("error") or first.get("error", "")
    return merged


def fetch_threads_http(url, timeout=20):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content = response.read().decode("utf-8", errors="replace")
            return parse_threads_http(content, url, response.geturl())
    except Exception as error:
        return {"channel": "", "metrics": empty_metrics(), "error": f"HTTP: {error}"}


async def fetch_threads_browser(browser, url):
    result = {"channel": "", "metrics": empty_metrics(), "error": ""}
    page = await browser.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.locator('svg[title="Like"]').first.wait_for(timeout=10000)
        except PlaywrightTimeoutError:
            pass
        if not same_threads_post(url, page.url):
            result["error"] = "Bài Threads chuyển hướng sang URL khác"
            return result
        username = post_identity(page.url)[0]
        extracted = await page.evaluate(r"""({username, shortcode}) => {
            const identity = href => {
                try {
                    const url = new URL(href, location.href);
                    if (!/^(www\.)?threads\.(com|net)$/i.test(url.hostname)) return null;
                    const match = url.pathname.match(/^\/@([^/]+)\/post\/([^/]+)\/?$/);
                    return match ? [match[1].toLowerCase(), match[2]] : null;
                } catch { return null; }
            };
            const matches = href => {
                const id = identity(href);
                return id && id[0] === username && id[1] === shortcode;
            };
            const anchors = [...document.querySelectorAll('a[href]')].filter(a => matches(a.href));
            const roots = new Set();
            for (const anchor of anchors) {
                let node = anchor.parentElement;
                while (node && node !== document.body && node !== document.documentElement) {
                    const postLinks = [...node.querySelectorAll('a[href]')].filter(a => identity(a.href));
                    // Crossing into replies/recommendations makes a container ambiguous.
                    if (postLinks.some(a => !matches(a.href))) break;
                    if (node.querySelector('svg[title="Like"]') && node.querySelector('svg[title="Comment"]')) {
                        roots.add(node);
                        break;
                    }
                    node = node.parentElement;
                }
            }
            // A sticky page title may link to the same post. Prefer its inner
            // card; multiple disjoint cards remain ambiguous.
            const cards = [...roots].filter(root => ![...roots].some(other => other !== root && root.contains(other)));
            if (cards.length !== 1) return null;
            const root = cards[0];
            const values = {};
            for (const [key, title] of Object.entries({likes:'Like', comments:'Comment', reposts:'Repost', shares:'Share'})) {
                const actions = root.querySelectorAll(`svg[title="${title}"]`);
                if (actions.length !== 1) continue;
                const button = actions[0].closest('[role="button"],button');
                if (button && root.contains(button)) values[key] = button.innerText.trim();
            }
            // Only an explicit views control is evidence; never regex caption/body text.
            const viewControls = [...root.querySelectorAll('[role="button"],button,[aria-label]')]
                .filter(node => /\bviews?\b/i.test(node.getAttribute('aria-label') || '')
                    || node.querySelector('svg[title="Views"],svg[title="View"]'));
            const viewCounts = new Set();
            for (const control of viewControls) {
                const text = (control.innerText || '').trim();
                const match = text.match(/^(\d[\d,.]*\s*[KMB]?)\s*(?:views?)?$/i);
                if (match) viewCounts.add(match[1]);
            }
            if (viewCounts.size === 1) values.views = [...viewCounts][0];
            return values;
        }""", {"username": username, "shortcode": post_identity(page.url)[1]})
        if extracted is None:
            result["error"] = "Không xác minh được bài Threads cần quét"
            return result
        result["channel"] = username
        for key, value in extracted.items():
            result["metrics"][key] = parse_action_count(value)
        # The browser may expose the same embedded view count as HTTP even when
        # the UI does not have a dedicated views control.
        embedded = parse_threads_http(await page.content(), url, page.url)
        if embedded["metrics"]["views"] is not None:
            result["metrics"]["views"] = embedded["metrics"]["views"]
        return result
    except Exception as error:
        result["error"] = f"Browser: {error}"
        return result
    finally:
        await page.close()


def column_index(sheet, header):
    wanted = normalize_header(header)
    for cell in sheet[1]:
        if normalize_header(cell.value) == wanted:
            return cell.column
    return None


def ensure_column(sheet, header, *, hidden=False):
    index = column_index(sheet, header)
    if index is None:
        index = sheet.max_column + 1
        sheet.cell(row=1, column=index, value=header)
    if hidden:
        sheet.column_dimensions[openpyxl.utils.get_column_letter(index)].hidden = True
    return index


def write_threads_result(sheet, row_index, result):
    channel = result.get("channel")
    channel_column = ensure_column(sheet, "Tên Kênh")
    if channel:
        sheet.cell(row=row_index, column=channel_column).value = channel
    for key, header in METRICS.items():
        value = result["metrics"].get(key)
        if value is not None:
            sheet.cell(row=row_index, column=ensure_column(sheet, header)).value = value
    ensure_column(sheet, "REPOST")
    status = result_status(result)
    sheet.cell(row=row_index, column=ensure_column(sheet, THREADS_SCAN_STATUS_HEADER, hidden=True)).value = status
    if channel or any(value is not None for value in result["metrics"].values()):
        sheet.cell(row=row_index, column=ensure_column(sheet, "Cập nhật lần cuối")).value = format_display_datetime()
    return status


def collect_threads_rows(workbook, sheet_name="", selected_partners=None):
    selected = {clean_text(value).casefold() for value in selected_partners or [] if clean_text(value)}
    names = [sheet_name] if sheet_name else workbook_data_sheet_names(workbook)
    rows = []
    for name in names:
        if name not in workbook.sheetnames:
            continue
        sheet = workbook[name]
        link_column = worksheet_find_link_column_index(sheet)
        if not link_column:
            continue
        partner_columns = worksheet_partner_column_indexes(sheet)
        for index in range(2, sheet.max_row + 1):
            url = normalize_threads_url(sheet.cell(row=index, column=link_column).value)
            if not is_threads_link(url):
                continue
            partners = worksheet_row_partners(sheet, index, partner_columns) if partner_columns else []
            if selected and not selected.intersection(value.casefold() for value in partners):
                continue
            rows.append({"sheet_name": name, "row": index, "url": url, "partners": partners})
    return rows


def save_workbook_atomic(workbook, path):
    with tempfile.NamedTemporaryFile(suffix=".xlsx", dir=os.path.dirname(os.path.abspath(path)), delete=False) as temp:
        temp_path = temp.name
    try:
        workbook.save(temp_path)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


async def finish_owned_task(task):
    """Do not leave an atomic writer/cleanup running after cancellation."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError()
    return result


async def run_threads_scraper(file_path, websocket_manager=None, worker_count=3, selected_partners=None,
                              sheet_name="", mode="hybrid"):
    workbook = openpyxl.load_workbook(file_path)
    rows = collect_threads_rows(workbook, sheet_name=sheet_name, selected_partners=selected_partners)
    buckets = {}
    for row in rows:
        key = post_identity(row["url"]) or ("share", row["url"].casefold())
        buckets.setdefault(key, {"url": row["url"], "rows": []})["rows"].append(row)
    items = list(buckets.values())
    total = len(items)
    workers = max(1, min(int(worker_count or 3), 5 if mode != "request" else 10))
    if websocket_manager:
        duplicates = [
            {"id": index, "url": item["url"], "locations": [
                {"sheetName": row["sheet_name"], "row": row["row"]} for row in item["rows"]
            ]}
            for index, item in enumerate(items, 1) if len(item["rows"]) > 1
        ]
        await websocket_manager.broadcast_duplicates({"items": duplicates, "duplicateRowCount": len(rows) - total})
        await websocket_manager.broadcast_log(f"Threads: {len(rows)} dòng, {total} URL; chế độ {mode}, {workers} luồng.")
    if not total:
        if websocket_manager:
            await websocket_manager.broadcast_log("Không tìm thấy link Threads trong sheet hoặc đối tác đã chọn.")
            await websocket_manager.broadcast_status({"total": 0, "processed": 0, "success": 0, "hidden": 0, "error": 0, "done": True})
        workbook.close()
        return

    semaphore = asyncio.Semaphore(workers)
    processed = success = hidden = errors = 0
    dirty = False
    completed = False
    started = time.monotonic()

    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(headless=True) if mode != "request" else None
        except BaseException:
            workbook.close()
            raise

        async def save_pending():
            nonlocal dirty
            await asyncio.to_thread(save_workbook_atomic, workbook, file_path)
            dirty = False

        async def check(item):
            async with semaphore:
                url = item["url"]
                request_result = await asyncio.to_thread(fetch_threads_http, url) if mode != "browser" else None
                if mode == "request" or (request_result and result_status(request_result) == "Success"):
                    return item, request_result, "Request"
                browser_result = await fetch_threads_browser(browser, url)
                return item, merge_results(request_result, browser_result) if request_result else browser_result, "Browser"

        tasks = [asyncio.create_task(check(item)) for item in items]
        try:
            for task in asyncio.as_completed(tasks):
                item, result, source = await task
                processed += 1
                status = result_status(result)
                if status == "Success":
                    success += 1
                elif status.startswith("Error:"):
                    errors += 1
                else:
                    hidden += 1
                for row in item["rows"]:
                    write_threads_result(workbook[row["sheet_name"]], row["row"], result)
                    dirty = True
                if websocket_manager:
                    await websocket_manager.broadcast_log(f"Threads {processed}/{total} [{source}] {status}: {item['url']}")
                    primary = item["rows"][0]
                    await websocket_manager.broadcast_data({
                        "id": processed, "url": item["url"], "sheetName": primary["sheet_name"],
                        "channelName": result["channel"], "views": result["metrics"]["views"],
                        "likes": result["metrics"]["likes"], "comments": result["metrics"]["comments"],
                        "saves": result["metrics"]["reposts"], "shares": result["metrics"]["shares"],
                        "status": status, "worker": source, "singlePartner": len(primary["partners"]) == 1,
                    })
                    await websocket_manager.broadcast_status({
                        "total": total, "processed": processed, "success": success,
                        "hidden": hidden, "error": errors, "workers": workers,
                        "mode": "partner" if selected_partners else "full", "done": False,
                        "rate": round(processed / max(time.monotonic() - started, 0.1) * 60, 1),
                    })
                if processed % 5 == 0:
                    await finish_owned_task(asyncio.create_task(save_pending()))
            completed = True
        finally:
            async def cleanup():
                for task in tasks:
                    if not task.done():
                        task.cancel()
                try:
                    await asyncio.gather(*tasks, return_exceptions=True)
                    if dirty:
                        await save_pending()
                finally:
                    try:
                        if browser:
                            await browser.close()
                    finally:
                        workbook.close()

            await finish_owned_task(asyncio.create_task(cleanup()))
    if completed and websocket_manager:
        await websocket_manager.broadcast_status({
            "total": total, "processed": processed, "success": success,
            "hidden": hidden, "error": errors, "workers": workers,
            "mode": "partner" if selected_partners else "full", "done": True,
        })
