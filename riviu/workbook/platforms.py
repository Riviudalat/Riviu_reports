"""The scanned-platform registry: every per-platform difference lives in one PlatformSpec."""

from dataclasses import dataclass
from typing import Callable

from riviu.workbook.channels import display_channel_name_from_file, display_threads_channel
from riviu.workbook.constants import (
    ALL_METRIC_COLUMNS,
    LAST_UPDATE_COLUMN,
    REPORT_COLUMNS,
    THREADS_REPORT_COLUMNS,
)
from riviu.workbook.links import is_threads_link, is_tiktok_link, normalize_threads_url, normalize_tiktok_url
from riviu.workbook.text import clean_text


@dataclass(frozen=True)
class PlatformSpec:
    """Everything that differs between scanned platforms; adding a platform means adding one entry."""

    key: str
    label: str
    report_columns: tuple
    normalize_url: Callable[[object], str]
    is_link: Callable[[object], bool]
    display_channel: Callable[[str, str], str]
    highlights_video_links: bool = False
    # False: the report modal hides partners with no link of this platform (e.g. TikTok-only
    # partners in a mixed workbook stay out of the Threads list).
    lists_partners_without_links: bool = False
    file_tag: str = ""
    google_sheet_prefix: str = ""
    # Inserted into the per-sheet "Tổng kết" tab title; TikTok keeps the legacy untagged title.
    summary_tag: str = ""

    @property
    def metric_columns(self):
        return tuple(column for column in self.report_columns if column in ALL_METRIC_COLUMNS)

    @property
    def summary_columns(self):
        """Summary tab layout: the report's metrics in report order (REPOST in the LƯỢT LƯU slot)."""
        return ("Stt", "ĐỐI TÁC", "TỔNG LINK", *(f"TỔNG {metric}" for metric in self.metric_columns), LAST_UPDATE_COLUMN)


PLATFORMS = {
    "tiktok": PlatformSpec(
        key="tiktok",
        label="TikTok",
        report_columns=tuple(REPORT_COLUMNS),
        normalize_url=lambda value: normalize_tiktok_url(value),
        is_link=lambda value: is_tiktok_link(value),
        display_channel=lambda link, raw: display_channel_name_from_file(link, raw),
        highlights_video_links=True,
        lists_partners_without_links=True,
    ),
    "threads": PlatformSpec(
        key="threads",
        label="Threads",
        report_columns=tuple(THREADS_REPORT_COLUMNS),
        normalize_url=lambda value: normalize_threads_url(value),
        is_link=lambda value: is_threads_link(value),
        display_channel=lambda link, raw: display_threads_channel(link, raw),
        file_tag="Threads",
        google_sheet_prefix="Report Seeding Threads",
        summary_tag="Threads",
    ),
}


def get_platform(key):
    spec = PLATFORMS.get(clean_text(key).lower())
    if spec is None:
        raise ValueError("Nền tảng không hợp lệ")
    return spec
