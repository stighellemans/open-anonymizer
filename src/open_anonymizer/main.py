from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from open_anonymizer.branding import application_icon
from open_anonymizer.ui import MainWindow


WINDOWS_APP_ID = "com.openanonymizer.app"
STARTUP_BACKEND_WARMUP_DELAY_MS = 0

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
    padding: 14px 12px 12px 12px;
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
    padding: 7px;
    selection-background-color: #d3e4dc;
}
QListWidget#documentList {
    background: #f8f3ec;
    border: 1px solid #d8cfc4;
    border-radius: 14px;
    padding: 6px;
    outline: 0;
}
QListWidget#documentList::item {
    border: none;
    padding: 0;
    margin: 0;
}
QListWidget#documentList::item:selected {
    background: transparent;
}
QPushButton, QToolButton {
    background: #1f4d45;
    color: #ffffff;
    border: none;
    border-radius: 10px;
    padding: 8px 14px;
    min-height: 18px;
}
QPushButton#secondaryButton, QToolButton#secondaryButton {
    background: #fffdf9;
    color: #1f4d45;
    border: 1px solid #c8beb2;
}
QPushButton#secondaryButton:hover:!disabled, QToolButton#secondaryButton:hover:!disabled {
    background: #f6efe6;
}
QPushButton:disabled, QToolButton:disabled {
    background: #a7b0ae;
    color: #eef2f1;
}
QPushButton:hover:!disabled, QToolButton:hover:!disabled {
    background: #163b35;
}
QToolButton#exportButton {
    min-width: 122px;
    padding: 8px 30px 8px 14px;
}
QToolButton#exportButton::menu-button {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 22px;
    border-left: 1px solid rgba(255, 255, 255, 0.18);
    border-top-right-radius: 10px;
    border-bottom-right-radius: 10px;
}
QToolButton#exportButton::menu-arrow {
    width: 8px;
    height: 8px;
}
QMenu {
    background: #fffdf9;
    border: 1px solid #d6d0c8;
    border-radius: 12px;
    padding: 8px;
}
QMenu::item {
    border-radius: 8px;
    padding: 8px 14px;
    margin: 2px 0;
}
QMenu::item:selected {
    background: #dbeafe;
    color: #173f7a;
}
QLabel#windowTitle {
    font-size: 26px;
    font-weight: 700;
}
QLabel#windowSubtitle {
    color: #52606d;
    margin-bottom: 2px;
}
QToolButton#headerInfoButton {
    background: #fffdf9;
    color: #1f4d45;
    border: 1px solid #c8beb2;
    border-radius: 10px;
    font-size: 12px;
    font-weight: 700;
    min-width: 20px;
    max-width: 20px;
    min-height: 20px;
    max-height: 20px;
    padding: 0;
}
QToolButton#headerInfoButton:hover:!disabled {
    background: #f6efe6;
    color: #163b35;
}
QLabel#headerIcon {
    background: #1f4d45;
    border: 1px solid #163b35;
    border-radius: 18px;
    padding: 10px;
}
QLabel#documentStatus {
    color: #52606d;
}
QStatusBar {
    background: #f6f3ef;
    color: #52606d;
    padding: 0;
}
QStatusBar::item {
    border: none;
}
QToolButton#bugReportLinkButton, QToolButton#appInfoBugReportButton, QToolButton#appInfoBelgianDeduceButton, QToolButton#appInfoOriginalDeduceButton {
    background: transparent;
    color: #1f4d45;
    border: none;
    font-size: 11px;
    padding: 0;
    margin: 0 2px 0 6px;
}
QToolButton#bugReportLinkButton:hover:!disabled, QToolButton#appInfoBugReportButton:hover:!disabled, QToolButton#appInfoBelgianDeduceButton:hover:!disabled, QToolButton#appInfoOriginalDeduceButton:hover:!disabled {
    background: transparent;
    color: #163b35;
}
QToolButton#bugReportLinkButton:pressed, QToolButton#appInfoBugReportButton:pressed, QToolButton#appInfoBelgianDeduceButton:pressed, QToolButton#appInfoOriginalDeduceButton:pressed {
    background: transparent;
}
QDialog#appInfoDialog {
    background: #fbfaf8;
}
QLabel#appInfoTitle {
    font-size: 20px;
    font-weight: 700;
}
QLabel#appInfoBody {
    color: #3f4c59;
}
QLabel#appInfoMeta {
    color: #52606d;
    font-weight: 600;
}
QFrame#appInfoWarning {
    background: #fff6ed;
    border: 1px solid #ead6bf;
    border-radius: 12px;
}
QLabel#appInfoWarningTitle {
    color: #8a3b12;
    font-weight: 700;
}
QLabel#appInfoWarningBody {
    color: #8a3b12;
}
QScrollArea#anonymizationSummaryScroll {
    border: none;
    background: transparent;
}
QWidget#anonymizationSummaryBody {
    background: transparent;
}
QFrame#dropArea {
    border: 2px dashed #b7afa4;
    border-radius: 14px;
    background: #fdfbf7;
}
QFrame#dropArea QLabel#dropAreaLabel {
    background: transparent;
}
QFrame#dropArea[dragActive="true"] {
    border-color: #1f4d45;
    background: #eef6f2;
}
QFrame#dropArea[dragActive="true"] QLabel#dropAreaLabel {
    background: transparent;
}
"""


def main() -> int:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setApplicationName("Open Anonymizer")
    app.setOrganizationName("Open Anonymizer")
    app.setStyleSheet(APP_STYLESHEET)
    app.setWindowIcon(application_icon())

    window = MainWindow()
    window.show()
    window.schedule_background_backend_warmup(
        STARTUP_BACKEND_WARMUP_DELAY_MS,
    )
    try:
        return app.exec()
    finally:
        from open_anonymizer.services.deduce_backend import (
            release_backend_resources,
        )

        release_backend_resources()
