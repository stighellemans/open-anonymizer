from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import threading

from PySide6.QtCore import QObject, Signal

from open_anonymizer.models import (
    AnonymizationSettings,
    ImportedDocument,
    ProcessBatchRequest,
    ProcessedDocument,
)
from open_anonymizer.services import backend_runtime
from open_anonymizer.services.deduce_backend import warm_backend_for_settings
from open_anonymizer.services.deidentifier import deidentify_document
from open_anonymizer.services.importer import (
    DocumentImportError,
    ImportCancelledError,
    import_file,
)


@dataclass
class BatchProcessingResult:
    processed_documents: dict[str, ProcessedDocument]
    errors: dict[str, str]
    document_ids: list[str]
    context_generation: int


@dataclass
class BatchProcessingFailure:
    message: str
    context_generation: int


@dataclass(frozen=True)
class ImportDocumentRequest:
    path: Path
    document_id: str


@dataclass(frozen=True)
class ImportDocumentResult:
    document: ImportedDocument


@dataclass(frozen=True)
class BackendWarmupRequest:
    settings: AnonymizationSettings
    flags_key: tuple[bool, ...]


@dataclass(frozen=True)
class BackendWarmupResult:
    flags_key: tuple[bool, ...]


@dataclass(frozen=True)
class BackendWarmupFailure:
    message: str
    flags_key: tuple[bool, ...]


class BatchWorkerSignals(QObject):
    completed = Signal(object)
    failed = Signal(object)


class ImportWorkerSignals(QObject):
    completed = Signal(object)


class BackendWarmupSignals(QObject):
    completed = Signal(object)
    failed = Signal(object)


class _DaemonWorker:
    def __init__(self, thread_name: str):
        self._thread_name = thread_name
        self._thread: threading.Thread | None = None
        self._cancel_event = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            return

        self._thread = threading.Thread(
            target=self._run,
            name=self._thread_name,
            daemon=True,
        )
        self._thread.start()

    def cancel(self) -> None:
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def _run(self) -> None:
        raise NotImplementedError


class BatchProcessorRunnable(_DaemonWorker):
    def __init__(self, request: ProcessBatchRequest):
        super().__init__(thread_name=f"open-anonymizer-batch-{request.context_generation}")
        self.request = request
        self.signals = BatchWorkerSignals()
        self._backend_job_handle = None

    def start(self) -> None:
        if self._should_use_backend_runtime():
            self._backend_job_handle = backend_runtime.submit_batch_job(
                self.request,
                completed=self._handle_backend_runtime_completed,
                failed=self._handle_backend_runtime_failed,
            )
            return

        super().start()

    def cancel(self) -> None:
        if self._backend_job_handle is not None:
            self._backend_job_handle.cancel()
            self._backend_job_handle = None
            return

        super().cancel()

    def _should_use_backend_runtime(self) -> bool:
        if os.getenv("OPEN_ANONYMIZER_DISABLE_BACKEND_RUNTIME", "").strip() == "1":
            return False
        return deidentify_document is _DEFAULT_DEIDENTIFY_DOCUMENT

    def _run(self) -> None:
        try:
            processed_documents: dict[str, ProcessedDocument] = {}
            errors: dict[str, str] = {}
            for document in self.request.documents:
                if self.is_cancelled():
                    return

                try:
                    processed_documents[document.id] = deidentify_document(
                        document,
                        self.request.anonymization_settings,
                    )
                except Exception as exc:
                    if self.is_cancelled():
                        return
                    errors[document.id] = str(exc)

            if self.is_cancelled():
                return

            self.signals.completed.emit(
                BatchProcessingResult(
                    processed_documents=processed_documents,
                    errors=errors,
                    document_ids=[document.id for document in self.request.documents],
                    context_generation=self.request.context_generation,
                )
            )
        except Exception as exc:
            if self.is_cancelled():
                return
            self.signals.failed.emit(
                BatchProcessingFailure(
                    message=str(exc),
                    context_generation=self.request.context_generation,
                )
            )

    def _handle_backend_runtime_completed(self, response: dict[str, object]) -> None:
        self._backend_job_handle = None
        self.signals.completed.emit(
            BatchProcessingResult(
                processed_documents=response.get("processed_documents", {}),
                errors=response.get("errors", {}),
                document_ids=response.get("document_ids", []),
                context_generation=response.get(
                    "context_generation",
                    self.request.context_generation,
                ),
            )
        )

    def _handle_backend_runtime_failed(self, message: str) -> None:
        self._backend_job_handle = None
        self.signals.failed.emit(
            BatchProcessingFailure(
                message=message,
                context_generation=self.request.context_generation,
            )
        )


