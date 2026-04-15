from __future__ import annotations

from datetime import date

import pytest

from open_anonymizer.models import AnonymizationSettings, RecognitionFlags
from open_anonymizer.services.deduce_backend import (
    build_backend_config,
    build_backend_metadata,
    deidentify_text,
)


def test_build_backend_config_filters_disabled_groups() -> None:
    config = build_backend_config(
        RecognitionFlags(
            names=False,
            dates=False,
            identifiers=False,
        )
    )

    annotators = config["annotators"]
    assert "metadata_entities" in annotators
    assert "patient_name" in annotators
    assert {
        name
        for name, spec in annotators.items()
        if spec.get("group") == "names"
    } == {"patient_name"}
    assert all(spec.get("group") != "dates" for spec in annotators.values())
    assert all(spec.get("group") != "identifiers" for spec in annotators.values())
    assert any(spec.get("group") == "locations" for spec in annotators.values())


def test_build_backend_metadata_contains_patient_aliases_and_custom_entities() -> None:
    metadata = build_backend_metadata(
        AnonymizationSettings(
            first_name="Jean",
            last_name="Dupont",
            other_names=["Sophie Martin"],
            custom_addresses=["Rue de la Loi 12, 1000 Bruxelles"],
        )
    )

    assert metadata is not None
    assert metadata["patient"].aliases == ["Jean Dupont", "Jean", "Dupont"]
    entity_pairs = {(entity.tag, entity.text) for entity in metadata["entities"]}
    assert ("person", "Sophie Martin") in entity_pairs
    assert ("person", "Martin, Sophie") in entity_pairs
    assert ("location", "Rue de la Loi 12, 1000 Bruxelles") in entity_pairs
    assert ("location", "12 Rue de la Loi 1000 Bruxelles") in entity_pairs
    assert ("location", "Rue de la Loi 12") in entity_pairs


def test_deidentify_text_keeps_patient_name_when_names_disabled() -> None:
    result = deidentify_text(
        "Jean Dupont consulte aujourd'hui.",
        AnonymizationSettings(
            first_name="Jean",
            last_name="Dupont",
            recognition_flags=RecognitionFlags(names=False),
        ),
    )

    assert result == "[PATIENT] consulte aujourd'hui."


def test_deidentify_text_keeps_patient_birthdate_when_dates_disabled() -> None:
    result = deidentify_text(
        "Patient Jean Dupont, né le 12 mars 1980.",
        AnonymizationSettings(
            first_name="Jean",
            last_name="Dupont",
            birthdate=date(1980, 3, 12),
            recognition_flags=RecognitionFlags(dates=False),
        ),
    )

    assert "[DATE-1]" in result


def test_deidentify_text_keeps_custom_name_and_address_when_detection_is_disabled() -> None:
    result = deidentify_text(
        "Sophie Martin habite Rue de la Loi 12, 1000 Bruxelles.",
        AnonymizationSettings(
            other_names=["Sophie Martin"],
            custom_addresses=["Rue de la Loi 12, 1000 Bruxelles"],
            recognition_flags=RecognitionFlags(names=False, locations=False),
        ),
    )

    assert result == "[PERSON-1] habite [LOCATION-1]."


def test_deidentify_text_matches_reordered_custom_name_and_address_variants() -> None:
    result = deidentify_text(
        "Martin, Sophie habite 12 Rue de la Loi 1000 Bruxelles.",
        AnonymizationSettings(
            other_names=["Sophie Martin"],
            custom_addresses=["Rue de la Loi 12, 1000 Bruxelles"],
            recognition_flags=RecognitionFlags(names=False, locations=False),
        ),
    )

    assert result == "[PERSON-1] habite [LOCATION-1]."


@pytest.mark.parametrize(
    ("recognition_flags", "text", "blocked_tokens", "expected_literals"),
    [
        (
            RecognitionFlags(dates=False),
            "Il revient le 10 oktober 2018.",
            ["[DATE-"],
            ["10 oktober 2018"],
        ),
        (
            RecognitionFlags(ages=False),
            "Il is 64 jaar oud.",
            ["[AGE-"],
            ["64 jaar oud"],
        ),
        (
            RecognitionFlags(institutions=False),
            "Il est sorti de UZ Leuven.",
            ["[HOSPITAL-", "[INSTITUTION-"],
            ["UZ"],
        ),
        (
            RecognitionFlags(identifiers=False),
            "Rijksregisternummer 85.07.30-033.28 en patnr 000334433.",
            ["[NATIONAL_REGISTER_NUMBER-", "[ID-"],
            ["85.07.30-033.28", "000334433"],
        ),
        (
            RecognitionFlags(phone_numbers=False),
            "Contact: 0470 12 34 56.",
            ["[PHONE_NUMBER-"],
            ["0470 12 34 56"],
        ),
        (
            RecognitionFlags(email_addresses=False),
            "Mail: jean.dupont@example.com.",
            ["[EMAIL-"],
            ["jean.dupont@example.com"],
        ),
        (
            RecognitionFlags(urls=False),
            "Site: https://example.com.",
            ["[URL-"],
            ["https://example.com"],
        ),
    ],
)
def test_deidentify_text_respects_disabled_recognition_groups(
    recognition_flags: RecognitionFlags,
    text: str,
    blocked_tokens: list[str],
    expected_literals: list[str],
) -> None:
    result = deidentify_text(
        text,
        AnonymizationSettings(recognition_flags=recognition_flags),
    )

    for blocked_token in blocked_tokens:
        assert blocked_token not in result

    for expected_literal in expected_literals:
        assert expected_literal in result
