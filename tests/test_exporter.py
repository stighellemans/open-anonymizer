from pathlib import Path
from zipfile import ZipFile

from open_anonymizer.models import ImportedDocument, ProcessedDocument
from open_anonymizer.services.exporter import export_processed_documents, sanitize_stem


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
        "file-1": ProcessedDocument(document_id="file-1", output_text="<PATIENT>"),
        "paste-1": ProcessedDocument(document_id="paste-1", output_text="<DATE>"),
    }
    zip_path = tmp_path / "export.zip"

    result = export_processed_documents(documents, processed, zip_path)

    assert result.exported_count == 2
    assert result.skipped_count == 1

    with ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        assert "report_deidentified.txt" in names
        assert "pasted-text-001_deidentified.txt" in names
        report = archive.read("export-report.txt").decode("utf-8")
        assert "scan.pdf" in report
        assert "PDF does not contain extractable text." in report


def test_export_processed_documents_keeps_html_extension_for_html_inputs(tmp_path: Path) -> None:
    documents = [
        ImportedDocument(
            id="file-1",
            source_kind="text_file",
            display_name="report.html",
            path=tmp_path / "report.html",
            raw_text="<p>input</p>",
            status="ready",
        ),
    ]
    processed = {
        "file-1": ProcessedDocument(document_id="file-1", output_text="<p><PATIENT></p>"),
    }
    zip_path = tmp_path / "export.zip"

    export_processed_documents(documents, processed, zip_path)

    with ZipFile(zip_path) as archive:
        assert "report_deidentified.html" in archive.namelist()
        assert archive.read("report_deidentified.html").decode("utf-8") == "<p><PATIENT></p>"
