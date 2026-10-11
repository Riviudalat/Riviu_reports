"""Pure HTML/JSON parsing of TikTok pages: metrics, item status, channel names and URLs."""
import html
import json
import re
import unicodedata

from riviu.workbook_utils import (
    clean_text,
    is_generated_username_channel,
    is_generic_tiktok_channel_name,
    metric_number,
    normalize_tiktok_url,
)
from riviu.platforms.tiktok.constants import COUNT_FIELD_MAP
from riviu.platforms.tiktok.statuses import (
    STATUS_MEDIA_REDIRECT_MISMATCH,
    STATUS_METRICS_UNREADABLE,
    STATUS_TIKTOK_NO_STATS,
    STATUS_TIKTOK_NOT_FOUND_UNCONFIRMED,
)


UNIVERSAL_DETAIL_KEY_MARKERS = (
    "video-detail",
    "photo-detail",
    "photomode",
    "image-detail",
    "reflow.video.detail",
    "reflow.photo.detail",
)
EMBEDDED_STATE_SCRIPT_PATTERNS = (
    r"<script\b[^>]*\bid\s*=\s*['\"]__UNIVERSAL_DATA_FOR_REHYDRATION__['\"][^>]*>(.*?)</script\s*>",
    r"<script\b[^>]*\bid\s*=\s*['\"]SIGI_STATE['\"][^>]*>(.*?)</script\s*>",
    r"<script\b[^>]*\bid\s*=\s*['\"]api-data['\"][^>]*>(.*?)</script\s*>",
)


REQUEST_METRIC_HINT_PATTERN = re.compile(r'"(?:playCount|diggCount)"\s*:\s*"?(\d+)"?')


def normalize_text(value):
    text = str(value or "").strip().upper()
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text)


def extract_media_id(url):
    text = clean_text(url)
    for pattern in (r"/video/(\d+)", r"/photo/(\d+)"):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def parse_count_value(raw):
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        number = float(raw)
        return str(int(number))

    text = clean_text(str(raw)).replace(",", "").replace(" ", "")
    if not text:
        return None
    if re.fullmatch(r"\d+", text):
        return text

    match = re.fullmatch(r"(\d+(?:\.\d+)?)([KMB])", text, flags=re.IGNORECASE)
    if match:
        number = float(match.group(1))
        suffix = match.group(2).upper()
        multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[suffix]
        return str(int(number * multiplier))

    if re.fullmatch(r"\d+\.0+", text):
        return str(int(float(text)))
    return None


def empty_metrics():
    """Metrics for a scan that confirmed nothing: every value unknown (blank)."""
    return {metric: None for metric in COUNT_FIELD_MAP}


def metric_or_none(value):
    """A confirmed count as int, or None when the value is blank/unknown/not a number."""
    if value is None or isinstance(value, bool):
        return None
    text = clean_text(value)
    if not text:
        return None
    number = metric_number(value)
    if number == 0 and not re.fullmatch(r"0+(?:[.,]0+)?", text.replace(" ", "")):
        return None
    return number


def metrics_from_stats_sources(*sources):
    """Read metrics from stats/statsV2 blobs and take the max per field."""
    field_values = {metric: [] for metric in COUNT_FIELD_MAP}

    def collect_from_dict(obj):
        if not isinstance(obj, dict):
            return
        blobs = []
        if isinstance(obj.get("stats"), dict):
            blobs.append(obj["stats"])
        if isinstance(obj.get("statsV2"), dict):
            blobs.append(obj["statsV2"])
        if not blobs:
            blobs.append(obj)
        for blob in blobs:
            if not isinstance(blob, dict):
                continue
            for metric, field in COUNT_FIELD_MAP.items():
                parsed = parse_count_value(blob.get(field))
                if parsed is not None:
                    field_values[metric].append(int(parsed))

    for source in sources:
        collect_from_dict(source)

    metrics = {}
    for metric in COUNT_FIELD_MAP:
        if not field_values[metric]:
            # Some items omit collectCount: Saves is unknown (blank), not a confirmed 0.
            if metric == "Saves":
                metrics[metric] = None
                continue
            return None
        metrics[metric] = str(max(field_values[metric]))
    return metrics


