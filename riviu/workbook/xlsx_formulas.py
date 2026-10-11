"""Formula text read straight from the .xlsx sheet XML (pandas only sees cached values)."""

import html
import io
import posixpath
import re
import zipfile
from xml.etree import ElementTree

from openpyxl.formula.translate import Translator


XLSX_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
XLSX_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XLSX_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_XML_FORMULA_RE = re.compile(r"<(?:[\w.-]+:)?f\b([^>]*?)(?:/>|>(.*?)</(?:[\w.-]+:)?f>)", re.S)
_XML_ATTRIBUTE_RE = re.compile(r'([\w:.-]+)\s*=\s*"([^"]*)"')


def worksheet_xml_part(archive, sheet_name):
    workbook_xml = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    relation_id = next(
        (
            sheet.get(f"{{{XLSX_REL_NS}}}id")
            for sheet in workbook_xml.iter(f"{{{XLSX_MAIN_NS}}}sheet")
            if sheet.get("name") == sheet_name
        ),
        None,
    )
    if not relation_id:
        return None
    relations = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    for relation in relations.iter(f"{{{XLSX_PACKAGE_REL_NS}}}Relationship"):
        if relation.get("Id") == relation_id:
            target = relation.get("Target", "")
            return target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
    return None


def worksheet_column_formulas(content, sheet_name, column_letter):
    """Formulas of one column, read straight from the sheet XML ({row: "=..."}).

    pandas reads cached values only, and openpyxl-saved workbooks carry no cached values, so a
    formula column looks blank. Shared formulas are expanded like openpyxl does on load.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            part = worksheet_xml_part(archive, sheet_name)
            if not part:
                return {}
            sheet_xml = archive.read(part).decode("utf-8")
    except (KeyError, zipfile.BadZipFile, ElementTree.ParseError, UnicodeDecodeError):
        return {}
    cell_pattern = re.compile(
        r'<(?:[\w.-]+:)?c\s[^>]*?\br="' + re.escape(column_letter) + r'(\d+)"[^>]*?(?:/>|>(.*?)</(?:[\w.-]+:)?c>)',
        re.S,
    )
    formulas = {}
    shared = {}
    for match in cell_pattern.finditer(sheet_xml):
        formula_match = _XML_FORMULA_RE.search(match.group(2) or "")
        if not formula_match:
            continue
        row = int(match.group(1))
        attributes = dict(_XML_ATTRIBUTE_RE.findall(formula_match.group(1)))
        text = html.unescape(formula_match.group(2) or "").strip()
        if attributes.get("t") == "shared" and "si" in attributes:
            if text:
                shared[attributes["si"]] = (f"={text}", f"{column_letter}{row}")
            elif attributes["si"] in shared:
                formula, origin = shared[attributes["si"]]
                formulas[row] = Translator(formula, origin=origin).translate_formula(f"{column_letter}{row}")
                continue
        if text:
            formulas[row] = f"={text}"
    return formulas
