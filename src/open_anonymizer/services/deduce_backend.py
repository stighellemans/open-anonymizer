from __future__ import annotations

import atexit
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
import importlib.resources as importlib_resources
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

from rapidfuzz.distance import DamerauLevenshtein

from open_anonymizer.models import (
    AnonymizationSettings,
    RECOGNITION_GROUPS,
    RecognitionFlags,
)
from open_anonymizer.services.configured_matching import (
    address_metadata_variants,
    person_metadata_variants,
)

_ANNOTATORS_TO_KEEP_WHEN_DISABLED = {
    "names": {"patient_name"},
}
EMAIL_PATTERN = re.compile(r"(?<!\w)[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?!\w)", re.IGNORECASE)
EMAIL_MASK_PREFIX = "OPENANONYMIZEREMAILTOKEN"
PLACEHOLDER_TAG_NAMES = {
    "date": "DATE",
    "datum": "DATE",
    "person": "PERSON",
    "persoon": "PERSON",
    "location": "LOCATION",
    "locatie": "LOCATION",
    "hospital": "HOSPITAL",
    "ziekenhuis": "HOSPITAL",
    "institution": "INSTITUTION",
    "zorginstelling": "INSTITUTION",
    "age": "AGE",
    "leeftijd": "AGE",
}
FRENCH_MONTH_PATTERN = (
    "janvier|janv|fevrier|février|fevr|févr|mars|avril|mai|juin|juillet|"
    "aout|août|septembre|sept|octobre|oct|novembre|nov|decembre|décembre|dec"
)
FRENCH_MONTH_WORDS = [
    "janvier",
    "janv",
    "fevrier",
    "février",
    "fevr",
    "févr",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "aout",
    "août",
    "septembre",
    "sept",
    "octobre",
    "oct",
    "novembre",
    "nov",
    "decembre",
    "décembre",
    "dec",
]


@dataclass(frozen=True)
class BackendAnnotation:
    text: str
    tag: str
    start_char: int
    end_char: int


@dataclass(frozen=True)
class BackendDeidentifyResult:
    deidentified_text: str
    placeholder_references: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class BackendAnalysisResult:
    text: str
    annotations: tuple[BackendAnnotation, ...]
    masked_email_replacements: dict[str, str]

    def restore_text(self, text: str) -> str:
        return _restore_masked_emails(text, self.masked_email_replacements)


