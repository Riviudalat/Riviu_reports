"""Progress, log and duplicate-link payloads broadcast to the UI during a scan."""
import time

from riviu.workbook_utils import clean_text
from riviu.platforms.tiktok.constants import METRIC_KEYS
from riviu.platforms.tiktok.parsing import metric_or_none
from riviu.platforms.tiktok.statuses import is_hidden_stats_status


def build_duplicate_link_payload(bucket_order):
    """Build the duplicate-link summary shown in the live UI."""
    items = []
    duplicate_row_count = 0

    for bucket in bucket_order:
        rows = bucket.get("rows", [])
        if len(rows) < 2:
            continue

        locations = [
            {
                "sheetName": row.get("sheet_name", ""),
                "row": row.get("row", ""),
            }
            for row in rows
        ]
        duplicate_row_count += len(locations) - 1
        items.append({
            "id": len(items) + 1,
            "url": bucket.get("url", ""),
            "locations": locations,
        })

    return {
        "duplicateUrlCount": len(items),
        "duplicateRowCount": duplicate_row_count,
        "items": items,
    }


def scrape_metric_details(data):
    """Log/broadcast metrics: int for confirmed counts, None for unknown."""
    data = data or {}
    return {metric_key: metric_or_none(data.get(data_key)) for metric_key, data_key in METRIC_KEYS.items()}


def format_metric_log_plain(data):
    metrics = {key: "—" if value is None else value for key, value in scrape_metric_details(data).items()}
    return (
        f"Lượt xem {metrics['views']} • Tim {metrics['likes']} • Bình luận {metrics['comments']} • "
        f"Lưu {metrics['saves']} • Chia sẻ {metrics['shares']}"
    )


def format_scrape_result_log(result, processed, total):
    url = clean_text(result.get("url", ""))
    worker = result.get("worker", "?")
    status = clean_text(result.get("status", ""))
    attempts = result.get("attempts", 0)
    elapsed = float(result.get("elapsed") or 0)
    data = result.get("data") or {}
    rows = result.get("rows") or []
    channel = clean_text(result.get("channel_name", "")) or "—"
    row_refs = ", ".join(
        f'{clean_text(row.get("sheet_name", ""))}#{row.get("row", "")}'
        for row in rows[:6]
    )
    if len(rows) > 6:
        row_refs = f"{row_refs} +{len(rows) - 6}"

    base = {
        "processed": processed,
        "total": total,
        "worker": worker,
        "elapsed": round(elapsed, 1),
        "rows": row_refs or "—",
        "url": url,
    }

    if status == "Success":
        metrics = scrape_metric_details(data)
        message = (
            f"[{processed}/{total}] OK • {channel} • {format_metric_log_plain(data)} • "
            f"{elapsed:.1f}s • luồng {worker} • dòng {row_refs or '—'}"
        )
        details = {
            "kind": "scrape_ok",
            **base,
            "channel": channel,
            "metrics": metrics,
        }
        return message, "OK", details

    if is_hidden_stats_status(status):
        message = (
            f"[{processed}/{total}] Ẩn số liệu • {elapsed:.1f}s • luồng {worker} • "
            f"TikTok không trả lượt xem • dòng {row_refs or '—'}"
        )
        details = {
            "kind": "scrape_hidden",
            **base,
            "attempts": attempts,
            "status": status,
        }
        return message, "WARN", details

    message = (
        f"[{processed}/{total}] Lỗi • {elapsed:.1f}s • luồng {worker} • "
        f"thử {attempts} lần • {status} • dòng {row_refs or '—'}"
    )
    details = {
        "kind": "scrape_error",
        **base,
        "attempts": attempts,
        "status": status,
    }
    return message, "ERROR", details


def progress_payload(total, processed, success_count, error_count, worker_count, started_at, done=False, mode="full", partner="", phase="scanning", uses_browser=False, hidden_count=0):
    elapsed = max(time.perf_counter() - started_at, 0.001)
    rate = processed / elapsed * 60 if processed else 0
    remaining = max(total - processed, 0)
    eta_seconds = int((remaining / rate) * 60) if rate > 0 else None
    return {
        "total": total,
        "processed": processed,
        "success": success_count,
        "error": error_count,
        "hidden": hidden_count,
        "workers": worker_count,
        "rate": round(rate, 1),
        "etaSeconds": eta_seconds,
        "done": done,
        "mode": mode,
        "partner": partner,
        "phase": phase,
        "usesBrowser": uses_browser,
    }
