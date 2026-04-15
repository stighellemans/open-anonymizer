# Windows Distribution

This is the release path for shipping a single `setup.exe` installer instead of asking users to unpack a PyInstaller folder manually.

## Prerequisites

- A Windows machine or GitHub Actions `windows-latest` runner
- Inno Setup installed with the `ISCC.exe` compiler available
- A self-contained OCR runtime staged in `vendor/tesseract_runtime` if OCR should work out of the box

## Build

Stage the OCR runtime first if needed:

```bash
python scripts/stage_tesseract_runtime.py C:\path\to\self-contained\tesseract-runtime
```

Build the Windows app folder:

```bash
python -m pip install -e .[dev]
python scripts/build_desktop.py
```

Compile the installer:

```bash
python scripts/build_windows_installer.py
```

That produces a versioned installer executable in `release/`.

## Installer Behavior

- Installs into `Program Files`
- Creates a Start Menu shortcut
- Optionally creates a desktop shortcut
- Bundles the entire PyInstaller app folder, including OCR runtime files if they were staged before the build
- Launches the app after install unless the installer is run silently

## Signing

- Unsigned installers will usually trigger Windows SmartScreen warnings
- If you later add Authenticode signing, sign both the app executable and the generated installer