def _item_id(obj):
    return clean_text(obj.get("id") or obj.get("awemeId") or obj.get("itemId"))


def _is_item_detail_key(key):
    """True for an embedded-state scope key that holds the post's own item detail."""
    key_norm = normalize_text(key).casefold()
    return any(marker in key_norm for marker in UNIVERSAL_DETAIL_KEY_MARKERS)


def handle_fallback(profile_username):
    """The @handle shown when no nickname is parsed; blank for auto-generated user ids."""
    if profile_username and not is_generated_username_channel(profile_username):
        return f"@{profile_username.lstrip('@')}"
    return ""


def extract_embedded_state_blobs(content):
    blobs = []
    for pattern in EMBEDDED_STATE_SCRIPT_PATTERNS:
        for match in re.finditer(pattern, content or "", flags=re.DOTALL | re.IGNORECASE):
            data = json_loads_safe(match.group(1))
            if data:
                blobs.append(data)
    return blobs


def universal_detail_scope_metrics(scope, media_id=""):
    if not isinstance(scope, dict):
        return None
    for key, value in scope.items():
        if not _is_item_detail_key(key):
            continue
        if not isinstance(value, dict):
            continue
        item_struct = item_struct_from_scope_value(value)
        if not isinstance(item_struct, dict):
            continue
        item_id = clean_text(item_struct.get("id") or item_struct.get("awemeId"))
        if media_id and item_id != media_id:
            continue
        metrics = metrics_from_stats_sources(item_struct)
        if metrics:
            return metrics
    return None


def parse_counts_from_universal_data(data, media_id=""):
    if not isinstance(data, dict):
        return None

    scope = data.get("__DEFAULT_SCOPE__")
    return universal_detail_scope_metrics(scope, media_id=media_id) or None


def parse_counts_from_sigi_state(data, media_id=""):
    if not isinstance(data, dict):
        return None

    item_module = data.get("ItemModule")
    if isinstance(item_module, dict) and media_id:
        for module_key in (media_id, str(media_id)):
            item = item_module.get(module_key)
            if isinstance(item, dict):
                item_id = _item_id(item)
                if item_id and item_id != media_id:
                    continue
                metrics = metrics_from_stats_sources(item)
                if metrics:
                    return metrics

    if media_id:
        for obj in iter_nested_dicts(data):
            if not isinstance(obj, dict):
                continue
            item_id = _item_id(obj)
            if item_id != media_id:
                continue
            metrics = metrics_from_stats_sources(obj)
            if metrics:
                return metrics
    return None


def parse_counts_from_embedded_json(content, media_id=""):
    for blob in extract_embedded_state_blobs(content):
        metrics = parse_counts_from_universal_data(blob, media_id=media_id)
        if metrics:
            return metrics, True
        metrics = parse_counts_from_sigi_state(blob, media_id=media_id)
        if metrics:
            return metrics, True
    return None, False


def validate_metrics(data):
    """Views must be known; an unknown (None) engagement metric is skipped, not treated as 0."""
    if not data:
        return False

    values = {}
    for metric in COUNT_FIELD_MAP:
        raw = data.get(metric)
        if raw is None:
            values[metric] = None
            continue
        parsed = parse_count_value(raw)
        if parsed is None:
            return False
        values[metric] = int(parsed)

    views = values["Views"]
    if views is None or views < 0:
        return False

    engagement = [values[metric] for metric in ("Likes", "Comments", "Saves", "Shares") if values[metric] is not None]
    if views == 0 and sum(engagement) > 0:
        return False
    return all(value >= 0 and not (views > 0 and value > views * 5) for value in engagement)


def counts_match(left, right):
    if not left or not right:
        return False
    return all(metric_or_none(left.get(metric)) == metric_or_none(right.get(metric)) for metric in COUNT_FIELD_MAP)


