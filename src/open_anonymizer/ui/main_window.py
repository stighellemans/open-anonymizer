from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QThreadPool, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from open_anonymizer.models import ImportedDocument, PatientContext, ProcessBatchRequest
from open_anonymizer.services.deidentifier import ProcessingError, parse_birthdate
from open_anonymizer.services.exporter import export_processed_documents
from open_anonymizer.services.importer import DocumentImportError, import_file
from open_anonymizer.services.workers import BatchProcessingResult, BatchProcessorRunnable
from open_anonymizer.ui.drop_area import DropArea


STATUS_LABELS = {
    "pending": "Pending",
    "processing": "Processing",
    "ready": "Ready",
    "error": "Error",
}
STATUS_COLORS = {
    "pending": QColor("#4b5563"),
    "processing": QColor("#0f766e"),
    "ready": QColor("#166534"),
    "error": QColor("#b91c1c"),
}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.documents: list[ImportedDocument] = []
        self.processed_documents = {}
        self.thread_pool = QThreadPool.globalInstance()
        self.processing_active = False
        self.pending_document_ids: set[str] = set()
        self.active_batch_document_ids: set[str] = set()
        self.patient_context_generation = 0
        self.patient_context_error_message: str | None = None
        self.patient_context_timer = QTimer(self)
        self.patient_context_timer.setSingleShot(True)
        self.patient_context_timer.setInterval(350)
        self.patient_context_timer.timeout.connect(self.handle_patient_identifiers_changed)
        self._paste_counter = 0

        self.setWindowTitle("Open Anonymizer")
        self.resize(1220, 760)
        self._build_ui()
        self.refresh_document_list()
        self.refresh_actions()

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(20, 20, 20, 20)
        root_layout.setSpacing(16)

        title = QLabel("Open Anonymizer")
        title.setObjectName("windowTitle")
        subtitle = QLabel("Drop text or files, remove explicit patient identifiers, and export clean output.")
        subtitle.setObjectName("windowSubtitle")

        root_layout.addWidget(title)
        root_layout.addWidget(subtitle)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setSpacing(14)

        patient_group = QGroupBox("Patient identifiers")
        patient_form = QFormLayout(patient_group)
        patient_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.first_name_input = QLineEdit()
        self.last_name_input = QLineEdit()
        self.birthdate_input = QLineEdit()
        self.birthdate_input.setPlaceholderText("DD/MM/YYYY")
        self.first_name_input.textChanged.connect(self.schedule_patient_identifier_reprocess)
        self.last_name_input.textChanged.connect(self.schedule_patient_identifier_reprocess)
        self.birthdate_input.textChanged.connect(self.schedule_patient_identifier_reprocess)
        patient_form.addRow("First name", self.first_name_input)
        patient_form.addRow("Last name", self.last_name_input)
        patient_form.addRow("Birthdate", self.birthdate_input)

        self.drop_area = DropArea()
        self.drop_area.setMinimumHeight(110)
        self.drop_area.files_dropped.connect(self.handle_dropped_paths)
        self.drop_area.text_dropped.connect(self.handle_dropped_text)

        import_actions = QHBoxLayout()
        self.import_button = QPushButton("Import Files")
        self.import_button.clicked.connect(self.import_files)
        import_actions.addWidget(self.import_button)
        import_actions.addStretch(1)

        paste_group = QGroupBox("Paste text")
        paste_layout = QVBoxLayout(paste_group)
        self.paste_input = QPlainTextEdit()
        self.paste_input.setPlaceholderText("Paste medical text here and add it as a document.")
        self.paste_input.setMinimumHeight(140)
        self.paste_input.textChanged.connect(self.refresh_actions)
        self.add_paste_button = QPushButton("Add Pasted Text")
        self.add_paste_button.clicked.connect(self.add_pasted_text_from_input)
        paste_layout.addWidget(self.paste_input)
        paste_layout.addWidget(self.add_paste_button)

        self.document_list = QListWidget()
        self.document_list.currentItemChanged.connect(self.handle_document_selection_changed)
        self.document_list.setAlternatingRowColors(True)

        document_actions = QHBoxLayout()
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove_selected_document)
        self.clear_button = QPushButton("Clear All")
        self.clear_button.clicked.connect(self.clear_all_documents)
        document_actions.addWidget(self.remove_button)
        document_actions.addWidget(self.clear_button)

        left_layout.addWidget(patient_group)
        left_layout.addWidget(self.drop_area)
        left_layout.addLayout(import_actions)
        left_layout.addWidget(paste_group)
        left_layout.addWidget(QLabel("Documents"))
        left_layout.addWidget(self.document_list, stretch=1)
        left_layout.addLayout(document_actions)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setSpacing(14)

        self.document_status_label = QLabel("No document selected.")
        self.document_status_label.setWordWrap(True)
        self.document_status_label.setObjectName("documentStatus")

        self.output_view = QPlainTextEdit()
        self.output_view.setReadOnly(True)
        self.output_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.output_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        output_actions = QHBoxLayout()
        self.copy_button = QPushButton("Copy Output")
        self.copy_button.clicked.connect(self.copy_output)
        self.export_button = QPushButton("Export ZIP")
        self.export_button.clicked.connect(self.export_zip)
        output_actions.addWidget(self.copy_button)
        output_actions.addWidget(self.export_button)
        output_actions.addStretch(1)

        right_layout.addWidget(QLabel("Output"))
        right_layout.addWidget(self.document_status_label)
        right_layout.addWidget(self.output_view, stretch=1)
        right_layout.addLayout(output_actions)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([390, 770])

        root_layout.addWidget(splitter, stretch=1)
        self.setCentralWidget(root)

    def import_files(self) -> None:
        filenames, _ = QFileDialog.getOpenFileNames(
            self,
            "Import Documents",
            "",
            "Documents (*.txt *.html *.htm *.pdf)",
        )
        if filenames:
            self.handle_dropped_paths([Path(name) for name in filenames])

    def add_pasted_text_from_input(self) -> None:
        text = self.paste_input.toPlainText().strip()
        if not text:
            return
        self.add_pasted_text(text)
        self.paste_input.clear()

    def add_pasted_text(self, text: str) -> ImportedDocument:
        self._paste_counter += 1
        document = ImportedDocument(
            id=self._new_document_id(),
            source_kind="paste",
            display_name=f"Pasted Text {self._paste_counter:03d}",
            raw_text=text.strip(),
        )
        self._append_documents([document])
        return document

    def handle_dropped_text(self, text: str) -> None:
        self.add_pasted_text(text)

    def handle_dropped_paths(self, paths: list[Path]) -> None:
        imported_documents = [self._import_or_error_document(path) for path in paths]
        self._append_documents(imported_documents)

    def _import_or_error_document(self, path: Path) -> ImportedDocument:
        try:
            return import_file(path, self._new_document_id())
        except DocumentImportError as exc:
            source_kind = "pdf" if path.suffix.lower() == ".pdf" else "text_file"
            return ImportedDocument(
                id=self._new_document_id(),
                source_kind=source_kind,
                display_name=path.name,
                path=path,
                status="error",
                error_message=str(exc),
            )

    def _append_documents(self, documents: list[ImportedDocument]) -> None:
        if not documents:
            return

        self.documents.extend(documents)
        self.pending_document_ids.update(
            document.id for document in documents if document.raw_text is not None
        )
        self.refresh_document_list(select_document_id=documents[-1].id)
        if len(documents) == 1:
            self.statusBar().showMessage(f"Loaded {documents[0].display_name}", 3000)
        else:
            self.statusBar().showMessage(f"Loaded {len(documents)} document(s).", 3000)
        self.start_processing_if_possible()

    def _new_document_id(self) -> str:
        return uuid4().hex

    def handle_document_selection_changed(self) -> None:
        self.update_output_panel()
        self.refresh_actions()

    def current_document(self) -> ImportedDocument | None:
        item = self.document_list.currentItem()
        if not item:
            return None
        document_id = item.data(Qt.ItemDataRole.UserRole)
        return next((document for document in self.documents if document.id == document_id), None)

    def build_patient_context(self) -> PatientContext:
        try:
            birthdate = parse_birthdate(self.birthdate_input.text())
        except ProcessingError as exc:
            raise exc

        return PatientContext(
            first_name=self.first_name_input.text().strip(),
            last_name=self.last_name_input.text().strip(),
            birthdate=birthdate,
        )

    def schedule_patient_identifier_reprocess(self) -> None:
        self.patient_context_generation += 1
        self.patient_context_error_message = None
        for document in self.documents:
            if document.raw_text is None:
                continue
            document.status = "pending"
            document.error_message = None
            self.pending_document_ids.add(document.id)
            self.processed_documents.pop(document.id, None)

        if self.documents:
            self.refresh_document_list(preserve_selection=True)

        self.patient_context_timer.start()

    def handle_patient_identifiers_changed(self) -> None:
        self.start_processing_if_possible()

    def start_processing_if_possible(self) -> None:
        if self.processing_active or self.patient_context_timer.isActive():
            self.refresh_actions()
            return

        documents_to_process = [
            document
            for document in self.documents
            if document.id in self.pending_document_ids and document.raw_text is not None
        ]
        if not documents_to_process:
            self.refresh_actions()
            return

        try:
            patient_context = self.build_patient_context()
        except ProcessingError as exc:
            self.patient_context_error_message = str(exc)
            self.statusBar().showMessage(f"Waiting for valid patient identifiers. {exc}", 5000)
            self.refresh_document_list(preserve_selection=True)
            self.refresh_actions()
            return

        self.patient_context_error_message = None
        for document in documents_to_process:
            document.status = "processing"
            document.error_message = None
            self.processed_documents.pop(document.id, None)

        request = ProcessBatchRequest(
            patient_context=patient_context,
            documents=documents_to_process,
            context_generation=self.patient_context_generation,
        )
        worker = BatchProcessorRunnable(request)
        worker.signals.completed.connect(self.on_batch_completed)
        worker.signals.failed.connect(self.on_batch_failed)

        self.processing_active = True
        self.active_batch_document_ids = {document.id for document in documents_to_process}
        self.pending_document_ids.difference_update(self.active_batch_document_ids)
        self.refresh_document_list()
        self.refresh_actions()
        self.statusBar().showMessage(f"Processing {len(documents_to_process)} document(s)...")
        self.thread_pool.start(worker)

    def on_batch_completed(self, result: BatchProcessingResult) -> None:
        self.processing_active = False
        self.active_batch_document_ids.clear()

        documents_by_id = {document.id: document for document in self.documents}
        batch_is_current = (
            result.context_generation == self.patient_context_generation
            and not self.patient_context_timer.isActive()
        )

        if batch_is_current:
            for document_id in result.document_ids:
                document = documents_by_id.get(document_id)
                if not document or document.raw_text is None:
                    continue
                if document_id in result.processed_documents:
                    document.status = "ready"
                    document.error_message = None
                    self.processed_documents[document_id] = result.processed_documents[document_id]
                elif document_id in result.errors:
                    document.status = "error"
                    document.error_message = result.errors[document_id]
                    self.processed_documents.pop(document_id, None)

            self.statusBar().showMessage(
                f"Finished processing {len(result.processed_documents)} document(s).",
                5000,
            )
        else:
            for document_id in result.document_ids:
                document = documents_by_id.get(document_id)
                if not document or document.raw_text is None:
                    continue
                document.status = "pending"
                document.error_message = None
                self.pending_document_ids.add(document_id)
                self.processed_documents.pop(document_id, None)

            if self.pending_document_ids:
                self.statusBar().showMessage("Patient identifiers changed. Reprocessing queued.", 5000)

        self.refresh_document_list(preserve_selection=True)
        self.refresh_actions()
        self.start_processing_if_possible()

    def on_batch_failed(self, message: str) -> None:
        self.processing_active = False
        for document in self.documents:
            if document.id not in self.active_batch_document_ids:
                continue
            document.status = "error"
            document.error_message = message
            self.processed_documents.pop(document.id, None)
        self.active_batch_document_ids.clear()
        self.statusBar().showMessage("Processing failed.", 5000)
        QMessageBox.critical(self, "Processing failed", message)
        self.refresh_document_list(preserve_selection=True)
        self.refresh_actions()

    def refresh_document_list(
        self,
        select_document_id: str | None = None,
        preserve_selection: bool = False,
    ) -> None:
        selected_id = None
        if preserve_selection and self.document_list.currentItem():
            selected_id = self.document_list.currentItem().data(Qt.ItemDataRole.UserRole)
        if select_document_id:
            selected_id = select_document_id

        self.document_list.clear()
        for document in self.documents:
            item = QListWidgetItem(f"{document.display_name}  ·  {STATUS_LABELS[document.status]}")
            item.setData(Qt.ItemDataRole.UserRole, document.id)
            item.setForeground(STATUS_COLORS[document.status])
            if document.status == "error" and document.error_message:
                item.setToolTip(document.error_message)
            self.document_list.addItem(item)

        if selected_id:
            for row in range(self.document_list.count()):
                item = self.document_list.item(row)
                if item.data(Qt.ItemDataRole.UserRole) == selected_id:
                    self.document_list.setCurrentRow(row)
                    break
        elif self.document_list.count() > 0:
            self.document_list.setCurrentRow(0)

        self.update_output_panel()
        self.refresh_actions()

    def update_output_panel(self) -> None:
        document = self.current_document()
        if not document:
            self.document_status_label.setText("No document selected.")
            self.output_view.clear()
            return

        processed = self.processed_documents.get(document.id)
        if processed:
            self.output_view.setPlainText(processed.output_text)
            warning_text = f" Warnings: {'; '.join(processed.warnings)}" if processed.warnings else ""
            self.document_status_label.setText(
                f"{document.display_name} is ready for copy/export.{warning_text}"
            )
            return

        if document.status == "error":
            self.output_view.setPlainText(document.raw_text or "")
            self.document_status_label.setText(
                f"{document.display_name} could not be processed. {document.error_message or ''}".strip()
            )
            return

        self.output_view.setPlainText(document.raw_text or "")
        if document.status == "processing":
            self.document_status_label.setText(f"{document.display_name} is currently processing.")
        else:
            if self.patient_context_error_message:
                self.document_status_label.setText(
                    f"{document.display_name} is waiting for valid patient identifiers. "
                    f"{self.patient_context_error_message}"
                )
            else:
                self.document_status_label.setText(
                    f"{document.display_name} is queued for automatic de-identification. "
                    "The source text is shown here."
                )

    def copy_output(self) -> None:
        document = self.current_document()
        if not document:
            return
        processed = self.processed_documents.get(document.id)
        if not processed:
            return
        QApplication.clipboard().setText(processed.output_text)
        self.statusBar().showMessage(f"Copied output for {document.display_name}.", 3000)

    def export_zip(self) -> None:
        if not self.processed_documents:
            QMessageBox.warning(self, "Nothing to export", "Process at least one document before exporting.")
            return

        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export ZIP",
            "open-anonymizer-export.zip",
            "ZIP archive (*.zip)",
        )
        if not filename:
            return

        result = export_processed_documents(self.documents, self.processed_documents, Path(filename))
        self.statusBar().showMessage(
            f"Exported {result.exported_count} document(s) to {result.zip_path.name}.",
            5000,
        )
        QMessageBox.information(
            self,
            "Export complete",
            (
                f"ZIP saved to:\n{result.zip_path}\n\n"
                f"Exported: {result.exported_count}\n"
                f"Skipped: {result.skipped_count}"
            ),
        )

    def remove_selected_document(self) -> None:
        document = self.current_document()
        if not document:
            return
        self.documents = [item for item in self.documents if item.id != document.id]
        self.pending_document_ids.discard(document.id)
        self.active_batch_document_ids.discard(document.id)
        self.processed_documents.pop(document.id, None)
        self.refresh_document_list(preserve_selection=True)

    def clear_all_documents(self) -> None:
        if self.processing_active:
            return
        self.documents.clear()
        self.pending_document_ids.clear()
        self.active_batch_document_ids.clear()
        self.processed_documents.clear()
        self.patient_context_error_message = None
        self.patient_context_timer.stop()
        self.refresh_document_list()
        self.statusBar().clearMessage()

    def refresh_actions(self) -> None:
        has_documents = bool(self.documents)
        current_document = self.current_document()
        has_processed_current = bool(
            current_document and self.processed_documents.get(current_document.id)
        )

        self.add_paste_button.setEnabled(bool(self.paste_input.toPlainText().strip()))
        self.remove_button.setEnabled(current_document is not None and not self.processing_active)
        self.clear_button.setEnabled(has_documents and not self.processing_active)
        self.copy_button.setEnabled(has_processed_current)
        self.export_button.setEnabled(bool(self.processed_documents) and not self.processing_active)
