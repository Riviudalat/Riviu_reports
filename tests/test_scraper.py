from scraper import (
    channel_name_for_sheet,
    channel_name_quality,
    counts_match,
    enrich_channel_name,
    extract_media_id,
    fetch_profile_channel_name_request,
    format_metric_log_line,
    format_scrape_result_log,
    is_tiktok_error_page,
    parse_count_value,
    parse_counts,
    validate_metrics,
)


def test_parse_counts_extracts_metrics_from_reflow_detail_json():
    content = """
    <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">
    {
      "__DEFAULT_SCOPE__": {
        "webapp.reflow.video.detail": {
          "statusCode": 0,
          "itemInfo": {
            "itemStruct": {
              "id": "555",
              "statsV2": {
                "playCount": "4321",
                "diggCount": "99",
                "commentCount": "4",
                "collectCount": "2",
                "shareCount": "1"
              }
            }
          }
        }
      }
    }
    </script>
    """
    data, found = parse_counts(content, media_id="555")
    assert found is True
    assert data["Views"] == "4321"
    assert data["Likes"] == "99"


def test_build_request_url_candidates_includes_video_rewrite():
    from scraper import build_request_url_candidates

    candidates = build_request_url_candidates(
        "https://www.tiktok.com/@demo/photo/764002"
    )
    assert "https://www.tiktok.com/@demo/photo/764002" in candidates
    assert "https://www.tiktok.com/@demo/video/764002" in candidates
    assert "https://www.tiktok.com/@demo/photo/764002?_r=1" in candidates
    assert candidates.index("https://www.tiktok.com/@demo/photo/764002?_r=1") < candidates.index(
        "https://www.tiktok.com/@demo/video/764002"
    )


def test_scrape_link_request_parses_photo_statsv2():
    content = """
    <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">
    {
      "__DEFAULT_SCOPE__": {
        "webapp.photo-detail": {
          "itemInfo": {
            "itemStruct": {
              "id": "764002",
              "stats": {
                "playCount": 0,
                "diggCount": 45,
                "commentCount": 2,
                "collectCount": 0,
                "shareCount": 1
              },
              "statsV2": {
                "playCount": "1234",
                "diggCount": "45",
                "commentCount": "2",
                "collectCount": "0",
                "shareCount": "1"
              }
            }
          }
        }
      }
    }
    </script>
    """
    from scraper import scrape_link_request

    def fake_fetch(url, timeout=30):
        return "https://www.tiktok.com/@demo/photo/764002", content

    import scraper as scraper_module

    original = scraper_module.fetch_tiktok_html
    scraper_module.fetch_tiktok_html = fake_fetch
    try:
        data, channel, status = scrape_link_request("https://www.tiktok.com/@demo/photo/764002")
    finally:
        scraper_module.fetch_tiktok_html = original

    assert status == "Success"
    assert data["Views"] == "1234"


def test_hybrid_browser_worker_count_caps_fallback_workers():
    from scraper import hybrid_browser_worker_count, MAX_BROWSER_FALLBACK_WORKERS

    assert hybrid_browser_worker_count(50, 1000) == MAX_BROWSER_FALLBACK_WORKERS
    assert hybrid_browser_worker_count(6, 20) >= 3


def test_parse_counts_uses_statsv2_when_stats_playcount_is_zero():
    content = """
    <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">
    {
      "__DEFAULT_SCOPE__": {
        "webapp.photo-detail": {
          "itemInfo": {
            "itemStruct": {
              "id": "764002",
              "stats": {
                "playCount": 0,
                "diggCount": 45,
                "commentCount": 2,
                "collectCount": 0,
                "shareCount": 1
              },
              "statsV2": {
                "playCount": "1234",
                "diggCount": "45",
                "commentCount": "2",
                "collectCount": "0",
                "shareCount": "1"
              }
            }
          }
        }
      }
    }
    </script>
    """
    data, found = parse_counts(content, media_id="764002")
    assert found is True
    assert data["Views"] == "1234"
    assert data["Likes"] == "45"


def test_validate_metrics_rejects_zero_views_with_engagement():
    assert validate_metrics(
        {"Views": "0", "Likes": "45", "Comments": "0", "Saves": "0", "Shares": "1"}
    ) is False
    assert validate_metrics(
        {"Views": "0", "Likes": "0", "Comments": "0", "Saves": "0", "Shares": "0"}
    ) is True


