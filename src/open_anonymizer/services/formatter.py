from __future__ import annotations

from io import BytesIO
import re
from html.parser import HTMLParser

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMarginsF, QSizeF
from PySide6.QtGui import (
    QGuiApplication,
    QFont,
    QPageLayout,
    QPageSize,
    QPdfWriter,
    QTextDocument,
)
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    create_string_object,
)

from open_anonymizer.models import ImportedDocument, ProcessedDocument

DEFAULT_PDF_PAGE_WIDTH = 612.0
DEFAULT_PDF_PAGE_HEIGHT = 792.0
PDF_LEFT_MARGIN = 54.0
PDF_TOP_MARGIN = 58.0
PDF_BOTTOM_MARGIN = 58.0
PDF_RIGHT_MARGIN = 54.0
PDF_FONT_SIZE = 11.0
HTML_BLOCK_BREAK_TAGS = {
    "article",
    "br",
    "div",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "ol",
    "p",
    "section",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}
HTML_IGNORED_TAGS = {"script", "style"}
_PDF_APPLICATION: QGuiApplication | None = None


def render_document_as_plain_text(
    document: ImportedDocument,
    processed_document: ProcessedDocument,
) -> str:
    if document.source_kind == "html":
        return html_to_plain_text(processed_document.output_text)
    return processed_document.output_text


def render_document_as_pdf(
    document: ImportedDocument,
    processed_document: ProcessedDocument,
) -> bytes:
    _ensure_pdf_application()
    page_texts = processed_document.pdf_page_texts or [processed_document.output_text]
    page_sizes = [
        (page.width, page.height)
        for page in document.pdf_pages
    ] or [(DEFAULT_PDF_PAGE_WIDTH, DEFAULT_PDF_PAGE_HEIGHT)]

    pdf_writer = PdfWriter()
    for page_index, page_text in enumerate(page_texts):
        width, height = page_sizes[min(page_index, len(page_sizes) - 1)]
        for page in _render_pdf_pages(page_text, width, height):
            pdf_writer.add_page(page)

    if not pdf_writer.pages:
        page_pdf = _render_pdf_page_pdf_bytes(
            "",
            DEFAULT_PDF_PAGE_WIDTH,
            DEFAULT_PDF_PAGE_HEIGHT,
        )
        reader = PdfReader(BytesIO(page_pdf))
        for page in reader.pages:
            pdf_writer.add_page(page)

    output = BytesIO()
    pdf_writer.write(output)
    return output.getvalue()


class _HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._ignored_tag_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in HTML_IGNORED_TAGS:
            self._ignored_tag_stack.append(tag)
            return
        if tag in HTML_BLOCK_BREAK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._ignored_tag_stack and self._ignored_tag_stack[-1] == tag:
            self._ignored_tag_stack.pop()
            return
        if tag in HTML_BLOCK_BREAK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_tag_stack:
            return
        self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def html_to_plain_text(text: str) -> str:
    parser = _HtmlTextExtractor()
    parser.feed(text)
    parser.close()
    return _normalize_text(parser.get_text())


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    normalized_lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]

    compacted: list[str] = []
    blank_run = 0
    for line in normalized_lines:
        if line:
            compacted.append(line)
            blank_run = 0
            continue
        if blank_run == 0:
            compacted.append("")
        blank_run += 1

    return "\n".join(compacted).strip()


def _render_pdf_page_pdf_bytes(
    text: str,
    page_width: float,
    page_height: float,
) -> bytes:
    byte_array = QByteArray()
    buffer = QBuffer(byte_array)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)

    writer = QPdfWriter(buffer)
    writer.setResolution(72)
    writer.setPageSize(
        QPageSize(QSizeF(page_width, page_height), QPageSize.Unit.Point)
    )
    writer.setPageMargins(
        QMarginsF(
            PDF_LEFT_MARGIN,
            PDF_TOP_MARGIN,
            PDF_RIGHT_MARGIN,
            PDF_BOTTOM_MARGIN,
        ),
        QPageLayout.Unit.Point,
    )

    document = QTextDocument()
    document.setDefaultFont(_pdf_font())
    document.setDocumentMargin(0.0)
    document.setPlainText(text)
    document.setPageSize(
        QSizeF(
            max(1.0, page_width - PDF_LEFT_MARGIN - PDF_RIGHT_MARGIN),
            max(1.0, page_height - PDF_TOP_MARGIN - PDF_BOTTOM_MARGIN),
        )
    )
    document.print_(writer)

    buffer.close()
    return bytes(byte_array)