def parse_counts(content, media_id):
    """Read metrics only from the embedded item whose id is media_id."""
    if media_id:
        metrics, found = parse_counts_from_embedded_json(content, media_id=media_id)
        if found and metrics and validate_metrics(metrics):
            return metrics, True
    return empty_metrics(), False


def json_loads_safe(raw_value):
    if not isinstance(raw_value, str):
        return None
    candidates = [raw_value]
    unescaped = html.unescape(raw_value)
    if unescaped != raw_value:
        candidates.append(unescaped)
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def iter_nested_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_nested_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_nested_dicts(child)


def author_name_from_object(obj, profile_username="", *, require_username_match=True):
    if not isinstance(obj, dict):
        return ""

    unique_id = clean_text(obj.get("uniqueId") or obj.get("unique_id") or obj.get("uniqueID"))
    if require_username_match:
        if not profile_username:
            return ""
        if normalize_text(unique_id.lstrip("@")) != normalize_text(profile_username.lstrip("@")):
            return ""
        handle = profile_username
    else:
        if not unique_id:
            return ""
        handle = unique_id

    profile_url = f"https://www.tiktok.com/@{handle.lstrip('@')}"
    for key in ("nickname", "nickName", "authorName", "name", "displayName"):
        candidate = clean_text(obj.get(key))
        if (
            candidate
            and not is_generated_username_channel(candidate, profile_url)
            and not is_generic_tiktok_channel_name(candidate)
        ):
            validated = valid_channel_candidate(candidate, handle)
            if validated:
                return validated
    return ""


def iter_matching_item_structs(content, media_id=""):
    media_id = clean_text(media_id)
    if not media_id:
        return

    for blob in extract_embedded_state_blobs(content):
        scope = blob.get("__DEFAULT_SCOPE__")
        if isinstance(scope, dict):
            for value in scope.values():
                item_struct = item_struct_from_scope_value(value)
                if not isinstance(item_struct, dict):
                    continue
                item_id = clean_text(item_struct.get("id") or item_struct.get("awemeId"))
                if item_id == media_id:
                    yield item_struct

        item_module = blob.get("ItemModule")
        if isinstance(item_module, dict):
            item = item_module.get(media_id) or item_module.get(str(media_id))
            if isinstance(item, dict):
                item_id = _item_id(item)
                if not item_id or item_id == media_id:
                    yield item

        for obj in iter_nested_dicts(blob):
            if not isinstance(obj, dict):
                continue
            item_id = _item_id(obj)
            if item_id != media_id:
                continue
            if isinstance(obj.get("author"), dict) or "stats" in obj or "statsV2" in obj:
                yield obj


def channel_name_from_item_struct(item_struct):
    if not isinstance(item_struct, dict):
        return ""
    author = item_struct.get("author")
    if isinstance(author, dict):
        candidate = author_name_from_object(author, require_username_match=False)
        if candidate:
            return candidate
    return author_name_from_object(item_struct, require_username_match=False)


def parse_channel_name_from_page(content, profile_username="", media_id=""):
    if media_id:
        for item_struct in iter_matching_item_structs(content, media_id):
            candidate = channel_name_from_item_struct(item_struct)
            if candidate:
                return candidate
    return parse_channel_name(content, profile_username)


TIKTOK_NOT_FOUND_PHRASES = (
    "couldn't find this account",
    "couldnt find this account",
    "couldn't find this video",
    "video currently unavailable",
    "this video is currently unavailable",
    "page not available",
    "page unavailable",
    "video unavailable",
    "không tìm thấy tài khoản này",
    "không tìm thấy video này",
    "khong tim thay tai khoan nay",
    "khong tim thay video nay",
    "video này hiện không khả dụng",
    "trang không khả dụng",
    "trang này không khả dụng",
    "this post isn't available",
    "this post is not available",
    "unable to find",
)

# TikTok answers HTTP 200 for deleted/nonexistent posts; the only signal is the
# item status code in the embedded state. Only "item not found" means the post
# is gone; private/restricted codes are deliberately not treated as deleted.
TIKTOK_ITEM_NOT_FOUND_STATUS_CODES = frozenset({10204})


