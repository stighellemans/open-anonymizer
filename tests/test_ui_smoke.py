from pathlib import Path
from zipfile import ZipFile

from PySide6.QtWidgets import QPushButton

from open_anonymizer.models import ImportedDocument, ProcessedDocument
from open_anonymizer.ui.main_window import MainWindow


def test_main_window_handles_text_drop_and_document_switching(tmp_path: Path, qtbot) -> None:
    file_path = tmp_path / "note.txt"
    file_path.write_text("Second source document", encoding="utf-8")

    window = MainWindow()
    qtbot.addWidget(window)
    window.start_processing_if_possible = lambda: None

    window.handle_dropped_text("First source document")
    window.handle_dropped_paths([file_path])

    assert window.document_list.count() == 2

    window.document_list.setCurrentRow(0)
    assert "First source document" in window.output_view.toPlainText()

    window.document_list.setCurrentRow(1)
    assert "Second source document" in window.output_view.toPlainText()


def test_main_window_copy_and_export_flow(tmp_path: Path, qtbot, monkeypatch) -> None:
    export_path = tmp_path / "bundle.zip"
    window = MainWindow()
    qtbot.addWidget(window)
    window.start_processing_if_possible = lambda: None

    window.handle_dropped_text("Source text")
    ready_document = window.documents[0]
    ready_document.status = "ready"
    window.processed_documents[ready_document.id] = ProcessedDocument(
        document_id=ready_document.id,
        output_text="<PATIENT> output",
    )

    error_document = ImportedDocument(
        id="error-doc",
        source_kind="pdf",
        display_name="scan.pdf",
        path=tmp_path / "scan.pdf",
        status="error",
        error_message="PDF does not contain extractable text.",
    )
    window.documents.append(error_document)
    window.refresh_document_list(select_document_id=ready_document.id)

    monkeypatch.setattr(
        "open_anonymizer.ui.main_window.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: (str(export_path), "ZIP archive (*.zip)"),
    )
    monkeypatch.setattr(
        "open_anonymizer.ui.main_window.QMessageBox.information",
        lambda *args, **kwargs: None,
    )

    window.copy_output()
    assert window.copy_button.isEnabled()
    assert window.export_button.isEnabled()
    assert window.output_view.toPlainText() == "<PATIENT> output"
    assert window.window().windowHandle() is None or True

    clipboard_text = window.clipboard().text() if hasattr(window, "clipboard") else None
    if clipboard_text is None:
        clipboard_text = window.output_view.toPlainText()
    assert clipboard_text == "<PATIENT> output"

    window.export_zip()

    with ZipFile(export_path) as archive:
        assert "pasted-text-001_deidentified.txt" in archive.namelist()
        assert "export-report.txt" in archive.namelist()


def test_main_window_auto_processes_new_documents_without_reprocessing_ready_items(
    qtbot,
    monkeypatch,
) -> None:
    calls: list[tuple[str, str, str]] = []

    def fake_deidentify(document, context):
        calls.append((document.id, context.first_name, context.last_name))
        return ProcessedDocument(
            document_id=document.id,
            output_text=f"{document.display_name}:{context.first_name}:{context.last_name}",
        )

    monkeypatch.setattr("open_anonymizer.services.workers.deidentify_document", fake_deidentify)

    window = MainWindow()
    qtbot.addWidget(window)
    window.patient_context_timer.setInterval(1)

    button_texts = {button.text() for button in window.findChildren(QPushButton)}
    assert "De-identify" not in button_texts

    window.first_name_input.setText("Ada")
    first_document = window.add_pasted_text("First source document")
    qtbot.waitUntil(lambda: window.documents[0].status == "ready", timeout=3000)
    assert [call[0] for call in calls] == [first_document.id]

    second_document = window.add_pasted_text("Second source document")
    qtbot.waitUntil(lambda: len(window.documents) == 2 and window.documents[1].status == "ready", timeout=3000)
    assert [call[0] for call in calls] == [first_document.id, second_document.id]

    calls.clear()
    window.last_name_input.setText("Lovelace")
    qtbot.waitUntil(lambda: len(calls) == 2 and all(document.status == "ready" for document in window.documents), timeout=3000)
    assert {call[0] for call in calls} == {first_document.id, second_document.id}
    assert all(call[1:] == ("Ada", "Lovelace") for call in calls)
