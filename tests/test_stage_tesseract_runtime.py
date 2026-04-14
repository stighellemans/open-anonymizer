from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_stage_tesseract_runtime_copies_required_files(tmp_path: Path) -> None:
    source_root = tmp_path / "source-runtime"
    destination_root = tmp_path / "vendor-runtime"

    executable_name = "tesseract.exe" if __import__("os").name == "nt" else "tesseract"
    (source_root / "bin").mkdir(parents=True)
    (source_root / "lib").mkdir(parents=True)
    (source_root / "share" / "tessdata").mkdir(parents=True)

    (source_root / "bin" / executable_name).write_text("binary", encoding="utf-8")
    (source_root / "lib" / "libtesseract.dylib").write_text("lib", encoding="utf-8")
    for lang in ("eng", "fra", "nld", "osd"):
        (source_root / "share" / "tessdata" / f"{lang}.traineddata").write_text(lang, encoding="utf-8")

    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "stage_tesseract_runtime.py"),
            str(source_root),
            "--destination",
            str(destination_root),
        ],
        capture_output=True,
        text=True,
        cwd=repo_root,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (destination_root / "bin" / executable_name).exists()
    assert (destination_root / "lib" / "libtesseract.dylib").exists()
    assert (destination_root / "share" / "tessdata" / "fra.traineddata").exists()