def _status_code_value(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = clean_text(value)
    return int(text) if text.isdigit() else None


def _detail_status_codes(container):
    if not isinstance(container, dict):
        return []
    return [
        code
        for code in (
            _status_code_value(container.get(key))
            for key in ("statusCode", "status_code")
        )
        if code is not None
    ]


def embedded_item_status_codes(content):
    """Status codes TikTok reports for the requested post in its embedded state."""
    codes = []
    for blob in extract_embedded_state_blobs(content):
        if not isinstance(blob, dict):
            continue
        # <script id="api-data">{"videoDetail": {"statusCode": ...}}
        for key in ("videoDetail", "photoDetail"):
            codes.extend(_detail_status_codes(blob.get(key)))
        # __UNIVERSAL_DATA_FOR_REHYDRATION__ -> __DEFAULT_SCOPE__["webapp.video-detail"]
        scope = blob.get("__DEFAULT_SCOPE__")
        if isinstance(scope, dict):
            for key, value in scope.items():
                if _is_item_detail_key(key):
                    codes.extend(_detail_status_codes(value))
    return codes


def is_tiktok_item_not_found(content):
    return any(code in TIKTOK_ITEM_NOT_FOUND_STATUS_CODES for code in embedded_item_status_codes(content))

TIKTOK_ERROR_HINT_WORDS = (
    "couldn't",
    "couldnt",
    "unavailable",
    "trending creators",
    "discover more",
    "try searching",
    "log in",
    "back to home",
    "go home",
)


def is_tiktok_error_page(content):
    if is_tiktok_item_not_found(content):
        return True
    visible_html =re.sub(r"<script\b[^>]*>.*?</script>", " ", content or "", flags=re.DOTALL | re.IGNORECASE)
    visible_html = re.sub(r"<style\b[^>]*>.*?</style>", " ", visible_html, flags=re.DOTALL | re.IGNORECASE)
    surfaces = []
    heading_pattern = re.compile(
        r"<(?P<tag>title|h[1-3])\b[^>]*>(?P<body>.*?)</(?P=tag)>",
        flags=re.DOTALL | re.IGNORECASE,
    )
    for match in heading_pattern.finditer(visible_html):
        surfaces.append(strip_html_text(match.group("body")).casefold())

    alert_pattern = re.compile(
        r"<(?P<tag>[a-z0-9]+)\b(?P<attrs>[^>]*(?:role=[\"']alert[\"']|(?:class|id|data-e2e)=[\"'][^\"']*(?:error|not-found|unavailable|PMsgTitle)[^\"']*[\"'])[^>]*)>"
        r"(?P<body>.*?)</(?P=tag)>",
        flags=re.DOTALL | re.IGNORECASE,
    )
    for match in alert_pattern.finditer(visible_html):
        surfaces.append(strip_html_text(match.group("body")).casefold())

    return any(
        phrase in surface
        for surface in surfaces
        for phrase in TIKTOK_NOT_FOUND_PHRASES
    )


def looks_like_error_text(value):
    text = str(value or "").lower().replace("\u2019", "'").replace("\u2018", "'")
    if not text:
        return False
    if any(phrase in text for phrase in TIKTOK_NOT_FOUND_PHRASES):
        return True
    if any(word in text for word in TIKTOK_ERROR_HINT_WORDS):
        return True
    return False


def valid_channel_candidate(value, profile_username):
    candidate = clean_text(value)
    if not candidate:
        return ""

    profile_url = f"https://www.tiktok.com/@{profile_username}"
    normalized_candidate = normalize_text(candidate.lstrip("@"))
    normalized_username = normalize_text(profile_username.lstrip("@"))
    blocked_labels = {
        "FOLLOW",
        "MESSAGE",
        "FOLLOWING",
        "FOLLOWERS",
        "LIKES",
    }
    if (
        candidate.startswith("@")
        or normalized_candidate == normalized_username
        or normalized_candidate in blocked_labels
        or "FOLLOWERS" in normalized_candidate
        or "FOLLOWING" in normalized_candidate
        or len(candidate) > 80
        or looks_like_error_text(candidate)
        or is_generated_username_channel(candidate, profile_url)
        or is_generic_tiktok_channel_name(candidate)
    ):
        return ""
    return candidate


def parse_channel_name(content, profile_username):
    if not profile_username:
        return ""

    script_patterns = [
        r'<script[^>]+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
        r'<script[^>]+id="SIGI_STATE"[^>]*>(.*?)</script>',
    ]
    for pattern in script_patterns:
        match = re.search(pattern, content, flags=re.DOTALL)
        if not match:
            continue
        data = json_loads_safe(match.group(1))
        for obj in iter_nested_dicts(data):
            candidate = author_name_from_object(obj, profile_username)
            if candidate:
                return candidate

    # Photo posts often expose only the embedded "author": {...} object
    author_blocks_quoted = re.finditer(
        r'"author"\s*:\s*(\{[^{}]*"uniqueId"\s*:\s*"([^"]+)"[^{}]*\})',
        content,
    )
    for block_match in author_blocks_quoted:
        if normalize_text(block_match.group(2).lstrip("@")) != normalize_text(profile_username.lstrip("@")):
            continue
        data = json_loads_safe(block_match.group(1))
        candidate = author_name_from_object(data, profile_username)
        if candidate:
            return candidate

    author_blocks = re.finditer(r'\{[^{}]*"uniqueId"\s*:\s*"([^"]+)"[^{}]*\}', content)
    for block_match in author_blocks:
        if normalize_text(block_match.group(1).lstrip("@")) != normalize_text(profile_username.lstrip("@")):
            continue
        data = json_loads_safe(block_match.group(0))
        candidate = author_name_from_object(data, profile_username)
        if candidate:
            return candidate
    return ""


def strip_html_text(value):
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return clean_text(re.sub(r"\s+", " ", text))


def profile_title_candidate(raw_title, profile_username):
    title = strip_html_text(raw_title)
    if not title:
        return ""

    candidate = re.split(
        r"\s+\|\s+TikTok|\s+-\s+TikTok|\s+on\s+TikTok",
        title,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    candidate = re.sub(
        rf"\s*\(@?{re.escape(profile_username)}\)\s*$",
        "",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        rf"\s*@{re.escape(profile_username)}\s*$",
        "",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = clean_text(candidate.strip(" -|"))
    return valid_channel_candidate(candidate, profile_username)


def parse_profile_channel_name(content, profile_username, media_id=""):
    candidate = parse_channel_name_from_page(content, profile_username, media_id=media_id)
    if candidate:
        return candidate

    for tag in re.findall(r"<meta\b[^>]*>", content or "", flags=re.IGNORECASE):
        if not re.search(r'(?:property|name)=["\'](?:og:)?title["\']', tag, flags=re.IGNORECASE):
            continue
        match = re.search(r'content=["\']([^"\']+)["\']', tag, flags=re.IGNORECASE)
        if not match:
            continue
        candidate = profile_title_candidate(match.group(1), profile_username)
        if candidate:
            return candidate

    match = re.search(r"<title[^>]*>(.*?)</title>", content or "", flags=re.DOTALL | re.IGNORECASE)
    if match:
        candidate = profile_title_candidate(match.group(1), profile_username)
        if candidate:
            return candidate
    return ""


def extract_profile_username(url):
    match = re.search(r"tiktok\.com/@([^/?]+)", url or "", re.IGNORECASE)
    return match.group(1).strip() if match else ""


def username_for_channel_lookup(source_url, resolved_url=""):
    for candidate in (resolved_url, source_url):
        normalized = normalize_tiktok_url(clean_text(candidate))
        if not normalized:
            continue
        username = extract_profile_username(normalized)
        if username:
            return username
    return ""


def profile_text_candidates(text):
    for line in str(text or "").splitlines():
        line = clean_text(line)
        if line:
            yield line


def item_struct_from_scope_value(value):
    if not isinstance(value, dict):
        return None
    item_info = value.get("itemInfo")
    if isinstance(item_info, dict):
        item_struct = item_info.get("itemStruct")
        if isinstance(item_struct, dict):
            return item_struct
    item_struct = value.get("itemStruct")
    if isinstance(item_struct, dict):
        return item_struct
    extra_info = value.get("extra_info")
    if isinstance(extra_info, dict):
        nested = item_struct_from_scope_value(extra_info)
        if nested:
            return nested
        nested = extra_info.get("itemStruct")
        if isinstance(nested, dict):
            return nested
    return None


def request_html_has_metric_hints(content):
    return bool(REQUEST_METRIC_HINT_PATTERN.search(content or ""))


def item_struct_explicitly_hides_stats(item_struct):
    hidden_when_true = {
        "hidestats",
        "hideviewcount",
        "isstatshidden",
        "statshidden",
        "viewcounthidden",
    }
    hidden_when_false = {
        "isstatsvisible",
        "statisticsvisible",
        "statsvisible",
        "viewcountvisible",
    }
    visibility_fields = {"statisticsvisibility", "statsvisibility"}

    for obj in iter_nested_dicts(item_struct):
        for key, value in obj.items():
            normalized_key = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            if normalized_key not in hidden_when_true | hidden_when_false | visibility_fields:
                continue
            normalized_value = clean_text(value).casefold()
            if normalized_key in hidden_when_true and (
                value is True or normalized_value in {"1", "true", "hidden"}
            ):
                return True
            if normalized_key in hidden_when_false and (
                value is False or normalized_value in {"0", "false", "hidden"}
            ):
                return True
            if normalized_key in visibility_fields and normalized_value in {"hidden", "private", "disabled"}:
                return True
    return False


def no_metrics_status(content, media_id=""):
    """Only classify hidden stats when the matching item says so explicitly."""
    if media_id:
        for item_struct in iter_matching_item_structs(content, media_id):
            if item_struct_explicitly_hides_stats(item_struct):
                return STATUS_TIKTOK_NO_STATS
    return STATUS_METRICS_UNREADABLE


def media_redirect_mismatch(source_url, final_url):
    source_media_id = extract_media_id(source_url)
    final_media_id = extract_media_id(final_url)
    return bool(source_media_id and final_media_id != source_media_id)


def build_request_url_candidates(url):
    url = normalize_tiktok_url(url)
    candidates = []
    seen = set()

    def add(candidate):
        normalized = normalize_tiktok_url(candidate) if candidate else ""
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        candidates.append(normalized)

    add(url)
    base = url.split("?")[0]
    media_id = extract_media_id(url)
    profile_username = extract_profile_username(url)
    if media_id and "/photo/" in url.lower():
        if "_r=1" not in url.lower():
            add(f"{base}?_r=1")
        if profile_username:
            add(f"https://www.tiktok.com/@{profile_username}/video/{media_id}")
    add(base)

    return candidates


def parse_fetched_request_page(source_url, final_url, content):
    empty = empty_metrics()
    source_media_id = extract_media_id(source_url)
    final_media_id = extract_media_id(final_url)
    if media_redirect_mismatch(source_url, final_url):
        return empty, "", STATUS_MEDIA_REDIRECT_MISMATCH

    media_id = source_media_id or final_media_id
    profile_username = extract_profile_username(source_url) or extract_profile_username(final_url)
    if not media_id:
        return empty, handle_fallback(profile_username), STATUS_METRICS_UNREADABLE

    metrics, found = parse_counts(content, media_id=media_id)
    channel_name = parse_profile_channel_name(content, profile_username, media_id=media_id)
    channel_name = channel_name or handle_fallback(profile_username)
    if found:
        return metrics, channel_name, "Success"
    if is_tiktok_error_page(content):
        return empty, "", STATUS_TIKTOK_NOT_FOUND_UNCONFIRMED
    return empty, channel_name, no_metrics_status(content, media_id=media_id)
