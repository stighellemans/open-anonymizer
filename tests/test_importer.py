from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from open_anonymizer.services import importer as importer_module
from open_anonymizer.services.importer import (
    DocumentImportError,
    UnsupportedPdfError,
    extract_pdf_text,
    import_file,
    read_text_file,
)
from tests.helpers import (
    write_pdf_pages,
    write_pdf_stream,
    write_positioned_words_pdf,
    write_text_pdf,
    write_visible_graphics_pdf,
)


def test_read_text_file_decodes_cp1252(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_bytes("Résumé clinique".encode("cp1252"))

    assert read_text_file(file_path) == "Résumé clinique"


def test_import_file_reads_html_documents_as_text(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.html"
    file_path.write_text("<p>Résumé clinique</p>", encoding="utf-8")

    document = import_file(file_path, "doc-1")

    assert document.source_kind == "html"
    assert document.display_name == "sample.html"
    assert document.path == file_path
    assert document.raw_text == "<p>Résumé clinique</p>"


def test_extract_pdf_text_reads_text_based_pdf(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.pdf"
    write_text_pdf(file_path, "Bonjour clinique")

    assert "Bonjour clinique" in extract_pdf_text(file_path)

    document = import_file(file_path, "doc-2")
    assert document.source_kind == "pdf"
    assert len(document.pdf_pages) == 1
    assert document.pdf_pages[0].text == "Bonjour clinique"


def test_extract_pdf_text_recovers_spaces_for_positioned_words(tmp_path: Path) -> None:
    file_path = tmp_path / "positioned-words.pdf"
    write_positioned_words_pdf(
        file_path,
        [
            ("Bonjour", 72, 720),
            ("clinique", 140, 720),
        ],
    )

    assert extract_pdf_text(file_path) == "Bonjour clinique"


def test_extract_pdf_text_uses_whitespace_fallback_candidate(tmp_path: Path) -> None:
    file_path = tmp_path / "tj-spacing.pdf"
    stream = (
        "BT\n"
        "/F1 18 Tf\n"
        "72 720 Td\n"
        "[(Bonjour) 1200 (clinique)] TJ\n"
        "ET\n"
    ).encode("latin-1")
    write_pdf_stream(file_path, stream)

    assert extract_pdf_text(file_path) == "Bonjour clinique"


def test_extract_pdf_text_falls_back_when_pdfium_text_extraction_errors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    file_path = tmp_path / "pdfium-lookup-error.pdf"
    write_text_pdf(file_path, "Bonjour clinique")

    class FakePdfiumDocument:
        def __init__(self, _path: str) -> None:
            pass

        def __len__(self) -> int:
            return 1

        def close(self) -> None:
            pass

    def raise_lookup_error(_document: object, _page_index: int) -> str:
        raise LookupError("unknown encoding: utf-16-le")

    monkeypatch.setattr(
        importer_module,
        "pdfium",
        SimpleNamespace(PdfDocument=FakePdfiumDocument),
    )
    monkeypatch.setattr(importer_module, "_extract_pdfium_page_text", raise_lookup_error)

    assert extract_pdf_text(file_path) == "Bonjour clinique"


def test_extract_pdf_text_uses_ocr_fallback_for_visible_non_text_page(
    tmp_path: Path,
    monkeypatch,
) -> None:
    file_path = tmp_path / "scanned-like.pdf"
    write_visible_graphics_pdf(file_path)

    monkeypatch.setattr(
        "open_anonymizer.services.importer._run_tesseract_ocr",
        lambda image_path: "Texte clinique scanne",
    )

    assert extract_pdf_text(file_path) == "Texte clinique scanne"


def test_extract_pdf_text_only_ocrs_pages_without_native_text(
    tmp_path: Path,
    monkeypatch,
) -> None:
    file_path = tmp_path / "mixed.pdf"
    text_stream = "BT\n/F1 18 Tf\n72 720 Td\n(Bonjour clinique) Tj\nET\n".encode("latin-1")
    graphics_stream = b"0 0 0 rg\n72 700 200 50 re\nf\n"
    write_pdf_pages(file_path, [text_stream, graphics_stream])

    calls: list[str] = []

    def fake_ocr(image_path: Path) -> str:
        calls.append(image_path.name)
        return "Page scannee"

    monkeypatch.setattr("open_anonymizer.services.importer._run_tesseract_ocr", fake_ocr)

    assert extract_pdf_text(file_path) == "Bonjour clinique\n\nPage scannee"
    assert len(calls) == 1


def test_extract_pdf_text_errors_when_ocr_is_required_but_tesseract_is_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    file_path = tmp_path / "scanned-like.pdf"
    write_visible_graphics_pdf(file_path)

    monkeypatch.setattr("open_anonymizer.services.importer.find_tesseract_binary", lambda: None)

    with pytest.raises(DocumentImportError, match="Tesseract"):
        extract_pdf_text(file_path)


def test_extract_pdf_text_rejects_empty_pdf(tmp_path: Path) -> None:
    file_path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with file_path.open("wb") as handle:
        writer.write(handle)

    with pytest.raises(UnsupportedPdfError):
        extract_pdf_text(file_path)
