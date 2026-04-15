from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
import hashlib
import hmac
import importlib.resources as importlib_resources
import os
from pathlib import Path
import random
import re
import sys

from rapidfuzz.distance import DamerauLevenshtein

from open_anonymizer.models import AnonymizationSettings
from open_anonymizer.services.configured_matching import (
    address_text_patterns,
    person_text_patterns,
)
from open_anonymizer.services.deduce_backend import (
    BackendAnalysisResult,
    BackendAnnotation,
    analyze_text,
    canonical_annotation_tag,
    placeholder_tag_name,
)


SMART_DATE_SHIFT_WARNING = (
    "Smart pseudonyms shifted dates using a document-level fallback because no patient "
    "anchor was configured."
)
PERSON_TAGS = {"patient", "person", "persoon"}
INSTITUTION_TAGS = {
    "hospital",
    "institution",
    "healthcare_institution",
    "ziekenhuis",
    "zorginstelling",
}
DATE_TAGS = {"date", "datum"}
ADDRESS_PLACEHOLDER_TAG = "LOCATION"

FRENCH_LANGUAGE_MARKERS = (
    "clinique",
    "hopital",
    "hôpital",
    "centre",
    "cabinet",
    "maison",
    "sante",
    "santé",
)
DUTCH_LANGUAGE_MARKERS = (
    "ziekenhuis",
    "kliniek",
    "praktijk",
    "zorg",
    "centrum",
    "az ",
    " uz",
    "uz ",
)
HOSPITAL_PREFIXES = {
    "fr": ("Clinique", "Hopital", "Centre Hospitalier"),
    "nl": ("AZ", "Ziekenhuis", "Kliniek"),
}
FRENCH_DESCRIPTOR_TEMPLATES = (
    "du {noun}",
    "des {plural_noun}",
    "de {first_name}",
    "{surname}",
)
FLEMISH_DESCRIPTOR_TEMPLATES = (
    "{noun}",
    "De {noun}",
    "{first_name}",
    "{surname}",
)
FRENCH_NOUNS = (
    "Acacias",
    "Amandiers",
    "Cedres",
    "Erables",
    "Lilas",
    "Mimosas",
    "Tilleuls",
    "Vergers",
)
FLEMISH_NOUNS = (
    "Anker",
    "Brug",
    "Horizon",
    "Kompas",
    "Linde",
    "Oever",
    "Park",
    "Wende",
)
MONTH_RENDERING = {
    "nl_full": (
        "januari",
        "februari",
        "maart",
        "april",
        "mei",
        "juni",
        "juli",
        "augustus",
        "september",
        "oktober",
        "november",
        "december",
    ),
    "nl_abbr": ("jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"),
    "fr_full": (
        "janvier",
        "fevrier",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "aout",
        "septembre",
        "octobre",
        "novembre",
        "decembre",
    ),
    "fr_full_accented": (
        "janvier",
        "fevrier",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "aout",
        "septembre",
        "octobre",
        "novembre",
        "decembre",
    ),
    "fr_abbr": ("janv", "fevr", "mars", "avr", "mai", "juin", "juil", "aout", "sept", "oct", "nov", "dec"),
    "fr_abbr_accented": (
        "janv",
        "fevr",
        "mars",
        "avr",
        "mai",
        "juin",
        "juil",
        "aout",
        "sept",
        "oct",
        "nov",
        "dec",
    ),
    "shared_abbr": ("jan", "fev", "mar", "avr", "mai", "jun", "jul", "aou", "sep", "oct", "nov", "dec"),
}
ACCENTED_MONTH_OVERRIDES = {
    "fr_full_accented": {2: "fevrier", 8: "aout", 12: "decembre"},
    "fr_abbr_accented": {2: "fevr"},
}

