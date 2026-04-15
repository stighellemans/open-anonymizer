from datetime import date
from pathlib import Path
import re

from open_anonymizer.models import AnonymizationSettings, ImportedDocument, PdfPage
from open_anonymizer.services.deduce_backend import (
    BackendAnalysisResult,
    BackendAnnotation,
    BackendDeidentifyResult,
)
from open_anonymizer.services.deidentifier import (
    build_birthdate_variants,
    deidentify_document,
)
from open_anonymizer.services.smart_pseudonymizer import (
    SMART_DATE_SHIFT_WARNING,
    _parse_date_literal,
)


def test_birthdate_variants_cover_numeric_dutch_and_french_formats() -> None:
    variants = build_birthdate_variants(date(1990, 2, 1))

    assert "01/02/1990" in variants
    assert "1 februari 1990" in variants
    assert "1 février 1990" in variants


def test_deidentify_document_uses_backend_native_placeholders() -> None:
    document = ImportedDocument(
        id="doc-1",
        source_kind="paste",
        display_name="Pasted Text 001",
        raw_text=(
            "Jean Dubois est vu le 1 février 1990. "
            "Le patient Jean Dubois vit à Namur."
        ),
    )
    anonymization_settings = AnonymizationSettings(
        first_name="Jean",
        last_name="Dubois",
        birthdate=date(1990, 2, 1),
    )

    result = deidentify_document(document, anonymization_settings)

    assert "[PATIENT]" in result.output_text
    assert "[DATE-1]" in result.output_text
    assert "Jean Dubois" not in result.output_text
    assert "1 février 1990" not in result.output_text
    assert result.placeholder_references["[PATIENT]"] == ("Jean Dubois",)
    assert result.placeholder_references["[DATE-1]"] == ("1 février 1990",)


def test_deidentify_document_preserves_html_markup(monkeypatch) -> None:
    def fake_deidentify_text(text: str, settings: AnonymizationSettings) -> BackendDeidentifyResult:
        del settings
        return BackendDeidentifyResult(
            deidentified_text=(
                text.replace("Jean Dubois", "[PATIENT]")
                .replace("Jean", "[PATIENT]")
                .replace("Dubois", "[PATIENT]")
            ),
            placeholder_references={"[PATIENT]": ("Jean Dubois", "Jean", "Dubois")},
        )

    monkeypatch.setattr(
        "open_anonymizer.services.deidentifier.backend_deidentify_text_with_references",
        fake_deidentify_text,
    )

    document = ImportedDocument(
        id="doc-2",
        source_kind="html",
        display_name="report.html",
        path=Path("report.html"),
        raw_text=(
            '<p title="Jean Dubois">Bonjour <strong>Jean</strong> Dubois</p>'
            '<script>const patientName = "Jean Dubois";</script>'
        ),
    )

    result = deidentify_document(document, AnonymizationSettings())

    assert result.output_text == (
        '<p title="[PATIENT]">Bonjour <strong>[PATIENT]</strong> [PATIENT]</p>'
        '<script>const patientName = "Jean Dubois";</script>'
    )
    assert result.warnings == []
    assert result.placeholder_references["[PATIENT]"] == (
        "Jean Dubois",
        "Jean",
        "Dubois",
    )


def test_deidentify_document_preserves_pdf_page_boundaries(monkeypatch) -> None:
    def fake_deidentify_text(text: str, settings: AnonymizationSettings) -> BackendDeidentifyResult:
        del settings
        return BackendDeidentifyResult(
            deidentified_text=(
                text.replace("Jean Dubois", "[PATIENT]")
                .replace("12 Rue de Namur", "[LOCATION-1]")
            ),
            placeholder_references={
                "[PATIENT]": ("Jean Dubois",),
                "[LOCATION-1]": ("12 Rue de Namur",),
            },
        )

    monkeypatch.setattr(
        "open_anonymizer.services.deidentifier.backend_deidentify_text_with_references",
        fake_deidentify_text,
    )

    document = ImportedDocument(
        id="doc-3",
        source_kind="pdf",
        display_name="report.pdf",
        path=Path("report.pdf"),
        raw_text="Jean Dubois\n\n12 Rue de Namur",
        pdf_pages=[
            PdfPage(text="Jean Dubois", width=300.0, height=400.0),
            PdfPage(text="12 Rue de Namur", width=320.0, height=420.0),
        ],
    )

    result = deidentify_document(document, AnonymizationSettings())

    assert result.output_text == "[PATIENT]\n\n[LOCATION-1]"
    assert result.pdf_page_texts == ["[PATIENT]", "[LOCATION-1]"]
    assert result.placeholder_references == {
        "[PATIENT]": ("Jean Dubois",),
        "[LOCATION-1]": ("12 Rue de Namur",),
    }


