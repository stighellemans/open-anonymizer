from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout


class DropArea(QFrame):
    files_dropped = Signal(list)
    text_dropped = Signal(str)
    import_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("dropArea")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self.label = QLabel("Drop files or text here")
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

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        mime_data = event.mimeData()
        if mime_data.hasUrls() or mime_data.hasText():
            self.setProperty("dragActive", True)
            self.style().unpolish(self)
            self.style().polish(self)
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.setProperty("dragActive", False)
        self.style().unpolish(self)
        self.style().polish(self)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        self.setProperty("dragActive", False)
        self.style().unpolish(self)
        self.style().polish(self)

        mime_data = event.mimeData()
        local_paths = [Path(url.toLocalFile()) for url in mime_data.urls() if url.isLocalFile()]
        if local_paths:
            self.files_dropped.emit(local_paths)
            event.acceptProposedAction()
            return

        text = mime_data.text().strip()
        if text:
            self.text_dropped.emit(text)
            event.acceptProposedAction()
            return

        event.ignore()
