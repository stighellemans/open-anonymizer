from __future__ import annotations

from importlib.resources import as_file, files

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon


_APP_ICON_RESOURCE = files("open_anonymizer.assets").joinpath("fingerprint.png")
_BUG_REPORT_ICON_RESOURCE = files("open_anonymizer.assets").joinpath("bug-report.png")
_APP_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)


def application_icon() -> QIcon:
    with as_file(_APP_ICON_RESOURCE) as icon_path:
        source_icon = QIcon(str(icon_path))
        if source_icon.isNull():
            return QIcon()

        icon = QIcon()
        for size in _APP_ICON_SIZES:
            pixmap = source_icon.pixmap(QSize(size, size))
            if not pixmap.isNull():
                icon.addPixmap(pixmap)

        return icon if not icon.isNull() else source_icon


def bug_report_icon() -> QIcon:
    with as_file(_BUG_REPORT_ICON_RESOURCE) as icon_path:
        return QIcon(str(icon_path))
