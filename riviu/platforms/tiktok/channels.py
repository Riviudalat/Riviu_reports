"""Channel names for scanned posts: overrides, shared cache, profile lookups and the sheet value."""
from riviu.workbook_utils import (
    clean_text,
    is_generated_username_channel,
    is_generic_tiktok_channel_name,
    is_numeric_channel_garbage,
    normalize_tiktok_url,
    resolve_channel_name,
)
from riviu.platforms.tiktok import fetch_html, throttle
from riviu.platforms.tiktok.constants import DEFAULT_REQUEST_TIMEOUT
from riviu.platforms.tiktok.parsing import (
    extract_media_id,
    extract_profile_username,
    iter_matching_item_structs,
    parse_profile_channel_name,
    username_for_channel_lookup,
)


def fetch_profile_channel_name_request(profile_username, timeout=DEFAULT_REQUEST_TIMEOUT):
    username = clean_text(profile_username).lstrip("@")
    if not username:
        return ""
    profile_url = f"https://www.tiktok.com/@{username}"
    try:
        _final_url, content = fetch_html.fetch_tiktok_html(profile_url, timeout=timeout)
        return parse_profile_channel_name(content, username)
    except Exception:
        return ""


def is_usable_channel_name(name, url=""):
    text = clean_text(name)
    if not text or text == "Lỗi" or is_numeric_channel_garbage(text):
        return False
    if is_generated_username_channel(text, url) or is_generic_tiktok_channel_name(text):
        return False
    return True


def author_unique_id_from_post(url, resolved_url="", timeout=DEFAULT_REQUEST_TIMEOUT):
    media_id = extract_media_id(resolved_url) or extract_media_id(url)
    if not media_id:
        return ""
    for candidate_url in (resolved_url, url):
        normalized = normalize_tiktok_url(clean_text(candidate_url))
        if not normalized:
            continue
        try:
            _final_url, content = fetch_html.fetch_tiktok_html(normalized, timeout=timeout)
        except Exception:
            continue
        for item_struct in iter_matching_item_structs(content, media_id):
            author = item_struct.get("author")
            if isinstance(author, dict):
                unique_id = clean_text(author.get("uniqueId") or author.get("unique_id"))
                if unique_id:
                    return unique_id
    return ""


def enrich_channel_name(
    url,
    channel_name,
    *,
    resolved_url="",
    existing_channel="",
    channel_cache=None,
    channel_overrides=None,
    profile_lookup_attempted=None,
    cache_lock=None,
    timeout=DEFAULT_REQUEST_TIMEOUT,
    allow_network=True,
):
    """Best channel name for a scraped post.

    Blocking when allow_network is True (profile page fetch), so async code must
    run it in an executor. With allow_network=False it only uses the parsed
    name, overrides, cache, the existing sheet value and the @handle fallback.
    Pass shared (possibly empty) cache/set objects to dedupe lookups across rows.
    """
    source_url = normalize_tiktok_url(url)
    lookup_url = normalize_tiktok_url(resolved_url or url)
    quality_url = lookup_url or source_url
    username = username_for_channel_lookup(source_url, lookup_url)
    overrides = channel_overrides or {}
    parsed = clean_text(channel_name)
    key = username.casefold()

    def locked(action):
        if cache_lock:
            with cache_lock:
                return action()
        return action()

    def read_cache():
        if channel_cache is None or not username:
            return ""
        return clean_text(locked(lambda: channel_cache.get(key, "")))

    def write_cache(name):
        if channel_cache is not None and username and name:
            locked(lambda: channel_cache.__setitem__(key, name))

    def claim_profile_lookup():
        """True when this call should fetch the profile (first caller per username)."""
        if not username:
            return False
        if profile_lookup_attempted is None:
            return True

        def claim():
            if key in profile_lookup_attempted:
                return False
            profile_lookup_attempted.add(key)
            return True

        return locked(claim)

    resolved = resolve_channel_name(source_url, parsed.lstrip("@") if parsed.startswith("@") else parsed, overrides)
    if not resolved and parsed.startswith("@") and is_usable_channel_name(parsed, quality_url):
        resolved = parsed
    if is_usable_channel_name(resolved, quality_url):
        write_cache(resolved)
        return resolved

    cached = read_cache()
    if is_usable_channel_name(cached, quality_url):
        return cached

    # The sheet already holds a real nickname: a profile fetch could not improve it.
    existing = clean_text(existing_channel)
    if channel_name_quality(existing, quality_url) == 2:
        return existing

    if allow_network and claim_profile_lookup():
        fetched = fetch_profile_channel_name_request(username, timeout=timeout)
        if not fetched and is_generated_username_channel(username) and not throttle.session_uses_proxy():
            alt_handle = author_unique_id_from_post(source_url, lookup_url, timeout=timeout)
            if alt_handle and alt_handle.casefold() != key:
                fetched = fetch_profile_channel_name_request(alt_handle, timeout=timeout)
        resolved = resolve_channel_name(source_url, fetched, overrides) or fetched
        if is_usable_channel_name(resolved, quality_url):
            write_cache(resolved)
            return resolved

    if username:
        handle = f"@{username.lstrip('@')}"
        if allow_network:
            write_cache(handle)
        return handle

    return ""


def seed_channel_cache_from_workbook(rows_to_process, sheet_contexts, channel_cache):
    for item in rows_to_process:
        username = extract_profile_username(item.get("url", ""))
        if not username:
            continue
        context = sheet_contexts.get(item["sheet_name"])
        if not context:
            continue
        channel_col = context["columns"].get("channel")
        if not channel_col:
            continue
        raw = clean_text(context["worksheet"].cell(row=item["row"], column=channel_col).value)
        if raw and raw != "Lỗi" and is_usable_channel_name(raw, item["url"]):
            channel_cache[username.casefold()] = raw


def channel_name_for_sheet(
    url,
    channel_name,
    *,
    resolved_url="",
    status="Success",
    channel_cache=None,
    channel_overrides=None,
    cache_lock=None,
):
    """Channel value to write; never does network I/O (lookups happen in the workers)."""
    source_url = normalize_tiktok_url(url)
    resolved = enrich_channel_name(
        source_url,
        channel_name,
        resolved_url=resolved_url,
        channel_cache=channel_cache,
        channel_overrides=channel_overrides,
        cache_lock=cache_lock,
        allow_network=False,
    )
    lookup_url = normalize_tiktok_url(resolved_url or url)
    if is_usable_channel_name(resolved, lookup_url or source_url):
        return resolved
    username = username_for_channel_lookup(source_url, lookup_url)
    if status == "Success" and username and not is_generated_username_channel(username):
        return f"@{username.lstrip('@')}"
    return "Lỗi"


def channel_name_quality(name, url=""):
    """Rank a channel cell so re-scrapes never downgrade a better existing value.

    2 = real nickname, 1 = @handle fallback, 0 = failed/garbage/empty.
    """
    text = clean_text(name)
    if not is_usable_channel_name(text, url):
        return 0
    return 1 if text.startswith("@") else 2