def test_deidentify_document_smart_mode_uses_stable_surrogates(monkeypatch) -> None:
    text = (
        "Jean Dubois est vu le 1 février 1990 à UZ Leuven avec Sophie Martin. "
        "Jean Dubois revient le 8 février 1990."
    )
    first_name = "Jean Dubois"
    first_date = "1 février 1990"
    institution = "UZ Leuven"
    other_person = "Sophie Martin"
    second_name = "Jean Dubois"
    second_date = "8 février 1990"

    def fake_analyze_text(source_text: str, settings: AnonymizationSettings) -> BackendAnalysisResult:
        del settings
        assert source_text == text
        return BackendAnalysisResult(
            text=source_text,
            annotations=(
                BackendAnnotation(
                    text=first_name,
                    tag="patient",
                    start_char=source_text.index(first_name),
                    end_char=source_text.index(first_name) + len(first_name),
                ),
                BackendAnnotation(
                    text=first_date,
                    tag="datum",
                    start_char=source_text.index(first_date),
                    end_char=source_text.index(first_date) + len(first_date),
                ),
                BackendAnnotation(
                    text=institution,
                    tag="hospital",
                    start_char=source_text.index(institution),
                    end_char=source_text.index(institution) + len(institution),
                ),
                BackendAnnotation(
                    text=other_person,
                    tag="person",
                    start_char=source_text.index(other_person),
                    end_char=source_text.index(other_person) + len(other_person),
                ),
                BackendAnnotation(
                    text=second_name,
                    tag="patient",
                    start_char=source_text.rindex(second_name),
                    end_char=source_text.rindex(second_name) + len(second_name),
                ),
                BackendAnnotation(
                    text=second_date,
                    tag="datum",
                    start_char=source_text.index(second_date),
                    end_char=source_text.index(second_date) + len(second_date),
                ),
            ),
            masked_email_replacements={},
        )

    monkeypatch.setattr(
        "open_anonymizer.services.smart_pseudonymizer.analyze_text",
        fake_analyze_text,
    )
    monkeypatch.setattr(
        "open_anonymizer.services.smart_pseudonymizer._local_secret_bytes",
        lambda: b"\x11" * 32,
    )

    document = ImportedDocument(
        id="doc-smart-1",
        source_kind="paste",
        display_name="Pasted Text 001",
        raw_text=text,
    )
    settings = AnonymizationSettings(
        first_name="Jean",
        last_name="Dubois",
        birthdate=date(1990, 2, 1),
        mode="smart_pseudonyms",
    )

    first_result = deidentify_document(document, settings)
    second_result = deidentify_document(document, settings)

    assert first_result.output_text == second_result.output_text
    assert "Jean Dubois" not in first_result.output_text
    assert "UZ Leuven" not in first_result.output_text
    assert "Sophie Martin" not in first_result.output_text
    assert "[PATIENT]" not in first_result.output_text
    assert "[PERSON-" not in first_result.output_text
    assert "[HOSPITAL-" not in first_result.output_text

    match = re.fullmatch(
        r"(?P<patient>.+?) est vu le (?P<date_1>\d{1,2} [A-Za-zÀ-ÿ]+ \d{4}) "
        r"à (?P<hospital>.+?) avec (?P<person>.+?)\. "
        r"(?P=patient) revient le (?P<date_2>\d{1,2} [A-Za-zÀ-ÿ]+ \d{4})\.",
        first_result.output_text,
    )
    assert match is not None
    replacements = match.groupdict()
    assert first_result.placeholder_references[replacements["patient"]] == ("Jean Dubois",)
    assert first_result.placeholder_references[replacements["hospital"]] == ("UZ Leuven",)
    assert first_result.placeholder_references[replacements["person"]] == ("Sophie Martin",)
    assert first_result.placeholder_references[replacements["date_1"]] == ("1 février 1990",)
    assert first_result.placeholder_references[replacements["date_2"]] == ("8 février 1990",)

    shifted_dates = re.findall(r"\d{1,2} [A-Za-zÀ-ÿ]+ \d{4}", first_result.output_text)
    assert len(shifted_dates) == 2
    first_shifted_date, _ = _parse_date_literal(shifted_dates[0])
    second_shifted_date, _ = _parse_date_literal(shifted_dates[1])
    assert (second_shifted_date - first_shifted_date).days == 7


def test_deidentify_document_smart_mode_uses_explicit_date_shift_days(monkeypatch) -> None:
    text = "Jean Dubois est vu le 1 février 1990 puis revient le 8 février 1990."

    monkeypatch.setattr(
        "open_anonymizer.services.smart_pseudonymizer._local_secret_bytes",
        lambda: b"\x22" * 32,
    )

    document = ImportedDocument(
        id="doc-smart-explicit-shift",
        source_kind="paste",
        display_name="Pasted Text Explicit Shift",
        raw_text=text,
    )
    settings = AnonymizationSettings(
        first_name="Jean",
        last_name="Dubois",
        birthdate=date(1990, 2, 1),
        date_shift_days=10,
        mode="smart_pseudonyms",
    )

    result = deidentify_document(document, settings)

    assert "11 fevrier 1990" in result.output_text
    assert "18 fevrier 1990" in result.output_text
    assert SMART_DATE_SHIFT_WARNING not in result.warnings


