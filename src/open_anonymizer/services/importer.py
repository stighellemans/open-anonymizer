from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from time import monotonic
from typing import Callable, Iterable, List, NamedTuple, Optional

from pypdf import PdfReader

try:
    import pypdfium2 as pdfium
except Exception:  # pragma: no cover - fallback for incomplete environments
    pdfium = None

from open_anonymizer.models import ImportedDocument, PdfPage
from open_anonymizer.services.ocr_runtime import (
    build_tesseract_subprocess_env,
    find_tessdata_dir,
    find_tesseract_binary,
)

TEXT_FILE_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
HTML_FILE_SUFFIXES = {".html", ".htm"}
LAYOUT_EXTRACTION_KWARGS = {
    "extraction_mode": "layout",
    "layout_mode_space_vertically": False,
}
DEFAULT_OCR_LANGS = "nld+fra+eng"
OCR_RENDER_DPI = 300
OCR_TIMEOUT_SECONDS = 120
OCR_VISIBLE_CONTENT_THRESHOLD = 250
DEFAULT_PDF_PAGE_WIDTH = 612.0
DEFAULT_PDF_PAGE_HEIGHT = 792.0


class DocumentImportError(Exception):
    pass


class UnsupportedPdfError(DocumentImportError):
    pass


class ImportCancelledError(Exception):
    pass


class PdfTextCandidate(NamedTuple):
    engine: str
    text: str


@dataclass(frozen=True)
class PdfImportResult:
    text: str
    pages: list[PdfPage]


CancelCallback = Callable[[], bool]


def read_text_file(path: Path) -> str:
    data = path.read_bytes()
    for encoding in TEXT_FILE_ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DocumentImportError(f"Could not decode text file: {path.name}")


def _ensure_not_cancelled(should_cancel: CancelCallback | None) -> None:
    if should_cancel is not None and should_cancel():
        raise ImportCancelledError()


def _normalize_pdf_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x0c", "\n").replace("\xa0", " ")
    normalized = re.sub(r"[^\S\n]+", " ", normalized)
    normalized = re.sub(r" *\n *", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _text_skeleton(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _whitespace_quality(text: str) -> int:
    word_spaces = len(re.findall(r"(?<=\w) (?=\w)", text))
    word_newlines = len(re.findall(r"(?<=\w)\n(?=\w)", text))
    alpha_tokens = re.findall(r"\b[^\W\d_]+\b", text, flags=re.UNICODE)
    single_letter_tokens = sum(len(token) == 1 for token in alpha_tokens)
    return (word_spaces * 5) - (word_newlines * 4) - single_letter_tokens


def _choose_best_text(candidates: Iterable[PdfTextCandidate]) -> str:
    normalized_candidates = [candidate for candidate in candidates if candidate.text]
    if not normalized_candidates:
        return ""

    best = normalized_candidates[0]
    best_skeleton = _text_skeleton(best.text)
    best_quality = _whitespace_quality(best.text)

    for candidate in normalized_candidates[1:]:
        candidate_skeleton = _text_skeleton(candidate.text)
        if not candidate_skeleton:
            continue
        if candidate_skeleton == best_skeleton:
            candidate_quality = _whitespace_quality(candidate.text)
            if candidate_quality > best_quality:
                best = candidate
                best_quality = candidate_quality
            continue
        if len(candidate_skeleton) > len(best_skeleton):
            best = candidate
            best_skeleton = candidate_skeleton
            best_quality = _whitespace_quality(candidate.text)

    return best.text


def _extract_pdfium_page_text(document: object, page_index: int) -> str:
    if pdfium is None or page_index >= len(document):
        return ""

    page = document[page_index]
    try:
        text_page = page.get_textpage()
        try:
            return _normalize_pdf_text(text_page.get_text_bounded())
        finally:
            text_page.close()
    finally:
        page.close()


def _safe_extract_pdfium_page_text(document: object, page_index: int) -> str:
    try:
        return _extract_pdfium_page_text(document, page_index)
    except Exception:
        # PDFium text extraction can raise raw codec lookup errors in frozen builds.
        # Fall back to pypdf and OCR instead of aborting the whole import.
        return ""


def _extract_pypdf_page_text(page: object, **kwargs: object) -> str:
    try:
        text = page.extract_text(**kwargs) or ""
    except Exception:
        return ""
    return _normalize_pdf_text(text)


def _get_ocr_languages() -> str:
    return os.getenv("OPEN_ANONYMIZER_OCR_LANGS", DEFAULT_OCR_LANGS).strip()


def _get_ocr_timeout_seconds() -> int:
    raw_value = os.getenv("OPEN_ANONYMIZER_OCR_TIMEOUT_SECONDS", str(OCR_TIMEOUT_SECONDS)).strip()
    try:
        return max(1, int(raw_value))
    except ValueError:
        return OCR_TIMEOUT_SECONDS


def _build_ocr_unavailable_error() -> DocumentImportError:
    return DocumentImportError(
        "PDF appears to require OCR, but Tesseract is not installed or not on PATH. "
        "Install Tesseract with Dutch/French language packs to process scanned PDFs."
    )


def _bitmap_has_visible_content(bitmap: object) -> bool:
    return min(bitmap.buffer) < OCR_VISIBLE_CONTENT_THRESHOLD


def _write_bitmap_as_ppm(bitmap: object, output_path: Path) -> None:
    header = f"P6\n{bitmap.width} {bitmap.height}\n255\n".encode("ascii")
    with output_path.open("wb") as handle:
        handle.write(header)
        handle.write(memoryview(bitmap.buffer))


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return

    process.terminate()
    try:
        process.wait(timeout=0.25)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=0.25)


def _run_tesseract_ocr(
    image_path: Path,
    *,
    should_cancel: CancelCallback | None = None,
) -> str:
    tesseract_binary = find_tesseract_binary()
    if tesseract_binary is None:
        raise _build_ocr_unavailable_error()

    command = [str(tesseract_binary), str(image_path), "stdout"]
    tessdata_dir = find_tessdata_dir(tesseract_binary)
    if tessdata_dir is not None:
        command.extend(["--tessdata-dir", str(tessdata_dir)])
    languages = _get_ocr_languages()
    if languages:
        command.extend(["-l", languages])
    command.extend(["--psm", "3"])

    process: subprocess.Popen[str] | None = None
    deadline = monotonic() + _get_ocr_timeout_seconds()

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=build_tesseract_subprocess_env(tesseract_binary),
        )

        while True:
            _ensure_not_cancelled(should_cancel)
            remaining_seconds = deadline - monotonic()
            if remaining_seconds <= 0:
                _terminate_process(process)
                process.communicate()
                raise DocumentImportError(
                    "OCR timed out while processing the scanned PDF."
                )

            try:
                stdout, stderr = process.communicate(
                    timeout=min(0.1, remaining_seconds)
                )
                break
            except subprocess.TimeoutExpired:
                continue
    except OSError as exc:
        raise DocumentImportError("Could not start Tesseract for OCR processing.") from exc
    except ImportCancelledError:
        if process is not None:
            _terminate_process(process)
            process.communicate()
        raise

    if process is None:
        raise DocumentImportError("Could not start Tesseract for OCR processing.")

    if process.returncode != 0:
        stderr = (stderr or "").strip()
        detail = stderr.splitlines()[0] if stderr else "unknown OCR error"
        raise DocumentImportError(f"OCR failed while processing the scanned PDF: {detail}")

    return _normalize_pdf_text(stdout)