def test_parse_counts_extracts_metrics_from_photo_detail_json():
    content = """
    <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">
    {
      "__DEFAULT_SCOPE__": {
        "webapp.photo-detail": {
          "itemInfo": {
            "itemStruct": {
              "id": "764001",
              "stats": {
                "playCount": 321,
                "diggCount": 12,
                "commentCount": 3,
                "collectCount": 1,
                "shareCount": 2
              }
            }
          }
        }
      }
    }
    </script>
    """
    data, found = parse_counts(content, media_id="764001")
    assert found is True
    assert data["Views"] == "321"


def test_parse_counts_extracts_metrics_from_embedded_json():
    content = """
    <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">
    {
      "__DEFAULT_SCOPE__": {
        "webapp.video-detail": {
          "itemInfo": {
            "itemStruct": {
              "id": "999",
              "stats": {
                "playCount": 12345,
                "diggCount": 678,
                "commentCount": 90,
                "collectCount": 12,
                "shareCount": 34
              }
            }
          }
        }
      }
    }
    </script>
    """
    data, found = parse_counts(content, media_id="999")
    assert found is True
    assert data["Views"] == "12345"
    assert data["Likes"] == "678"
    assert data["Comments"] == "90"
    assert data["Saves"] == "12"
    assert data["Shares"] == "34"


def test_parse_counts_prefers_matching_media_id_over_other_videos():
    content = """
    <script id="SIGI_STATE" type="application/json">
    {
      "ItemModule": {
        "111": {
          "id": "111",
          "stats": {
            "playCount": 10,
            "diggCount": 1,
            "commentCount": 0,
            "collectCount": 0,
            "shareCount": 0
          }
        },
        "222": {
          "id": "222",
          "stats": {
            "playCount": 50000,
            "diggCount": 4000,
            "commentCount": 200,
            "collectCount": 50,
            "shareCount": 30
          }
        }
      }
    }
    </script>
    """
    data, found = parse_counts(content, media_id="222")
    assert found is True
    assert data["Views"] == "50000"
    assert data["Likes"] == "4000"


def test_parse_counts_returns_defaults_when_missing():
    data, found = parse_counts("<html></html>")
    assert found is False
    assert data["Views"] == "0"


def test_parse_counts_rejects_ambiguous_multiple_items_without_media_id():
    content = """
    <script id="SIGI_STATE" type="application/json">
    {
      "ItemModule": {
        "111": {"id":"111","stats":{"playCount":10,"diggCount":1,"commentCount":0,"collectCount":0,"shareCount":0}},
        "222": {"id":"222","stats":{"playCount":20,"diggCount":2,"commentCount":0,"collectCount":0,"shareCount":0}}
      }
    }
    </script>
    """
    data, found = parse_counts(content, media_id="")
    assert found is False
    assert data["Views"] == "0"


def test_parse_count_value_supports_suffixes():
    assert parse_count_value("1.2M") == "1200000"
    assert parse_count_value("12K") == "12000"
    assert parse_count_value(4567) == "4567"


def test_validate_metrics_rejects_implausible_values():
    assert validate_metrics(
        {"Views": "100", "Likes": "900", "Comments": "0", "Saves": "0", "Shares": "0"}
    ) is False
    assert validate_metrics(
        {"Views": "1000", "Likes": "50", "Comments": "4", "Saves": "2", "Shares": "1"}
    ) is True


def test_counts_match_requires_all_metrics_equal():
    left = {"Views": "10", "Likes": "1", "Comments": "0", "Saves": "0", "Shares": "0"}
    right = {"Views": "10", "Likes": "1", "Comments": "0", "Saves": "0", "Shares": "0"}
    assert counts_match(left, right) is True
    assert counts_match(left, {**right, "Views": "11"}) is False