def test_deidentify_document_smart_mode_treats_institutions_like_hospitals(monkeypatch) -> None:
    text = "Consultatie in UZ Leuven."
    institution_text = "UZ Leuven"

    def fake_analyze_text_with_tag(tag: str):
        def fake_analyze_text(
            source_text: str,
            settings: AnonymizationSettings,
        ) -> BackendAnalysisResult:
            del settings
            return BackendAnalysisResult(
                text=source_text,
                annotations=(
                    BackendAnnotation(
                        text=institution_text,
                        tag=tag,
                        start_char=source_text.index(institution_text),
                        end_char=source_text.index(institution_text) + len(institution_text),
                    ),
                ),
                masked_email_replacements={},
            )

        return fake_analyze_text

    monkeypatch.setattr(
        "open_anonymizer.services.smart_pseudonymizer._local_secret_bytes",
        lambda: b"\x44" * 32,
    )

    document = ImportedDocument(
        id="doc-smart-institution",
        source_kind="paste",
        display_name="Pasted Text 003",
        raw_text=text,
    )
    settings = AnonymizationSettings(mode="smart_pseudonyms")

    monkeypatch.setattr(
        "open_anonymizer.services.smart_pseudonymizer.analyze_text",
        fake_analyze_text_with_tag("hospital"),
    )
    hospital_result = deidentify_document(document, settings)

    monkeypatch.setattr(
        "open_anonymizer.services.smart_pseudonymizer.analyze_text",
        fake_analyze_text_with_tag("institution"),
    )
    institution_result = deidentify_document(document, settings)

    assert hospital_result.output_text == institution_result.output_text
    assert hospital_result.placeholder_references == institution_result.placeholder_references
    replacement = next(iter(hospital_result.placeholder_references))
    assert hospital_result.placeholder_references[replacement] == (institution_text,)


def test_deidentify_document_smart_mode_preserves_doctor_titles(monkeypatch) -> None:
    text = "Dr. Sophie Martin ziet Jean Dubois."
    doctor_text = "Dr. Sophie Martin"
    patient_text = "Jean Dubois"

    def fake_analyze_text(source_text: str, settings: AnonymizationSettings) -> BackendAnalysisResult:
        del settings
        return BackendAnalysisResult(
            text=source_text,
            annotations=(
                BackendAnnotation(
                    text=doctor_text,
                    tag="person",
                    start_char=source_text.index(doctor_text),
                    end_char=source_text.index(doctor_text) + len(doctor_text),
                ),
                BackendAnnotation(
                    text=patient_text,
                    tag="patient",
                    start_char=source_text.index(patient_text),
                    end_char=source_text.index(patient_text) + len(patient_text),
                ),
            ),
            masked_email_replacements={},
        )

    monkeypatch.setattr(
        "open_anonymizer.services.smart_pseudonymizer.analyze_text",
        fake_analyze_text,
    )
    monkeypatch.setattr(
        "open_anonymizer.services.smart_pseudonymizer._local_secret_bytes",
        lambda: b"\x55" * 32,
    )

    document = ImportedDocument(
        id="doc-smart-doctor",
        source_kind="paste",
        display_name="Pasted Text 004",
        raw_text=text,
    )
    settings = AnonymizationSettings(
        first_name="Jean",
        last_name="Dubois",
        mode="smart_pseudonyms",
    )

    result = deidentify_document(document, settings)

    match = re.fullmatch(r"(?P<doctor>Dr\. .+) ziet (?P<patient>.+)\.", result.output_text)
    assert match is not None
    replacements = match.groupdict()
    assert replacements["doctor"].startswith("Dr. ")
    assert replacements["doctor"] != doctor_text
    assert result.placeholder_references[replacements["doctor"]] == (doctor_text,)
    assert result.placeholder_references[replacements["patient"]] == (patient_text,)