MONTH_STYLE_BY_TOKEN = {
    "januari": ("nl_full", 1),
    "jan": ("nl_abbr", 1),
    "februari": ("nl_full", 2),
    "feb": ("nl_abbr", 2),
    "maart": ("nl_full", 3),
    "mrt": ("nl_abbr", 3),
    "april": ("nl_full", 4),
    "apr": ("nl_abbr", 4),
    "mei": ("nl_full", 5),
    "juni": ("nl_full", 6),
    "jun": ("nl_abbr", 6),
    "juli": ("nl_full", 7),
    "jul": ("nl_abbr", 7),
    "augustus": ("nl_full", 8),
    "aug": ("nl_abbr", 8),
    "september": ("nl_full", 9),
    "sep": ("nl_abbr", 9),
    "oktober": ("nl_full", 10),
    "okt": ("nl_abbr", 10),
    "november": ("nl_full", 11),
    "nov": ("nl_abbr", 11),
    "december": ("nl_full", 12),
    "dec": ("nl_abbr", 12),
    "janvier": ("fr_full", 1),
    "janv": ("fr_abbr", 1),
    "fevrier": ("fr_full", 2),
    "février": ("fr_full_accented", 2),
    "fevr": ("fr_abbr", 2),
    "févr": ("fr_abbr_accented", 2),
    "mars": ("fr_full", 3),
    "avril": ("fr_full", 4),
    "mai": ("fr_full", 5),
    "juin": ("fr_full", 6),
    "juillet": ("fr_full", 7),
    "aout": ("fr_full", 8),
    "août": ("fr_full_accented", 8),
    "septembre": ("fr_full", 9),
    "octobre": ("fr_full", 10),
    "novembre": ("fr_full", 11),
    "decembre": ("fr_full", 12),
    "décembre": ("fr_full_accented", 12),
}

