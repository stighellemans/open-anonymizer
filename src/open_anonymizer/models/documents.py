from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal

DocumentSourceKind = Literal["paste", "text_file", "html", "pdf"]
DocumentStatus = Literal["pending", "processing", "ready", "error"]
ExportMode = Literal["original_formats", "text_files"]
AnonymizationMode = Literal["placeholders", "smart_pseudonyms"]
RecognitionGroupName = Literal[
    "names",
    "locations",
    "institutions",
    "dates",
    "ages",
    "identifiers",
    "phone_numbers",
    "email_addresses",
    "urls",
]

RECOGNITION_GROUPS: tuple[RecognitionGroupName, ...] = (
    "names",
    "locations",
    "institutions",
    "dates",
    "ages",
    "identifiers",
    "phone_numbers",
    "email_addresses",
    "urls",
)


@dataclass(frozen=True)
class RecognitionFlags:
    names: bool = True
    locations: bool = True
    institutions: bool = True
    dates: bool = True
    ages: bool = True
    identifiers: bool = True
    phone_numbers: bool = True
    email_addresses: bool = True
    urls: bool = True

    def as_key(self) -> tuple[bool, ...]:
        return tuple(getattr(self, name) for name in RECOGNITION_GROUPS)


@dataclass
class AnonymizationSettings:
    first_name: str = ""
    last_name: str = ""
    birthdate: date | None = None
    date_shift_days: int | None = None
    other_names: list[str] = field(default_factory=list)
    custom_addresses: list[str] = field(default_factory=list)
    deidentify_filenames: bool = True
    mode: AnonymizationMode = "placeholders"
    recognition_flags: RecognitionFlags = field(default_factory=RecognitionFlags)


@dataclass
class ImportedDocument:
    id: str
    source_kind: DocumentSourceKind
    display_name: str
    path: Path | None = None
    raw_text: str | None = None
    pdf_pages: list["PdfPage"] = field(default_factory=list)
    status: DocumentStatus = "pending"
    error_message: str | None = None


@dataclass(frozen=True)
class PdfPage:
    text: str
    width: float = 612.0
    height: float = 792.0


@dataclass
class ProcessedDocument:
    document_id: str
    output_text: str
    pdf_page_texts: list[str] = field(default_factory=list)
    placeholder_references: dict[str, tuple[str, ...]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ProcessBatchRequest:
    anonymization_settings: AnonymizationSettings
    documents: list[ImportedDocument]
    context_generation: int


@dataclass
class ExportResult:
    zip_path: Path
    exported_count: int
    skipped_count: int
