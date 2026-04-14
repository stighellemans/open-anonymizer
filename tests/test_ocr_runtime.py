from __future__ import annotations

import os
from pathlib import Path

from open_anonymizer.services.ocr_runtime import (
    build_tesseract_subprocess_env,
    find_tessdata_dir,
    find_tesseract_binary,
)


def test_find_tesseract_binary_prefers_bundled_runtime(tmp_path: Path, monkeypatch) -> None:
    executable_name = "tesseract.exe" if __import__("os").name == "nt" else "tesseract"
    binary_path = tmp_path / "tesseract_runtime" / "bin" / executable_name
    binary_path.parent.mkdir(parents=True)
    binary_path.write_text("", encoding="utf-8")

    monkeypatch.setenv("OPEN_ANONYMIZER_BUNDLE_ROOT", str(tmp_path))
    monkeypatch.delenv("OPEN_ANONYMIZER_TESSERACT_BIN", raising=False)

    assert find_tesseract_binary() == binary_path.resolve()


def test_find_tessdata_dir_prefers_bundled_runtime(tmp_path: Path, monkeypatch) -> None:
    tessdata_dir = tmp_path / "tesseract_runtime" / "share" / "tessdata"
    tessdata_dir.mkdir(parents=True)

    monkeypatch.setenv("OPEN_ANONYMIZER_BUNDLE_ROOT", str(tmp_path))
    monkeypatch.delenv("OPEN_ANONYMIZER_TESSDATA_DIR", raising=False)

    assert find_tessdata_dir() == tessdata_dir.resolve()


def test_find_tesseract_runtime_supports_macos_bundle_layout(tmp_path: Path, monkeypatch) -> None:
    executable_name = "tesseract.exe" if __import__("os").name == "nt" else "tesseract"
    binary_path = tmp_path / "Contents" / "Frameworks" / "tesseract_runtime" / "bin" / executable_name
    tessdata_dir = tmp_path / "Contents" / "Resources" / "tesseract_runtime" / "share" / "tessdata"
    binary_path.parent.mkdir(parents=True)
    tessdata_dir.mkdir(parents=True)
    binary_path.write_text("", encoding="utf-8")

    monkeypatch.setenv("OPEN_ANONYMIZER_BUNDLE_ROOT", str(tmp_path / "Contents"))
    monkeypatch.delenv("OPEN_ANONYMIZER_TESSERACT_BIN", raising=False)
    monkeypatch.delenv("OPEN_ANONYMIZER_TESSDATA_DIR", raising=False)

    assert find_tesseract_binary() == binary_path.resolve()
    assert find_tessdata_dir(binary_path.resolve()) == tessdata_dir.resolve()


def test_build_tesseract_subprocess_env_exposes_bundled_paths(tmp_path: Path, monkeypatch) -> None:
    executable_name = "tesseract.exe" if __import__("os").name == "nt" else "tesseract"
    runtime_root = tmp_path / "tesseract_runtime"
    binary_path = runtime_root / "bin" / executable_name
    tessdata_dir = runtime_root / "share" / "tessdata"
    lib_dir = runtime_root / "lib"

    binary_path.parent.mkdir(parents=True)
    tessdata_dir.mkdir(parents=True)
    lib_dir.mkdir(parents=True)
    binary_path.write_text("", encoding="utf-8")

    monkeypatch.setenv("OPEN_ANONYMIZER_BUNDLE_ROOT", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin")

    env = build_tesseract_subprocess_env(binary_path)

    assert env["PATH"].split(os.pathsep)[0] == str(binary_path.parent)
    assert str(lib_dir) in env["PATH"]
    assert env["TESSDATA_PREFIX"] == str(tessdata_dir.parent)
