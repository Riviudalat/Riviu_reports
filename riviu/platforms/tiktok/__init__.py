"""TikTok scanner: Request / Browser / Hybrid scans of a workbook's TikTok links.

Modules, lowest layer first:
- constants, statuses: shared settings and scan status strings/rules
- parsing: pure HTML/JSON -> metrics, item status, channel names
- throttle: the process-wide HTTP limiter and back-off state
- fetch_html, gone: one TikTok page GET; oEmbed confirmation that a post is gone
- channels: channel-name enrichment (cache, overrides, profile lookups)
- fetch_request, fetch_browser: one link in Request mode / in Chromium, with retries
- lifecycle: owned async work, lazy Chromium launch, bounded browser close and driver stop
- workers: the Request and browser worker loops
- workbook_io, history, progress: workbook reads/writes, scrape history, UI payloads
- runner: run_scraper

Siblings call I/O seams through their module (``fetch_html.fetch_tiktok_html``,
``throttle.session_uses_proxy``), so a test patches a seam once, on the module
that defines it. This package re-exports the public names for existing imports;
patching a name here does not reach the code that uses it.
"""
from riviu.platforms.tiktok.constants import (
    BROWSER_GEOLOCATION,
    BROWSER_LOCALE,
    BROWSER_TIMEZONE,
    COUNT_FIELD_MAP,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_RETRIES,
    DEFAULT_SAVE_EVERY,
    DEFAULT_USER_AGENT,
    DEFAULT_WORKERS,
    LAST_UPDATE_HEADER,
    MAX_WORKERS,
    METRIC_HEADERS,
    METRIC_KEYS,
    USER_AGENTS,
)
from riviu.platforms.tiktok.statuses import (
    is_hidden_stats_status,
    is_request_rate_limited_status,
    is_suspected_gone_status,
    is_transient_network_status,
    should_clear_stale_metrics,
    STATUS_MEDIA_REDIRECT_MISMATCH,
    STATUS_METRICS_UNREADABLE,
    STATUS_TIKTOK_NO_STATS,
    STATUS_TIKTOK_NO_STATS_LEGACY,
    STATUS_TIKTOK_NOT_FOUND_UNCONFIRMED,
    STATUS_TIKTOK_UNAVAILABLE,
    stronger_terminal_result,
    terminal_status_priority,
)
from riviu.platforms.tiktok.parsing import (
    author_name_from_object,
    build_request_url_candidates,
    channel_name_from_item_struct,
    counts_match,
    embedded_item_status_codes,
    EMBEDDED_STATE_SCRIPT_PATTERNS,
    empty_metrics,
    extract_embedded_state_blobs,
    extract_media_id,
    extract_profile_username,
    handle_fallback,
    is_tiktok_error_page,
    is_tiktok_item_not_found,
    item_struct_explicitly_hides_stats,
    item_struct_from_scope_value,
    iter_matching_item_structs,
    iter_nested_dicts,
    json_loads_safe,
    looks_like_error_text,
    media_redirect_mismatch,
    metric_or_none,
    metrics_from_stats_sources,
    no_metrics_status,
    normalize_text,
    parse_channel_name,
    parse_channel_name_from_page,
    parse_count_value,
    parse_counts,
    parse_counts_from_embedded_json,
    parse_counts_from_sigi_state,
    parse_counts_from_universal_data,
    parse_fetched_request_page,
    parse_profile_channel_name,
    profile_text_candidates,
    profile_title_candidate,
    request_html_has_metric_hints,
    REQUEST_METRIC_HINT_PATTERN,
    strip_html_text,
    TIKTOK_ERROR_HINT_WORDS,
    TIKTOK_ITEM_NOT_FOUND_STATUS_CODES,
    TIKTOK_NOT_FOUND_PHRASES,
    UNIVERSAL_DETAIL_KEY_MARKERS,
    universal_detail_scope_metrics,
    username_for_channel_lookup,
    valid_channel_candidate,
    validate_metrics,
)
from riviu.platforms.tiktok.throttle import (
    clamp_int,
    clamp_worker_count,
    configure_request_concurrency,
    MAX_CONCURRENT_REQUESTS,
    NETWORK_FAIL_PAUSE_SECONDS,
    NETWORK_FAIL_STREAK_PAUSE,
    note_network_failure,
    note_network_success,
    note_request_rate_limit,
    request_candidate_limit,
    REQUEST_CANDIDATE_LIMIT_DIRECT,
    REQUEST_CANDIDATE_LIMIT_PROXY,
    request_shell_retry_delay,
    REQUEST_SHELL_RETRY_DELAY,
    REQUEST_SHELL_RETRY_DELAY_PROXY,
    session_uses_proxy,
    wait_if_request_blocked,
)
from riviu.platforms.tiktok.fetch_html import (
    fetch_tiktok_html,
)
from riviu.platforms.tiktok.gone import (
    confirm_tiktok_post_gone,
    TIKTOK_OEMBED_ENDPOINT,
    tiktok_oembed_target,
)
from riviu.platforms.tiktok.channels import (
    author_unique_id_from_post,
    channel_name_for_sheet,
    channel_name_quality,
    enrich_channel_name,
    fetch_profile_channel_name_request,
    is_usable_channel_name,
    seed_channel_cache_from_workbook,
)
from riviu.platforms.tiktok.fetch_request import (
    _scrape_link_request_impl,
    scrape_link_with_retries_request,
)
from riviu.platforms.tiktok.fetch_browser import (
    block_heavy_resources,
    BLOCKED_RESOURCE_TYPES,
    make_browser_context,
    navigate_tiktok_page,
    read_channel_name_from_dom,
    read_profile_channel_name,
    read_profile_channel_name_from_dom,
    scrape_single_link,
    scrape_with_retries,
)
from riviu.platforms.tiktok.lifecycle import (
    browser_cleanup_pending,
    BROWSER_CLOSE_TIMEOUT,
    close_browser_bounded,
    finish_pending_task,
    playwright_session,
    PLAYWRIGHT_STOP_TIMEOUT,
)
from riviu.platforms.tiktok.workers import (
    hybrid_browser_worker_count,
    MAX_BROWSER_FALLBACK_WORKERS,
    request_worker_loop,
    select_fallback_result,
    worker_loop,
)
from riviu.platforms.tiktok.workbook_io import (
    append_sheet_total_rows,
    build_result_sheet,
    build_sheet_contexts,
    CHANNEL_HEADER,
    clear_existing_total_rows,
    collect_rows,
    detect_columns,
    ensure_columns,
    INTERNAL_COLUMN_HEADERS,
    is_tiktok_post_url,
    is_total_row,
    last_occupied_row,
    normalize_selected_partners,
    RESULT_SHEET_HEADERS,
    save_workbook,
    selected_data_sheet_names,
    TIKTOK_SHORT_LINK_HOSTS,
    trusted_resolved_media_id,
    write_result,
)
from riviu.platforms.tiktok.history import (
    append_scrape_history,
    _compute_session_totals,
    read_scrape_history,
    SCRAPE_HISTORY_FILENAME,
    SCRAPE_HISTORY_LIMIT,
    scrape_history_path,
    TOTAL_KEYS,
)
from riviu.platforms.tiktok.progress import (
    build_duplicate_link_payload,
    format_metric_log_plain,
    format_scrape_result_log,
    progress_payload,
    scrape_metric_details,
)
from riviu.platforms.tiktok.runner import (
    build_url_buckets,
    DIRECT_MAX_WORKERS,
    guard_result_media_identity,
    MAX_WORKERS_PER_PROXY,
    run_scraper,
    save_pending_results,
    selected_partner_label,
)
