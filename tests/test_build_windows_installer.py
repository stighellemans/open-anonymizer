from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_build_windows_installer_module():
    module_path = REPO_ROOT / "scripts" / "build_windows_installer.py"
    spec = importlib.util.spec_from_file_location("build_windows_installer_test_module", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_installer_script_targets_setup_exe_output(tmp_path: Path) -> None:
    build_windows_installer = _load_build_windows_installer_module()
    dist_dir = tmp_path / "dist" / "OpenAnonymizer"
    output_dir = tmp_path / "release"
    license_file = tmp_path / "LICENSE"
    setup_icon_file = tmp_path / "build" / "OpenAnonymizer.ico"
    license_file.write_text("MIT", encoding="utf-8")
    setup_icon_file.parent.mkdir(parents=True, exist_ok=True)
    setup_icon_file.write_bytes(b"ico")

    script = build_windows_installer.build_installer_script(
        dist_dir=dist_dir,
        output_dir=output_dir,
        output_base_filename=build_windows_installer.default_output_base_filename(),
        license_file=license_file,
        setup_icon_file=setup_icon_file,
    )

    assert "ArchitecturesAllowed=x64compatible" in script
    assert "ArchitecturesInstallIn64BitMode=x64compatible" in script
    assert f"OutputBaseFilename={build_windows_installer.default_output_base_filename()}" in script
    assert f"LicenseFile={license_file.resolve()}" in script
    assert f"SetupIconFile={setup_icon_file.resolve()}" in script
    assert (
        f'Source: "{dist_dir.resolve()}\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs'
        in script
    )
    assert (
        f'Filename: "{{app}}\\{build_windows_installer.APP_EXECUTABLE_NAME}"; Description: "Launch '
        f'{build_windows_installer.APP_NAME}"'
    ) in script


def test_find_inno_setup_compiler_uses_path_lookup(monkeypatch, tmp_path: Path) -> None:
    build_windows_installer = _load_build_windows_installer_module()
    compiler_path = tmp_path / "ISCC.exe"
    compiler_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(build_windows_installer.shutil, "which", lambda command: str(compiler_path))

    assert build_windows_installer.find_inno_setup_compiler() == compiler_path.resolve()
