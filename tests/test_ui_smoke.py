from datetime import date
from pathlib import Path
import threading
import time
from zipfile import ZipFile

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QLabel, QPushButton, QToolButton

from open_anonymizer.models import (
    AnonymizationSettings,
    ImportedDocument,
    ProcessedDocument,
)
from open_anonymizer.services.importer import ImportCancelledError
from open_anonymizer.ui.anonymization_dialog import save_anonymization_settings
from open_anonymizer.ui.main_window import (
    DOCUMENT_ID_ROLE,
    MainWindow,
    PREPARING_BACKEND_TEXT,
    STATUS_COLORS,
    layout_profile_for_window_height,
    recommended_window_size,
)


def _wait_for_document_import(window: MainWindow, qtbot, document: ImportedDocument) -> None:
    qtbot.waitUntil(
        lambda: document.raw_text is not None or document.status == "error",
        timeout=3000,
    )


def _average_nontransparent_lightness(image) -> float:
    total = 0
    count = 0
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.alpha() == 0:
                continue
            total += max(color.red(), color.green(), color.blue())
            count += 1

    return total / count if count else 0.0


def test_main_window_keeps_pasted_text_out_of_imported_file_list(
    tmp_path: Path,
    qtbot,
) -> None:
    file_path = tmp_path / "note.txt"
    file_path.write_text("Second source document", encoding="utf-8")

    window = MainWindow()
    qtbot.addWidget(window)
    window.start_processing_if_possible = lambda: None

    window.handle_dropped_text("First source document")
    window.handle_dropped_paths([file_path])
    _wait_for_document_import(window, qtbot, window.documents[0])

    assert window.document_list.count() == 1
    assert window.document_list.item(0).text() == "note.txt"
    assert "First source document" in window.output_view.toPlainText()

    window.paste_input.clear()

    assert "Second source document" in window.output_view.toPlainText()


def test_main_window_shows_application_branding_icon(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)

    header_icon = window.findChild(QLabel, "headerIcon")

    assert header_icon is not None
    assert window.windowIcon().isNull() is False
    assert header_icon.pixmap().isNull() is False


def test_main_window_uses_white_header_icon_variant(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)

    header_icon = window.findChild(QLabel, "headerIcon")

    assert header_icon is not None

    header_pixmap = header_icon.pixmap()
    window_pixmap = window.windowIcon().pixmap(header_pixmap.size())

    assert _average_nontransparent_lightness(header_pixmap.toImage()) > 240
    assert _average_nontransparent_lightness(window_pixmap.toImage()) < 80


def test_recommended_window_size_stays_within_available_screen() -> None:
    size = recommended_window_size(QSize(900, 580))

    assert size.width() <= 900
    assert size.height() <= 580
    assert size.width() > 0
    assert size.height() > 0


def test_main_window_compacts_fixed_sections_for_smaller_screens(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        MainWindow,
        "_recommended_window_size",
        lambda self: QSize(852, 532),
    )

    window = MainWindow()
    qtbot.addWidget(window)
    layout_profile = layout_profile_for_window_height(532)

    assert window.findChild(QLabel, "anonymizationSummaryBody") is None
    assert window.drop_area.minimumHeight() == layout_profile.drop_area_height
    assert window.paste_input.minimumHeight() == layout_profile.paste_input_height
    assert (
        window.customize_anonymization_button.sizePolicy().horizontalPolicy()
        == window.customize_anonymization_button.sizePolicy().Policy.Maximum
    )


def test_main_window_shows_bug_report_link(qtbot, monkeypatch) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    opened_urls: list[str] = []

    monkeypatch.setattr(
        "open_anonymizer.ui.main_window.QDesktopServices.openUrl",
        lambda url: opened_urls.append(url.toString()),
    )

    bug_report_link = window.statusBar().findChild(
        QToolButton, "bugReportLinkButton"
    )

    assert bug_report_link is not None
    assert bug_report_link.text() == "report a bug or incomplete anonimization"
    assert bug_report_link.icon().isNull() is False

    bug_report_link.click()

    assert opened_urls == ["https://forms.gle/Ww8d6JajzAsbpxH38"]


