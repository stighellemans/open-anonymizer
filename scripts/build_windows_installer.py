from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from textwrap import dedent


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from open_anonymizer import __version__


APP_ID = "com.openanonymizer.app"
APP_NAME = "Open Anonymizer"
APP_PUBLISHER = "Stig Hellemans"
APP_EXECUTABLE_NAME = "OpenAnonymizer.exe"
DEFAULT_INSTALLER_ICON_PATH = REPO_ROOT / "build" / "OpenAnonymizer.ico"


def _escape_inno_value(value: str) -> str:
    return value.replace('"', '""')


def _inno_path(path: Path) -> str:
    return _escape_inno_value(str(path.resolve()))


def default_output_base_filename() -> str:
    return f"OpenAnonymizer-{__version__}-Setup"


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a Windows setup.exe installer with Inno Setup.")
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=REPO_ROOT / "dist" / "OpenAnonymizer",
        help="Directory containing the PyInstaller onedir Windows app.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "release",
        help="Directory where the compiled installer will be written.",
    )
    parser.add_argument(
        "--output-base-filename",
        default=default_output_base_filename(),
        help="Base filename for the compiled installer, without the .exe extension.",
    )
    parser.add_argument(
        "--script-path",
        type=Path,
        default=REPO_ROOT / "build" / "OpenAnonymizerSetup.iss",
        help="Path where the generated Inno Setup script should be written.",
    )
    parser.add_argument(
        "--compiler",
        type=Path,
        help="Optional path to ISCC.exe. If omitted, common install locations and PATH are searched.",
    )
    parser.add_argument(
        "--setup-icon-file",
        type=Path,
        default=DEFAULT_INSTALLER_ICON_PATH,
        help="Optional .ico file to embed into the generated setup.exe.",
    )
    return parser


def build_installer_script(
    *,
    dist_dir: Path,
    output_dir: Path,
    output_base_filename: str,
    license_file: Path,
    setup_icon_file: Path | None = None,
) -> str:
    setup_icon_line = (
        f"SetupIconFile={_inno_path(setup_icon_file)}"
        if setup_icon_file is not None
        else ""
    )
    return dedent(
        f"""
        [Setup]
        AppId={APP_ID}
        AppName={APP_NAME}
        AppVersion={_escape_inno_value(__version__)}
        AppPublisher={APP_PUBLISHER}
        DefaultDirName={{pf}}\\{APP_NAME}
        DefaultGroupName={APP_NAME}
        DisableProgramGroupPage=yes
        LicenseFile={_inno_path(license_file)}
        UninstallDisplayIcon={{app}}\\{APP_EXECUTABLE_NAME}
        {setup_icon_line}
        Compression=lzma
        SolidCompression=yes
        WizardStyle=modern
        PrivilegesRequired=admin
        ArchitecturesAllowed=x64compatible
        ArchitecturesInstallIn64BitMode=x64compatible
        OutputDir={_inno_path(output_dir)}
        OutputBaseFilename={_escape_inno_value(output_base_filename)}

        [Languages]
        Name: "english"; MessagesFile: "compiler:Default.isl"

        [Tasks]
        Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

        [Files]
        Source: "{_inno_path(dist_dir)}\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs

        [Icons]
        Name: "{{group}}\\{APP_NAME}"; Filename: "{{app}}\\{APP_EXECUTABLE_NAME}"; WorkingDir: "{{app}}"
        Name: "{{autodesktop}}\\{APP_NAME}"; Filename: "{{app}}\\{APP_EXECUTABLE_NAME}"; Tasks: desktopicon; WorkingDir: "{{app}}"

        [Run]
        Filename: "{{app}}\\{APP_EXECUTABLE_NAME}"; Description: "Launch {APP_NAME}"; Flags: nowait postinstall skipifsilent
        """
    ).strip() + "\n"


def find_inno_setup_compiler(explicit_path: Path | None = None) -> Path:
    if explicit_path is not None:
        candidate = explicit_path.expanduser().resolve()
        if candidate.exists():
            return candidate
        raise FileNotFoundError(f"Inno Setup compiler was not found: {candidate}")

    for command_name in ("ISCC.exe", "iscc"):
        resolved = shutil.which(command_name)
        if resolved:
            return Path(resolved).resolve()

    candidates: list[Path] = []
    for env_name in ("ProgramFiles(x86)", "ProgramFiles"):
        base_dir = os.environ.get(env_name, "").strip()
        if not base_dir:
            continue
        candidates.extend(
            [
                Path(base_dir) / "Inno Setup 6" / "ISCC.exe",
                Path(base_dir) / "Inno Setup 7" / "ISCC.exe",
            ]
        )

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()

    raise FileNotFoundError(
        "Could not find ISCC.exe. Install Inno Setup or pass --compiler with the compiler path."
    )


def main() -> int:
    args = build_argument_parser().parse_args()

    dist_dir = args.dist_dir.resolve()
    output_dir = args.output_dir.resolve()
    script_path = args.script_path.resolve()
    license_file = (REPO_ROOT / "LICENSE").resolve()
    setup_icon_file = args.setup_icon_file.resolve() if args.setup_icon_file else None

    if not dist_dir.exists():
        raise FileNotFoundError(f"Distribution directory does not exist: {dist_dir}")
    if not (dist_dir / APP_EXECUTABLE_NAME).exists():
        raise FileNotFoundError(
            f"Expected Windows application executable was not found in the distribution directory: "
            f"{dist_dir / APP_EXECUTABLE_NAME}"
        )
    if not license_file.exists():
        raise FileNotFoundError(f"License file does not exist: {license_file}")

    output_dir.mkdir(parents=True, exist_ok=True)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(
        build_installer_script(
            dist_dir=dist_dir,
            output_dir=output_dir,
            output_base_filename=args.output_base_filename,
            license_file=license_file,
            setup_icon_file=setup_icon_file if setup_icon_file and setup_icon_file.exists() else None,
        ),
        encoding="utf-8",
    )

    compiler_path = find_inno_setup_compiler(args.compiler)
    print("Compiling Windows installer with:", compiler_path)
    subprocess.run([str(compiler_path), str(script_path)], check=True)
    print(output_dir / f"{args.output_base_filename}.exe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
