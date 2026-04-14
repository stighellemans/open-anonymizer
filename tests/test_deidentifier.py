from datetime import date
from pathlib import Path

from open_anonymizer.models import ImportedDocument, PatientContext
from open_anonymizer.services.deidentifier import (
    apply_guaranteed_cleanup,
    build_birthdate_variants,
    deidentify_document,
)


def test_guaranteed_cleanup_replaces_name_literals_case_insensitively() -> None:
    context = PatientContext(first_name="Jean", last_name="Dubois")

    cleaned = apply_guaranteed_cleanup("JEAN Dubois et jean", context)

    assert "Jean" not in cleaned
    assert "Dubois" not in cleaned
    assert cleaned.count("<PATIENT>") >= 2


def test_birthdate_variants_cover_numeric_dutch_and_french_formats() -> None:
    variants = build_birthdate_variants(date(1990, 2, 1))

    assert "01/02/1990" in variants
    assert "1 februari 1990" in variants
    assert "1 février 1990" in variants


def test_deidentify_document_removes_explicit_patient_fields() -> None:
    document = ImportedDocument(
        id="doc-1",
        source_kind="paste",
        display_name="Pasted Text 001",
        raw_text=(
            "Jean Dubois est vu le 1 février 1990. "
            "Le patient Jean Dubois vit à Namur."
        ),
    )
    context = PatientContext(first_name="Jean", last_name="Dubois", birthdate=date(1990, 2, 1))

    result = deidentify_document(document, context)

    assert "<PATIENT>" in result.output_text
    assert "<DATE>" in result.output_text or "<DATE-1>" in result.output_text
    assert "Jean" not in result.output_text
    assert "Dubois" not in result.output_text
    assert "1 février 1990" not in result.output_text


def test_deidentify_document_preserves_html_markup(monkeypatch) -> None:
    monkeypatch.setattr(
        "open_anonymizer.services.deidentifier.deduce.annotate_text",
        lambda text, **kwargs: text,
    )
    monkeypatch.setattr(
        "open_anonymizer.services.deidentifier.deduce.deidentify_annotations",
        lambda annotated: annotated,
    )

    document = ImportedDocument(
        id="doc-2",
        source_kind="text_file",
        display_name="report.html",
        path=Path("report.html"),
        raw_text=(
            '<p title="Jean Dubois">Bonjour <strong>Jean</strong> Dubois</p>'
            '<script>const patientName = "Jean Dubois";</script>'
        ),
    )
    context = PatientContext(first_name="Jean", last_name="Dubois")

    result = deidentify_document(document, context)

    assert result.output_text == (
        '<p title="&lt;PATIENT&gt;">Bonjour <strong>&lt;PATIENT&gt;</strong> &lt;PATIENT&gt;</p>'
        '<script>const patientName = "Jean Dubois";</script>'
    )
    assert result.warnings == [
        "Guaranteed cleanup replaced literals that remained after backend processing."
    ]
