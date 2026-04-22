from __future__ import annotations

import argparse
import plistlib
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "Open Anonymizer"
APP_BUNDLE_NAME = "OpenAnonymizer.app"
DEFAULT_VOLUME_NAME = APP_NAME
DEFAULT_ICON_PATH = REPO_ROOT / "build" / "OpenAnonymizer.icns"
DEFAULT_OUTPUT_PATH = REPO_ROOT / "release" / "open-anonymizer-macos.dmg"
DEFAULT_APP_PATH = REPO_ROOT / "dist" / APP_BUNDLE_NAME


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a macOS DMG for the packaged desktop app.")
    parser.add_argument(
        "--app-path",
        type=Path,
        default=DEFAULT_APP_PATH,
        help="Path to the built .app bundle to package inside the DMG.",
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Path where the generated DMG should be written.",
    )
    parser.add_argument(
        "--icon-path",
        type=Path,
        default=DEFAULT_ICON_PATH,
        help="Path to the .icns file used for the mounted volume and DMG file icon.",
    )
    parser.add_argument(
        "--volume-name",
        default=DEFAULT_VOLUME_NAME,
        help="Mounted DMG volume name shown in Finder.",
    )
    return parser


def _run(command: list[str], *, stdout=None, capture_output: bool = False):
    if capture_output:
        return subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    return subprocess.run(command, check=True, stdout=stdout)


def _stage_app_bundle(app_path: Path, staging_root: Path) -> None:
    staging_root.mkdir(parents=True, exist_ok=True)
    _run(["ditto", str(app_path), str(staging_root / app_path.name)])

    applications_link = staging_root / "Applications"
    if not applications_link.exists():
        applications_link.symlink_to("/Applications")


def _apply_volume_icon(mountpoint: Path, icon_path: Path) -> None:
    volume_icon_path = mountpoint / ".VolumeIcon.icns"
    _run(["ditto", str(icon_path), str(volume_icon_path)])
    _run(["SetFile", "-a", "C", str(mountpoint)])
    _run(["SetFile", "-a", "V", str(volume_icon_path)])


def _stamp_dmg_icon(dmg_path: Path, icon_path: Path, temp_dir: Path) -> None:
    icon_resource_path = temp_dir / "dmg-icon.rsrc"
    _run(["sips", "-i", str(icon_path)], stdout=subprocess.DEVNULL)
    with icon_resource_path.open("wb") as handle:
        _run(["DeRez", "-only", "icns", str(icon_path)], stdout=handle)
    _run(["Rez", "-append", str(icon_resource_path), "-o", str(dmg_path)])
    _run(["SetFile", "-a", "C", str(dmg_path)])


def _available_macos_icon_tools(*tool_names: str) -> bool:
    return all(shutil.which(tool_name) is not None for tool_name in tool_names)


def _maybe_apply_volume_icon(mountpoint: Path, icon_path: Path) -> None:
    if not _available_macos_icon_tools("SetFile"):
        print("Skipping DMG volume icon metadata because SetFile is unavailable.")
        return

    _apply_volume_icon(mountpoint, icon_path)


def _maybe_stamp_dmg_icon(dmg_path: Path, icon_path: Path, temp_dir: Path) -> None:
    if not _available_macos_icon_tools("sips", "DeRez", "Rez", "SetFile"):
        print("Skipping DMG file icon stamping because developer tools are unavailable.")
        return

    _stamp_dmg_icon(dmg_path, icon_path, temp_dir)


def _base_disk_identifier(device_identifier: str) -> str:
    base_identifier = device_identifier
    while re.search(r"s\d+$", base_identifier):
        base_identifier = re.sub(r"s\d+$", "", base_identifier)
    return base_identifier


