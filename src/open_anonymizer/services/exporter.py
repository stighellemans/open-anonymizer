from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from open_anonymizer.models import (
    AnonymizationSettings,
    ExportMode,
    ExportResult,
    ImportedDocument,
    ProcessedDocument,
)
from open_anonymizer.services.deidentifier import (
    deidentify_filename_stem_result,
    hashed_deidentified_filename_stem,
)
from open_anonymizer.services.formatter import (
    render_document_as_pdf,
    render_document_as_plain_text,
)
from open_anonymizer.services.smart_pseudonymizer import (
    effective_date_shift_days,
    format_date_shift_days,
)

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
PLACEHOLDER_PATTERN = re.compile(r"\[[A-Z][A-Z0-9_+]*(?:-\d+)?\]")


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
    if document.source_kind == "html" and document.path:
        return document.path.suffix.lower()
    if document.source_kind == "pdf":
        return ".pdf"
    return ".txt"


def _export_bytes_for_document(
    document: ImportedDocument,
    processed_document: ProcessedDocument,
    export_mode: ExportMode,
) -> bytes:
    if export_mode == "text_files":
        return render_document_as_plain_text(document, processed_document).encode("utf-8")

    if document.source_kind == "html":
        return processed_document.output_text.encode("utf-8")

    if document.source_kind == "pdf":
        return render_document_as_pdf(document, processed_document)

    return processed_document.output_text.encode("utf-8")


def _export_mode_label(export_mode: ExportMode) -> str:
    if export_mode == "original_formats":
        return "Original formats"
    return "Text files"


def _source_stem(document: ImportedDocument) -> str:
    return document.path.stem if document.path else Path(document.display_name).stem


def _display_suffix(document: ImportedDocument) -> str:
    return document.path.suffix if document.path else Path(document.display_name).suffix


def _export_stem(
    document: ImportedDocument,
    anonymization_settings: AnonymizationSettings,
) -> str:
    stem = _source_stem(document)
    if anonymization_settings.deidentify_filenames:
        stem, recognized_sensitive_content = deidentify_filename_stem_result(
            stem,
            anonymization_settings,
            document=document,
        )
        if recognized_sensitive_content:
            stem = hashed_deidentified_filename_stem(stem, document=document)
    return sanitize_stem(stem)


def _report_display_name(
    document: ImportedDocument,
    anonymization_settings: AnonymizationSettings,
) -> str:
    if not anonymization_settings.deidentify_filenames or document.source_kind == "paste":
        return document.display_name

    suffix = _display_suffix(document)
    stem = _export_stem(document, anonymization_settings)
    return f"{stem}{suffix}"


def _date_shift_report_lines(
    exported_documents: list[tuple[ImportedDocument, str]],
    anonymization_settings: AnonymizationSettings,
) -> list[str]:
    if anonymization_settings.mode != "smart_pseudonyms":
        return ["Date shift: Not used"]

    if anonymization_settings.date_shift_days is not None:
        return [
            f"Date shift: {format_date_shift_days(anonymization_settings.date_shift_days)} (manual)"
        ]

    auto_shift_days, _ = effective_date_shift_days(
        anonymization_settings,
    )
    if auto_shift_days is not None:
        return [f"Date shift: {format_date_shift_days(auto_shift_days)} (auto)"]
    return ["Date shift: Auto"]


def _smart_placeholder_report_lines(
    exported_documents: list[tuple[ImportedDocument, str, ProcessedDocument]],
    anonymization_settings: AnonymizationSettings,
) -> list[str]:
    if anonymization_settings.mode != "smart_pseudonyms":
        return []

    lines = ["", "Smart placeholder mappings (original => replacement)"]
    has_mappings = False

    for _, export_name, processed_document in exported_documents:
        pseudonym_lines: list[str] = []
        for replacement, originals in processed_document.placeholder_references.items():
            if PLACEHOLDER_PATTERN.fullmatch(replacement):
                continue

            for original in originals:
                pseudonym_lines.append(f"- {original} => {replacement}")

        if not pseudonym_lines:
            continue

        has_mappings = True
        lines.append(f"{export_name}")
        lines.extend(pseudonym_lines)

    if not has_mappings:
        lines.append("- None")

    return lines


def export_processed_documents(
    documents: list[ImportedDocument],
    processed_documents: dict[str, ProcessedDocument],
    destination_zip: Path,
    export_mode: ExportMode = "original_formats",
    anonymization_settings: AnonymizationSettings | None = None,
) -> ExportResult:
    anonymization_settings = anonymization_settings or AnonymizationSettings()
    destination_zip.parent.mkdir(parents=True, exist_ok=True)

    exported_count = 0
    skipped_count = 0
    used_names: set[str] = set()
    paste_counter = 0
    skipped_lines: list[str] = []
    exported_documents: list[tuple[ImportedDocument, str, ProcessedDocument]] = []

    with ZipFile(destination_zip, "w", compression=ZIP_DEFLATED) as archive:
        for document in documents:
            processed = processed_documents.get(document.id)
            if not processed:
                skipped_count += 1
                reason = document.error_message or "Document was not processed successfully."
                skipped_lines.append(
                    f"- {_report_display_name(document, anonymization_settings)}: {reason}"
                )
                continue

            if document.source_kind == "paste":
                paste_counter += 1
                export_name = f"pasted-text-{paste_counter:03d}.txt"
            else:
                if export_mode == "text_files":
                    export_suffix = ".txt"
                else:
                    export_suffix = _export_suffix_for_document(document)
                export_name = f"{_export_stem(document, anonymization_settings)}{export_suffix}"

            export_name = _ensure_unique_name(export_name, used_names)
            archive.writestr(
                export_name,
                _export_bytes_for_document(document, processed, export_mode),
            )
            exported_count += 1
            exported_documents.append((document, export_name, processed))

        report_lines = [
            "Open Anonymizer Export Report",
            "",
            f"Export mode: {_export_mode_label(export_mode)}",
            *_date_shift_report_lines(
                [(document, export_name) for document, export_name, _ in exported_documents],
                anonymization_settings,
            ),
            *_smart_placeholder_report_lines(exported_documents, anonymization_settings),
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
