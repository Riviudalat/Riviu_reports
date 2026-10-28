"""Compatibility facade: the workbook code lives in the `riviu.workbook` package.

Every name importable from here before the split still is, and is the very same object as in its
new module, so module-level state (WORKBOOK_READ_CACHE, PLATFORMS) exists once. Patch a name where
it is looked up: in its `riviu.workbook.<module>`, or in the importing module.
"""

from riviu.workbook.constants import (  # noqa: F401
    ALL_METRIC_COLUMNS,
    CHANNEL_OVERRIDE_FILENAME,
    COLUMN_ALIASES,
    DATA_DIR_NAME,
    DISPLAY_DATETIME_FORMAT,
    FILENAME_DATETIME_FORMAT,
    GOOGLE_SHEET_DATETIME_FORMAT,
    GOOGLE_SHEET_LABEL,
    GOOGLE_SHEET_REGISTRY_FILENAME,
    GOOGLE_SHEET_TITLE_MAP_PROPERTY,
    LAST_UPDATE_COLUMN,
    LEGACY_GOOGLE_SHEET_FILE_ID,
    LEGACY_PHOTO_LINK_FILL_COLOR,
    PARTNER_HEADING_MARKERS,
    REPORT_COLUMNS,
    RESULT_SHEET_PREFIX,
    SHEET_DATETIME_FORMAT,
    SINGLE_LINK_FILL_COLOR,
    SUMMARY_COLUMNS,
    SUMMARY_SHEET_NAME,
    SUMMARY_SHEET_TITLE_PREFIX,
    THREADS_REPORT_COLUMNS,
    THREADS_RESULT_SHEET_PREFIX,
    THREADS_SCAN_STATUS_HEADER,
    TIKTOK_MEDIA_PHOTO,
    TIKTOK_MEDIA_VIDEO,
    TTBD_INTERNAL_HEADERS,
    TTBD_RESOLVED_URL_HEADER,
    TTBD_SCAN_STATUS_HEADER,
    TTBD_SOURCE_URL_HEADER,
    VIDEO_LINK_FILL_COLOR,
)
from riviu.workbook.text import (  # noqa: F401
    clean_text,
    format_metric,
    is_total_label,
    metric_number,
    metric_total,
    normalize_header,
    normalize_key,
)
from riviu.workbook.dates import (  # noqa: F401
    display_datetime_sort_key,
    format_display_datetime,
    format_excel_sheet_datetime,
    format_filename_datetime,
    format_google_sheet_datetime,
    parse_filename_datetime_stamp,
    preview_datetime_text,
)
from riviu.workbook.links import (  # noqa: F401
    detect_tiktok_media_type,
    extract_threads_username,
    extract_tiktok_username,
    is_post_url,
    is_scrapable_tiktok_url,
    is_threads_link,
    is_tiktok_link,
    is_tiktok_video_link,
    normalize_threads_url,
    normalize_tiktok_url,
    should_highlight_video_link,
    TIKTOK_HOSTS,
)
from riviu.workbook.channels import (  # noqa: F401
    display_channel_name_from_file,
    display_threads_channel,
    is_failed_channel_name,
    is_generated_username_channel,
    is_generic_tiktok_channel_name,
    is_numeric_channel_garbage,
    normalize_channel_display,
    resolve_channel_name,
)
from riviu.workbook.platforms import (  # noqa: F401
    get_platform,
    PLATFORMS,
    PlatformSpec,
)
from riviu.workbook.columns import (  # noqa: F401
    dataframe_partner_columns,
    find_column_name,
    find_link_column_name,
    LINK_COLUMN_SAMPLE_ROWS,
    NON_POST_LINK_HEADER_MARKERS,
    pick_link_column,
    set_cell_literal,
    worksheet_ensure_column,
    worksheet_find_column_index,
    worksheet_find_last_update_column_index,
    worksheet_find_link_column_index,
    worksheet_headers,
    worksheet_partner_column_indexes,
)
from riviu.workbook.partners import (  # noqa: F401
    _normalize_partner_token,
    _partner_text_dedup_key,
    _split_partner_text,
    extract_row_partners,
    normalize_selected_partner_keys,
    partner_dedup_key,
    row_partners_from_values,
    split_partner_value,
    unique_preserve_order,
    worksheet_row_partners,
)
from riviu.workbook.naming import (  # noqa: F401
    data_sheet_name_for_summary_title,
    find_data_sheet_names_in_workbook,
    hidden_sheet_names_in_workbook,
    is_result_sheet_name,
    is_result_sheet_timestamp_name,
    is_summary_sheet_name,
    month_label_for_sheet_name,
    resolve_summary_sheet,
    result_sheet_display_name,
    RESULT_SHEET_TIMESTAMP_RE,
    safe_excel_sheet_title,
    safe_workbook_filename,
    summary_sheet_title_for_data_sheet,
    workbook_data_sheet_names,
)
from riviu.workbook.xlsx_formulas import (  # noqa: F401
    _XML_ATTRIBUTE_RE,
    _XML_FORMULA_RE,
    worksheet_column_formulas,
    worksheet_xml_part,
    XLSX_MAIN_NS,
    XLSX_PACKAGE_REL_NS,
    XLSX_REL_NS,
)
from riviu.workbook.snapshot import (  # noqa: F401
    find_data_sheet_listing,
    find_data_sheet_names,
    load_excel_file,
    open_workbook_snapshot,
    workbook_path_key,
    WORKBOOK_READ_CACHE,
    WORKBOOK_READ_CACHE_MAX_ENTRIES,
    workbook_sheet_names,
    WorkbookReadCache,
    WorkbookSnapshot,
)
from riviu.workbook.atomic import (  # noqa: F401
    _replace_atomically,
    save_workbook_atomic,
    write_json_atomic,
)
from riviu.workbook.files import (  # noqa: F401
    base_dir_for_file,
    channel_override_path,
    ensure_data_dir,
    is_internal_workbook_filename,
    load_channel_overrides,
    safe_join,
)
from riviu.workbook.google_registry import (  # noqa: F401
    google_sheet_file_id_from_title,
    google_sheet_filename_to_label,
    google_sheet_original_title,
    google_sheet_registry_path,
    google_sheet_source_for_file,
    google_sheet_sync_label,
    google_sheet_sync_timestamp_display,
    google_to_excel_sheet_titles,
    load_google_sheet_registry,
    parse_google_spreadsheet_id,
    register_google_sheet_source,
    save_google_sheet_registry,
    store_google_sheet_title_map,
)
from riviu.workbook.google_fetch import (  # noqa: F401
    download_google_sheet,
    fetch_google_spreadsheet_title,
    google_sheet_export_url,
)
from riviu.workbook.catalog import (  # noqa: F401
    workbook_file_entries,
)
from riviu.workbook.report_rows import (  # noqa: F401
    _build_sheet_report_rows,
    _shared_report_rows,
    build_partner_report_rows,
    group_rows_by_partner,
    partner_link_counts,
    build_workbook_rows,
    copy_report_row,
    fill_missing_dates_from_previous,
    is_exportable_report_row,
    list_workbook_partners,
    list_workbook_partners_with_link_counts,
    sheet_report_rows,
    SheetReportRows,
)
from riviu.workbook.preview import (  # noqa: F401
    _sequence_number,
    build_sheet_preview,
    clean_preview_value,
    evaluate_sequence_formula,
    fill_formula_sequence_numbers,
    fill_preview_total_row,
    preview_column_labels,
    read_sheet_preview,
    read_summary_dashboard,
    SUMMARY_TOTAL_KEYS,
)
from riviu.workbook.summary import (  # noqa: F401
    build_partner_summary_rows,
    read_existing_summary_updates,
    rebuild_summary_sheet,
)
from riviu.workbook.highlight import (  # noqa: F401
    _cell_has_fill,
    highlight_single_partner_link_rows,
    highlight_video_link_rows,
)