def test_format_scrape_result_log_error_and_success():
    success_msg, success_level, success_details = format_scrape_result_log(
        {
            "url": "https://www.tiktok.com/@a/video/1",
            "worker": 2,
            "status": "Success",
            "attempts": 1,
            "elapsed": 1.25,
            "channel_name": "Channel A",
            "data": {"Views": "10", "Likes": "1", "Comments": "0", "Saves": "0", "Shares": "0"},
            "rows": [{"sheet_name": "Tháng 6", "row": 5}],
        },
        3,
        10,
    )
    assert success_level == "OK"
    assert success_details["kind"] == "scrape_ok"
    assert success_details["metrics"]["views"] == 10
    assert "Lượt xem 10" in success_msg
    assert "Tim 1" in success_msg
    assert "Tháng 6#5" in success_msg


def test_format_metric_log_line_uses_integers():
    assert format_metric_log_line(
        {"Views": "39", "Likes": "5", "Comments": "0", "Saves": "0", "Shares": "0"}
    ) == "view=39 tim=5 cmt=0 save=0 share=0"


def test_parse_channel_name_from_page_uses_item_struct_when_url_handle_mismatch():
    content = """
    <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">
    {
      "__DEFAULT_SCOPE__": {
        "webapp.photo-detail": {
          "itemInfo": {
            "itemStruct": {
              "id": "7647351675514146069",
              "author": {
                "uniqueId": "vc.balo.i.trn",
                "nickname": "Vác Balo Đi Trốn"
              },
              "statsV2": {
                "playCount": "281",
                "diggCount": "13",
                "commentCount": "0",
                "collectCount": "3",
                "shareCount": "3"
              }
            }
          }
        }
      }
    }
    </script>
    """
    from scraper import parse_channel_name_from_page, parse_fetched_request_page

    name = parse_channel_name_from_page(
        content,
        "user16205752394791",
        media_id="7647351675514146069",
    )
    assert name == "Vác Balo Đi Trốn"

    metrics, channel, status = parse_fetched_request_page(
        "https://www.tiktok.com/@user16205752394791/photo/7647351675514146069",
        "https://www.tiktok.com/@user16205752394791/photo/7647351675514146069",
        content,
    )
    assert status == "Success"
    assert channel == "Vác Balo Đi Trốn"
    assert metrics["Views"] == "281"


def test_is_usable_channel_name_rejects_numeric_garbage():
    from scraper import is_usable_channel_name

    assert is_usable_channel_name("1.0") is False
    assert is_usable_channel_name("1") is False
    assert is_usable_channel_name("Lỗi") is False
    assert is_usable_channel_name("Đà Lạt Hệ Chill") is True


def test_channel_name_quality_ranks_real_name_above_handle():
    url = "https://www.tiktok.com/@demo/photo/1"
    assert channel_name_quality("Đà Lạt Hệ Chill", url) == 2
    assert channel_name_quality("@demo", url) == 1
    assert channel_name_quality("Lỗi", url) == 0
    assert channel_name_quality("1.0", url) == 0
    assert channel_name_quality("", url) == 0
    assert channel_name_quality("@user1234567890", url) == 0


def test_channel_name_for_sheet_does_not_write_generated_handle():
    url = "https://www.tiktok.com/@user16205752394791/photo/1"
    result = channel_name_for_sheet(
        url,
        "",
        resolved_url=url,
        status="Success",
        profile_lookup_attempted={"user16205752394791"},
    )
    assert result == "Lỗi"


def test_channel_name_for_sheet_keeps_real_handle_fallback():
    url = "https://www.tiktok.com/@.lt.h.chill/photo/1"
    result = channel_name_for_sheet(
        url,
        "",
        resolved_url=url,
        status="Success",
        profile_lookup_attempted={".lt.h.chill"},
    )
    assert result == "@.lt.h.chill"


def test_fetch_profile_channel_name_request_allows_user_id_accounts():
    profile_html = """
    <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">
    {"userInfo": {"user": {"uniqueId": "user2657244715931", "nickname": "Lê Việt Khoa"}}}
    </script>
    """
    import scraper as scraper_module

    original = scraper_module.fetch_tiktok_html
    scraper_module.fetch_tiktok_html = lambda url, timeout=30: (url, profile_html)
    try:
        assert fetch_profile_channel_name_request("user2657244715931") == "Lê Việt Khoa"
    finally:
        scraper_module.fetch_tiktok_html = original


