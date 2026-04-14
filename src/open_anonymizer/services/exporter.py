from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from open_anonymizer.models import ExportResult, ImportedDocument, ProcessedDocument

WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}
HTML_FILE_SUFFIXES = {".html", ".htm"}


def sanitize_stem(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", ascii_value).strip("._-")
    if not cleaned:
        cleaned = "document"
    if cleaned.upper() in WINDOWS_RESERVED_NAMES:
        cleaned = f"{cleaned}-file"
    return cleaned


def _ensure_unique_name(candidate: str, used_names: set[str]) -> str:
    if candidate not in used_names:
        used_names.add(candidate)
        return candidate

    stem = Path(candidate).stem
    suffix = Path(candidate).suffix
    counter = 2
    while True:
        alternative = f"{stem}-{counter}{suffix}"
        if alternative not in used_names:
            used_names.add(alternative)
            return alternative
        counter += 1


def _export_suffix_for_document(document: ImportedDocument) -> str:
    if document.path and document.path.suffix.lower() in HTML_FILE_SUFFIXES:
        return document.path.suffix.lower()
    return ".txt"


def export_processed_documents(
    documents: list[ImportedDocument],
    processed_documents: dict[str, ProcessedDocument],
    destination_zip: Path,
) -> ExportResult:
    destination_zip.parent.mkdir(parents=True, exist_ok=True)

    exported_count = 0
    skipped_count = 0
    used_names: set[str] = set()
    paste_counter = 0
    skipped_lines: list[str] = []

    with ZipFile(destination_zip, "w", compression=ZIP_DEFLATED) as archive:
        for document in documents:
            processed = processed_documents.get(document.id)
            if not processed:
                skipped_count += 1
                reason = document.error_message or "Document was not processed successfully."
                skipped_lines.append(f"- {document.display_name}: {reason}")
                continue

            if document.source_kind == "paste":
                paste_counter += 1
                export_name = f"pasted-text-{paste_counter:03d}_deidentified.txt"
            else:
                source_name = document.path.stem if document.path else Path(document.display_name).stem
                export_name = f"{sanitize_stem(source_name)}_deidentified{_export_suffix_for_document(document)}"

            export_name = _ensure_unique_name(export_name, used_names)
            archive.writestr(export_name, processed.output_text)
            exported_count += 1

        report_lines = [
            "Open Anonymizer Export Report",
            "",
            f"Exported documents: {exported_count}",
            f"Skipped documents: {skipped_count}",
            "",
            "Skipped",
        ]
        if skipped_lines:
            report_lines.extend(skipped_lines)
        else:
            report_lines.append("- None")

        archive.writestr("export-report.txt", "\n".join(report_lines).strip() + "\n")

    return ExportResult(zip_path=destination_zip, exported_count=exported_count, skipped_count=skipped_count)
