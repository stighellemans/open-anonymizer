from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from open_anonymizer.ui import MainWindow

APP_STYLESHEET = """
QWidget {
    background: #f6f3ef;
    color: #1f2933;
    font-family: "Avenir Next", "Segoe UI", sans-serif;
    font-size: 13px;
}
QMainWindow {
    background: #f6f3ef;
}
QGroupBox {
    border: 1px solid #d6d0c8;
    border-radius: 12px;
    margin-top: 10px;
    padding: 16px 14px 14px 14px;
    background: #fbfaf8;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px 0 6px;
}
QPlainTextEdit, QListWidget, QLineEdit {
    background: #fffdf9;
    border: 1px solid #d6d0c8;
    border-radius: 10px;
    padding: 8px;
    selection-background-color: #d3e4dc;
}
QPushButton {
    background: #1f4d45;
    color: #ffffff;
    border: none;
    border-radius: 10px;
    padding: 10px 14px;
    min-height: 18px;
}
QPushButton:disabled {
    background: #a7b0ae;
    color: #eef2f1;
}
QPushButton:hover:!disabled {
    background: #163b35;
}
QLabel#windowTitle {
    font-size: 28px;
    font-weight: 700;
}
QLabel#windowSubtitle {
    color: #52606d;
    margin-bottom: 4px;
}
QLabel#documentStatus {
    color: #52606d;
}
QFrame#dropArea {
    border: 2px dashed #b7afa4;
    border-radius: 16px;
    background: #fdfbf7;
}
QFrame#dropArea[dragActive="true"] {
    border-color: #1f4d45;
    background: #eef6f2;
}
"""


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Open Anonymizer")
    app.setOrganizationName("Open Anonymizer")
    app.setStyleSheet(APP_STYLESHEET)

    window = MainWindow()
    window.show()
    return app.exec()
