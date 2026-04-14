from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_pdf_corpus_harness_passes_starter_manifest(tmp_path: Path) -> None:
    report_path = tmp_path / "corpus-report.json"
    repo_root = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "run_pdf_corpus.py"),
            "--manifest",
            str(repo_root / "tests" / "corpus" / "starter_manifest.json"),
            "--json-out",
            str(report_path),
        ],
        capture_output=True,
        text=True,
        cwd=repo_root,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Summary:" in result.stdout
    assert report_path.exists()
