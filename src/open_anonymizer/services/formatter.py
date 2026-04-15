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
        page_pdf = _render_pdf_page_pdf_bytes(page_text, width, height)
        reader = PdfReader(BytesIO(page_pdf))
        for page in reader.pages:
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
