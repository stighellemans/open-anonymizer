# PDF Test Matrix

This repo now treats PDF parsing quality as a matrix, not a single happy-path test.

## Core matrix

| Bucket | Goal | Covered by |
| --- | --- | --- |
| Native text | Basic text-layer extraction works | `native-simple` starter corpus case |
| Word spacing | Positioned words keep spaces | `positioned-words-spacing` starter corpus case |
| TJ spacing | `TJ` arrays normalize to readable spacing | `tj-array-spacing` starter corpus case |
| Multi-page | Page boundaries are preserved | `multi-page-native` starter corpus case |
| Blank pages | Blank pages do not poison otherwise valid documents | `text-plus-blank-page` starter corpus case |
| Empty PDFs | Fully blank PDFs still error clearly | `blank-document` starter corpus case |
| OCR fallback | Visible non-text pages trigger OCR when needed | `tests/test_importer.py` mocked OCR tests |
| OCR runtime missing | Scanned PDFs fail with a clear installation message if Tesseract is unavailable | `tests/test_importer.py` |

## Starter harness

Run the checked-in starter corpus:

```bash
python scripts/run_pdf_corpus.py
```

Write a JSON report:

```bash
python scripts/run_pdf_corpus.py --json-out /tmp/open-anonymizer-pdf-corpus.json
```

The default manifest lives at `tests/corpus/starter_manifest.json`.

## How to grow it

Add new cases in one of two ways:

1. Add generated fixture cases to `tests/corpus/starter_manifest.json` for deterministic parser regressions.
2. Add external real-world PDFs via `"path"` entries in a separate manifest and point the harness at that manifest.

Recommended next public corpora to sample:

- `pdf.js` regression PDFs: https://github.com/mozilla/pdf.js/tree/master/test/pdfs
- `pdfium` regression corpus: https://pdfium.googlesource.com/pdfium_tests/
- PDF Association corpus index: https://github.com/pdf-association/pdf-corpora
- SafeDocs / GovDocs1 wild PDFs: https://digitalcorpora.org/corpora/file-corpora/
- DocLayNet / OmniDocBench for broader document diversity:
  https://research.ibm.com/publications/doclaynet-a-large-human-annotated-dataset-for-document-layout-segmentation
  https://github.com/opendatalab/OmniDocBench

## Practical rule

Use the starter corpus for deterministic regressions in CI.
Use sampled public corpora offline to evaluate parser changes before upgrading extraction libraries or OCR behavior.
