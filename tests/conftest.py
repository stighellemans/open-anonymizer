import os
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def isolate_qsettings(tmp_path: Path):
    settings_dir = tmp_path / "qsettings"
    settings_dir.mkdir()

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        str(settings_dir),
    )

    settings = QSettings(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        "Open Anonymizer",
        "Open Anonymizer",
    )
    settings.clear()
    settings.sync()

    yield

    settings.clear()
    settings.sync()
