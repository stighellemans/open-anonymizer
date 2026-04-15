from io import BytesIO
import hashlib
from pathlib import Path
from zipfile import ZipFile

from pypdf import PdfReader

from open_anonymizer.models import AnonymizationSettings, ImportedDocument, PdfPage, ProcessedDocument
from open_anonymizer.services.exporter import export_processed_documents, sanitize_stem
from open_anonymizer.services.smart_pseudonymizer import effective_date_shift_days


def test_sanitize_stem_removes_unsafe_characters() -> None:
    assert sanitize_stem('Patient: "John"/Report?') == "Patient-John-Report"


def test_export_processed_documents_writes_zip_and_report(tmp_path: Path) -> None:
    documents = [
        ImportedDocument(
            id="file-1",
            source_kind="text_file",
            display_name="report.txt",
            path=tmp_path / "report.txt",
            raw_text="input",
            status="ready",
        ),
        ImportedDocument(
            id="paste-1",
            source_kind="paste",
            display_name="Pasted Text 001",
            raw_text="input",
            status="ready",
        ),
        ImportedDocument(
            id="bad-1",
            source_kind="pdf",
            display_name="scan.pdf",
            path=tmp_path / "scan.pdf",
            status="error",
            error_message="PDF does not contain extractable text.",
        ),
    ]
    processed = {
        "file-1": ProcessedDocument(document_id="file-1", output_text="[PATIENT]"),
        "paste-1": ProcessedDocument(document_id="paste-1", output_text="[DATE-1]"),
    }
    zip_path = tmp_path / "export.zip"

    result = export_processed_documents(documents, processed, zip_path)

    assert result.exported_count == 2
    assert result.skipped_count == 1

    with ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        assert "report.txt" in names
        assert "pasted-text-001.txt" in names
        report = archive.read("export-report.txt").decode("utf-8")
        assert "Date shift: Not used" in report
        assert "scan.pdf" in report
        assert "PDF does not contain extractable text." in report


def test_export_processed_documents_report_includes_manual_date_shift(tmp_path: Path) -> None:
    documents = [
        ImportedDocument(
            id="file-1",
            source_kind="text_file",
            display_name="report.txt",
            path=tmp_path / "report.txt",
            raw_text="input",
            status="ready",
        ),
    ]
    processed = {
        "file-1": ProcessedDocument(document_id="file-1", output_text="[PATIENT]"),
    }
    zip_path = tmp_path / "export.zip"

    export_processed_documents(
        documents,
        processed,
        zip_path,
        anonymization_settings=AnonymizationSettings(
            mode="smart_pseudonyms",
            date_shift_days=12,
        ),
    )

    with ZipFile(zip_path) as archive:
        report = archive.read("export-report.txt").decode("utf-8")
        assert "Date shift: +12 days (manual)" in report


def test_export_processed_documents_report_includes_smart_placeholder_mappings(
    tmp_path: Path,
) -> None:
    documents = [
        ImportedDocument(
            id="file-1",
            source_kind="text_file",
            display_name="report.txt",
            path=tmp_path / "report.txt",
            raw_text="input",
            status="ready",
        ),
    ]
    processed = {
        "file-1": ProcessedDocument(
            document_id="file-1",
            output_text="Marie Peeters bezocht Ziekenhuis Horizon op 11 fevrier 1990.",
            placeholder_references={
                "Marie Peeters": ("Jean Dupont",),
                "Ziekenhuis Horizon": ("UZ Leuven",),
                "11 fevrier 1990": ("1 février 1990",),
                "[IDENTIFIER-1]": ("12345",),
            },
        ),
    }
    zip_path = tmp_path / "export.zip"

    export_processed_documents(
        documents,
        processed,
        zip_path,
        anonymization_settings=AnonymizationSettings(mode="smart_pseudonyms"),
    )

    with ZipFile(zip_path) as archive:
        report = archive.read("export-report.txt").decode("utf-8")
        assert "Smart placeholder mappings (original => replacement)" in report
        assert "report.txt" in report
        assert "- Jean Dupont => Marie Peeters" in report
        assert "- UZ Leuven => Ziekenhuis Horizon" in report
        assert "- 1 février 1990 => 11 fevrier 1990" in report
        assert "[IDENTIFIER-1]" not in report


def test_export_processed_documents_report_lists_per_document_auto_date_shifts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    documents = [
        ImportedDocument(
            id="file-1",
            source_kind="text_file",
            display_name="first.txt",
            path=tmp_path / "first.txt",
            raw_text="input one",
            status="ready",
        ),
        ImportedDocument(
            id="file-2",
            source_kind="text_file",
            display_name="second.txt",
            path=tmp_path / "second.txt",
            raw_text="input two",
            status="ready",
        ),
    ]
    processed = {
        "file-1": ProcessedDocument(document_id="file-1", output_text="[PATIENT]"),
        "file-2": ProcessedDocument(document_id="file-2", output_text="[PATIENT]"),
    }
    zip_path = tmp_path / "export.zip"
    settings = AnonymizationSettings(mode="smart_pseudonyms")

    monkeypatch.setattr(
        "open_anonymizer.services.smart_pseudonymizer._local_secret_bytes",
        lambda: b"\x77" * 32,
    )

    export_processed_documents(
        documents,
        processed,
        zip_path,
        anonymization_settings=settings,
    )

    with ZipFile(zip_path) as archive:
        report = archive.read("export-report.txt").decode("utf-8")
        auto_shift, _ = effective_date_shift_days(settings)
        assert f"Date shift: {auto_shift:+d} {'day' if abs(auto_shift or 0) == 1 else 'days'} (auto)" in report


