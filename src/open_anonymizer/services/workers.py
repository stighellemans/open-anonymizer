from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QRunnable, Signal

from open_anonymizer.models import ProcessBatchRequest, ProcessedDocument
from open_anonymizer.services.deidentifier import deidentify_document


@dataclass
class BatchProcessingResult:
    processed_documents: dict[str, ProcessedDocument]
    errors: dict[str, str]
    document_ids: list[str]
    context_generation: int


class BatchWorkerSignals(QObject):
    completed = Signal(object)
    failed = Signal(str)


class BatchProcessorRunnable(QRunnable):
    def __init__(self, request: ProcessBatchRequest):
        super().__init__()
        self.request = request
        self.signals = BatchWorkerSignals()

    def run(self) -> None:
        try:
            processed_documents: dict[str, ProcessedDocument] = {}
            errors: dict[str, str] = {}
            for document in self.request.documents:
                try:
                    processed_documents[document.id] = deidentify_document(
                        document,
                        self.request.patient_context,
                    )
                except Exception as exc:
                    errors[document.id] = str(exc)

            self.signals.completed.emit(
                BatchProcessingResult(
                    processed_documents=processed_documents,
                    errors=errors,
                    document_ids=[document.id for document in self.request.documents],
                    context_generation=self.request.context_generation,
                )
            )
        except Exception as exc:
            self.signals.failed.emit(str(exc))
