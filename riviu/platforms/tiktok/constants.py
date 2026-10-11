"""Shared TikTok scanner settings: metric columns, browser identity and run defaults."""


USER_AGENTS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 12; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPad; CPU OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1",
]
DEFAULT_USER_AGENT = USER_AGENTS[0]
BROWSER_LOCALE = "vi-VN"
BROWSER_TIMEZONE = "Asia/Ho_Chi_Minh"
BROWSER_GEOLOCATION = {"latitude": 11.9404, "longitude": 108.4583}

METRIC_HEADERS = {
    "views": "LƯỢT XEM",
    "likes": "TIM",
    "comments": "BÌNH LUẬN",
    "saves": "LƯỢT LƯU",
    "shares": "CHIA SẺ",
}

METRIC_KEYS = {
    "views": "Views",
    "likes": "Likes",
    "comments": "Comments",
    "saves": "Saves",
    "shares": "Shares",
}

LAST_UPDATE_HEADER = "Cập nhật lần cuối"

COUNT_FIELD_MAP = {
    "Views": "playCount",
    "Likes": "diggCount",
    "Comments": "commentCount",
    "Saves": "collectCount",
    "Shares": "shareCount",
}


MAX_WORKERS = 50
DEFAULT_WORKERS = 5
DEFAULT_RETRIES = 2
DEFAULT_SAVE_EVERY = 25


DEFAULT_REQUEST_TIMEOUT = 30