def test_export_processed_documents_keeps_html_extension_for_html_inputs(tmp_path: Path) -> None:
    documents = [
        ImportedDocument(
            id="file-1",
            source_kind="html",
            display_name="report.html",
            path=tmp_path / "report.html",
            raw_text="<p>input</p>",
            status="ready",
        ),
    ]
    processed = {
        "file-1": ProcessedDocument(document_id="file-1", output_text="<p>[PATIENT]</p>"),
    }
    zip_path = tmp_path / "export.zip"

    export_processed_documents(documents, processed, zip_path)

    with ZipFile(zip_path) as archive:
        assert "report.html" in archive.namelist()
        assert archive.read("report.html").decode("utf-8") == "<p>[PATIENT]</p>"


def test_export_processed_documents_text_mode_exports_html_as_plain_text(
    tmp_path: Path,
) -> None:
    documents = [
        ImportedDocument(
            id="file-1",
            source_kind="html",
            display_name="report.html",
            path=tmp_path / "report.html",
            raw_text="<p>input</p>",
            status="ready",
        ),
    ]
    processed = {
        "file-1": ProcessedDocument(
            document_id="file-1",
            output_text="<p>[PATIENT]</p><p>Line two</p>",
        ),
    }
    zip_path = tmp_path / "export.zip"

    export_processed_documents(
        documents,
        processed,
        zip_path,
        export_mode="text_files",
    )

    with ZipFile(zip_path) as archive:
        assert "report.txt" in archive.namelist()
        assert archive.read("report.txt").decode("utf-8") == "[PATIENT]\n\nLine two"


def test_export_processed_documents_rebuilds_pdf_in_original_mode(tmp_path: Path) -> None:
    documents = [
        ImportedDocument(
            id="file-1",
            source_kind="pdf",
            display_name="report.pdf",
            path=tmp_path / "report.pdf",
            raw_text="Source text",
            pdf_pages=[
                PdfPage(text="Source text", width=320.0, height=400.0),
                PdfPage(text="Second page", width=320.0, height=420.0),
            ],
            status="ready",
        ),
    ]
    processed = {
        "file-1": ProcessedDocument(
            document_id="file-1",
            output_text="[PATIENT]",
            pdf_page_texts=["[PATIENT]", "[DATE-1]"],
        ),
    }
    zip_path = tmp_path / "export.zip"

    export_processed_documents(
        documents,
        processed,
        zip_path,
        export_mode="original_formats",
    )

    with ZipFile(zip_path) as archive:
        assert "report.pdf" in archive.namelist()
        pdf_reader = PdfReader(BytesIO(archive.read("report.pdf")))
        assert len(pdf_reader.pages) == 2
        assert "[PATIENT]" in (pdf_reader.pages[0].extract_text() or "")
        assert "[DATE-1]" in (pdf_reader.pages[1].extract_text() or "")


def test_export_processed_documents_rebuilds_pdf_with_unicode_text(tmp_path: Path) -> None:
    documents = [
        ImportedDocument(
            id="file-1",
            source_kind="pdf",
            display_name="report.pdf",
            path=tmp_path / "report.pdf",
            raw_text="patiënt",
            pdf_pages=[PdfPage(text="patiënt", width=320.0, height=400.0)],
            status="ready",
        ),
    ]
    processed = {
        "file-1": ProcessedDocument(
            document_id="file-1",
            output_text="patiënt élève Český",
            pdf_page_texts=["patiënt élève Český"],
        ),
    }
    zip_path = tmp_path / "export.zip"

    export_processed_documents(
        documents,
        processed,
        zip_path,
        export_mode="original_formats",
    )

    with ZipFile(zip_path) as archive:
        pdf_reader = PdfReader(BytesIO(archive.read("report.pdf")))
        extracted = pdf_reader.pages[0].extract_text() or ""
        assert "patiënt" in extracted
        assert "élève" in extracted
        assert "Český" in extracted


def test_export_processed_documents_deidentifies_file_names_when_enabled(tmp_path: Path) -> None:
    document_path = tmp_path / "Jean_Dupont_report.txt"
    documents = [
        ImportedDocument(
            id="file-1",
            source_kind="text_file",
            display_name=document_path.name,
            path=document_path,
            raw_text="input",
            status="ready",
        ),
    ]
    processed = {
        "file-1": ProcessedDocument(document_id="file-1", output_text="[PATIENT]"),
    }
    zip_path = tmp_path / "export.zip"

    export_processed_documents(
        documents,
        processed,
        zip_path,
        anonymization_settings=AnonymizationSettings(
            first_name="Jean",
            last_name="Dupont",
        ),
    )

    with ZipFile(zip_path) as archive:
        expected_hash = hashlib.sha256(b"input").hexdigest()[:10]
        assert f"{expected_hash}_deid.txt" in archive.namelist()


def test_export_processed_documents_keeps_original_file_name_when_disabled(tmp_path: Path) -> None:
    document_path = tmp_path / "Jean Dupont report.txt"
    documents = [
        ImportedDocument(
            id="file-1",
            source_kind="text_file",
            display_name=document_path.name,
            path=document_path,
            raw_text="input",
            status="ready",
        ),
    ]
    processed = {
        "file-1": ProcessedDocument(document_id="file-1", output_text="[PATIENT]"),
    }
    zip_path = tmp_path / "export.zip"

    export_processed_documents(
        documents,
        processed,
        zip_path,
        anonymization_settings=AnonymizationSettings(
            first_name="Jean",
            last_name="Dupont",
            deidentify_filenames=False,
        ),
    )

    with ZipFile(zip_path) as archive:
        assert "Jean-Dupont-report.txt" in archive.namelist()