class ImportDocumentRunnable(_DaemonWorker):
    def __init__(self, request: ImportDocumentRequest):
        super().__init__(thread_name=f"open-anonymizer-import-{request.document_id}")
        self.request = request
        self.signals = ImportWorkerSignals()

    def _run(self) -> None:
        try:
            document = self._import_or_error_document()
        except ImportCancelledError:
            return

        if self.is_cancelled():
            return

        self.signals.completed.emit(ImportDocumentResult(document=document))

    def _import_or_error_document(self) -> ImportedDocument:
        path = self.request.path
        document_id = self.request.document_id
        try:
            return import_file(
                path,
                document_id,
                should_cancel=self.is_cancelled,
            )
        except ImportCancelledError:
            raise
        except DocumentImportError as exc:
            return _build_import_error_document(path, document_id, str(exc))
        except Exception as exc:
            return _build_import_error_document(
                path,
                document_id,
                str(exc) or "Unexpected import failure.",
            )


class BackendWarmupRunnable(_DaemonWorker):
    def __init__(self, request: BackendWarmupRequest):
        super().__init__(
            thread_name=(
                "open-anonymizer-backend-warmup-"
                + "".join("1" if enabled else "0" for enabled in request.flags_key)
            )
        )
        self.request = request
        self.signals = BackendWarmupSignals()
        self._backend_job_handle = None

    def start(self) -> None:
        if self._should_use_backend_runtime():
            self._backend_job_handle = backend_runtime.submit_warmup_job(
                settings=self.request.settings,
                flags_key=self.request.flags_key,
                completed=self._handle_backend_runtime_completed,
                failed=self._handle_backend_runtime_failed,
            )
            return

        super().start()

    def cancel(self) -> None:
        if self._backend_job_handle is not None:
            self._backend_job_handle.cancel()
            self._backend_job_handle = None
            return

        super().cancel()

    def _should_use_backend_runtime(self) -> bool:
        if os.getenv("OPEN_ANONYMIZER_DISABLE_BACKEND_RUNTIME", "").strip() == "1":
            return False
        return warm_backend_for_settings is _DEFAULT_WARM_BACKEND_FOR_SETTINGS

    def _run(self) -> None:
        try:
            warm_backend_for_settings(self.request.settings)
        except Exception as exc:
            if self.is_cancelled():
                return
            self.signals.failed.emit(
                BackendWarmupFailure(
                    message=str(exc) or "Backend warmup failed.",
                    flags_key=self.request.flags_key,
                )
            )
            return

        if self.is_cancelled():
            return

        self.signals.completed.emit(
            BackendWarmupResult(flags_key=self.request.flags_key)
        )

    def _handle_backend_runtime_completed(self, response: dict[str, object]) -> None:
        self._backend_job_handle = None
        self.signals.completed.emit(
            BackendWarmupResult(
                flags_key=response.get("flags_key", self.request.flags_key)
            )
        )

    def _handle_backend_runtime_failed(self, message: str) -> None:
        self._backend_job_handle = None
        self.signals.failed.emit(
            BackendWarmupFailure(
                message=message,
                flags_key=self.request.flags_key,
            )
        )


def _build_import_error_document(
    path: Path,
    document_id: str,
    message: str,
) -> ImportedDocument:
    return ImportedDocument(
        id=document_id,
        source_kind=_source_kind_for_path(path),
        display_name=path.name,
        path=path,
        status="error",
        error_message=message,
    )


def _source_kind_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".html", ".htm"}:
        return "html"
    return "text_file"


_DEFAULT_DEIDENTIFY_DOCUMENT = deidentify_document
_DEFAULT_WARM_BACKEND_FOR_SETTINGS = warm_backend_for_settings
