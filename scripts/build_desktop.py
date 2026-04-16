from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import PyInstaller.__main__
from PyInstaller.utils.hooks import collect_data_files


REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = REPO_ROOT / "dist"
sys.path.insert(0, str(REPO_ROOT / "src"))

from open_anonymizer.services.ocr_runtime import BUNDLED_TESSERACT_DIRNAME


APP_ICON_SOURCE = REPO_ROOT / "src" / "open_anonymizer" / "assets" / "white_fingerprint.svg"
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
WINDOWS_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
RUNTIME_BINARY_SUFFIXES = {".dylib", ".dll", ".exe", ".so"}
DEFAULT_EXCLUDED_PYSIDE6_MODULES = (
    "PySide6.QtNetwork",
    "PySide6.QtOpenGL",
    "PySide6.QtPdf",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtVirtualKeyboard",
)
NESTED_LOOKUP_CACHE_FRAGMENT = "data/lookup/cache/cache"
BUNDLED_LOOKUP_CACHE_RELATIVE_PATH = Path(
    "belgian_deduce",
    "data",
    "lookup",
    "cache",
    "lookup_structs.pickle",
)


def _pyinstaller_mapping(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
) -> str:
    return f"{os.fspath(source)}{os.pathsep}{os.fspath(destination)}"


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


def _collect_package_data_args(
    package_name: str,
    *,
    excluded_fragments: tuple[str, ...] = (),
) -> list[str]:
    args: list[str] = []
    normalized_exclusions = tuple(
        fragment.replace("\\", "/").strip("/")
        for fragment in excluded_fragments
    )

    for source, destination in collect_data_files(package_name):
        normalized_source = source.replace("\\", "/")
        normalized_destination = destination.replace("\\", "/")
        if any(
            fragment
            and (
                fragment in normalized_source
                or fragment in normalized_destination
            )
            for fragment in normalized_exclusions
        ):
            continue

        args.extend(
            [
                "--add-data",
                _pyinstaller_mapping(source, destination),
            ]
        )

    return args


def _bundled_lookup_cache_paths() -> list[Path]:
    if sys.platform == "darwin":
        return [
            DIST_DIR
            / "OpenAnonymizer.app"
            / "Contents"
            / "Resources"
            / BUNDLED_LOOKUP_CACHE_RELATIVE_PATH
        ]

    return [
        DIST_DIR / "OpenAnonymizer" / "_internal" / BUNDLED_LOOKUP_CACHE_RELATIVE_PATH
    ]


def verify_bundled_lookup_cache() -> Path:
    candidates = _bundled_lookup_cache_paths()
    for candidate in candidates:
        if candidate.exists():
            return candidate

    searched_locations = "\n".join(f"- {candidate}" for candidate in candidates)
    raise RuntimeError(
        "The packaged belgian-deduce lookup cache is missing from the PyInstaller output. "
        "The installer would ship without the prebuilt cache.\n"
        f"Searched:\n{searched_locations}"
    )


def _render_icon_image(icon_path: Path, size: int):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter

    if icon_path.suffix.lower() == ".svg":
        from PySide6.QtSvg import QSvgRenderer

        renderer = QSvgRenderer(str(icon_path))
        if not renderer.isValid():
            return QImage()

        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)

        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        return image

    source_image = QImage(str(icon_path))
    if source_image.isNull():
        return QImage()

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
    return image


def _encode_png_image(image) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    png_data = QByteArray()
    buffer = QBuffer(png_data)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise RuntimeError("Failed to open the icon buffer for PNG encoding.")

    try:
        if not image.save(buffer, "PNG"):
            raise RuntimeError("Failed to encode the icon image as PNG.")
    finally:
        buffer.close()

    return bytes(png_data)


def _write_windows_ico(icon_images: list[tuple[int, bytes]], ico_path: Path) -> None:
    icon_count = len(icon_images)
    image_offset = 6 + (16 * icon_count)
    icon_dir = bytearray()
    icon_dir.extend((0).to_bytes(2, "little"))
    icon_dir.extend((1).to_bytes(2, "little"))
    icon_dir.extend(icon_count.to_bytes(2, "little"))

    directory_entries: list[bytes] = []
    encoded_images: list[bytes] = []

    for size, png_bytes in icon_images:
        entry = bytearray()
        entry.append(0 if size >= 256 else size)
        entry.append(0 if size >= 256 else size)
        entry.append(0)
        entry.append(0)
        entry.extend((1).to_bytes(2, "little"))
        entry.extend((32).to_bytes(2, "little"))
        entry.extend(len(png_bytes).to_bytes(4, "little"))
        entry.extend(image_offset.to_bytes(4, "little"))
        directory_entries.append(bytes(entry))
        encoded_images.append(png_bytes)
        image_offset += len(png_bytes)

    ico_path.write_bytes(bytes(icon_dir) + b"".join(directory_entries) + b"".join(encoded_images))


def _build_macos_icns(icon_path: Path) -> Path | None:
    if sys.platform != "darwin" or not icon_path.exists():
        return None

    iconutil = shutil.which("iconutil")
    if iconutil is None:
        print("Skipping application bundle icon because iconutil is unavailable.")
        return None

    probe_image = _render_icon_image(icon_path, 1024)
    if probe_image.isNull():
        print(f"Skipping application bundle icon because {icon_path} could not be loaded.")
        return None

    build_dir = REPO_ROOT / "build"
    iconset_dir = build_dir / "OpenAnonymizer.iconset"
    icns_path = build_dir / "OpenAnonymizer.icns"
    iconset_dir.mkdir(parents=True, exist_ok=True)

    for size, filename in MACOS_ICON_SIZES:
        image = _render_icon_image(icon_path, size)

        if not image.save(str(iconset_dir / filename)):
            raise RuntimeError(f"Failed to render icon asset: {iconset_dir / filename}")

    subprocess.run(
        [iconutil, "-c", "icns", str(iconset_dir), "-o", str(icns_path)],
        check=True,
    )
    return icns_path


def _build_windows_ico(icon_path: Path) -> Path | None:
    if sys.platform != "win32" or not icon_path.exists():
        return None

    icon_images: list[tuple[int, bytes]] = []
    for size in WINDOWS_ICON_SIZES:
        image = _render_icon_image(icon_path, size)
        if image.isNull():
            print(f"Skipping Windows executable icon because {icon_path} could not be loaded.")
            return None
        icon_images.append((size, _encode_png_image(image)))

    build_dir = REPO_ROOT / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    ico_path = build_dir / "OpenAnonymizer.ico"
    _write_windows_ico(icon_images, ico_path)
    return ico_path


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
        *_collect_package_data_args(
            "belgian_deduce",
            excluded_fragments=(NESTED_LOOKUP_CACHE_FRAGMENT,),
        ),
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
    elif sys.platform == "win32":
        windows_icon = _build_windows_ico(APP_ICON_SOURCE)
        if windows_icon is not None:
            pyinstaller_args.extend(["--icon", str(windows_icon)])

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
    bundled_lookup_cache = verify_bundled_lookup_cache()
    print("Bundled belgian-deduce lookup cache:", bundled_lookup_cache)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