def test_main_window_places_output_actions_with_imported_files(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)

    actions_parent = window.clear_button.parentWidget()

    assert actions_parent is not None
    assert window.copy_button.parentWidget() is actions_parent
    assert window.export_button.parentWidget() is actions_parent
    assert window.output_view.parentWidget() is not actions_parent


def test_main_window_pasted_text_copy_flow_disables_export(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)

    window.paste_input.setPlainText("Source text")
    window.paste_processing_timer.stop()
    window.paste_processed_document = ProcessedDocument(
        document_id="paste-preview",
        output_text="[PATIENT] output",
        placeholder_references={"[PATIENT]": ("Source text",)},
    )
    window.update_output_panel()
    window.refresh_actions()

    window.copy_output()

    assert window.copy_button.isEnabled()
    assert window.export_button.isEnabled() is False
    assert window.output_view.toPlainText() == "[PATIENT] output"
    assert (
        window.output_view.tooltip_text_for_position(1)
        == "Placeholder: [PATIENT]\nOriginal text: Source text"
    )

    clipboard_text = window.clipboard().text() if hasattr(window, "clipboard") else None
    if clipboard_text is None:
        clipboard_text = window.output_view.toPlainText()
    assert clipboard_text == "[PATIENT] output"


def test_main_window_copy_and_export_flow(tmp_path: Path, qtbot, monkeypatch) -> None:
    export_path = tmp_path / "bundle.zip"
    file_path = tmp_path / "source.txt"
    file_path.write_text("Source text", encoding="utf-8")

    window = MainWindow()
    qtbot.addWidget(window)
    window.start_processing_if_possible = lambda: None

    window.handle_dropped_paths([file_path])
    ready_document = window.documents[0]
    _wait_for_document_import(window, qtbot, ready_document)
    ready_document.status = "ready"
    window.processed_documents[ready_document.id] = ProcessedDocument(
        document_id=ready_document.id,
        output_text="[PATIENT] output",
        placeholder_references={"[PATIENT]": ("Source text",)},
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
    assert window.export_original_action.isEnabled()
    assert window.export_text_action.isEnabled()
    assert window.output_view.toPlainText() == "[PATIENT] output"
    assert (
        window.output_view.tooltip_text_for_position(1)
        == "Placeholder: [PATIENT]\nOriginal text: Source text"
    )

    clipboard_text = window.clipboard().text() if hasattr(window, "clipboard") else None
    if clipboard_text is None:
        clipboard_text = window.output_view.toPlainText()
    assert clipboard_text == "[PATIENT] output"

    window.export_zip()

    with ZipFile(export_path) as archive:
        assert "source.txt" in archive.namelist()
        assert "export-report.txt" in archive.namelist()


def test_main_window_export_defaults_to_downloads_folder(
    tmp_path: Path,
    qtbot,
    monkeypatch,
) -> None:
    downloads_dir = tmp_path / "Downloads"
    downloads_dir.mkdir()
    file_path = tmp_path / "source.txt"
    file_path.write_text("Source text", encoding="utf-8")
    captured: dict[str, str] = {}

    window = MainWindow()
    qtbot.addWidget(window)
    window.start_processing_if_possible = lambda: None
    window.handle_dropped_paths([file_path])
    ready_document = window.documents[0]
    _wait_for_document_import(window, qtbot, ready_document)
    ready_document.status = "ready"
    window.processed_documents[ready_document.id] = ProcessedDocument(
        document_id=ready_document.id,
        output_text="[PATIENT] output",
    )
    window.refresh_document_list(select_document_id=ready_document.id)

    monkeypatch.setattr(
        "open_anonymizer.ui.main_window.default_export_directory",
        lambda: downloads_dir,
    )

    def fake_get_save_file_name(*args, **kwargs):
        del kwargs
        captured["path"] = args[2] if len(args) > 2 else ""
        return ("", "ZIP archive (*.zip)")

    monkeypatch.setattr(
        "open_anonymizer.ui.main_window.QFileDialog.getSaveFileName",
        fake_get_save_file_name,
    )

    window.export_original_formats()

    assert Path(captured["path"]) == downloads_dir / "open-anonymizer-original-export.zip"


def test_main_window_export_remembers_last_saved_folder(
    tmp_path: Path,
    qtbot,
    monkeypatch,
) -> None:
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    export_path = export_dir / "bundle.zip"
    first_file = tmp_path / "first-source.txt"
    first_file.write_text("Source text", encoding="utf-8")
    second_file = tmp_path / "second-source.txt"
    second_file.write_text("Second source text", encoding="utf-8")
    captured: dict[str, str] = {}

    first_window = MainWindow()
    qtbot.addWidget(first_window)
    first_window.start_processing_if_possible = lambda: None
    first_window.handle_dropped_paths([first_file])
    ready_document = first_window.documents[0]
    _wait_for_document_import(first_window, qtbot, ready_document)
    ready_document.status = "ready"
    first_window.processed_documents[ready_document.id] = ProcessedDocument(
        document_id=ready_document.id,
        output_text="[PATIENT] output",
    )
    first_window.refresh_document_list(select_document_id=ready_document.id)

    monkeypatch.setattr(
        "open_anonymizer.ui.main_window.QMessageBox.information",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "open_anonymizer.ui.main_window.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: (str(export_path), "ZIP archive (*.zip)"),
    )

    first_window.export_original_formats()

    second_window = MainWindow()
    qtbot.addWidget(second_window)
    second_window.start_processing_if_possible = lambda: None
    second_window.handle_dropped_paths([second_file])
    second_ready_document = second_window.documents[0]
    _wait_for_document_import(second_window, qtbot, second_ready_document)
    second_ready_document.status = "ready"
    second_window.processed_documents[second_ready_document.id] = ProcessedDocument(
        document_id=second_ready_document.id,
        output_text="[PATIENT] output",
    )
    second_window.refresh_document_list(select_document_id=second_ready_document.id)

    def fake_get_save_file_name(*args, **kwargs):
        del kwargs
        captured["path"] = args[2]
        return ("", "ZIP archive (*.zip)")

    monkeypatch.setattr(
        "open_anonymizer.ui.main_window.QFileDialog.getSaveFileName",
        fake_get_save_file_name,
    )

    second_window.export_text_files()

    assert Path(captured["path"]) == export_dir / "open-anonymizer-text-export.zip"


def test_main_window_shows_processing_overlay_for_selected_processing_document(
    tmp_path: Path,
    qtbot,
) -> None:
    file_path = tmp_path / "source.txt"
    file_path.write_text("Source text", encoding="utf-8")

    window = MainWindow()
    qtbot.addWidget(window)
    window.start_processing_if_possible = lambda: None

    window.handle_dropped_paths([file_path])
    document = window.documents[0]
    _wait_for_document_import(window, qtbot, document)
    document.status = "processing"
    window.refresh_document_list(select_document_id=document.id)

    assert window.document_list.item(0).text() == document.display_name
    assert window.document_list_spinner_timer.isActive() is True
    assert STATUS_COLORS["processing"].name().lower() == "#f59e0b"
    assert window.output_view.toPlainText() == "Source text"
    assert window.output_view.is_processing_active() is True
    assert window.document_status_label.text() == f"{document.display_name} is processing."


def test_main_window_document_list_hides_ready_suffix_and_stops_spinner(
    tmp_path: Path,
    qtbot,
) -> None:
    file_path = tmp_path / "source.txt"
    file_path.write_text("Source text", encoding="utf-8")

    window = MainWindow()
    qtbot.addWidget(window)
    window.start_processing_if_possible = lambda: None

    window.handle_dropped_paths([file_path])
    document = window.documents[0]
    _wait_for_document_import(window, qtbot, document)
    document.status = "processing"
    window.refresh_document_list(select_document_id=document.id)
    assert window.document_list_spinner_timer.isActive() is True

    document.status = "ready"
    window.refresh_document_list(select_document_id=document.id)

    assert window.document_list.item(0).text() == document.display_name
    assert "ready" not in window.document_list.item(0).text().lower()
    assert window.document_list_spinner_timer.isActive() is False
    assert STATUS_COLORS["ready"].name().lower() == "#16a34a"


def test_main_window_hover_text_uses_placeholder_and_pseudonym_references(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.paste_input.setPlainText("Source text")
    window.paste_processing_timer.stop()
    window.paste_processed_document = ProcessedDocument(
        document_id="paste-preview",
        output_text="Marie Peeters met [PATIENT] op.",
        placeholder_references={
            "Marie Peeters": ("Jean Dupont",),
            "[PATIENT]": ("Jean Dupont",),
        },
    )
    window.update_output_panel()
    window.refresh_actions()

    highlighted = {
        selection.cursor.selectedText(): selection.format.background().color().name().lower()
        for selection in window.output_view.extraSelections()
    }

    assert (
        window.output_view.tooltip_text_for_position(1)
        == "Pseudonym: Marie Peeters\nOriginal text: Jean Dupont"
    )
    assert highlighted["Marie Peeters"] == "#dbeafe"
    assert highlighted["[PATIENT]"] == "#fef3c7"
    assert window.output_view.tooltip_text_for_position(
        window.output_view.toPlainText().index("[PATIENT]") + 1
    ) == "Placeholder: [PATIENT]\nOriginal text: Jean Dupont"
    assert window.document_status_label.text() == "Pasted text"
    assert "hover a highlight" in window.document_status_label.toolTip().lower()
    assert window.output_view.tooltip_text_for_position(
        len(window.output_view.toPlainText())
    ) is None


def test_main_window_ready_document_status_label_shows_filename_only(
    tmp_path: Path,
    qtbot,
) -> None:
    file_path = tmp_path / "source.txt"
    file_path.write_text("Source text", encoding="utf-8")

    window = MainWindow()
    qtbot.addWidget(window)
    window.start_processing_if_possible = lambda: None

    window.handle_dropped_paths([file_path])
    ready_document = window.documents[0]
    _wait_for_document_import(window, qtbot, ready_document)
    ready_document.status = "ready"
    window.processed_documents[ready_document.id] = ProcessedDocument(
        document_id=ready_document.id,
        output_text="[PATIENT] output",
        placeholder_references={"[PATIENT]": ("Source text",)},
    )

    window.refresh_document_list(select_document_id=ready_document.id)

    assert window.document_status_label.text() == ready_document.display_name
    assert "hover a highlight" in window.document_status_label.toolTip().lower()


def test_main_window_ready_document_status_label_surfaces_warnings(
    tmp_path: Path,
    qtbot,
) -> None:
    file_path = tmp_path / "source.txt"
    file_path.write_text("Source text", encoding="utf-8")

    window = MainWindow()
    qtbot.addWidget(window)
    window.start_processing_if_possible = lambda: None

    window.handle_dropped_paths([file_path])
    ready_document = window.documents[0]
    _wait_for_document_import(window, qtbot, ready_document)
    ready_document.status = "ready"
    window.processed_documents[ready_document.id] = ProcessedDocument(
        document_id=ready_document.id,
        output_text="[PATIENT] output",
        warnings=["OCR confidence is low"],
    )

    window.refresh_document_list(select_document_id=ready_document.id)

    assert (
        window.document_status_label.text()
        == f"{ready_document.display_name}. Warning: OCR confidence is low"
    )


def test_main_window_applies_dialog_settings(qtbot, monkeypatch) -> None:
    class FakeDialog:
        def __init__(self, anonymization_settings, preview_document_key=None, parent=None):
            del anonymization_settings, preview_document_key, parent

        def exec(self):
            return 1

        def settings(self):
            return AnonymizationSettings(
                first_name="Ada",
                last_name="Lovelace",
                birthdate=date(1815, 12, 10),
                other_names=["Charles Babbage"],
                custom_addresses=["12 St. James's Square"],
                deidentify_filenames=False,
                mode="smart_pseudonyms",
            )

    monkeypatch.setattr("open_anonymizer.ui.main_window.AnonymizationDialog", FakeDialog)

    window = MainWindow()
    qtbot.addWidget(window)
    window.start_processing_if_possible = lambda: None

    button = next(
        button
        for button in window.findChildren(QPushButton)
        if button.text() == "Customize anonymization"
    )
    button.click()

    assert window.anonymization_settings.first_name == "Ada"
    assert window.anonymization_settings.mode == "smart_pseudonyms"
    assert window.anonymization_settings.deidentify_filenames is False
    assert window.customize_anonymization_button.text() == "Customize anonymization"
    assert window.findChild(QLabel, "anonymizationSummaryBody") is None


def test_main_window_cancel_keeps_existing_settings(qtbot, monkeypatch) -> None:
    class FakeDialog:
        def __init__(self, anonymization_settings, preview_document_key=None, parent=None):
            del anonymization_settings, preview_document_key, parent

        def exec(self):
            return 0

    window = MainWindow()
    qtbot.addWidget(window)
    original_generation = window.anonymization_settings_generation
    original_settings = window.anonymization_settings

    monkeypatch.setattr("open_anonymizer.ui.main_window.AnonymizationDialog", FakeDialog)
    window.open_anonymization_dialog()

    assert window.anonymization_settings == original_settings
    assert window.anonymization_settings_generation == original_generation


def test_main_window_keeps_customize_button_available_for_pasted_text(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "open_anonymizer.services.smart_pseudonymizer._session_auto_date_shift_days",
        lambda: 98,
    )

    window = MainWindow()
    qtbot.addWidget(window)
    window.apply_anonymization_settings(
        AnonymizationSettings(mode="smart_pseudonyms"),
        persist=False,
        reprocess=False,
    )

    window.set_pasted_text("Example medical note")
    qtbot.waitUntil(
        lambda: window.current_pasted_text() == "Example medical note",
        timeout=3000,
    )

    assert window.customize_anonymization_button.text() == "Customize anonymization"


def test_main_window_auto_processes_new_documents_and_reprocesses_after_settings_change(
    tmp_path: Path,
    qtbot,
    monkeypatch,
) -> None:
    calls: list[tuple[str, str, str]] = []
    first_file = tmp_path / "first.txt"
    first_file.write_text("First source document", encoding="utf-8")
    second_file = tmp_path / "second.txt"
    second_file.write_text("Second source document", encoding="utf-8")

    def fake_deidentify(document, settings):
        calls.append((document.id, settings.first_name, settings.last_name))
        return ProcessedDocument(
            document_id=document.id,
            output_text=f"{document.display_name}:{settings.first_name}:{settings.last_name}",
        )

    monkeypatch.setattr("open_anonymizer.services.workers.deidentify_document", fake_deidentify)

    window = MainWindow()
    qtbot.addWidget(window)
    window.apply_anonymization_settings(
        AnonymizationSettings(first_name="Ada"),
        persist=False,
        reprocess=False,
    )

    window.handle_dropped_paths([first_file])
    first_document = window.documents[0]
    qtbot.waitUntil(lambda: window.documents[0].status == "ready", timeout=3000)
    assert [call[0] for call in calls] == [first_document.id]

    window.handle_dropped_paths([second_file])
    second_document = window.documents[1]
    qtbot.waitUntil(
        lambda: len(window.documents) == 2 and window.documents[1].status == "ready",
        timeout=3000,
    )
    assert [call[0] for call in calls] == [first_document.id, second_document.id]

    calls.clear()
    window.apply_anonymization_settings(
        AnonymizationSettings(first_name="Ada", last_name="Lovelace"),
        persist=False,
        reprocess=True,
    )
    qtbot.waitUntil(
        lambda: len(calls) == 2 and all(document.status == "ready" for document in window.documents),
        timeout=3000,
    )
    assert {call[0] for call in calls} == {first_document.id, second_document.id}
    assert all(call[1:] == ("Ada", "Lovelace") for call in calls)


def test_main_window_auto_processes_pasted_text_and_reprocesses_after_settings_change(
    qtbot,
    monkeypatch,
) -> None:
    calls: list[tuple[str, str, str]] = []

    def fake_deidentify(document, settings):
        calls.append((document.source_kind, document.raw_text or "", settings.first_name))
        return ProcessedDocument(
            document_id=document.id,
            output_text=f"{settings.first_name}:{document.raw_text}",
        )

    monkeypatch.setattr("open_anonymizer.services.workers.deidentify_document", fake_deidentify)

    window = MainWindow()
    qtbot.addWidget(window)
    window.apply_anonymization_settings(
        AnonymizationSettings(first_name="Ada"),
        persist=False,
        reprocess=False,
    )

    window.set_pasted_text("First source document")
    qtbot.waitUntil(lambda: window.paste_processed_document is not None, timeout=3000)

    assert calls == [("paste", "First source document", "Ada")]
    assert window.output_view.toPlainText() == "Ada:First source document"

    calls.clear()
    window.apply_anonymization_settings(
        AnonymizationSettings(first_name="Grace"),
        persist=False,
        reprocess=True,
    )
    qtbot.waitUntil(
        lambda: len(calls) == 1 and window.paste_processed_document is not None,
        timeout=3000,
    )

    assert calls == [("paste", "First source document", "Grace")]
    assert window.output_view.toPlainText() == "Grace:First source document"


def test_main_window_queues_paste_reprocessing_while_worker_is_active(qtbot, monkeypatch) -> None:
    started: list[str] = []
    cancelled: list[str] = []

    class FakeWorker:
        def cancel(self) -> None:
            cancelled.append("cancelled")

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_pasted_text("First source document")
    window.paste_processing_timer.stop()
    window.paste_processing_worker = FakeWorker()
    window.paste_processing_active = True
    monkeypatch.setattr(window, "process_pasted_text", lambda: started.append("started"))

    window.schedule_pasted_text_processing(debounce=False)

    assert started == []
    assert cancelled == ["cancelled"]
    assert window._paste_processing_restart_requested is True
    assert window._paste_processing_restart_debounce is False


def test_main_window_shows_preparing_message_for_pasted_text_while_backend_initializes(
    qtbot,
    monkeypatch,
) -> None:
    class FakeWorker:
        def cancel(self) -> None:
            return

    window = MainWindow()
    qtbot.addWidget(window)

    monkeypatch.setattr(
        "open_anonymizer.ui.main_window.backend_is_ready",
        lambda flags_key: False,
    )

    window.set_pasted_text("Source text")
    window.paste_processing_timer.stop()
    window.paste_processing_active = True
    window.paste_processing_worker = FakeWorker()
    window._active_paste_processing_flags_key = window.current_backend_flags_key()
    window._set_expected_backend_preparation(window.current_backend_flags_key())
    window.update_output_panel()

    assert window.output_view.toPlainText() == PREPARING_BACKEND_TEXT
    assert window.output_view.is_processing_active() is True
    assert window.document_status_label.text().startswith(PREPARING_BACKEND_TEXT)


def test_main_window_starts_background_warmup_while_imports_are_active(
    qtbot,
    monkeypatch,
) -> None:
    class FakeImportWorker:
        def cancel(self) -> None:
            return

    class FakeSignal:
        def connect(self, callback) -> None:
            self.callback = callback

    class FakeSignals:
        def __init__(self) -> None:
            self.completed = FakeSignal()
            self.failed = FakeSignal()

    class FakeWarmupWorker:
        def __init__(self) -> None:
            self.signals = FakeSignals()
            self.started = False

        def start(self) -> None:
            self.started = True

        def cancel(self) -> None:
            return

    window = MainWindow()
    qtbot.addWidget(window)

    monkeypatch.setattr(
        "open_anonymizer.ui.main_window.backend_is_ready",
        lambda flags_key: False,
    )

    created_workers: list[FakeWarmupWorker] = []

    monkeypatch.setattr(
        "open_anonymizer.ui.main_window.BackendWarmupRunnable",
        lambda *args, **kwargs: created_workers.append(FakeWarmupWorker())
        or created_workers[-1],
    )

    window.schedule_background_backend_warmup(25)
    window.active_import_workers["importing-doc"] = FakeImportWorker()
    window.start_background_backend_warmup()

    assert len(created_workers) == 1
    assert created_workers[0].started is True
    assert window.backend_warmup_worker is created_workers[0]
    window.backend_warmup_start_timer.stop()


def test_main_window_selects_newly_imported_file_after_import(
    tmp_path: Path,
    qtbot,
    monkeypatch,
) -> None:
    def fake_deidentify(document, settings):
        del settings
        return ProcessedDocument(
            document_id=document.id,
            output_text=document.display_name,
        )

    monkeypatch.setattr("open_anonymizer.services.workers.deidentify_document", fake_deidentify)

    first_file = tmp_path / "first.txt"
    first_file.write_text("First source document", encoding="utf-8")
    second_file = tmp_path / "second.txt"
    second_file.write_text("Second source document", encoding="utf-8")

    window = MainWindow()
    qtbot.addWidget(window)

    window.handle_dropped_paths([first_file])
    qtbot.waitUntil(lambda: window.documents[0].status == "ready", timeout=3000)

    window.handle_dropped_paths([second_file])

    assert window.current_document() is not None
    assert window.current_document().id == window.documents[-1].id


def test_main_window_removes_document_from_inline_item_button(qtbot) -> None:
    first_document = ImportedDocument(
        id="first-doc",
        source_kind="text_file",
        display_name="first.txt",
        raw_text="First source document",
        status="ready",
    )
    second_document = ImportedDocument(
        id="second-doc",
        source_kind="text_file",
        display_name="second.txt",
        raw_text="Second source document",
        status="ready",
    )

    window = MainWindow()
    qtbot.addWidget(window)
    window.documents = [first_document, second_document]
    window.refresh_document_list(select_document_id=first_document.id)
    window.show()

    second_item = window.document_list.item(1)
    qtbot.waitUntil(
        lambda: not window.document_list.visualItemRect(second_item).isNull(),
        timeout=1000,
    )

    remove_button_rect = window.document_list_delegate.remove_button_rect(
        window.document_list.visualItemRect(second_item)
    )
    qtbot.mouseClick(
        window.document_list.viewport(),
        Qt.MouseButton.LeftButton,
        pos=remove_button_rect.center().toPoint(),
    )

    assert all(button.text() != "Remove" for button in window.findChildren(QPushButton))
    assert [document.id for document in window.documents] == [first_document.id]
    assert window.document_list.count() == 1
    assert window.document_list.item(0).data(DOCUMENT_ID_ROLE) == first_document.id
    assert window.current_document() is not None
    assert window.current_document().id == first_document.id


def test_main_window_shows_busy_placeholder_while_file_import_runs(
    tmp_path: Path,
    qtbot,
    monkeypatch,
) -> None:
    file_path = tmp_path / "source.txt"
    file_path.write_text("Source text", encoding="utf-8")
    import_started = threading.Event()
    finish_import = threading.Event()

    def fake_import_file(path, document_id, should_cancel=None):
        del path
        import_started.set()
        finish_import.wait(timeout=3)
        return ImportedDocument(
            id=document_id,
            source_kind="text_file",
            display_name=file_path.name,
            path=file_path,
            raw_text="Source text",
        )

    monkeypatch.setattr("open_anonymizer.services.workers.import_file", fake_import_file)

    window = MainWindow()
    qtbot.addWidget(window)
    window.start_processing_if_possible = lambda: None

    window.handle_dropped_paths([file_path])
    document = window.documents[0]
    qtbot.waitUntil(import_started.is_set, timeout=1000)

    assert document.status == "processing"
    assert document.raw_text is None
    assert window.document_list_spinner_timer.isActive() is True
    assert window.output_view.is_processing_active() is True
    assert window.document_status_label.text() == f"{file_path.name} is importing."

    finish_import.set()
    _wait_for_document_import(window, qtbot, document)

    assert document.status == "pending"
    assert document.raw_text == "Source text"


def test_main_window_queues_large_import_batches_without_processing_mid_import(
    tmp_path: Path,
    qtbot,
    monkeypatch,
) -> None:
    file_paths = [
        tmp_path / "first.txt",
        tmp_path / "second.txt",
        tmp_path / "third.txt",
    ]
    for index, file_path in enumerate(file_paths, start=1):
        file_path.write_text(f"Source text {index}", encoding="utf-8")

    started: list[str] = []
    deidentify_calls: list[str] = []
    start_events = {file_path.name: threading.Event() for file_path in file_paths}
    release_events = {file_path.name: threading.Event() for file_path in file_paths}

    def fake_import_file(path, document_id, should_cancel=None):
        started.append(path.name)
        start_events[path.name].set()
        while not release_events[path.name].wait(timeout=0.01):
            if should_cancel is not None and should_cancel():
                raise ImportCancelledError()
        return ImportedDocument(
            id=document_id,
            source_kind="text_file",
            display_name=path.name,
            path=path,
            raw_text=path.read_text(encoding="utf-8"),
        )

    def fake_deidentify(document, settings):
        del settings
        deidentify_calls.append(document.display_name)
        return ProcessedDocument(
            document_id=document.id,
            output_text=document.raw_text or "",
        )

    monkeypatch.setattr("open_anonymizer.services.workers.import_file", fake_import_file)
    monkeypatch.setattr("open_anonymizer.services.workers.deidentify_document", fake_deidentify)
    monkeypatch.setattr(
        "open_anonymizer.ui.main_window.configured_max_concurrent_imports",
        lambda: 1,
    )

    window = MainWindow()
    qtbot.addWidget(window)

    window.handle_dropped_paths(file_paths)

    qtbot.waitUntil(start_events["first.txt"].is_set, timeout=1000)
    assert started == ["first.txt"]
    assert start_events["second.txt"].wait(timeout=0.2) is False
    assert start_events["third.txt"].wait(timeout=0.2) is False

    release_events["first.txt"].set()
    qtbot.waitUntil(start_events["second.txt"].is_set, timeout=1000)
    qtbot.wait(100)
    assert started == ["first.txt", "second.txt"]
    assert deidentify_calls == []
    assert start_events["third.txt"].wait(timeout=0.2) is False

    release_events["second.txt"].set()
    qtbot.waitUntil(start_events["third.txt"].is_set, timeout=1000)
    qtbot.wait(100)
    assert started == ["first.txt", "second.txt", "third.txt"]
    assert deidentify_calls == []

    release_events["third.txt"].set()
    qtbot.waitUntil(
        lambda: len(deidentify_calls) == 3
        and all(document.status == "ready" for document in window.documents),
        timeout=3000,
    )
    assert [document.display_name for document in window.documents] == [
        "first.txt",
        "second.txt",
        "third.txt",
    ]


def test_main_window_close_cancels_background_import_without_waiting(
    tmp_path: Path,
    qtbot,
    monkeypatch,
) -> None:
    file_path = tmp_path / "source.txt"
    file_path.write_text("Source text", encoding="utf-8")
    import_started = threading.Event()
    import_cancelled = threading.Event()

    def fake_import_file(path, document_id, should_cancel=None):
        del path, document_id
        import_started.set()
        while True:
            if should_cancel is not None and should_cancel():
                import_cancelled.set()
                raise ImportCancelledError()
            time.sleep(0.01)

    monkeypatch.setattr("open_anonymizer.services.workers.import_file", fake_import_file)

    window = MainWindow()
    qtbot.addWidget(window)

    window.handle_dropped_paths([file_path])
    qtbot.waitUntil(import_started.is_set, timeout=1000)

    started_at = time.monotonic()
    assert window.close() is True
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.5
    qtbot.waitUntil(import_cancelled.is_set, timeout=1000)


def test_main_window_restores_saved_settings(qtbot) -> None:
    save_anonymization_settings(
        AnonymizationSettings(
            first_name="Jean",
            last_name="Dupont",
            birthdate=date(1980, 3, 12),
            other_names=["Sophie Martin"],
            custom_addresses=["Rue de la Loi 12, 1000 Bruxelles"],
            deidentify_filenames=False,
            mode="smart_pseudonyms",
        )
    )

    window = MainWindow()
    qtbot.addWidget(window)

    assert window.anonymization_settings == AnonymizationSettings(
        first_name="Jean",
        last_name="Dupont",
        birthdate=date(1980, 3, 12),
        other_names=["Sophie Martin"],
        custom_addresses=["Rue de la Loi 12, 1000 Bruxelles"],
        deidentify_filenames=False,
        mode="smart_pseudonyms",
    )
    assert window.customize_anonymization_button.text() == "Customize anonymization"
