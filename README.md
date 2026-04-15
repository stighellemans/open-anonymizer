# Open Anonymizer

Open Anonymizer is a local desktop application for de-identifying Dutch and French medical text. It uses the [`belgian-deduce`](https://github.com/stighellemans/belgian-deduce) backend and wraps it in a drag-and-drop interface for pasted text, `.txt` files, `.html` files, and text-based `.pdf` files.

## Features

- Fully local processing. No network calls are required for de-identification.
- Drag and drop multiple `.txt`, `.html`, and `.pdf` files into the window.
- Paste raw text directly into the app and add it as a document.
- Customize anonymization from a popup with patient first name, last name, birthdate, other people and addresses to always hide, and per-category recognition toggles.
- Switch between bracketed placeholders and smart pseudonyms for more readable review output.
- Automatically de-identify newly added documents and re-run processing when anonymization settings change.
- Persist anonymization settings across launches.
- Review each processed document, copy the output, and export all successful results as a ZIP archive.
- Hover pseudonyms or placeholders in the review pane to see the original matched text.
- Keep imported HTML documents as HTML when exporting de-identified output back out.
- Track skipped documents with an `export-report.txt` summary inside the ZIP.
- Use backend-native English placeholders such as `[PATIENT]`, `[DATE-1]`, and `[PERSON-1]`.

## Tech Stack

- Python 3.9
- PySide6
- `belgian-deduce` pinned to commit `bbe733a33325688a94ff65798c192a153d424cf9`
- `pypdfium2` with `pypdf` fallbacks for text-based PDF extraction
- PyInstaller for desktop packaging

## Local Development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .[dev]
python main.py
```

During local development, OCR fallback uses `tesseract` from `PATH` unless a bundled runtime is present.

## Tests

```bash
pytest
```

Run the PDF starter corpus harness:

```bash
python scripts/run_pdf_corpus.py
```

## Build Desktop Artifacts

```bash
python -m pip install -e .[dev]
python scripts/build_desktop.py
```

The build output is written to `dist/`.

On Windows, turn the PyInstaller app folder into a single installer executable:

```bash
python scripts/build_windows_installer.py
```

That writes a `setup.exe` style installer to `release/`.

Assemble a website upload bundle with the installers, checksums, a manifest, and a simple download page:

```bash
python scripts/prepare_web_release.py
```

That writes a self-contained folder to `release/web-ready/`.

## Packaging Notes

- v1 targets macOS and Windows.
- Public release assets are a zipped `.app` bundle on macOS and a `setup.exe` installer on Windows.
- PDF extraction prefers PDFium and falls back to `pypdf` heuristics for better spacing recovery.
- Scanned pages can use OCR fallback through bundled `tesseract_runtime` files inside the app, or through `tesseract` on `PATH` during development.
- To ship a click-and-play build with OCR included, stage a self-contained runtime in `vendor/tesseract_runtime/` before running `python scripts/build_desktop.py`. Use `python scripts/stage_tesseract_runtime.py /path/to/runtime` to copy a prepared runtime into place.
- The runtime should include `eng`, `fra`, `nld`, and `osd` traineddata files so Dutch/French OCR works automatically without a language choice prompt.
- Release assets are unsigned unless signing credentials are added later.

The parser test matrix is documented in [docs/pdf-test-matrix.md](docs/pdf-test-matrix.md).
The macOS signing/notarization flow is documented in [docs/macos-distribution.md](docs/macos-distribution.md).
The Windows installer flow is documented in [docs/windows-distribution.md](docs/windows-distribution.md).

## License

The application code in this repository is MIT licensed. The bundled `belgian-deduce` dependency remains subject to its own LGPLv3 license. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
