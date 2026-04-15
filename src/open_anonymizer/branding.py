from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon


_APP_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)


def _asset_path(filename: str) -> Path:
    candidates: list[Path] = []

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "open_anonymizer" / "assets" / filename)

    module_dir = Path(__file__).resolve().parent
    candidates.append(module_dir / "assets" / filename)

    executable_dir = Path(sys.executable).resolve().parent
    candidates.extend(
        [
            executable_dir / "_internal" / "open_anonymizer" / "assets" / filename,
            executable_dir.parent / "Resources" / "open_anonymizer" / "assets" / filename,
        ]
    )

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return candidates[0]


def application_icon() -> QIcon:
    source_icon = QIcon(str(_asset_path("fingerprint.png")))
    if source_icon.isNull():
        return QIcon()

    icon = QIcon()
    for size in _APP_ICON_SIZES:
        pixmap = source_icon.pixmap(QSize(size, size))
        if not pixmap.isNull():
            icon.addPixmap(pixmap)

    return icon if not icon.isNull() else source_icon


def bug_report_icon() -> QIcon:
    return QIcon(str(_asset_path("bug-report.png")))