def _extract_ocr_text_from_pdfium_page(
    document: object,
    page_index: int,
    *,
    should_cancel: CancelCallback | None = None,
) -> str:
    if pdfium is None or page_index >= len(document):
        return ""

    _ensure_not_cancelled(should_cancel)
    page = document[page_index]
    try:
        bitmap = page.render(
            scale=OCR_RENDER_DPI / 72,
            bitmap_maker=partial(pdfium.PdfBitmap.new_native),
        )
        try:
            _ensure_not_cancelled(should_cancel)
            if not _bitmap_has_visible_content(bitmap):
                return ""

            with tempfile.TemporaryDirectory(prefix="open-anonymizer-ocr-") as temp_dir:
                _ensure_not_cancelled(should_cancel)
                image_path = Path(temp_dir) / f"page-{page_index + 1}.ppm"
                _write_bitmap_as_ppm(bitmap, image_path)
                if should_cancel is None:
                    return _run_tesseract_ocr(image_path)
                return _run_tesseract_ocr(
                    image_path,
                    should_cancel=should_cancel,
                )
        finally:
            bitmap.close()
    finally:
        page.close()


def _extract_pdf_page_texts(
    path: Path,
    *,
    should_cancel: CancelCallback | None = None,
) -> List[str]:
    pypdf_reader: Optional[PdfReader] = None
    pdfium_document = None

    try:
        pypdf_reader = PdfReader(str(path))
    except Exception:
        pypdf_reader = None

    if pdfium is not None:
        try:
            pdfium_document = pdfium.PdfDocument(str(path))
        except Exception:
            pdfium_document = None

    if pypdf_reader is None and pdfium_document is None:
        raise DocumentImportError(f"Could not open PDF: {path.name}")

    page_count = max(
        len(pypdf_reader.pages) if pypdf_reader is not None else 0,
        len(pdfium_document) if pdfium_document is not None else 0,
    )

    page_texts: List[str] = []
    try:
        for page_index in range(page_count):
            _ensure_not_cancelled(should_cancel)
            candidates: List[PdfTextCandidate] = []
            if pdfium_document is not None:
                pdfium_text = _safe_extract_pdfium_page_text(pdfium_document, page_index)
                if pdfium_text:
                    candidates.append(PdfTextCandidate("pdfium", pdfium_text))

            if pypdf_reader is not None and page_index < len(pypdf_reader.pages):
                page = pypdf_reader.pages[page_index]
                layout_text = _extract_pypdf_page_text(page, **LAYOUT_EXTRACTION_KWARGS)
                if layout_text:
                    candidates.append(PdfTextCandidate("pypdf-layout", layout_text))

                plain_text = _extract_pypdf_page_text(page)
                if plain_text:
                    candidates.append(PdfTextCandidate("pypdf-plain", plain_text))

            best_text = _choose_best_text(candidates)
            if not best_text and pdfium_document is not None:
                if should_cancel is None:
                    best_text = _extract_ocr_text_from_pdfium_page(
                        pdfium_document,
                        page_index,
                    )
                else:
                    best_text = _extract_ocr_text_from_pdfium_page(
                        pdfium_document,
                        page_index,
                        should_cancel=should_cancel,
                    )
            if best_text:
                page_texts.append(best_text)
    finally:
        if pdfium_document is not None:
            pdfium_document.close()

    return page_texts


