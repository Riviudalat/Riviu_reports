"""Faster openpyxl reads of inline-string cells (Google Sheets .xlsx exports).

A Google Sheets export stores every text cell as an inline string
(``<c t="inlineStr"><is><t>…</t></is></c>``) instead of the shared-string table
Excel and openpyxl write. openpyxl parses each one through its generic
``Text.from_tree`` deserializer, which costs most of the load time of a synced
month workbook (about 170,000 text cells for a 1,900-row TikTok report).

For the plain form, one ``<t>`` child and nothing else, the cell value is just
that element's text, so it is read directly. Every other form (rich-text runs,
phonetic runs, unexpected attributes or children) still goes through openpyxl's
own parser, so values are identical either way.
"""

from openpyxl.cell import text as _openpyxl_text
from openpyxl.worksheet import _reader as _openpyxl_reader


_SHEET_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_PLAIN_TEXT_TAG = "{%s}t" % _SHEET_MAIN_NS
_XML_SPACE_ATTRIBUTE = "{http://www.w3.org/XML/1998/namespace}space"


class _PlainInlineText:
    """What openpyxl's reader needs from a parsed inline string: its ``content``."""

    __slots__ = ("content",)

    def __init__(self, content):
        self.content = content


def plain_inline_text(node):
    """The text of ``<is><t>…</t></is>``, or None when the node is any other form."""
    if node.attrib or len(node) != 1:
        return None
    child = node[0]
    if child.tag != _PLAIN_TEXT_TAG or len(child):
        return None
    if child.attrib and set(child.attrib) != {_XML_SPACE_ATTRIBUTE}:
        return None
    return child.text or ""


class InlineText:
    """Stand-in for ``openpyxl.cell.text.Text`` inside openpyxl's worksheet reader."""

    @staticmethod
    def from_tree(node):
        content = plain_inline_text(node)
        if content is None:
            return _openpyxl_text.Text.from_tree(node)
        return _PlainInlineText(content)


def install():
    """Use the fast path in openpyxl's worksheet reader; a no-op when its layout differs.

    The reader looks up ``Text`` only to parse inline strings (openpyxl 3.1). If a
    future openpyxl stops doing so, nothing is replaced and reads stay as they are.
    """
    if getattr(_openpyxl_reader, "Text", None) is _openpyxl_text.Text:
        _openpyxl_reader.Text = InlineText
    return _openpyxl_reader.Text is InlineText


install()
