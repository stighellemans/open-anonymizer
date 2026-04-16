from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout


DEFAULT_DROP_AREA_LABEL = "Drop files anywhere in the window, or text here"


def mime_data_has_local_paths(mime_data: QMimeData) -> bool:
    return any(url.isLocalFile() for url in mime_data.urls())


def local_paths_from_mime_data(mime_data: QMimeData) -> list[Path]:
    return [Path(url.toLocalFile()) for url in mime_data.urls() if url.isLocalFile()]


def dropped_text_from_mime_data(mime_data: QMimeData) -> str:
    return mime_data.text().strip()


class DropArea(QFrame):
    files_dropped = Signal(list)
    text_dropped = Signal(str)
    import_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._file_import_enabled = True
        self.setAcceptDrops(True)
        self.setObjectName("dropArea")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self.label = QLabel(DEFAULT_DROP_AREA_LABEL)
        self.label.setObjectName("dropAreaLabel")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setWordWrap(True)

        self.import_button = QPushButton("Import Files")
        self.import_button.setObjectName("secondaryButton")
        self.import_button.clicked.connect(
            lambda checked=False: self.import_requested.emit()
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        layout.addWidget(self.label)
        layout.addWidget(
            self.import_button,
            0,
            Qt.AlignmentFlag.AlignHCenter,
        )

    def set_file_import_enabled(
        self,
        enabled: bool,
        *,
        disabled_message: str | None = None,
    ) -> None:
        message = (disabled_message or "").strip()
        self._file_import_enabled = enabled
        self.import_button.setEnabled(enabled)
        self.import_button.setToolTip(message)
        self.label.setToolTip(message)
        self.label.setText(DEFAULT_DROP_AREA_LABEL)
        if not enabled:
            self.set_drag_active(False)

    def set_drag_active(self, active: bool) -> None:
        if bool(self.property("dragActive")) == active:
            return

        self.setProperty("dragActive", active)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        mime_data = event.mimeData()
        if mime_data_has_local_paths(mime_data):
            if not self._file_import_enabled:
                event.ignore()
                return
            self.set_drag_active(True)
            event.acceptProposedAction()
            return
        if dropped_text_from_mime_data(mime_data):
            self.set_drag_active(True)
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        mime_data = event.mimeData()
        if mime_data_has_local_paths(mime_data):
            if not self._file_import_enabled:
                event.ignore()
                return
            event.acceptProposedAction()
            return
        if dropped_text_from_mime_data(mime_data):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.set_drag_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        self.set_drag_active(False)

        mime_data = event.mimeData()
        local_paths = local_paths_from_mime_data(mime_data)
        if local_paths:
            if not self._file_import_enabled:
                event.ignore()
                return
            self.files_dropped.emit(local_paths)
            event.acceptProposedAction()
            return

        text = dropped_text_from_mime_data(mime_data)
        if text:
            self.text_dropped.emit(text)
            event.acceptProposedAction()
            return

        event.ignore()
