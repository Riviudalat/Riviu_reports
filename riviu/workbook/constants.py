"""Column, sheet and file names shared by scanners, previews, reports and the Google sync."""

DATA_DIR_NAME = "data"
LEGACY_GOOGLE_SHEET_FILE_ID = "data/google_sheet_main.xlsx"
GOOGLE_SHEET_LABEL = "Google Sheet chính"
GOOGLE_SHEET_REGISTRY_FILENAME = "google_sheet_sources.json"
GOOGLE_SHEET_TITLE_MAP_PROPERTY = "Riviu.GoogleSheetTitles"
REPORT_COLUMNS = ["NGÀY AIR", "TÊN KÊNH", "LINK AIR", "LƯỢT XEM", "TIM", "BÌNH LUẬN", "LƯỢT LƯU", "CHIA SẺ"]
SUMMARY_SHEET_NAME = "Tổng kết"
SUMMARY_SHEET_TITLE_PREFIX = "Tổng kết "
LAST_UPDATE_COLUMN = "Cập nhật lần cuối"
SUMMARY_COLUMNS = ["Stt", "ĐỐI TÁC", "TỔNG LINK", "TỔNG LƯỢT XEM", "TỔNG TIM", "TỔNG BÌNH LUẬN", "TỔNG LƯỢT LƯU", "TỔNG CHIA SẺ", LAST_UPDATE_COLUMN]
# Cam cảnh báo đối tác chỉ có đúng 1 link — cần chú ý khi báo cáo/nghiệm thu.
SINGLE_LINK_FILL_COLOR = "FFC000"
# Xanh nhạt cho dòng link dạng video (/video/).
VIDEO_LINK_FILL_COLOR = "BDD7EE"
LEGACY_PHOTO_LINK_FILL_COLOR = "C6EFCE"
TIKTOK_MEDIA_VIDEO = "video"
TIKTOK_MEDIA_PHOTO = "photo"
TTBD_SCAN_STATUS_HEADER = "__TTBD_SCAN_STATUS"
TTBD_RESOLVED_URL_HEADER = "__TTBD_RESOLVED_URL"
TTBD_SOURCE_URL_HEADER = "__TTBD_SOURCE_URL"
THREADS_SCAN_STATUS_HEADER = "__THREADS_SCAN_STATUS"
TTBD_INTERNAL_HEADERS = {
    TTBD_SCAN_STATUS_HEADER,
    TTBD_RESOLVED_URL_HEADER,
    TTBD_SOURCE_URL_HEADER,
    THREADS_SCAN_STATUS_HEADER,
}
PARTNER_HEADING_MARKERS = ("DANH SÁCH", "DANH SACH", "BỘ ẢNH", "BO ANH")
CHANNEL_OVERRIDE_FILENAME = "channel_name_overrides.json"
RESULT_SHEET_PREFIX = "report seeding tiktok"
THREADS_RESULT_SHEET_PREFIX = "report seeding threads"
# Same layout as TikTok; REPOST takes the LƯỢT LƯU slot because Threads has no saves.
THREADS_REPORT_COLUMNS = ["NGÀY AIR", "TÊN KÊNH", "LINK AIR", "LƯỢT XEM", "TIM", "BÌNH LUẬN", "REPOST", "CHIA SẺ"]
ALL_METRIC_COLUMNS = ("LƯỢT XEM", "TIM", "BÌNH LUẬN", "LƯỢT LƯU", "REPOST", "CHIA SẺ")
# Single header vocabulary for scanners, previews and reports; matched with normalize_key
# (case/diacritic-insensitive) so a scan never appends a duplicate of a column the report reads.
COLUMN_ALIASES = {
    "link": ("LINK AIR", "Link", "URL"),
    "date": ("NGÀY AIR", "Ngày"),
    "TÊN KÊNH": ("TÊN KÊNH",),
    "Tên Kênh": ("TÊN KÊNH",),
    LAST_UPDATE_COLUMN: (LAST_UPDATE_COLUMN, "Ngày cập nhật"),
}
DISPLAY_DATETIME_FORMAT = "%d/%m/%Y-%H:%M"
FILENAME_DATETIME_FORMAT = "%d-%m-%Y-%H-%M"
SHEET_DATETIME_FORMAT = "%d-%m-%Y-%H-%M"
GOOGLE_SHEET_DATETIME_FORMAT = "%d-%m-%Y-%H:%M"
