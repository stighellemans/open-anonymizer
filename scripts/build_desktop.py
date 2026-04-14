from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import PyInstaller.__main__


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from open_anonymizer.services.ocr_runtime import BUNDLED_TESSERACT_DIRNAME


RUNTIME_BINARY_SUFFIXES = {".dylib", ".dll", ".exe", ".so"}


def _pyinstaller_mapping(source: Path, destination: Path) -> str:
    return f"{source}{os.pathsep}{destination}"


def _is_runtime_binary(path: Path) -> bool:
    if path.suffix.lower() in RUNTIME_BINARY_SUFFIXES:
        return True
    return path.parent.name in {"bin", "lib"}


def _collect_tesseract_runtime_args(runtime_dir: Path) -> list[str]:
    args: list[str] = []
    if not runtime_dir.exists():
        return args

    for source in sorted(path for path in runtime_dir.rglob("*") if path.is_file()):
        relative_parent = source.relative_to(runtime_dir).parent
        destination = Path(BUNDLED_TESSERACT_DIRNAME) / relative_parent
        option = "--add-binary" if _is_runtime_binary(source) else "--add-data"
        args.extend([option, _pyinstaller_mapping(source, destination)])
    return args


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the desktop app with optional bundled OCR runtime.")
    parser.add_argument(
        "--tesseract-runtime-dir",
        type=Path,
        default=REPO_ROOT / "vendor" / BUNDLED_TESSERACT_DIRNAME,
        help="Directory containing a self-contained Tesseract runtime to bundle into the app.",
    )
    parser.add_argument(
        "--bundle-identifier",
        default="com.openanonymizer.app",
        help="macOS bundle identifier to pass to PyInstaller.",
    )
    parser.add_argument(
        "--codesign-identity",
        help="Optional Developer ID Application identity for macOS code signing.",
    )
    parser.add_argument(
        "--osx-entitlements-file",
        type=Path,
        help="Optional entitlements file passed through to PyInstaller.",
    )
    args = parser.parse_args()

    pyinstaller_args = [
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        "OpenAnonymizer",
        "--collect-data",
        "deduce",
        "--collect-binaries",
        "pypdfium2_raw",
        "--osx-bundle-identifier",
        args.bundle_identifier,
        str(REPO_ROOT / "src" / "open_anonymizer" / "__main__.py"),
    ]

    if args.codesign_identity:
        pyinstaller_args.extend(["--codesign-identity", args.codesign_identity])
    if args.osx_entitlements_file:
        pyinstaller_args.extend(["--osx-entitlements-file", str(args.osx_entitlements_file.resolve())])

    runtime_dir = args.tesseract_runtime_dir.resolve()
    pyinstaller_args.extend(_collect_tesseract_runtime_args(runtime_dir))

    print("Bundling Tesseract runtime from:", runtime_dir if runtime_dir.exists() else "not found")
    PyInstaller.__main__.run(pyinstaller_args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