def _pdf_page_size(reader: PdfReader | None, page_index: int) -> tuple[float, float]:
    if reader is None or page_index >= len(reader.pages):
        return DEFAULT_PDF_PAGE_WIDTH, DEFAULT_PDF_PAGE_HEIGHT

    page = reader.pages[page_index]
    try:
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
    except Exception:
        return DEFAULT_PDF_PAGE_WIDTH, DEFAULT_PDF_PAGE_HEIGHT

    if width <= 0 or height <= 0:
        return DEFAULT_PDF_PAGE_WIDTH, DEFAULT_PDF_PAGE_HEIGHT

    return width, height


def extract_pdf_content(
    path: Path,
    *,
    should_cancel: CancelCallback | None = None,
) -> PdfImportResult:
    pypdf_reader: Optional[PdfReader] = None
    pdfium_document = None

    try:
        pypdf_reader = PdfReader(str(path))
    except Exception:
        pypdf_reader = None

    if pdfium is not None:
        try:
            pdfium_document = pdfium.PdfDocument(str(path))
        except Exception:
            pdfium_document = None

    if pypdf_reader is None and pdfium_document is None:
        raise DocumentImportError(f"Could not open PDF: {path.name}")

    page_count = max(
        len(pypdf_reader.pages) if pypdf_reader is not None else 0,
        len(pdfium_document) if pdfium_document is not None else 0,
    )

    pages: list[PdfPage] = []
    try:
        for page_index in range(page_count):
            _ensure_not_cancelled(should_cancel)
            candidates: List[PdfTextCandidate] = []
            if pdfium_document is not None:
                pdfium_text = _safe_extract_pdfium_page_text(pdfium_document, page_index)
                if pdfium_text:
                    candidates.append(PdfTextCandidate("pdfium", pdfium_text))

            if pypdf_reader is not None and page_index < len(pypdf_reader.pages):
                page = pypdf_reader.pages[page_index]
                layout_text = _extract_pypdf_page_text(page, **LAYOUT_EXTRACTION_KWARGS)
                if layout_text:
                    candidates.append(PdfTextCandidate("pypdf-layout", layout_text))

                plain_text = _extract_pypdf_page_text(page)
                if plain_text:
                    candidates.append(PdfTextCandidate("pypdf-plain", plain_text))

            best_text = _choose_best_text(candidates)
            if not best_text and pdfium_document is not None:
                if should_cancel is None:
                    best_text = _extract_ocr_text_from_pdfium_page(
                        pdfium_document,
                        page_index,
                    )
                else:
                    best_text = _extract_ocr_text_from_pdfium_page(
                        pdfium_document,
                        page_index,
                        should_cancel=should_cancel,
                    )

            width, height = _pdf_page_size(pypdf_reader, page_index)
            pages.append(PdfPage(text=best_text, width=width, height=height))
    finally:
        if pdfium_document is not None:
            pdfium_document.close()

    text = "\n\n".join(page.text for page in pages if page.text.strip()).strip()
    if not text:
        raise UnsupportedPdfError(
            "PDF does not contain readable text. Native extraction and OCR did not recover any text."
        )

    return PdfImportResult(text=text, pages=pages)


def extract_pdf_text(
    path: Path,
    *,
    should_cancel: CancelCallback | None = None,
) -> str:
    return extract_pdf_content(path, should_cancel=should_cancel).text


def import_file(
    path: Path,
    document_id: str,
    *,
    should_cancel: CancelCallback | None = None,
) -> ImportedDocument:
    _ensure_not_cancelled(should_cancel)
    suffix = path.suffix.lower()
    pdf_pages: list[PdfPage] = []
    if suffix == ".txt":
        raw_text = read_text_file(path)
        source_kind = "text_file"
    elif suffix in HTML_FILE_SUFFIXES:
        raw_text = read_text_file(path)
        source_kind = "html"
    elif suffix == ".pdf":
        pdf_import = extract_pdf_content(path, should_cancel=should_cancel)
        raw_text = pdf_import.text
        pdf_pages = pdf_import.pages
        source_kind = "pdf"
    else:
        raise DocumentImportError("Unsupported file type. Only .txt, .html, .htm, and .pdf are supported.")

    return ImportedDocument(
        id=document_id,
        source_kind=source_kind,
        display_name=path.name,
        path=path,
        raw_text=raw_text,
        pdf_pages=pdf_pages,
    )