def test_enrich_channel_name_uses_cache_for_generated_user_accounts():
    url = "https://www.tiktok.com/@user2663512211600/photo/1"
    cache = {"user2663512211600": "Quỳnh Tiên"}
    name = enrich_channel_name(url, "", channel_cache=cache)
    assert name == "Quỳnh Tiên"


def test_enrich_channel_name_uses_resolved_url_for_short_links():
    short_url = "https://vt.tiktok.com/ZSxcMvARr/"
    resolved = "https://www.tiktok.com/@uyn.nh8183/photo/7652254869969014034"
    profile_html = """
    <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">
    {"author": {"uniqueId": "uyn.nh8183", "nickname": "Uyển Như"}}
    </script>
    """
    import scraper as scraper_module

    original = scraper_module.fetch_tiktok_html
    scraper_module.fetch_tiktok_html = lambda url, timeout=30: (url, profile_html)
    try:
        name = enrich_channel_name(short_url, "", resolved_url=resolved, profile_lookup_attempted=set())
        assert name == "Uyển Như"
    finally:
        scraper_module.fetch_tiktok_html = original


def test_format_scrape_result_log_error():
    error_msg, error_level, error_details = format_scrape_result_log(
        {
            "url": "https://www.tiktok.com/@b/video/2",
            "worker": 1,
            "status": "Error: Không đọc được số liệu",
            "attempts": 3,
            "elapsed": 4.5,
            "rows": [{"sheet_name": "Tháng 6", "row": 9}],
        },
        4,
        10,
    )
    assert error_level == "ERROR"
    assert error_details["kind"] == "scrape_error"
    assert "Lỗi" in error_msg
    assert "Không đọc được số liệu" in error_msg


def test_extract_media_id_from_video_and_photo_urls():
    assert extract_media_id("https://www.tiktok.com/@a/video/1234567890") == "1234567890"
    assert extract_media_id("https://www.tiktok.com/@a/photo/9876543210") == "9876543210"


def test_is_tiktok_error_page_ignores_i18n_strings_in_script_tags():
    content = """
    <html><body>
    <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">
    {"couldn't find this account":"couldn't find this account","page not available":"page not available"}
    </script>
    <div>8629 views</div>
    </body></html>
    """
    assert is_tiktok_error_page(content) is False


def test_is_tiktok_error_page_detects_visible_error_text():
    content = """
    <html><body>
    <h1>Couldn't find this account</h1>
    </body></html>
    """
    assert is_tiktok_error_page(content) is True


def test_configure_request_concurrency_never_reduces_worker_count():
    import scraper
    from scraper import configure_request_concurrency

    configure_request_concurrency(20, proxy_count=0)
    assert scraper._request_semaphore._value == 20
    configure_request_concurrency(20, proxy_count=10)
    assert scraper._request_semaphore._value == 20


def test_clamp_worker_count_no_proxy_keeps_requested():
    from scraper import clamp_worker_count

    # Không proxy vẫn chạy đúng số luồng đã chọn, không tự giảm.
    assert clamp_worker_count(50, proxy_count=0) == 50


def test_clamp_worker_count_plenty_of_proxies_keeps_requested():
    from scraper import clamp_worker_count

    assert clamp_worker_count(30, proxy_count=10) == 30


def test_clamp_worker_count_few_proxies_keeps_requested_and_distributes_evenly():
    from scraper import clamp_worker_count

    # 50 luồng chỉ có 3 proxy -> vẫn chạy đủ 50 luồng, chia đều cho 3 proxy
    # (round-robin ở assign_worker_proxy), không tự giảm số luồng.
    assert clamp_worker_count(50, proxy_count=3) == 50


def test_clamp_worker_count_bounds_to_valid_range():
    from scraper import MAX_WORKERS, clamp_worker_count

    assert clamp_worker_count(0, proxy_count=0) == 1
    assert clamp_worker_count(999, proxy_count=0) == MAX_WORKERS


def test_is_request_rate_limited_status():
    from scraper import is_request_rate_limited_status

    assert is_request_rate_limited_status("Error: HTTP 403") is True
    assert is_request_rate_limited_status("Error: HTTP 429") is True
    assert is_request_rate_limited_status("Error: HTTP 404") is False