def _backend_cache_dir() -> Path:
    if sys.platform == "darwin":
        cache_root = Path.home() / "Library" / "Caches"
    elif os.name == "nt":
        cache_root = Path(
            os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        )
    else:
        cache_root = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))

    cache_dir = cache_root / "open-anonymizer" / "belgian_deduce"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _dedupe_nonempty(items: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()

    for item in items:
        stripped = item.strip()
        if not stripped:
            continue

        normalized = stripped.casefold()
        if normalized in seen:
            continue

        seen.add(normalized)
        deduped.append(stripped)

    return deduped


@lru_cache(maxsize=1)
def _base_config_json() -> str:
    config_path = importlib_resources.files("belgian_deduce").joinpath("base_config.json")
    return config_path.read_text(encoding="utf-8")


def build_backend_config(recognition_flags: RecognitionFlags) -> dict[str, Any]:
    config = json.loads(_base_config_json())
    _add_french_date_annotators(config)

    config["annotators"] = {
        annotator_name: annotator_spec
        for annotator_name, annotator_spec in config["annotators"].items()
        if _annotator_enabled(annotator_name, annotator_spec, recognition_flags)
    }
    return config


def _add_french_date_annotators(config: dict[str, Any]) -> None:
    annotators = config.get("annotators")
    if not isinstance(annotators, dict):
        return

    annotators.setdefault(
        "date_dmy_2_fr",
        {
            "annotator_type": "docdeid.process.RegexpAnnotator",
            "group": "dates",
            "args": {
                "regexp_pattern": (
                    rf"(?i)(?<!\d)(([1-9]|0[1-9]|[12][0-9]|3[01])[-/\. ]{{,2}}"
                    rf"({FRENCH_MONTH_PATTERN})[-/\. ]((19|20|\\'|`)?\d{{2}}))(?!\d)"
                ),
                "tag": "datum",
                "capturing_group": 1,
                "pre_match_words": FRENCH_MONTH_WORDS,
            },
        },
    )
    annotators.setdefault(
        "date_ymd_2_fr",
        {
            "annotator_type": "docdeid.process.RegexpAnnotator",
            "group": "dates",
            "args": {
                "regexp_pattern": (
                    rf"(?i)(?<!\d)(((19|20|\\'|`)\d{{2}})[-/\. ]{{,2}}"
                    rf"({FRENCH_MONTH_PATTERN})[-/\. ]([1-9]|0[1-9]|[12][0-9]|3[01]))(?!\d)"
                ),
                "tag": "datum",
                "capturing_group": 1,
                "pre_match_words": FRENCH_MONTH_WORDS,
            },
        },
    )


def _annotator_enabled(
    annotator_name: str,
    annotator_spec: dict[str, Any],
    recognition_flags: RecognitionFlags,
) -> bool:
    group_name = annotator_spec.get("group")
    if group_name is None:
        return True

    if getattr(recognition_flags, group_name):
        return True

    return annotator_name in _ANNOTATORS_TO_KEEP_WHEN_DISABLED.get(group_name, set())


def build_backend_metadata(settings: AnonymizationSettings) -> dict[str, Any] | None:
    from belgian_deduce import MetadataEntity, Person

    metadata: dict[str, Any] = {}

    patient_aliases = _dedupe_nonempty(
        [
            f"{settings.first_name} {settings.last_name}".strip(),
            settings.first_name,
            settings.last_name,
        ]
    )
    patient_first_names = settings.first_name.split() if settings.first_name.strip() else None

    if patient_first_names or settings.last_name.strip() or settings.birthdate or patient_aliases:
        metadata["patient"] = Person(
            first_names=patient_first_names or None,
            surname=settings.last_name.strip() or None,
            birth_date=settings.birthdate,
            aliases=patient_aliases or None,
        )

    entities = [
        MetadataEntity(text=name, tag="person")
        for name in _dedupe_nonempty(
            [
                variant
                for configured_name in settings.other_names
                for variant in person_metadata_variants(configured_name)
            ]
        )
    ]
    entities.extend(
        MetadataEntity(text=address, tag="location")
        for address in _dedupe_nonempty(
            [
                variant
                for configured_address in settings.custom_addresses
                for variant in address_metadata_variants(configured_address)
            ]
        )
    )

    if entities:
        metadata["entities"] = entities

    return metadata or None


def _flags_from_key(flags_key: tuple[bool, ...]) -> RecognitionFlags:
    return RecognitionFlags(**dict(zip(RECOGNITION_GROUPS, flags_key)))


@lru_cache(maxsize=64)
def _backend_model(flags_key: tuple[bool, ...]) -> Any:
    from belgian_deduce import Deduce

    return Deduce(
        load_base_config=False,
        config=build_backend_config(_flags_from_key(flags_key)),
        cache_path=_backend_cache_dir(),
    )


def release_backend_resources() -> None:
    _backend_model.cache_clear()


atexit.register(release_backend_resources)


def deidentify_text_with_references(
    text: str,
    settings: AnonymizationSettings,
) -> BackendDeidentifyResult:
    analysis = analyze_text(text, settings)
    deidentified_text, placeholder_references = _render_backend_output(analysis)
    return BackendDeidentifyResult(
        deidentified_text=analysis.restore_text(deidentified_text),
        placeholder_references=placeholder_references,
    )


def deidentify_text(text: str, settings: AnonymizationSettings) -> str:
    return deidentify_text_with_references(text, settings).deidentified_text


def placeholder_tag_name(tag: str) -> str:
    return PLACEHOLDER_TAG_NAMES.get(tag, tag.upper())


def analyze_text(
    text: str,
    settings: AnonymizationSettings,
) -> BackendAnalysisResult:
    masked_text, masked_emails = _mask_disabled_emails(text, settings.recognition_flags)
    document = _backend_model(settings.recognition_flags.as_key()).deidentify(
        masked_text,
        metadata=build_backend_metadata(settings),
        disabled={"redactor"},
    )
    annotations = tuple(
        BackendAnnotation(
            text=annotation.text,
            tag=annotation.tag,
            start_char=annotation.start_char,
            end_char=annotation.end_char,
        )
        for annotation in sorted(
            document.annotations,
            key=lambda item: (item.start_char, item.end_char, item.tag),
        )
    )
    annotations = _filtered_annotations(masked_text, annotations)
    return BackendAnalysisResult(
        text=masked_text,
        annotations=annotations,
        masked_email_replacements=masked_emails,
    )


def _filtered_annotations(
    text: str,
    annotations: tuple[BackendAnnotation, ...],
) -> tuple[BackendAnnotation, ...]:
    return tuple(
        annotation
        for annotation in annotations
        if not _is_spurious_person_article(annotation, text)
    )


def _is_spurious_person_article(annotation: BackendAnnotation, text: str) -> bool:
    if annotation.tag not in {"person", "persoon"}:
        return False

    if annotation.text.casefold() not in {"de", "den", "der", "la", "le", "les"}:
        return False

    trailing_text = text[annotation.end_char : annotation.end_char + 24].casefold()
    return trailing_text.lstrip().startswith("patient")


def _render_backend_output(
    analysis: BackendAnalysisResult,
) -> tuple[str, dict[str, tuple[str, ...]]]:
    annotations = analysis.annotations
    annotation_replacements = _annotation_replacements(annotations)

    parts: list[str] = []
    placeholder_values: dict[str, list[str]] = defaultdict(list)
    current_position = 0

    for annotation in annotations:
        parts.append(analysis.text[current_position : annotation.start_char])
        replacement = annotation_replacements[annotation]
        parts.append(replacement)
        if annotation.text not in placeholder_values[replacement]:
            placeholder_values[replacement].append(annotation.text)
        current_position = annotation.end_char

    parts.append(analysis.text[current_position:])

    return "".join(parts), {
        placeholder: tuple(values)
        for placeholder, values in placeholder_values.items()
    }


def _annotation_replacements(
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
            if tag == "patient":
                replacements[annotation] = "[PATIENT]"
                continue

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


def _mask_disabled_emails(
    text: str,
    recognition_flags: RecognitionFlags,
) -> tuple[str, dict[str, str]]:
    if recognition_flags.email_addresses:
        return text, {}

    replacements: dict[str, str] = {}

    def replace_match(match: re.Match[str]) -> str:
        original = match.group(0)
        prefix = f"{EMAIL_MASK_PREFIX}{len(replacements)}"
        if len(prefix) >= len(original):
            token = prefix[: len(original)]
        else:
            token = prefix + ("X" * (len(original) - len(prefix)))
        replacements[token] = match.group(0)
        return token

    return EMAIL_PATTERN.sub(replace_match, text), replacements


def _restore_masked_emails(text: str, replacements: dict[str, str]) -> str:
    for token, original_value in replacements.items():
        text = text.replace(token, original_value)
    return text