def test_deidentify_document_smart_mode_preserves_patient_titles(monkeypatch) -> None:
    text = "Dr. Jean Dubois komt terug."
    titled_patient_text = "Dr. Jean Dubois"

    def fake_analyze_text(source_text: str, settings: AnonymizationSettings) -> BackendAnalysisResult:
        del settings
        return BackendAnalysisResult(
            text=source_text,
            annotations=(
                BackendAnnotation(
                    text=titled_patient_text,
                    tag="patient",
                    start_char=source_text.index(titled_patient_text),
                    end_char=source_text.index(titled_patient_text) + len(titled_patient_text),
                ),
            ),
            masked_email_replacements={},
        )

    monkeypatch.setattr(
        "open_anonymizer.services.smart_pseudonymizer.analyze_text",
        fake_analyze_text,
    )
    monkeypatch.setattr(
        "open_anonymizer.services.smart_pseudonymizer._local_secret_bytes",
        lambda: b"\x66" * 32,
    )

    document = ImportedDocument(
        id="doc-smart-patient-title",
        source_kind="paste",
        display_name="Pasted Text 005",
        raw_text=text,
    )
    settings = AnonymizationSettings(
        first_name="Jean",
        last_name="Dubois",
        mode="smart_pseudonyms",
    )

    result = deidentify_document(document, settings)

    match = re.fullmatch(r"(?P<patient>Dr\. .+) komt terug\.", result.output_text)
    assert match is not None
    replacement = match.group("patient")
    assert replacement.startswith("Dr. ")
    assert replacement != titled_patient_text
    assert result.placeholder_references[replacement] == (titled_patient_text,)


def test_deidentify_document_smart_mode_uses_session_auto_shift_without_warning(monkeypatch) -> None:
    text = "Controle le 10 oktober 2018."
    date_text = "10 oktober 2018"

    def fake_analyze_text(source_text: str, settings: AnonymizationSettings) -> BackendAnalysisResult:
        del settings
        return BackendAnalysisResult(
            text=source_text,
            annotations=(
                BackendAnnotation(
                    text=date_text,
                    tag="datum",
                    start_char=source_text.index(date_text),
                    end_char=source_text.index(date_text) + len(date_text),
                ),
            ),
            masked_email_replacements={},
        )

    monkeypatch.setattr(
        "open_anonymizer.services.smart_pseudonymizer.analyze_text",
        fake_analyze_text,
    )
    monkeypatch.setattr(
        "open_anonymizer.services.smart_pseudonymizer._local_secret_bytes",
        lambda: b"\x22" * 32,
    )

    document = ImportedDocument(
        id="doc-smart-2",
        source_kind="paste",
        display_name="Pasted Text 002",
        raw_text=text,
    )

    result = deidentify_document(
        document,
        AnonymizationSettings(mode="smart_pseudonyms"),
    )

    assert SMART_DATE_SHIFT_WARNING not in result.warnings
    assert date_text not in result.output_text


def test_deidentify_document_smart_mode_preserves_html_markup(monkeypatch) -> None:
    title_text = "Jean Dubois au UZ Leuven le 1 février 1990"
    body_text = "Jean Dubois consulte."

    def fake_analyze_text(source_text: str, settings: AnonymizationSettings) -> BackendAnalysisResult:
        del settings
        annotations: list[BackendAnnotation] = []
        if "Jean Dubois" in source_text:
            start = source_text.index("Jean Dubois")
            annotations.append(
                BackendAnnotation(
                    text="Jean Dubois",
                    tag="patient",
                    start_char=start,
                    end_char=start + len("Jean Dubois"),
                )
            )
        if "UZ Leuven" in source_text:
            start = source_text.index("UZ Leuven")
            annotations.append(
                BackendAnnotation(
                    text="UZ Leuven",
                    tag="hospital",
                    start_char=start,
                    end_char=start + len("UZ Leuven"),
                )
            )
        if "1 février 1990" in source_text:
            start = source_text.index("1 février 1990")
            annotations.append(
                BackendAnnotation(
                    text="1 février 1990",
                    tag="datum",
                    start_char=start,
                    end_char=start + len("1 février 1990"),
                )
            )
        return BackendAnalysisResult(
            text=source_text,
            annotations=tuple(sorted(annotations, key=lambda item: (item.start_char, item.end_char))),
            masked_email_replacements={},
        )

    monkeypatch.setattr(
        "open_anonymizer.services.smart_pseudonymizer.analyze_text",
        fake_analyze_text,
    )
    monkeypatch.setattr(
        "open_anonymizer.services.smart_pseudonymizer._local_secret_bytes",
        lambda: b"\x33" * 32,
    )

    document = ImportedDocument(
        id="doc-smart-html",
        source_kind="html",
        display_name="report.html",
        path=Path("report.html"),
        raw_text=(
            f'<p title="{title_text}">{body_text}</p>'
            '<script>const hospital = "UZ Leuven";</script>'
        ),
    )

    result = deidentify_document(
        document,
        AnonymizationSettings(
            first_name="Jean",
            last_name="Dubois",
            birthdate=date(1990, 2, 1),
            mode="smart_pseudonyms",
        ),
    )

    assert "<p title=" in result.output_text
    assert "Jean Dubois" not in result.output_text
    assert "UZ Leuven" in result.output_text
    assert '<script>const hospital = "UZ Leuven";</script>' in result.output_text