def test_is_transient_network_status():
    from scraper import is_transient_network_status

    assert is_transient_network_status("Error: [Errno 11001] getaddrinfo failed") is True
    assert is_transient_network_status("Error: [WinError 10054] connection was forcibly closed") is True
    assert is_transient_network_status("Error: timed out") is True
    assert is_transient_network_status("Error: HTTP 404") is False
    assert is_transient_network_status("Lỗi: TikTok không trả số liệu") is False


def test_scrape_link_retries_transient_network_errors(monkeypatch):
    import scraper

    calls = {"n": 0}

    def fake_impl(url, timeout=30):
        calls["n"] += 1
        empty = {"Views": "0", "Likes": "0", "Comments": "0", "Saves": "0", "Shares": "0"}
        return empty, "", "Error: [Errno 11001] getaddrinfo failed", False, ""

    monkeypatch.setattr(scraper, "_scrape_link_request_impl", fake_impl)
    monkeypatch.setattr(scraper.time, "sleep", lambda *_args, **_kwargs: None)

    _data, _channel, status, attempts, _resolved = scraper.scrape_link_with_retries_request(
        "https://www.tiktok.com/@demo/video/1",
        retries=2,
    )
    assert attempts == 3
    assert calls["n"] == 3
    assert "11001" in status


def test_scrape_link_breaks_early_on_no_stats(monkeypatch):
    import scraper

    calls = {"n": 0}

    def fake_impl(url, timeout=30):
        calls["n"] += 1
        empty = {"Views": "0", "Likes": "0", "Comments": "0", "Saves": "0", "Shares": "0"}
        return empty, "", scraper.STATUS_TIKTOK_NO_STATS, False, ""

    monkeypatch.setattr(scraper, "_scrape_link_request_impl", fake_impl)
    monkeypatch.setattr(scraper.time, "sleep", lambda *_args, **_kwargs: None)

    _data, _channel, status, attempts, _resolved = scraper.scrape_link_with_retries_request(
        "https://www.tiktok.com/@demo/video/1",
        retries=2,
    )
    assert attempts == 1
    assert calls["n"] == 1
    assert status == scraper.STATUS_TIKTOK_NO_STATS


def test_note_network_failure_pauses_after_streak(monkeypatch):
    import scraper

    scraper.configure_request_concurrency(5)
    sleeps = []
    monkeypatch.setattr(scraper.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(scraper.time, "time", lambda: 1000.0)

    for _ in range(scraper.NETWORK_FAIL_STREAK_PAUSE):
        scraper.note_network_failure()

    scraper.wait_if_request_blocked()
    assert sleeps
    assert sleeps[0] >= scraper.NETWORK_FAIL_PAUSE_SECONDS - 0.01
    scraper.configure_request_concurrency(5)


def test_progress_payload_includes_hidden_count():
    from scraper import progress_payload

    payload = progress_payload(10, 5, 3, 1, 2, 1000.0, hidden_count=1)
    assert payload["success"] == 3
    assert payload["error"] == 1
    assert payload["hidden"] == 1


def test_write_result_updates_timestamp_only_on_success():
    import openpyxl
    from scraper import LAST_UPDATE_HEADER, write_result

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tháng 7"
    ws.append(["Link", "Tên Kênh", "LƯỢT XEM", "TIM", LAST_UPDATE_HEADER])
    ws.append(["https://www.tiktok.com/@a/video/1", "", 0, 0, "01/01/2026-00:00"])
    contexts = {
        "Tháng 7": {
            "worksheet": ws,
            "columns": {
                "views": 3,
                "likes": 4,
                "channel": 2,
                "last_update": 5,
            },
        }
    }
    item = {"sheet_name": "Tháng 7", "row": 2, "url": "https://www.tiktok.com/@a/video/1"}
    before = ws.cell(row=2, column=5).value

    write_result(
        contexts,
        item,
        {"Views": "0", "Likes": "0", "Comments": "0", "Saves": "0", "Shares": "0"},
        "",
        "Error: Worker đã dừng",
    )
    assert ws.cell(row=2, column=5).value == before

    write_result(
        contexts,
        item,
        {"Views": "10", "Likes": "1", "Comments": "0", "Saves": "0", "Shares": "0"},
        "@a",
        "Success",
    )
    assert ws.cell(row=2, column=5).value != before
    assert ws.cell(row=2, column=3).value == 10
    wb.close()
