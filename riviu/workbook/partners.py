"""Partner-cell parsing and de-duplication (pure)."""

import functools
import re
import unicodedata
from typing import Iterable

from riviu.workbook.constants import PARTNER_HEADING_MARKERS
from riviu.workbook.text import clean_text


def split_partner_value(value):
    text = clean_text(value)
    if not text:
        return []
    return list(_split_partner_text(text))


# Partner cells repeat the same few names thousands of times per sheet.
@functools.lru_cache(maxsize=4096)
def _split_partner_text(text):
    raw_lines = [line.strip() for line in text.replace("\r", "\n").split("\n") if line.strip()]
    if len(raw_lines) <= 1 and not any(marker in text.upper() for marker in PARTNER_HEADING_MARKERS) and not text.lstrip().startswith(("-", "*", "•")):
        cleaned = _normalize_partner_token(text)
        return (cleaned,) if cleaned else ()

    partners = []
    for line in raw_lines:
        cleaned = _normalize_partner_token(line)
        if not cleaned:
            continue
        upper_line = cleaned.upper()
        if any(marker in upper_line for marker in PARTNER_HEADING_MARKERS):
            continue
        partners.append(cleaned)
    return tuple(unique_preserve_order(partners))


def _normalize_partner_token(text):
    cleaned = clean_text(text)
    cleaned = unicodedata.normalize("NFC", cleaned)
    cleaned = re.sub(r"[​-‍﻿­]", "", cleaned)
    cleaned = re.sub(r"^[\-\*•]+\s*", "", cleaned)
    cleaned = re.sub(r"^\d+[\.\)]\s*", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -:\t")
    return cleaned


def partner_dedup_key(value):
    return _partner_text_dedup_key(str(value or ""))


@functools.lru_cache(maxsize=4096)
def _partner_text_dedup_key(text):
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[​-‍﻿­]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.casefold()


def unique_preserve_order(values):
    seen = set()
    result = []
    for value in values:
        key = partner_dedup_key(value)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def row_partners_from_values(values):
    partners = []
    for value in values:
        # Most partner cells are empty; skip them before the (slow) text normalisation.
        if value is None or (isinstance(value, float) and value != value) or (isinstance(value, str) and not value.strip()):
            continue
        partners.extend(split_partner_value(value))
    return unique_preserve_order(partners)


def partners_from_doi_tac(value):
    """Partners of Riviu Data's comma column `doi_tac` (Postgres backend).

    The twin of Riviu Data's `public.rda_split_doi_tac` / `rda.normalize.split_doi_tac`:
    split on commas, NFC, zero-width characters removed, whitespace collapsed, empties
    dropped, first spelling of a `partner_dedup_key` wins. The importer stores
    `doi_tac_from_partners(row_partners_from_values(cells))`, so for partner names
    without a comma this returns exactly the workbook's list (tests check the parity).
    """
    text = clean_text(value)
    if not text:
        return []
    names = []
    for part in text.split(","):
        name = unicodedata.normalize("NFC", part)
        name = re.sub(r"[\u200b-\u200d\ufeff\u00ad]", "", name)
        name = re.sub(r"\s+", " ", name).strip()
        if name:
            names.append(name)
    return unique_preserve_order(names)


def doi_tac_from_partners(partners):
    """The `doi_tac` text for a partner list; None when empty. A name with a comma cannot be stored."""
    names = [clean_text(name) for name in partners or [] if clean_text(name)]
    for name in names:
        if "," in name:
            raise ValueError(f"Tên đối tác chứa dấu phẩy, không lưu được vào doi_tac: {name}")
    return ", ".join(names) if names else None


def extract_row_partners(row, partner_columns: Iterable):
    return row_partners_from_values(row.get(column, "") for column in partner_columns)


def worksheet_row_partners(worksheet, row_index, partner_columns):
    return row_partners_from_values(
        worksheet.cell(row=row_index, column=column_index).value for column_index in partner_columns
    )


def normalize_selected_partner_keys(selected_partner=None, selected_partners=None):
    values = []
    if isinstance(selected_partners, (list, tuple, set)):
        values.extend(selected_partners)
    elif selected_partners:
        values.append(selected_partners)
    if isinstance(selected_partner, (list, tuple, set)):
        values.extend(selected_partner)
    elif selected_partner:
        values.append(selected_partner)
    return {clean_text(value).casefold() for value in values if clean_text(value)}
