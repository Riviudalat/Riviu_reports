from scraper import parse_counts


def test_parse_counts_extracts_metrics_from_embedded_json():
    content = """
    "playCount":12345,
    "diggCount":678,
    "commentCount":90,
    "collectCount":12,
    "shareCount":34
    """
    data, found = parse_counts(content)
    assert found is True
    assert data["Views"] == "12345"
    assert data["Likes"] == "678"
    assert data["Comments"] == "90"
    assert data["Saves"] == "12"
    assert data["Shares"] == "34"


def test_parse_counts_returns_defaults_when_missing():
    data, found = parse_counts("<html></html>")
    assert found is False
    assert data["Views"] == "0"
