import re

from open_anonymizer.services.configured_matching import (
    address_filename_patterns,
    address_metadata_variants,
    address_text_patterns,
    parse_person_components,
    person_filename_patterns,
    person_metadata_variants,
    person_text_patterns,
)


def _matches(patterns: tuple[re.Pattern[str], ...], text: str) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def test_person_patterns_match_common_name_order_variants() -> None:
    patterns = person_text_patterns("Stig Hellemans")

    assert _matches(patterns, "Stig Hellemans")
    assert _matches(patterns, "Hellemans, Stig")
    assert _matches(patterns, "Stig\nHellemans")
    assert not _matches(patterns, "Stigson Hellemans")


def test_person_filename_patterns_match_reordered_names() -> None:
    patterns = person_filename_patterns("Stig Hellemans")

    assert _matches(patterns, "Stig_Hellemans_report.pdf")
    assert _matches(patterns, "Hellemans-Stig-report.pdf")


def test_person_components_keep_apostrophe_surname_prefixes_together() -> None:
    assert parse_person_components("Veerle D'heygere") == ("Veerle", "D'heygere")


def test_person_metadata_variants_keep_apostrophe_surname_prefixes_with_family_name() -> None:
    variants = person_metadata_variants("Veerle D'heygere")

    assert "D heygere, Veerle" in variants
    assert "heygere, Veerle D" not in variants


def test_address_patterns_match_common_layout_variants() -> None:
    patterns = address_text_patterns("Rue de la Loi 12, 1000 Bruxelles")

    assert _matches(patterns, "Rue de la Loi 12, 1000 Bruxelles")
    assert _matches(patterns, "Rue de la Loi 12 1000 Bruxelles")
    assert _matches(patterns, "12 Rue de la Loi, 1000 Bruxelles")
    assert _matches(patterns, "Rue de la Loi\n12, 1000 Bruxelles")
    assert _matches(patterns, "Rue de la Loi 12")
    assert not _matches(patterns, "Rue de la Loi 13, 1000 Bruxelles")


def test_address_filename_patterns_ignore_punctuation() -> None:
    patterns = address_filename_patterns("Rue de la Loi 12, 1000 Bruxelles")

    assert _matches(patterns, "Rue_de_la_Loi_12_1000_Bruxelles.pdf")
    assert _matches(patterns, "12-Rue-de-la-Loi-1000-Bruxelles.pdf")
    assert _matches(patterns, "Rue_de_la_Loi,_12,_1000_Bruxelles.pdf")


def test_metadata_variants_include_reordered_names_and_addresses() -> None:
    assert "Hellemans, Stig" in person_metadata_variants("Stig Hellemans")
    assert "12 Rue de la Loi 1000 Bruxelles" in address_metadata_variants(
        "Rue de la Loi 12, 1000 Bruxelles"
    )
    assert "Rue de la Loi 12" in address_metadata_variants(
        "Rue de la Loi 12, 1000 Bruxelles"
    )