def _render_pdf_pages(
    text: str,
    page_width: float,
    page_height: float,
):
    reader = PdfReader(BytesIO(_render_pdf_page_pdf_bytes(text, page_width, page_height)))
    pages = list(reader.pages)
    if pages and not _pdf_pages_match_text(pages, text):
        pages[0].merge_page(_build_extractable_text_overlay_page(text, page_width, page_height))
    return pages


def _pdf_pages_match_text(pages, expected_text: str) -> bool:
    extracted_text = "\n".join(page.extract_text() or "" for page in pages)
    return _collapse_whitespace(extracted_text) == _collapse_whitespace(expected_text)


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _build_extractable_text_overlay_page(
    text: str,
    page_width: float,
    page_height: float,
):
    writer = PdfWriter()
    page = writer.add_blank_page(width=page_width, height=page_height)
    font_resource_name = NameObject("/F1")
    font_ref = writer._add_object(_build_extractable_font_dictionary(writer, text))
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject({font_resource_name: font_ref}),
        }
    )

    content = DecodedStreamObject()
    content.set_data(
        _build_extractable_text_content_stream(
            text,
            page_height,
            font_resource_name,
        )
    )
    page[NameObject("/Contents")] = writer._add_object(content)

    output = BytesIO()
    writer.write(output)
    return PdfReader(BytesIO(output.getvalue())).pages[0]


def _build_extractable_font_dictionary(
    writer: PdfWriter,
    text: str,
) -> DictionaryObject:
    to_unicode_stream = DecodedStreamObject()
    to_unicode_stream.set_data(_build_to_unicode_cmap(text))
    to_unicode_ref = writer._add_object(to_unicode_stream)

    cid_font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/CIDFontType2"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/CIDSystemInfo"): DictionaryObject(
                {
                    NameObject("/Registry"): create_string_object("Adobe"),
                    NameObject("/Ordering"): create_string_object("Identity"),
                    NameObject("/Supplement"): NumberObject(0),
                }
            ),
        }
    )
    cid_font_ref = writer._add_object(cid_font)

    return DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type0"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/Encoding"): NameObject("/Identity-H"),
            NameObject("/DescendantFonts"): ArrayObject([cid_font_ref]),
            NameObject("/ToUnicode"): to_unicode_ref,
        }
    )


def _build_extractable_text_content_stream(
    text: str,
    page_height: float,
    font_resource_name: NameObject,
) -> bytes:
    encoded_text, _ = _encode_text_for_overlay(text)
    y_position = max(1.0, page_height - 1.0)
    return (
        "q\n"
        "BT\n"
        f"{font_resource_name} 1 Tf\n"
        f"1 0 0 1 1 {_format_pdf_number(y_position)} Tm\n"
        "3 Tr\n"
        f"<{encoded_text}> Tj\n"
        "ET\n"
        "Q\n"
    ).encode("ascii")


def _build_to_unicode_cmap(text: str) -> bytes:
    _, character_codes = _encode_text_for_overlay(text)
    mapping_lines = [
        f"<{code:04X}> <{character.encode('utf-16-be').hex().upper()}>"
        for character, code in character_codes.items()
    ]
    lines = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<0001> <FFFF>",
        "endcodespacerange",
    ]
    for chunk_start in range(0, len(mapping_lines), 100):
        chunk = mapping_lines[chunk_start : chunk_start + 100]
        lines.append(f"{len(chunk)} beginbfchar")
        lines.extend(chunk)
        lines.append("endbfchar")
    lines.extend(
        [
            "endcmap",
            "CMapName currentdict /CMap defineresource pop",
            "end",
            "end",
            "",
        ]
    )
    return "\n".join(lines).encode("ascii")


def _encode_text_for_overlay(text: str) -> tuple[str, dict[str, int]]:
    character_codes: dict[str, int] = {}
    encoded_characters: list[str] = []
    next_code = 1

    for character in text:
        code = character_codes.get(character)
        if code is None:
            if next_code > 0xFFFF:
                raise ValueError("PDF overlay text exceeds the supported character set size.")
            code = next_code
            character_codes[character] = code
            next_code += 1
        encoded_characters.append(f"{code:04X}")

    return "".join(encoded_characters), character_codes


def _format_pdf_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _ensure_pdf_application() -> QGuiApplication:
    global _PDF_APPLICATION

    app = QGuiApplication.instance()
    if app is not None:
        return app

    if _PDF_APPLICATION is None:
        _PDF_APPLICATION = QGuiApplication([])

    return _PDF_APPLICATION


def _pdf_font() -> QFont:
    font = QGuiApplication.font()
    font.setPointSizeF(PDF_FONT_SIZE)
    return font