def _attached_device_identifier(attach_output: bytes, mountpoint: Path) -> str | None:
    try:
        attach_info = plistlib.loads(attach_output)
    except plistlib.InvalidFileException:
        return None

    entities = attach_info.get("system-entities", [])
    mountpoint_text = str(mountpoint)

    for entity in entities:
        if entity.get("mount-point") != mountpoint_text:
            continue
        device_identifier = entity.get("dev-entry")
        if isinstance(device_identifier, str) and device_identifier:
            return _base_disk_identifier(device_identifier)

    for entity in entities:
        device_identifier = entity.get("dev-entry")
        if isinstance(device_identifier, str) and device_identifier:
            return _base_disk_identifier(device_identifier)

    return None


def _attach_image(rw_dmg_path: Path, mountpoint: Path) -> str | None:
    result = _run(
        [
            "hdiutil",
            "attach",
            "-plist",
            "-readwrite",
            "-noverify",
            "-noautoopen",
            str(rw_dmg_path),
            "-mountpoint",
            str(mountpoint),
        ],
        capture_output=True,
    )
    return _attached_device_identifier(result.stdout, mountpoint)


def _detach_error_indicates_missing_target(error: subprocess.CalledProcessError) -> bool:
    output_parts: list[str] = []
    for stream in (error.stdout, error.stderr):
        if isinstance(stream, bytes):
            output_parts.append(stream.decode("utf-8", errors="ignore"))
        elif isinstance(stream, str):
            output_parts.append(stream)
    return "no such file or directory" in " ".join(output_parts).lower()


def _detach_image(
    mountpoint: Path,
    *,
    device_identifier: str | None = None,
    retries: int = 4,
    retry_delay_seconds: float = 1.0,
) -> None:
    detach_target = device_identifier or str(mountpoint)

    for attempt in range(retries):
        try:
            _run(["hdiutil", "detach", detach_target])
            return
        except subprocess.CalledProcessError as error:
            if error.returncode != 16:
                raise
            if attempt == retries - 1:
                break
            time.sleep(retry_delay_seconds)

    try:
        _run(["hdiutil", "detach", detach_target, "-force"])
    except subprocess.CalledProcessError as error:
        if _detach_error_indicates_missing_target(error):
            return
        raise


def build_dmg(
    *,
    app_path: Path,
    output_path: Path,
    icon_path: Path,
    volume_name: str,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        staging_root = temp_dir / "staging"
        rw_dmg_path = temp_dir / "OpenAnonymizer-readwrite.dmg"
        mountpoint = temp_dir / "mount"

        _stage_app_bundle(app_path, staging_root)
        mountpoint.mkdir(parents=True, exist_ok=True)

        _run(
            [
                "hdiutil",
                "create",
                "-ov",
                "-fs",
                "HFS+",
                "-format",
                "UDRW",
                "-volname",
                volume_name,
                "-srcfolder",
                str(staging_root),
                str(rw_dmg_path),
            ]
        )

        attached = False
        attached_device_identifier: str | None = None
        try:
            attached_device_identifier = _attach_image(rw_dmg_path, mountpoint)
            attached = True
            _maybe_apply_volume_icon(mountpoint, icon_path)
        finally:
            if attached:
                _detach_image(
                    mountpoint,
                    device_identifier=attached_device_identifier,
                )

        _run(
            [
                "hdiutil",
                "convert",
                str(rw_dmg_path),
                "-ov",
                "-format",
                "UDZO",
                "-imagekey",
                "zlib-level=9",
                "-o",
                str(output_path),
            ]
        )
        _maybe_stamp_dmg_icon(output_path, icon_path, temp_dir)

    return output_path


def main() -> int:
    args = build_argument_parser().parse_args()
    app_path = args.app_path.resolve()
    output_path = args.output_path.resolve()
    icon_path = args.icon_path.resolve()

    if not app_path.exists():
        raise FileNotFoundError(f"Application bundle does not exist: {app_path}")
    if not icon_path.exists():
        raise FileNotFoundError(
            f"DMG icon file does not exist: {icon_path}. Build the desktop app first to generate it."
        )

    dmg_path = build_dmg(
        app_path=app_path,
        output_path=output_path,
        icon_path=icon_path,
        volume_name=args.volume_name,
    )
    print(dmg_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
