from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal

DocumentSourceKind = Literal["paste", "text_file", "pdf"]
DocumentStatus = Literal["pending", "processing", "ready", "error"]


@dataclass
class PatientContext:
    first_name: str = ""
    last_name: str = ""
    birthdate: date | None = None


@dataclass
class ImportedDocument:
    id: str
    source_kind: DocumentSourceKind
    display_name: str
    path: Path | None = None
    raw_text: str | None = None
    status: DocumentStatus = "pending"
    error_message: str | None = None


@dataclass
class ProcessedDocument:
    document_id: str
    output_text: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class ProcessBatchRequest:
    patient_context: PatientContext
    documents: list[ImportedDocument]
    context_generation: int


@dataclass
class ExportResult:
    zip_path: Path
    exported_count: int
    skipped_count: int
