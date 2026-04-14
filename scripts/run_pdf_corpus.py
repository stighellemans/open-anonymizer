from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from pypdf import PdfWriter

from open_anonymizer.services.importer import DocumentImportError, extract_pdf_text
from tests.helpers import (
    escape_pdf_text,
    write_pdf_pages,
    write_pdf_stream,
    write_positioned_words_pdf,
    write_text_pdf,
    write_visible_graphics_pdf,
)


def normalize_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x0c", "\n").replace("\xa0", " ")
    normalized = re.sub(r"[^\S\n]+", " ", normalized)
    normalized = re.sub(r" *\n *", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def skeleton(text: str) -> str:
    return "".join(text.split())


def levenshtein_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            insertion = current[right_index - 1] + 1
            deletion = previous[right_index] + 1
            substitution = previous[right_index - 1] + (left_char != right_char)
            current.append(min(insertion, deletion, substitution))
        previous = current
    return previous[-1]


def similarity_score(left: str, right: str) -> float:
    denominator = max(len(left), len(right), 1)
    return 1 - (levenshtein_distance(left, right) / denominator)


def tesseract_available() -> bool:
    configured_binary = os.getenv("OPEN_ANONYMIZER_TESSERACT_BIN", "tesseract").strip()
    return bool(configured_binary and shutil.which(configured_binary))


def build_fixture(case: dict, output_dir: Path, manifest_dir: Path) -> Path:
    if "path" in case:
        return (manifest_dir / case["path"]).resolve()

    fixture = case["fixture"]
    fixture_kind = fixture["kind"]
    output_path = output_dir / f"{case['id']}.pdf"

    if fixture_kind == "text":
        write_text_pdf(output_path, fixture["text"])
    elif fixture_kind == "text_pages":
        streams = [
            f"BT\n/F1 18 Tf\n72 720 Td\n({escape_pdf_text(page_text)}) Tj\nET\n".encode("latin-1")
            for page_text in fixture["pages"]
        ]
        write_pdf_pages(output_path, streams)
    elif fixture_kind == "positioned_words":
        write_positioned_words_pdf(output_path, [tuple(word) for word in fixture["words"]])
    elif fixture_kind == "raw_stream":
        write_pdf_stream(output_path, fixture["stream"].encode("latin-1"))
    elif fixture_kind == "raw_pages":
        write_pdf_pages(output_path, [stream.encode("latin-1") for stream in fixture["streams"]])
    elif fixture_kind == "blank":
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        with output_path.open("wb") as handle:
            writer.write(handle)
    elif fixture_kind == "visible_graphics":
        write_visible_graphics_pdf(output_path)
    else:
        raise ValueError(f"Unsupported fixture kind: {fixture_kind}")

    return output_path


def evaluate_case(case: dict, pdf_path: Path) -> dict:
    try:
        actual_text = extract_pdf_text(pdf_path)
    except DocumentImportError as exc:
        expected_error = case.get("expected_error_contains")
        if expected_error and expected_error in str(exc):
            return {
                "id": case["id"],
                "status": "pass",
                "mode": "expected_error",
                "detail": str(exc),
            }
        return {
            "id": case["id"],
            "status": "fail",
            "mode": "unexpected_error",
            "detail": str(exc),
        }

    expected_text = case.get("expected_text")
    if expected_text is None:
        return {
            "id": case["id"],
            "status": "fail",
            "mode": "missing_expectation",
            "detail": "Case succeeded but no expected_text was provided.",
        }

    normalized_actual = normalize_text(actual_text)
    normalized_expected = normalize_text(expected_text)
    exact_match = normalized_actual == normalized_expected
    skeleton_match = skeleton(normalized_actual) == skeleton(normalized_expected)
    score = similarity_score(normalized_actual, normalized_expected)
    return {
        "id": case["id"],
        "status": "pass" if exact_match else "fail",
        "mode": "text_compare",
        "exact_match": exact_match,
        "skeleton_match": skeleton_match,
        "score": round(score, 4),
        "actual_text": normalized_actual,
        "expected_text": normalized_expected,
    }


def load_manifest(manifest_path: Path) -> dict:
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the PDF extraction corpus harness.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=REPO_ROOT / "tests" / "corpus" / "starter_manifest.json",
        help="Path to a corpus manifest JSON file.",
    )
    parser.add_argument(
        "--generated-dir",
        type=Path,
        help="Optional directory for generated fixture PDFs. Defaults to a temporary directory.",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        help="Optional path for a JSON summary report.",
    )
    args = parser.parse_args()

    manifest_path = args.manifest.resolve()
    manifest = load_manifest(manifest_path)
    cases = manifest.get("cases", [])
    manifest_dir = manifest_path.parent
    ocr_available = tesseract_available()

    if args.generated_dir is not None:
        generated_dir = args.generated_dir.resolve()
        generated_dir.mkdir(parents=True, exist_ok=True)
        temp_context = None
    else:
        temp_context = tempfile.TemporaryDirectory(prefix="open-anonymizer-corpus-")
        generated_dir = Path(temp_context.name)

    results: list[dict] = []
    try:
        for case in cases:
            if case.get("requires_ocr") and not ocr_available:
                results.append(
                    {
                        "id": case["id"],
                        "status": "skip",
                        "mode": "missing_ocr_runtime",
                        "detail": "Tesseract is not available on PATH.",
                    }
                )
                continue

            pdf_path = build_fixture(case, generated_dir, manifest_dir)
            result = evaluate_case(case, pdf_path)
            result["category"] = case.get("category", "")
            result["description"] = case.get("description", "")
            result["pdf_path"] = str(pdf_path)
            results.append(result)
    finally:
        if temp_context is not None:
            temp_context.cleanup()

    passed = sum(result["status"] == "pass" for result in results)
    failed = sum(result["status"] == "fail" for result in results)
    skipped = sum(result["status"] == "skip" for result in results)

    for result in results:
        if result["status"] == "pass" and result["mode"] == "text_compare":
            print(
                f"PASS  {result['id']:<24} exact={result['exact_match']} "
                f"skeleton={result['skeleton_match']} score={result['score']:.4f}"
            )
        elif result["status"] == "pass":
            print(f"PASS  {result['id']:<24} {result['mode']}")
        elif result["status"] == "skip":
            print(f"SKIP  {result['id']:<24} {result['detail']}")
        else:
            print(f"FAIL  {result['id']:<24} {result['mode']}: {result['detail']}")

    print(f"\nSummary: {passed} passed, {failed} failed, {skipped} skipped")

    if args.json_out is not None:
        args.json_out.write_text(
            json.dumps(
                {
                    "manifest": str(manifest_path),
                    "passed": passed,
                    "failed": failed,
                    "skipped": skipped,
                    "results": results,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
