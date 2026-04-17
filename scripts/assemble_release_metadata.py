from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from open_anonymizer import __version__


APP_NAME = "Open Anonymizer"
IGNORED_FILENAMES = {".DS_Store", "SHA256SUMS.txt", "release-manifest.json"}
WINDOWS_STABLE_ALIAS_NAME = "open-anonymizer-windows-setup.exe"
WINDOWS_VERSIONED_INSTALLER_PATTERN = re.compile(r"^OpenAnonymizer-.*-Setup\.exe$")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Flatten downloaded release artifacts into a single folder and "
            "generate SHA256 and manifest metadata."
        )
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        required=True,
        help="Directory containing downloaded GitHub Actions artifacts.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory where the flattened release assets should be written.",
    )
    return parser


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_artifact_file(source_path: Path, output_dir: Path) -> Path:
    destination_path = output_dir / source_path.name
    if destination_path.exists():
        if sha256_file(destination_path) == sha256_file(source_path):
            return destination_path
        raise FileExistsError(
            f"Refusing to overwrite {destination_path.name} with different contents."
        )

    shutil.copy2(source_path, destination_path)
    return destination_path


def _should_skip_source_path(source_path: Path, available_filenames: set[str]) -> bool:
    if source_path.name in IGNORED_FILENAMES:
        return True

    return (
        WINDOWS_STABLE_ALIAS_NAME in available_filenames
        and WINDOWS_VERSIONED_INSTALLER_PATTERN.fullmatch(source_path.name) is not None
    )


def flatten_release_artifacts(artifacts_dir: Path, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    flattened_paths: list[Path] = []
    source_paths = sorted(path for path in artifacts_dir.rglob("*") if path.is_file())
    available_filenames = {path.name for path in source_paths}

    for source_path in source_paths:
        if _should_skip_source_path(source_path, available_filenames):
            continue
        flattened_paths.append(_copy_artifact_file(source_path, output_dir))

    return sorted(flattened_paths)


def write_checksums(paths: list[Path], output_dir: Path) -> Path:
    checksum_path = output_dir / "SHA256SUMS.txt"
    lines = [f"{sha256_file(path)}  {path.name}" for path in paths]
    checksum_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return checksum_path


def write_manifest(paths: list[Path], output_dir: Path) -> Path:
    manifest_path = output_dir / "release-manifest.json"
    manifest = {
        "app_name": APP_NAME,
        "version": __version__,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "assets": [
            {
                "filename": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in paths
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def main() -> int:
    args = build_argument_parser().parse_args()
    artifacts_dir = args.artifacts_dir.resolve()
    output_dir = args.output_dir.resolve()

    if not artifacts_dir.exists():
        raise FileNotFoundError(f"Artifacts directory does not exist: {artifacts_dir}")

    flattened_paths = flatten_release_artifacts(artifacts_dir, output_dir)
    if not flattened_paths:
        raise FileNotFoundError(
            "No release assets were found after flattening the workflow artifacts."
        )

    write_checksums(flattened_paths, output_dir)
    write_manifest(flattened_paths, output_dir)
    print(output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
