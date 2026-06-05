from scraper import (
    counts_match,
    extract_media_id,
    format_scrape_result_log,
    is_tiktok_error_page,
    parse_count_value,
    parse_counts,
    validate_metrics,
)


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
    success_msg, success_level = format_scrape_result_log(
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
    assert "view=10" in success_msg
    assert "Tháng 6#5" in success_msg

    error_msg, error_level = format_scrape_result_log(
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
    assert "LỖI" in error_msg
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
