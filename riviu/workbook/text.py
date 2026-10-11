"""Cell text normalization and metric parsing (pure, no file access)."""

import re
import unicodedata

import pandas as pd


def normalize_header(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def normalize_key(value):
    text = str(value or "").replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text.strip()).casefold()


def clean_text(value):
    if pd.isna(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def is_total_label(value):
    return normalize_key(clean_text(value)) == "tong"


def metric_number(value):
    if pd.isna(value) or value == "" or value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(float(value))
    raw_text = str(value).strip()
    if not raw_text:
        return 0
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", raw_text):
        return int(re.sub(r"[.,]", "", raw_text))
    if re.fullmatch(r"\d+\.0+", raw_text):
        return int(float(raw_text))
    compact = re.sub(r"[,\s]", "", raw_text)
    try:
        return int(float(compact))
    except ValueError:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


def format_metric(value):
    """Cell value for a metric: unknown stays blank (None), never 0; numbers parse like totals do."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return int(number) if number.is_integer() else number
    text = clean_text(value)
    if not text:
        return None
    if re.fullmatch(r"[\d.,\s]+", text) and re.search(r"\d", text):
        return metric_number(text)
    return text


def metric_total(values):
    """Sum the known metric values (a confirmed 0 counts); blank only when nothing is known."""
    known = [value for value in values if clean_text(value)]
    if not known:
        return ""
    return sum(metric_number(value) for value in known)
