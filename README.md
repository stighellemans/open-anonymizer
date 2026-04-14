# Open Anonymizer

Open Anonymizer is a local desktop application for de-identifying Dutch and French medical text. It uses the [`deduced`](https://github.com/aenglebert/deduced) backend and wraps it in a minimal drag-and-drop interface for pasted text, `.txt` files, `.html` files, and text-based `.pdf` files.

## Features

- Fully local processing. No network calls are required for de-identification.
- Drag and drop multiple `.txt`, `.html`, and `.pdf` files into the window.
- Paste raw text directly into the app and add it as a document.
- Automatically de-identify newly added documents and re-run processing when patient identifiers change.
- Provide patient first name, last name, and birthdate to guarantee those literals are removed.
- Review each processed document, copy the output, and export all successful results as a ZIP archive.
- Keep imported HTML documents as HTML when exporting de-identified output back out.
- Track skipped documents with an `export-report.txt` summary inside the ZIP.

## Tech Stack

- Python 3.9
- PySide6
- `deduce` from the Belgian `deduced` repository
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

## Packaging Notes

- v1 targets macOS and Windows.
- PDF extraction prefers PDFium and falls back to `pypdf` heuristics for better spacing recovery.
- Scanned pages can use OCR fallback through bundled `tesseract_runtime` files inside the app, or through `tesseract` on `PATH` during development.
- To ship a click-and-play build with OCR included, stage a self-contained runtime in `vendor/tesseract_runtime/` before running `python scripts/build_desktop.py`. Use `python scripts/stage_tesseract_runtime.py /path/to/runtime` to copy a prepared runtime into place.
- The runtime should include `eng`, `fra`, `nld`, and `osd` traineddata files so Dutch/French OCR works automatically without a language choice prompt.
- Release assets are unsigned unless signing credentials are added later.

The parser test matrix is documented in [docs/pdf-test-matrix.md](docs/pdf-test-matrix.md).
The macOS signing/notarization flow is documented in [docs/macos-distribution.md](docs/macos-distribution.md).

## License

The application code in this repository is MIT licensed. The bundled `deduce` dependency remains subject to its own LGPLv3 license. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
