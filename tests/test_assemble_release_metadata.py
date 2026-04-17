from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    module_path = REPO_ROOT / "scripts" / "assemble_release_metadata.py"
    spec = importlib.util.spec_from_file_location(
        "assemble_release_metadata_test_module",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_flatten_release_artifacts_writes_checksums_and_manifest(tmp_path: Path) -> None:
    module = _load_module()
    artifacts_dir = tmp_path / "artifacts"
    output_dir = tmp_path / "release-assets"
    (artifacts_dir / "release-macos-arm64").mkdir(parents=True)
    (artifacts_dir / "release-windows-x64").mkdir(parents=True)
    (artifacts_dir / "release-python-distributions").mkdir(parents=True)

    macos_asset = artifacts_dir / "release-macos-arm64" / "open-anonymizer-macos-arm64.dmg"
    windows_asset = artifacts_dir / "release-windows-x64" / "open-anonymizer-windows-setup.exe"
    versioned_windows_asset = artifacts_dir / "release-windows-x64" / "OpenAnonymizer-0.1.1-Setup.exe"
    wheel_asset = artifacts_dir / "release-python-distributions" / "open_anonymizer-0.1.1-py3-none-any.whl"
    macos_asset.write_bytes(b"macos")
    windows_asset.write_bytes(b"windows")
    versioned_windows_asset.write_bytes(b"windows")
    wheel_asset.write_bytes(b"wheel")
    (artifacts_dir / "release-windows-x64" / ".DS_Store").write_bytes(b"ignore")

    flattened_paths = module.flatten_release_artifacts(artifacts_dir, output_dir)
    assert [path.name for path in flattened_paths] == [
        "open-anonymizer-macos-arm64.dmg",
        "open-anonymizer-windows-setup.exe",
        "open_anonymizer-0.1.1-py3-none-any.whl",
    ]

    checksum_path = module.write_checksums(flattened_paths, output_dir)
    manifest_path = module.write_manifest(flattened_paths, output_dir)

    checksums = checksum_path.read_text(encoding="utf-8")
    assert "open-anonymizer-macos-arm64.dmg" in checksums
    assert "open-anonymizer-windows-setup.exe" in checksums
    assert "open_anonymizer-0.1.1-py3-none-any.whl" in checksums

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["app_name"] == "Open Anonymizer"
    assert manifest["version"] == module.__version__
    assert [asset["filename"] for asset in manifest["assets"]] == [
        "open-anonymizer-macos-arm64.dmg",
        "open-anonymizer-windows-setup.exe",
        "open_anonymizer-0.1.1-py3-none-any.whl",
    ]


def test_flatten_release_artifacts_keeps_versioned_windows_installer_without_alias(tmp_path: Path) -> None:
    module = _load_module()
    artifacts_dir = tmp_path / "artifacts"
    output_dir = tmp_path / "release-assets"
    (artifacts_dir / "release-windows-x64").mkdir(parents=True)

    versioned_windows_asset = artifacts_dir / "release-windows-x64" / "OpenAnonymizer-0.1.1-Setup.exe"
    versioned_windows_asset.write_bytes(b"windows")

    flattened_paths = module.flatten_release_artifacts(artifacts_dir, output_dir)

    assert [path.name for path in flattened_paths] == ["OpenAnonymizer-0.1.1-Setup.exe"]


def test_flatten_release_artifacts_rejects_conflicting_duplicate_filenames(tmp_path: Path) -> None:
    module = _load_module()
    artifacts_dir = tmp_path / "artifacts"
    output_dir = tmp_path / "release-assets"
    first_dir = artifacts_dir / "first"
    second_dir = artifacts_dir / "second"
    first_dir.mkdir(parents=True)
    second_dir.mkdir(parents=True)

    (first_dir / "shared.bin").write_bytes(b"first")
    (second_dir / "shared.bin").write_bytes(b"second")

    try:
        module.flatten_release_artifacts(artifacts_dir, output_dir)
    except FileExistsError as exc:
        assert "shared.bin" in str(exc)
    else:
        raise AssertionError("Expected duplicate conflicting filenames to raise FileExistsError.")
