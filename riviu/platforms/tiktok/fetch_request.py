"""Request (HTTP) mode: scrape one link over its URL candidates, with retries and gone confirmation."""
import random
import time
import urllib.error

from riviu.proxy_utils import release_thread_proxy
from riviu.workbook_utils import normalize_tiktok_url
from riviu.platforms.tiktok import channels, fetch_html, gone, throttle
from riviu.platforms.tiktok.constants import DEFAULT_REQUEST_TIMEOUT, DEFAULT_RETRIES
from riviu.platforms.tiktok.parsing import (
    build_request_url_candidates,
    empty_metrics,
    media_redirect_mismatch,
    parse_fetched_request_page,
    request_html_has_metric_hints,
)
from riviu.platforms.tiktok.statuses import (
    STATUS_METRICS_UNREADABLE,
    STATUS_TIKTOK_UNAVAILABLE,
    is_request_rate_limited_status,
    is_suspected_gone_status,
    is_transient_network_status,
    should_clear_stale_metrics,
    stronger_terminal_result,
)


class _RequestScrapeOutcome:
    """Running best/last result across the HTTP candidates of one link."""

    def __init__(self):
        self.data = empty_metrics()
        self.channel = ""
        self.status = STATUS_METRICS_UNREADABLE
        self.resolved_url = ""
        self.saw_metric_hints = False
        self.best_success = None
        self.best_terminal = None

    def note_terminal(self, data, channel, status):
        if should_clear_stale_metrics(status) or is_suspected_gone_status(status):
            self.best_terminal = stronger_terminal_result(
                self.best_terminal, (data, channel, status, self.resolved_url)
            )

    def note_fetch_error(self, error):
        if isinstance(error, urllib.error.HTTPError):
            self.status = f"Error: HTTP {error.code}"
            self.note_terminal(self.data, self.channel, self.status)
            if error.code in (403, 429):
                throttle.note_request_rate_limit(error.code)
                release_thread_proxy()
            return
        self.status = f"Error: {error}"
        if is_transient_network_status(self.status):
            throttle.note_network_failure()

    def note_page(self, source_url, final_url, content):
        """Parse one fetched page; return True when it is a final success."""
        throttle.note_network_success()
        if final_url and not media_redirect_mismatch(source_url, final_url):
            self.resolved_url = final_url
        if request_html_has_metric_hints(content):
            self.saw_metric_hints = True
        data, channel_name, status = parse_fetched_request_page(source_url, final_url, content)
        self.data, self.channel, self.status = data, channel_name, status
        self.note_terminal(data, channel_name, status)
        if status != "Success":
            return False
        if self.best_success is None or (channel_name and not self.best_success[1]):
            self.best_success = (data, channel_name, status, final_url)
        return bool(channel_name)

    def result(self):
        chosen = self.best_success or self.best_terminal
        if chosen:
            data, channel_name, status, final_url = chosen
            return data, channel_name, status, self.saw_metric_hints, final_url
        return self.data, self.channel, self.status, self.saw_metric_hints, self.resolved_url


def _scrape_link_request_impl(url, timeout=DEFAULT_REQUEST_TIMEOUT):
    url = normalize_tiktok_url(url)
    outcome = _RequestScrapeOutcome()
    try:
        candidate_limit = throttle.request_candidate_limit()
        candidates = build_request_url_candidates(url)[:candidate_limit]

        for candidate in candidates:
            final_url = content = None
            # A page that hints at metrics but did not parse is often a shell: re-fetch once.
            for attempt in range(2):
                if attempt:
                    if not request_html_has_metric_hints(content):
                        break
                    time.sleep(throttle.request_shell_retry_delay())
                try:
                    final_url, content = fetch_html.fetch_tiktok_html(candidate, timeout=timeout)
                except Exception as error:
                    outcome.note_fetch_error(error)
                    final_url = None
                    break
                if outcome.note_page(url, final_url, content):
                    return outcome.result()
            if final_url is None:
                continue

            redirect_candidate = normalize_tiktok_url(final_url.split("?")[0])
            if (
                redirect_candidate
                and not media_redirect_mismatch(url, final_url)
                and redirect_candidate not in candidates
                and len(candidates) < candidate_limit
            ):
                candidates.append(redirect_candidate)

        return outcome.result()
    except Exception as error:
        status = f"Error: HTTP {error.code}" if isinstance(error, urllib.error.HTTPError) else f"Error: {error}"
        return outcome.data, outcome.channel, status, outcome.saw_metric_hints, outcome.resolved_url


def scrape_link_with_retries_request(
    url,
    retries=DEFAULT_RETRIES,
    timeout=DEFAULT_REQUEST_TIMEOUT,
    channel_cache=None,
    channel_overrides=None,
    profile_lookup_attempted=None,
    cache_lock=None,
    existing_channel="",
):
    last_data = empty_metrics()
    last_status = "Error: Chưa chạy"
    last_channel = ""
    last_resolved_url = ""

    attempts_used = 0
    confirmed_gone = None
    for attempt in range(retries + 1):
        if attempt > 0:
            if is_request_rate_limited_status(last_status):
                if throttle.session_uses_proxy():
                    time.sleep(0.4 + attempt * 0.35 + random.uniform(0.1, 0.25))
                else:
                    time.sleep(2.5 + attempt * 2.0 + random.uniform(0.5, 2.0))
            else:
                time.sleep(1.0 + attempt * 0.75 + random.uniform(0.2, 0.8))
        data, channel_name, status, saw_metric_hints, resolved_url = _scrape_link_request_impl(
            url, timeout=timeout
        )
        attempts_used = attempt + 1
        if resolved_url:
            last_resolved_url = resolved_url
        channel_name = channels.enrich_channel_name(
            url,
            channel_name,
            resolved_url=resolved_url if status == "Success" else "",
            existing_channel=existing_channel,
            channel_cache=channel_cache,
            channel_overrides=channel_overrides,
            profile_lookup_attempted=profile_lookup_attempted,
            cache_lock=cache_lock,
            timeout=timeout,
        )
        last_data, last_channel, last_status = data, channel_name, status
        if status == "Success":
            return data, channel_name, status, attempts_used, last_resolved_url
        if is_suspected_gone_status(status):
            if confirmed_gone is None:
                confirmed_gone = gone.confirm_tiktok_post_gone(url, last_resolved_url, timeout=min(timeout, 15))
            if confirmed_gone:
                return data, channel_name, STATUS_TIKTOK_UNAVAILABLE, attempts_used, last_resolved_url
            # Live (or unknown): the page answer was transient; spend the retry budget.
            continue
        if should_clear_stale_metrics(status):
            return data, channel_name, status, attempts_used, last_resolved_url
        if is_request_rate_limited_status(status):
            release_thread_proxy()
            continue
        if is_transient_network_status(status):
            continue
        if status == STATUS_METRICS_UNREADABLE:
            continue
        if not saw_metric_hints:
            break

    return last_data, last_channel, last_status, attempts_used or 1, last_resolved_url