NUMERIC_DATE_PATTERN = re.compile(
    r"^(?P<first>\d{1,4})(?P<sep1>[-/. ])(?P<second>\d{1,2})(?P<sep2>[-/. ])(?P<third>(?:19|20|'|`)?\d{2})$",
    re.IGNORECASE,
)
TEXTUAL_DMY_PATTERN = re.compile(
    r"^(?P<day>\d{1,2})(?P<sep1>[-/. ]{1,2})(?P<month>[A-Za-zÀ-ÿ]+)(?P<dot>\.?)(?P<sep2>[-/. ]+)(?P<year>(?:19|20|'|`)?\d{2})$",
    re.IGNORECASE,
)
TEXTUAL_YMD_PATTERN = re.compile(
    r"^(?P<year>(?:19|20|'|`)?\d{2})(?P<sep1>[-/. ]{1,2})(?P<month>[A-Za-zÀ-ÿ]+)(?P<dot>\.?)(?P<sep2>[-/. ]+)(?P<day>\d{1,2})$",
    re.IGNORECASE,
)
PERSON_TITLE_PATTERN = re.compile(
    r"^(?P<prefix>(?:(?:dr\.?|dokter|docteur|arts|prof\.?|professeur|pr\.?)\s+)+)(?P<body>.+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DateRenderStyle:
    order: str
    day_width: int
    month_width: int
    year_width: int
    first_separator: str
    second_separator: str
    month_style: str = "numeric"
    month_has_trailing_dot: bool = False
    year_prefix: str = ""


class SmartPseudonymizer:
    def __init__(
        self,
        anonymization_settings: AnonymizationSettings,
        *,
        document_key: str,
    ) -> None:
        self.anonymization_settings = anonymization_settings
        self.document_key = document_key
        self._patient_first_name, self._patient_last_name = self._build_patient_surrogate()
        self._person_replacements: dict[tuple[str, int], str] = {}
        self._institution_replacements: dict[str, str] = {}
        self._date_shift_days, self._uses_document_date_fallback = self._build_date_shift()

    def deidentify_text(
        self,
        text: str,
    ) -> tuple[str, list[str], dict[str, tuple[str, ...]]]:
        if not text or not text.strip():
            return text, [], {}

        analysis = analyze_text(text, self.anonymization_settings)
        rendered, placeholder_references, replaced_dates = self._render_analysis(analysis)
        rendered, cleanup_references, cleanup_replaced_dates = self._apply_cleanup(rendered)
        warnings: list[str] = []
        if (replaced_dates or cleanup_replaced_dates) and self._uses_document_date_fallback:
            warnings.append(SMART_DATE_SHIFT_WARNING)

        return (
            analysis.restore_text(rendered),
            warnings,
            _merge_placeholder_references(placeholder_references, cleanup_references),
        )

    def _render_analysis(
        self,
        analysis: BackendAnalysisResult,
    ) -> tuple[str, dict[str, tuple[str, ...]], bool]:
        placeholder_replacements = _build_placeholder_replacements(
            tuple(
                annotation
                for annotation in analysis.annotations
                if canonical_annotation_tag(annotation.tag)
                not in PERSON_TAGS | INSTITUTION_TAGS | DATE_TAGS
            )
        )
        replacement_references: dict[str, list[str]] = defaultdict(list)
        parts: list[str] = []
        current_position = 0
        replaced_dates = False

        for annotation in analysis.annotations:
            parts.append(analysis.text[current_position : annotation.start_char])
            normalized_tag = canonical_annotation_tag(annotation.tag)

            if normalized_tag in PERSON_TAGS:
                replacement = self._person_replacement(annotation.text, normalized_tag)
            elif normalized_tag in INSTITUTION_TAGS:
                replacement = self._institution_replacement(annotation.text, normalized_tag)
            elif normalized_tag in DATE_TAGS:
                replacement = self._shift_date_literal(annotation.text)
                replaced_dates = True
            else:
                replacement = placeholder_replacements[annotation]

            _append_replacement_reference(
                replacement_references,
                replacement,
                annotation.text,
            )

            parts.append(replacement)
            current_position = annotation.end_char

        parts.append(analysis.text[current_position:])
        return (
            "".join(parts),
            {
                placeholder: tuple(values)
                for placeholder, values in replacement_references.items()
            },
            replaced_dates,
        )

    def _apply_cleanup(
        self,
        text: str,
    ) -> tuple[str, dict[str, tuple[str, ...]], bool]:
        replacement_references: dict[str, list[str]] = {}
        replaced_dates = False

        patient_literals = [
            literal
            for literal in [
                f"{self.anonymization_settings.first_name} {self.anonymization_settings.last_name}".strip(),
                self.anonymization_settings.first_name,
                self.anonymization_settings.last_name,
            ]
            if literal
        ]
        for literal in patient_literals:
            text, replacements = _replace_literal(
                text,
                literal,
                lambda matched, patient_literal=literal: self._patient_literal_replacement(
                    matched,
                    patient_literal,
                ),
            )
            _append_literal_references(replacement_references, replacements)

        if self.anonymization_settings.birthdate:
            for literal in _build_birthdate_variants(self.anonymization_settings.birthdate):
                text, replacements = _replace_literal(
                    text,
                    literal,
                    lambda matched: self._shift_date_literal(
                        matched,
                        known_date=self.anonymization_settings.birthdate,
                    ),
                )
                if replacements:
                    replaced_dates = True
                _append_literal_references(replacement_references, replacements)

        for other_name in self.anonymization_settings.other_names:
            text, replacements = _replace_patterns(
                text,
                person_text_patterns(other_name),
                lambda matched, source_text=other_name: self._generic_person_replacement(
                    matched,
                    source_text,
                ),
            )
            _append_literal_references(replacement_references, replacements)

        for custom_address in self.anonymization_settings.custom_addresses:
            placeholder = _next_numbered_placeholder(text, ADDRESS_PLACEHOLDER_TAG)
            text, replacements = _replace_patterns(
                text,
                address_text_patterns(custom_address),
                lambda matched, replacement=placeholder: replacement,
            )
            _append_literal_references(replacement_references, replacements)

        return (
            text,
            {
                placeholder: tuple(values)
                for placeholder, values in replacement_references.items()
            },
            replaced_dates,
        )

    def _build_patient_surrogate(self) -> tuple[str, str]:
        if not any(
            [
                self.anonymization_settings.first_name.strip(),
                self.anonymization_settings.last_name.strip(),
                self.anonymization_settings.birthdate,
            ]
        ):
            return "", ""

        key = self._patient_anchor_key()
        token_count = 2 if self.anonymization_settings.last_name.strip() else 1
        components = _generate_person_components(
            self._rng("patient-name", key),
            token_count=token_count,
        )
        first_name = components[0] if components else ""
        last_name = components[-1] if token_count > 1 else ""

        original_full = _normalize_key(
            f"{self.anonymization_settings.first_name} {self.anonymization_settings.last_name}"
        )
        for attempt in range(6):
            candidate_full = _normalize_key(" ".join(part for part in [first_name, last_name] if part))
            if candidate_full and candidate_full != original_full:
                break
            components = _generate_person_components(
                self._rng("patient-name", key, str(attempt + 1)),
                token_count=token_count,
            )
            first_name = components[0] if components else ""
            last_name = components[-1] if token_count > 1 else ""

        return first_name, last_name

    def _build_date_shift(self) -> tuple[int, bool]:
        date_shift_days, uses_document_fallback = effective_date_shift_days(
            self.anonymization_settings,
            document_key=self.document_key,
        )
        return date_shift_days or 0, uses_document_fallback

    def _patient_anchor_key(self) -> str:
        return patient_anchor_key(self.anonymization_settings)

    def _person_replacement(self, original_text: str, tag: str) -> str:
        if tag == "patient":
            return self._patient_literal_replacement(original_text, original_text)
        return self._generic_person_replacement(original_text, original_text)

    def _generic_person_replacement(self, original_text: str, source_text: str) -> str:
        title_prefix, body_text = _split_person_title_prefix(original_text)
        source_title_prefix, source_body_text = _split_person_title_prefix(source_text)
        del source_title_prefix
        body_source = source_body_text or body_text or source_text
        token_count = max(1, len(body_source.split()))
        cache_key = (_normalize_key(body_source), token_count)
        replacement = self._person_replacements.get(cache_key)
        if replacement is None:
            components = _generate_person_components(
                self._rng("person", cache_key[0]),
                token_count=token_count,
            )
            replacement = " ".join(components)
            for attempt in range(6):
                if _normalize_key(replacement) != cache_key[0]:
                    break
                components = _generate_person_components(
                    self._rng("person", cache_key[0], str(attempt + 1)),
                    token_count=token_count,
                )
                replacement = " ".join(components)
            self._person_replacements[cache_key] = replacement

        if title_prefix:
            return f"{title_prefix}{_match_case_style(replacement, body_source)}"
        return _match_case_style(replacement, original_text)

    def _patient_literal_replacement(self, original_text: str, patient_literal: str) -> str:
        title_prefix, literal_body = _split_person_title_prefix(patient_literal)
        original_title_prefix, original_body = _split_person_title_prefix(original_text)
        if not title_prefix:
            title_prefix = original_title_prefix
        body_text = literal_body or original_body or patient_literal
        normalized_literal = _normalize_key(body_text)
        normalized_first = _normalize_key(self.anonymization_settings.first_name)
        normalized_last = _normalize_key(self.anonymization_settings.last_name)
        normalized_full = _normalize_key(
            f"{self.anonymization_settings.first_name} {self.anonymization_settings.last_name}"
        )

        if normalized_literal and normalized_literal == normalized_first and self._patient_first_name:
            return _apply_person_title_prefix(
                title_prefix,
                self._patient_first_name,
                original_body or original_text,
            )
        if normalized_literal and normalized_literal == normalized_last and self._patient_last_name:
            return _apply_person_title_prefix(
                title_prefix,
                self._patient_last_name,
                original_body or original_text,
            )

        full_name = " ".join(
            part for part in [self._patient_first_name, self._patient_last_name] if part
        ).strip()
        if normalized_literal and normalized_literal == normalized_full and full_name:
            return _apply_person_title_prefix(
                title_prefix,
                full_name,
                original_body or original_text,
            )

        original_body_text = original_body or original_text
        if original_body_text.strip().count(" ") == 0:
            fallback = self._patient_first_name or self._patient_last_name or full_name
        else:
            fallback = full_name or self._patient_first_name or self._patient_last_name

        if not fallback:
            return original_text
        return _apply_person_title_prefix(
            title_prefix,
            fallback,
            original_body_text,
        )

    def _institution_replacement(self, original_text: str, tag: str) -> str:
        del tag
        language = _language_for_text(original_text)
        cache_key = _normalize_key(original_text)
        replacement = self._institution_replacements.get(cache_key)
        if replacement is None:
            replacement = self._generate_institution_name(original_text, language)
            self._institution_replacements[cache_key] = replacement

        return _match_case_style(replacement, original_text)

    def _generate_institution_name(
        self,
        original_text: str,
        language: str,
    ) -> str:
        real_institutions = _real_institution_names()
        for attempt in range(24):
            rng = self._rng("institution", _normalize_key(original_text), str(attempt))
            descriptor = _institution_descriptor(rng, language)
            prefix = rng.choice(HOSPITAL_PREFIXES[language])

            if prefix == "AZ":
                candidate = f"{prefix} {descriptor}"
            else:
                candidate = f"{prefix} {descriptor}"

            if _normalize_key(candidate) not in real_institutions and _normalize_key(candidate) != _normalize_key(
                original_text
            ):
                return candidate

        fallback = "Clinique Horizon" if language == "fr" else "Ziekenhuis Horizon"
        return fallback

    def _shift_date_literal(
        self,
        original_text: str,
        *,
        known_date: date | None = None,
    ) -> str:
        source_date, render_style = _parse_date_literal(original_text)
        shifted = (known_date or source_date) + timedelta(days=self._date_shift_days)
        return _render_date_literal(shifted, render_style, original_text)

    def _rng(self, namespace: str, *parts: str) -> random.Random:
        return _stable_rng(namespace, *parts)


def format_date_shift_days(days: int) -> str:
    unit = "day" if abs(days) == 1 else "days"
    return f"{days:+d} {unit}"


@lru_cache(maxsize=1)
def _session_auto_date_shift_days() -> int:
    rng = random.SystemRandom()
    weeks = rng.randint(6, 104)
    direction = -1 if rng.random() < 0.5 else 1
    return direction * weeks * 7


def patient_anchor_key(anonymization_settings: AnonymizationSettings) -> str:
    parts = [
        anonymization_settings.first_name.strip(),
        anonymization_settings.last_name.strip(),
    ]
    if anonymization_settings.birthdate:
        parts.append(anonymization_settings.birthdate.isoformat())
    return "|".join(part for part in parts if part)


def effective_date_shift_days(
    anonymization_settings: AnonymizationSettings,
    *,
    document_key: str | None = None,
) -> tuple[int | None, bool]:
    del document_key
    if anonymization_settings.date_shift_days is not None:
        return anonymization_settings.date_shift_days, False

    return _session_auto_date_shift_days(), False


def _stable_rng(namespace: str, *parts: str) -> random.Random:
    digest = hmac.new(
        _local_secret_bytes(),
        "\x1f".join([namespace, *parts]).encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _merge_placeholder_references(
    *maps: dict[str, tuple[str, ...]] | dict[str, list[str]],
) -> dict[str, tuple[str, ...]]:
    merged: dict[str, list[str]] = {}
    for mapping in maps:
        for placeholder, values in mapping.items():
            existing = merged.setdefault(placeholder, [])
            for value in values:
                if value not in existing:
                    existing.append(value)

    return {
        placeholder: tuple(values)
        for placeholder, values in merged.items()
    }


def _append_replacement_reference(
    target: dict[str, list[str]],
    replacement: str,
    original: str,
) -> None:
    if not replacement or not original:
        return

    existing = target.setdefault(replacement, [])
    if original not in existing:
        existing.append(original)


def _append_literal_references(
    target: dict[str, list[str]],
    replacements: list[tuple[str, str]],
) -> None:
    for replacement, original in replacements:
        _append_replacement_reference(target, replacement, original)


def _generate_person_components(rng: random.Random, *, token_count: int) -> list[str]:
    first_names = _first_names()
    surnames = _surnames()
    if token_count <= 1:
        return [rng.choice(first_names)]

    return [
        *(rng.choice(first_names) for _ in range(token_count - 1)),
        rng.choice(surnames),
    ]


def _build_placeholder_replacements(
    annotations: tuple[BackendAnnotation, ...],
) -> dict[BackendAnnotation, str]:
    grouped_annotations: dict[str, list[BackendAnnotation]] = defaultdict(list)
    for annotation in annotations:
        grouped_annotations[annotation.tag].append(annotation)

    replacements: dict[BackendAnnotation, str] = {}
    for tag, annotation_group in grouped_annotations.items():
        grouped_replacements: dict[BackendAnnotation, str] = {}
        counter = 1

        for annotation in sorted(
            annotation_group,
            key=lambda item: (item.end_char, item.start_char, item.text),
        ):
            replacement: str | None = None
            for annotation_match, existing_replacement in grouped_replacements.items():
                if (
                    DamerauLevenshtein.distance(
                        annotation.text,
                        annotation_match.text,
                        score_cutoff=1,
                    )
                    <= 1
                ):
                    replacement = existing_replacement
                    break

            if replacement is None:
                replacement = f"[{placeholder_tag_name(annotation.tag)}-{counter}]"
                counter += 1

            grouped_replacements[annotation] = replacement

        replacements.update(grouped_replacements)

    return replacements


def _replace_literal(
    text: str,
    literal: str,
    replacement_factory,
) -> tuple[str, list[tuple[str, str]]]:
    return _replace_patterns(
        text,
        (_build_literal_pattern(literal),),
        replacement_factory,
    )


def _replace_patterns(
    text: str,
    patterns: tuple[re.Pattern[str], ...],
    replacement_factory,
) -> tuple[str, list[tuple[str, str]]]:
    replacements: list[tuple[str, str]] = []

    for pattern in patterns:
        def replace_match(match: re.Match[str]) -> str:
            matched_text = match.group(0)
            replacement = replacement_factory(matched_text)
            replacements.append((replacement, matched_text))
            return replacement

        text = pattern.sub(replace_match, text)

    return text, replacements


def _build_literal_pattern(literal: str) -> re.Pattern[str]:
    body = r"\s+".join(re.escape(part) for part in literal.split())
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


def _next_numbered_placeholder(text: str, tag_name: str) -> str:
    indices = [
        int(match.group(1))
        for match in re.finditer(rf"\[{re.escape(tag_name)}-(\d+)\]", text)
    ]
    return f"[{tag_name}-{max(indices, default=0) + 1}]"


def _parse_date_literal(value: str) -> tuple[date, DateRenderStyle]:
    stripped = value.strip()
    numeric_match = NUMERIC_DATE_PATTERN.fullmatch(stripped)
    if numeric_match:
        first = numeric_match.group("first")
        second = numeric_match.group("second")
        third = numeric_match.group("third")
        order = "ymd" if len(first) == 4 else "dmy"
        year_value, year_width, year_prefix = _expand_year_token(third if order == "dmy" else first)
        if order == "dmy":
            parsed_date = date(year_value, int(second), int(first))
            style = DateRenderStyle(
                order="dmy",
                day_width=len(first),
                month_width=len(second),
                year_width=year_width,
                first_separator=numeric_match.group("sep1"),
                second_separator=numeric_match.group("sep2"),
                year_prefix=year_prefix,
            )
        else:
            parsed_date = date(year_value, int(second), int(third))
            style = DateRenderStyle(
                order="ymd",
                day_width=len(third),
                month_width=len(second),
                year_width=year_width,
                first_separator=numeric_match.group("sep1"),
                second_separator=numeric_match.group("sep2"),
                year_prefix=year_prefix,
            )
        return parsed_date, style

    dmy_match = TEXTUAL_DMY_PATTERN.fullmatch(stripped)
    if dmy_match:
        month_style, month_value = _month_style_and_value(dmy_match.group("month"))
        year_value, year_width, year_prefix = _expand_year_token(dmy_match.group("year"))
        return (
            date(year_value, month_value, int(dmy_match.group("day"))),
            DateRenderStyle(
                order="dmy",
                day_width=len(dmy_match.group("day")),
                month_width=0,
                year_width=year_width,
                first_separator=dmy_match.group("sep1"),
                second_separator=dmy_match.group("sep2"),
                month_style=month_style,
                month_has_trailing_dot=bool(dmy_match.group("dot")),
                year_prefix=year_prefix,
            ),
        )

    ymd_match = TEXTUAL_YMD_PATTERN.fullmatch(stripped)
    if ymd_match:
        month_style, month_value = _month_style_and_value(ymd_match.group("month"))
        year_value, year_width, year_prefix = _expand_year_token(ymd_match.group("year"))
        return (
            date(year_value, month_value, int(ymd_match.group("day"))),
            DateRenderStyle(
                order="ymd",
                day_width=len(ymd_match.group("day")),
                month_width=0,
                year_width=year_width,
                first_separator=ymd_match.group("sep1"),
                second_separator=ymd_match.group("sep2"),
                month_style=month_style,
                month_has_trailing_dot=bool(ymd_match.group("dot")),
                year_prefix=year_prefix,
            ),
        )

    raise ValueError(f"Unsupported date literal: {value}")


def _render_date_literal(
    shifted_date: date,
    style: DateRenderStyle,
    original_text: str,
) -> str:
    day_part = f"{shifted_date.day:0{style.day_width}d}" if style.day_width > 1 else str(shifted_date.day)
    year_part = _render_year(shifted_date.year, style.year_width, style.year_prefix)

    if style.month_style == "numeric":
        month_part = (
            f"{shifted_date.month:0{style.month_width}d}"
            if style.month_width > 1
            else str(shifted_date.month)
        )
        if style.order == "ymd":
            rendered = (
                f"{year_part}{style.first_separator}{month_part}"
                f"{style.second_separator}{day_part}"
            )
        else:
            rendered = (
                f"{day_part}{style.first_separator}{month_part}"
                f"{style.second_separator}{year_part}"
            )
    else:
        month_part = _render_month(shifted_date.month, style.month_style, original_text)
        if style.month_has_trailing_dot:
            month_part = f"{month_part}."

        if style.order == "ymd":
            rendered = (
                f"{year_part}{style.first_separator}{month_part}"
                f"{style.second_separator}{day_part}"
            )
        else:
            rendered = (
                f"{day_part}{style.first_separator}{month_part}"
                f"{style.second_separator}{year_part}"
            )

    return _wrap_with_original_whitespace(rendered, original_text)


def _render_month(month_number: int, month_style: str, original_text: str) -> str:
    month_forms = MONTH_RENDERING[month_style]
    month_text = month_forms[month_number - 1]
    month_text = ACCENTED_MONTH_OVERRIDES.get(month_style, {}).get(month_number, month_text)
    return _match_case_style(month_text, _extract_month_token(original_text))


def _extract_month_token(value: str) -> str:
    for token in value.split():
        normalized = token.rstrip(".")
        if _normalize_month_token(normalized) in MONTH_STYLE_BY_TOKEN:
            return normalized
    return value


def _expand_year_token(token: str) -> tuple[int, int, str]:
    year_prefix = ""
    digits = token
    if token and token[0] in {"'", "`"}:
        year_prefix = token[0]
        digits = token[1:]

    if len(digits) == 4:
        return int(digits), 4, year_prefix

    year_suffix = int(digits)
    year_value = 2000 + year_suffix if year_suffix <= 30 else 1900 + year_suffix
    return year_value, 2, year_prefix


def _render_year(year: int, width: int, prefix: str) -> str:
    if width == 4:
        return f"{year:04d}"
    return f"{prefix}{year % 100:02d}"


def _month_style_and_value(token: str) -> tuple[str, int]:
    normalized = _normalize_month_token(token)
    month_style, month_value = MONTH_STYLE_BY_TOKEN[normalized]
    return month_style, month_value


def _normalize_month_token(token: str) -> str:
    return token.strip().casefold()


def _wrap_with_original_whitespace(rendered: str, original_text: str) -> str:
    match = re.match(r"^(\s*)(.*?)(\s*)$", original_text, flags=re.DOTALL)
    if not match:
        return rendered
    return f"{match.group(1)}{rendered}{match.group(3)}"


def _match_case_style(replacement: str, original_text: str) -> str:
    stripped_original = original_text.strip()
    if not stripped_original:
        return replacement

    if stripped_original.isupper():
        return replacement.upper()
    if stripped_original.islower():
        return replacement.lower()
    if stripped_original.istitle():
        return replacement.title()
    return replacement


def _language_for_text(text: str) -> str:
    normalized = f" {text.casefold()} "
    french_hits = sum(marker in normalized for marker in FRENCH_LANGUAGE_MARKERS)
    dutch_hits = sum(marker in normalized for marker in DUTCH_LANGUAGE_MARKERS)
    if french_hits > dutch_hits:
        return "fr"
    if dutch_hits > french_hits:
        return "nl"
    return "fr" if hashlib.sha256(normalized.encode("utf-8")).digest()[0] % 2 else "nl"


def _institution_descriptor(rng: random.Random, language: str) -> str:
    first_name = rng.choice(_first_names())
    surname = rng.choice(_surnames())
    if language == "fr":
        noun = rng.choice(FRENCH_NOUNS)
        plural_noun = rng.choice(FRENCH_NOUNS)
        template = rng.choice(FRENCH_DESCRIPTOR_TEMPLATES)
    else:
        noun = rng.choice(FLEMISH_NOUNS)
        plural_noun = rng.choice(FLEMISH_NOUNS)
        template = rng.choice(FLEMISH_DESCRIPTOR_TEMPLATES)

    return template.format(
        first_name=first_name,
        surname=surname,
        noun=noun,
        plural_noun=plural_noun,
    )


def _normalize_key(value: str) -> str:
    return " ".join(value.split()).casefold()


def _split_person_title_prefix(value: str) -> tuple[str, str]:
    stripped = value.strip()
    if not stripped:
        return "", ""

    match = PERSON_TITLE_PATTERN.match(stripped)
    if not match:
        return "", stripped

    return match.group("prefix"), match.group("body")


def _apply_person_title_prefix(
    title_prefix: str,
    replacement: str,
    original_body: str,
) -> str:
    styled_replacement = _match_case_style(replacement, original_body)
    if not title_prefix:
        return styled_replacement
    return f"{title_prefix}{styled_replacement}"


def _local_secret_bytes() -> bytes:
    path = _secret_file_path()
    if path.exists():
        return path.read_bytes()

    path.parent.mkdir(parents=True, exist_ok=True)
    secret = os.urandom(32)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    file_descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(file_descriptor, "wb") as handle:
            handle.write(secret)
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return secret


def _secret_file_path() -> Path:
    if sys.platform == "darwin":
        base_dir = Path.home() / "Library" / "Application Support"
        return base_dir / "Open Anonymizer" / "pseudonymization.key"
    if os.name == "nt":
        base_dir = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        return base_dir / "Open Anonymizer" / "pseudonymization.key"
    base_dir = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base_dir / "open-anonymizer" / "pseudonymization.key"


@lru_cache(maxsize=1)
def _first_names() -> tuple[str, ...]:
    path = importlib_resources.files("belgian_deduce").joinpath(
        "data/lookup/src/names/lst_first_name/items.txt"
    )
    return tuple(
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


@lru_cache(maxsize=1)
def _surnames() -> tuple[str, ...]:
    path = importlib_resources.files("belgian_deduce").joinpath(
        "data/lookup/src/names/lst_surname/items.txt"
    )
    return tuple(
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


@lru_cache(maxsize=1)
def _real_institution_names() -> set[str]:
    path = importlib_resources.files("belgian_deduce").joinpath(
        "data/lookup/src/institutions/lst_healthcare_institution/items.txt"
    )
    return {
        _normalize_key(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def _build_birthdate_variants(birthdate: date) -> set[str]:
    day = birthdate.day
    month = birthdate.month
    year = birthdate.year
    short_year = year % 100

    variants: set[str] = set()
    day_options = {str(day), f"{day:02d}"}
    month_options = {str(month), f"{month:02d}"}
    year_options = {str(year), f"{short_year:02d}"}

    for separator in ("/", "-", "."):
        for day_value in day_options:
            for month_value in month_options:
                for year_value in year_options:
                    variants.add(f"{day_value}{separator}{month_value}{separator}{year_value}")
                variants.add(f"{year}{separator}{month_value}{separator}{day_value}")

    month_names = {
        1: ("janvier", "janv", "januari", "jan"),
        2: ("fevrier", "février", "fevr", "févr", "februari", "feb"),
        3: ("mars", "maart", "mrt"),
        4: ("avril", "april", "apr"),
        5: ("mai", "mei"),
        6: ("juin", "juni", "jun"),
        7: ("juillet", "juli", "jul"),
        8: ("aout", "août", "augustus", "aug"),
        9: ("septembre", "september", "sept", "sep"),
        10: ("octobre", "oktober", "oct", "okt"),
        11: ("novembre", "november", "nov"),
        12: ("decembre", "décembre", "december", "dec"),
    }

    for month_name in month_names[month]:
        for day_value in day_options:
            for year_value in year_options:
                variants.add(f"{day_value} {month_name} {year_value}")
                variants.add(f"{day_value} {month_name}. {year_value}")

    return variants
