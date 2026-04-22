from __future__ import annotations

import importlib.util
import plistlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS DMG packaging is macOS-only")


def _load_build_macos_dmg_module():
    module_path = REPO_ROOT / "scripts" / "build_macos_dmg.py"
    spec = importlib.util.spec_from_file_location("build_macos_dmg_test_module", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _TemporaryDirectoryStub:
    def __init__(self, path: Path) -> None:
        self._path = path

    def __enter__(self) -> str:
        self._path.mkdir(parents=True, exist_ok=True)
        return str(self._path)

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def _attach_output(device_identifier: str, mountpoint: Path) -> bytes:
    return plistlib.dumps(
        {
            "system-entities": [
                {"dev-entry": device_identifier, "potentially-mountable": 1},
                {
                    "content-hint": "Apple_HFS",
                    "dev-entry": f"{device_identifier}s1",
                    "mount-point": str(mountpoint),
                },
            ]
        }
    )


def test_build_dmg_invokes_expected_macos_packaging_tools(monkeypatch, tmp_path: Path) -> None:
    build_macos_dmg = _load_build_macos_dmg_module()
    app_path = tmp_path / "dist" / build_macos_dmg.APP_BUNDLE_NAME
    icon_path = tmp_path / "build" / "OpenAnonymizer.icns"
    output_path = tmp_path / "release" / "open-anonymizer-macos.dmg"
    temp_dir = tmp_path / "temp"

    app_path.mkdir(parents=True)
    icon_path.parent.mkdir(parents=True, exist_ok=True)
    icon_path.write_bytes(b"icns")

    commands: list[list[str]] = []
    device_identifier = "/dev/disk9"

    def fake_run(command, check=True, stdout=None, stderr=None):
        commands.append(command)
        if command[:3] == ["hdiutil", "attach", "-plist"]:
            return SimpleNamespace(
                returncode=0,
                stdout=_attach_output(device_identifier, temp_dir / "mount"),
                stderr=b"",
            )
        if command[:2] == ["DeRez", "-only"] and stdout is not None:
            stdout.write(b"icns-resource")
        if command[:2] == ["hdiutil", "convert"]:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"dmg")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(build_macos_dmg.subprocess, "run", fake_run)
    monkeypatch.setattr(build_macos_dmg.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(
        build_macos_dmg.tempfile,
        "TemporaryDirectory",
        lambda: _TemporaryDirectoryStub(temp_dir),
    )

    dmg_path = build_macos_dmg.build_dmg(
        app_path=app_path,
        output_path=output_path,
        icon_path=icon_path,
        volume_name=build_macos_dmg.DEFAULT_VOLUME_NAME,
    )

    staging_root = temp_dir / "staging"
    mountpoint = temp_dir / "mount"
    rw_dmg_path = temp_dir / "OpenAnonymizer-readwrite.dmg"
    volume_icon_path = mountpoint / ".VolumeIcon.icns"
    icon_resource_path = temp_dir / "dmg-icon.rsrc"

    assert dmg_path == output_path
    assert (staging_root / "Applications").is_symlink()
    assert (staging_root / "Applications").resolve() == Path("/Applications")
    assert output_path.exists()
    assert commands == [
        ["ditto", str(app_path), str(staging_root / app_path.name)],
        [
            "hdiutil",
            "create",
            "-ov",
            "-fs",
            "HFS+",
            "-format",
            "UDRW",
            "-volname",
            build_macos_dmg.DEFAULT_VOLUME_NAME,
            "-srcfolder",
            str(staging_root),
            str(rw_dmg_path),
        ],
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
        ["ditto", str(icon_path), str(volume_icon_path)],
        ["SetFile", "-a", "C", str(mountpoint)],
        ["SetFile", "-a", "V", str(volume_icon_path)],
        ["hdiutil", "detach", device_identifier],
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
        ],
        ["sips", "-i", str(icon_path)],
        ["DeRez", "-only", "icns", str(icon_path)],
        ["Rez", "-append", str(icon_resource_path), "-o", str(output_path)],
        ["SetFile", "-a", "C", str(output_path)],
    ]


