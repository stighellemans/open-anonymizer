from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout


class DropArea(QFrame):
    files_dropped = Signal(list)
    text_dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("dropArea")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self.label = QLabel("Drop text, .txt files, or .pdf files here")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addWidget(self.label)

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

