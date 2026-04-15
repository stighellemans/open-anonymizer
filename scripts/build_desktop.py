from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import PyInstaller.__main__


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from open_anonymizer.services.ocr_runtime import BUNDLED_TESSERACT_DIRNAME


APP_ICON_SOURCE = REPO_ROOT / "src" / "open_anonymizer" / "assets" / "fingerprint.png"
CUSTOM_HOOKS_DIR = REPO_ROOT / "pyinstaller_hooks"
MACOS_ICON_SIZES = (
    (16, "icon_16x16.png"),
    (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"),
    (64, "icon_32x32@2x.png"),
    (128, "icon_128x128.png"),
    (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"),
    (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"),
    (1024, "icon_512x512@2x.png"),
)
RUNTIME_BINARY_SUFFIXES = {".dylib", ".dll", ".exe", ".so"}
DEFAULT_EXCLUDED_PYSIDE6_MODULES = (
    "PySide6.QtNetwork",
    "PySide6.QtOpenGL",
    "PySide6.QtPdf",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtVirtualKeyboard",
)


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


def _build_macos_icns(icon_path: Path) -> Path | None:
    if sys.platform != "darwin" or not icon_path.exists():
        return None

    iconutil = shutil.which("iconutil")
    if iconutil is None:
        print("Skipping application bundle icon because iconutil is unavailable.")
        return None

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter

    source_image = QImage(str(icon_path))
    if source_image.isNull():
        print(f"Skipping application bundle icon because {icon_path} could not be loaded.")
        return None

    build_dir = REPO_ROOT / "build"
    iconset_dir = build_dir / "OpenAnonymizer.iconset"
    icns_path = build_dir / "OpenAnonymizer.icns"
    iconset_dir.mkdir(parents=True, exist_ok=True)

    for size, filename in MACOS_ICON_SIZES:
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)

        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        scaled_image = source_image.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        x_offset = (size - scaled_image.width()) / 2
        y_offset = (size - scaled_image.height()) / 2
        painter.drawImage(int(x_offset), int(y_offset), scaled_image)
        painter.end()

        if not image.save(str(iconset_dir / filename)):
            raise RuntimeError(f"Failed to render icon asset: {iconset_dir / filename}")

    subprocess.run(
        [iconutil, "-c", "icns", str(iconset_dir), "-o", str(icns_path)],
        check=True,
    )
    return icns_path


def build_argument_parser() -> argparse.ArgumentParser:
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
    return parser


def build_pyinstaller_args(args: argparse.Namespace) -> list[str]:
    pyinstaller_args = [
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        "OpenAnonymizer",
        "--additional-hooks-dir",
        str(CUSTOM_HOOKS_DIR),
        "--collect-data",
        "open_anonymizer",
        "--collect-data",
        "belgian_deduce",
        "--copy-metadata",
        "belgian-deduce",
        "--copy-metadata",
        "docdeid",
        "--collect-submodules",
        "belgian_deduce",
        "--collect-binaries",
        "pypdfium2_raw",
    ]

    for module_name in DEFAULT_EXCLUDED_PYSIDE6_MODULES:
        pyinstaller_args.extend(["--exclude-module", module_name])

    if sys.platform == "darwin":
        pyinstaller_args.extend(["--osx-bundle-identifier", args.bundle_identifier])

        macos_icon = _build_macos_icns(APP_ICON_SOURCE)
        if macos_icon is not None:
            pyinstaller_args.extend(["--icon", str(macos_icon)])

        if args.codesign_identity:
            pyinstaller_args.extend(["--codesign-identity", args.codesign_identity])
        if args.osx_entitlements_file:
            pyinstaller_args.extend(["--osx-entitlements-file", str(args.osx_entitlements_file.resolve())])

    runtime_dir = args.tesseract_runtime_dir.resolve()
    pyinstaller_args.extend(_collect_tesseract_runtime_args(runtime_dir))
    pyinstaller_args.append(str(REPO_ROOT / "src" / "open_anonymizer" / "__main__.py"))
    return pyinstaller_args


def main() -> int:
    args = build_argument_parser().parse_args()
    pyinstaller_args = build_pyinstaller_args(args)
    runtime_dir = args.tesseract_runtime_dir.resolve()

    print("Bundling Tesseract runtime from:", runtime_dir if runtime_dir.exists() else "not found")
    PyInstaller.__main__.run(pyinstaller_args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