def test_build_dmg_skips_custom_icons_when_developer_tools_are_unavailable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    build_macos_dmg = _load_build_macos_dmg_module()
    app_path = tmp_path / "dist" / build_macos_dmg.APP_BUNDLE_NAME
    icon_path = tmp_path / "build" / "OpenAnonymizer.icns"
    output_path = tmp_path / "release" / "open-anonymizer-macos.dmg"
    temp_dir = tmp_path / "temp"

    app_path.mkdir(parents=True)
    icon_path.parent.mkdir(parents=True, exist_ok=True)
    icon_path.write_bytes(b"icns")

    commands: list[list[str]] = []
    device_identifier = "/dev/disk9"

    def fake_run(command, check=True, stdout=None, stderr=None):
        commands.append(command)
        if command[:3] == ["hdiutil", "attach", "-plist"]:
            return SimpleNamespace(
                returncode=0,
                stdout=_attach_output(device_identifier, temp_dir / "mount"),
                stderr=b"",
            )
        if command[:2] == ["hdiutil", "convert"]:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"dmg")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(build_macos_dmg.subprocess, "run", fake_run)
    monkeypatch.setattr(
        build_macos_dmg.shutil,
        "which",
        lambda command: None if command in {"SetFile", "DeRez", "Rez"} else f"/usr/bin/{command}",
    )
    monkeypatch.setattr(
        build_macos_dmg.tempfile,
        "TemporaryDirectory",
        lambda: _TemporaryDirectoryStub(temp_dir),
    )

    dmg_path = build_macos_dmg.build_dmg(
        app_path=app_path,
        output_path=output_path,
        icon_path=icon_path,
        volume_name=build_macos_dmg.DEFAULT_VOLUME_NAME,
    )

    staging_root = temp_dir / "staging"
    mountpoint = temp_dir / "mount"
    rw_dmg_path = temp_dir / "OpenAnonymizer-readwrite.dmg"

    assert dmg_path == output_path
    assert (staging_root / "Applications").is_symlink()
    assert output_path.exists()
    assert commands == [
        ["ditto", str(app_path), str(staging_root / app_path.name)],
        [
            "hdiutil",
            "create",
            "-ov",
            "-fs",
            "HFS+",
            "-format",
            "UDRW",
            "-volname",
            build_macos_dmg.DEFAULT_VOLUME_NAME,
            "-srcfolder",
            str(staging_root),
            str(rw_dmg_path),
        ],
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
        ["hdiutil", "detach", device_identifier],
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
        ],
    ]


def test_detach_image_retries_then_forces_on_resource_busy(monkeypatch, tmp_path: Path) -> None:
    build_macos_dmg = _load_build_macos_dmg_module()
    mountpoint = tmp_path / "mount"
    mountpoint.mkdir()
    device_identifier = "/dev/disk9"

    commands: list[list[str]] = []
    sleep_calls: list[float] = []

    def fake_run(command, check=True, stdout=None, stderr=None):
        commands.append(command)
        if command == ["hdiutil", "detach", device_identifier]:
            raise build_macos_dmg.subprocess.CalledProcessError(16, command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(build_macos_dmg.subprocess, "run", fake_run)
    monkeypatch.setattr(build_macos_dmg.time, "sleep", sleep_calls.append)

    build_macos_dmg._detach_image(
        mountpoint,
        device_identifier=device_identifier,
        retries=2,
        retry_delay_seconds=0.25,
    )

    assert commands == [
        ["hdiutil", "detach", device_identifier],
        ["hdiutil", "detach", device_identifier],
        ["hdiutil", "detach", device_identifier, "-force"],
    ]
    assert sleep_calls == [0.25]


def test_attached_device_identifier_uses_base_disk_for_mounted_partition(tmp_path: Path) -> None:
    build_macos_dmg = _load_build_macos_dmg_module()
    mountpoint = tmp_path / "mount"

    assert (
        build_macos_dmg._attached_device_identifier(
            _attach_output("/dev/disk9", mountpoint),
            mountpoint,
        )
        == "/dev/disk9"
    )
