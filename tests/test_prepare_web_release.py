from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_prepare_web_release_module():
    module_path = REPO_ROOT / "scripts" / "prepare_web_release.py"
    spec = importlib.util.spec_from_file_location("prepare_web_release_test_module", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_prepare_web_release_writes_manifest_checksums_and_page(tmp_path: Path) -> None:
    prepare_web_release = _load_prepare_web_release_module()
    release_dir = tmp_path / "release"
    output_dir = tmp_path / "web-ready"
    release_dir.mkdir()

    mac_archive = release_dir / prepare_web_release.DEFAULT_MACOS_ARCHIVE_NAME
    mac_archive.write_bytes(b"macos-build")

    args = prepare_web_release.build_argument_parser().parse_args(
        [
            "--release-dir",
            str(release_dir),
            "--output-dir",
            str(output_dir),
        ]
    )

    artifacts = prepare_web_release.build_release_artifacts(args)
    staged_artifacts = prepare_web_release.stage_artifacts(artifacts, output_dir)
    prepare_web_release.write_checksums(staged_artifacts, output_dir)
    prepare_web_release.write_manifest(staged_artifacts, output_dir)
    prepare_web_release.write_download_page(staged_artifacts, output_dir)

    copied_archive = output_dir / prepare_web_release.DEFAULT_MACOS_ARCHIVE_NAME
    assert copied_archive.exists()

    checksums = (output_dir / "SHA256SUMS.txt").read_text(encoding="utf-8")
    assert prepare_web_release.DEFAULT_MACOS_ARCHIVE_NAME in checksums

    manifest = json.loads((output_dir / "release-manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == prepare_web_release.__version__
    assert manifest["downloads"][0]["platform"] == "macos"
    assert manifest["downloads"][0]["available"] is True
    assert manifest["downloads"][1]["platform"] == "windows"
    assert manifest["downloads"][1]["available"] is False

    page = (output_dir / "index.html").read_text(encoding="utf-8")
    assert "Download for macOS" in page
    assert "Pending: build" in page
