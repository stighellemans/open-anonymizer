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
import shutil
import sys
import threading
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
    "healthcare_institution": "INSTITUTION",
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

_backend_assets_lock = threading.Lock()
_backend_assets_instance: "_SharedBackendAssets | None" = None
_backend_models_lock = threading.Lock()
_backend_models: dict[tuple[bool, ...], Any] = {}


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


@dataclass(frozen=True)
class _SharedBackendAssets:
    lookup_data_path: Path
    cache_path: Path
    tokenizer: Any
    lookup_structs: Any


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


def _lookup_data_dir() -> Path:
    import belgian_deduce

    return Path(belgian_deduce.__file__).resolve().parent / "data" / "lookup"


def _bundled_lookup_cache_dir() -> Path | None:
    lookup_dir = _lookup_data_dir()
    if (lookup_dir / "cache" / "lookup_structs.pickle").exists():
        return lookup_dir
    return None


def _bundled_lookup_cache_file() -> Path | None:
    bundled_cache_dir = _bundled_lookup_cache_dir()
    if bundled_cache_dir is None:
        return None
    return bundled_cache_dir / "cache" / "lookup_structs.pickle"


def _user_lookup_cache_file(cache_dir: Path) -> Path:
    return cache_dir / "cache" / "lookup_structs.pickle"


def _sync_bundled_lookup_cache(cache_dir: Path) -> None:
    bundled_cache_file = _bundled_lookup_cache_file()
    if bundled_cache_file is None:
        return

    user_cache_file = _user_lookup_cache_file(cache_dir)
    if user_cache_file.exists():
        bundled_stat = bundled_cache_file.stat()
        user_stat = user_cache_file.stat()
        if (
            user_stat.st_size == bundled_stat.st_size
            and user_stat.st_mtime_ns == bundled_stat.st_mtime_ns
        ):
            return

    user_cache_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_cache_file = user_cache_file.with_suffix(f"{user_cache_file.suffix}.tmp")
    try:
        shutil.copy2(bundled_cache_file, temporary_cache_file)
        temporary_cache_file.replace(user_cache_file)
    finally:
        if temporary_cache_file.exists():
            temporary_cache_file.unlink()


def _lookup_cache_dir() -> Path:
    cache_dir = _backend_cache_dir()
    # Package timestamps can invalidate a bundled cache unexpectedly, so keep a
    # synced copy in the user cache and load from there.
    _sync_bundled_lookup_cache(cache_dir)
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


def _build_backend_assets() -> _SharedBackendAssets:
    from belgian_deduce import Deduce
    from belgian_deduce import __version__ as package_version
    from belgian_deduce.lookup_structs import get_lookup_structs

    lookup_data_path = _lookup_data_dir()
    cache_path = _lookup_cache_dir()
    tokenizer = Deduce._initialize_tokenizer(lookup_data_path)
    lookup_structs = get_lookup_structs(
        lookup_path=lookup_data_path,
        cache_path=cache_path,
        tokenizer=tokenizer,
        package_version=package_version,
    )
    return _SharedBackendAssets(
        lookup_data_path=lookup_data_path,
        cache_path=cache_path,
        tokenizer=tokenizer,
        lookup_structs=lookup_structs,
    )


def _backend_assets() -> _SharedBackendAssets:
    global _backend_assets_instance

    assets = _backend_assets_instance
    if assets is not None:
        return assets

    with _backend_assets_lock:
        assets = _backend_assets_instance
        if assets is None:
            assets = _build_backend_assets()
            _backend_assets_instance = assets
        return assets


def _build_backend_model(flags_key: tuple[bool, ...]) -> Any:
    import docdeid as dd
    from belgian_deduce import Deduce
    from belgian_deduce.deduce import _DeduceProcessorLoader

    assets = _backend_assets()
    backend = Deduce.__new__(Deduce)
    dd.DocDeid.__init__(backend)
    backend.config = Deduce._initialize_config(
        load_base_config=False,
        user_config=build_backend_config(_flags_from_key(flags_key)),
    )
    backend.lookup_data_path = assets.lookup_data_path
    backend.cache_path = assets.cache_path
    backend.tokenizers = {"default": assets.tokenizer}
    backend.lookup_structs = assets.lookup_structs
    backend.processors = _DeduceProcessorLoader().load(
        config=backend.config,
        extras={"tokenizer": assets.tokenizer, "ds": assets.lookup_structs},
    )
    return backend


def _backend_model(flags_key: tuple[bool, ...]) -> Any:
    model = _backend_models.get(flags_key)
    if model is not None:
        return model

    with _backend_models_lock:
        model = _backend_models.get(flags_key)
        if model is None:
            model = _build_backend_model(flags_key)
            _backend_models[flags_key] = model
        return model


def prime_backend_resources(
    recognition_flags: RecognitionFlags | None = None,
) -> None:
    flags = recognition_flags or RecognitionFlags()
    _backend_assets()
    _backend_model(flags.as_key())


def warm_backend_for_settings(settings: AnonymizationSettings) -> None:
    prime_backend_resources(settings.recognition_flags)


def prime_backend_resources_async(
    recognition_flags: RecognitionFlags | None = None,
) -> threading.Thread:
    thread = threading.Thread(
        target=prime_backend_resources,
        kwargs={"recognition_flags": recognition_flags},
        name="open-anonymizer-backend-warmup",
        daemon=True,
    )
    thread.start()
    return thread


def backend_is_ready(flags_key: tuple[bool, ...]) -> bool:
    with _backend_models_lock:
        return flags_key in _backend_models


def release_backend_resources() -> None:
    global _backend_assets_instance

    with _backend_models_lock:
        _backend_models.clear()
    with _backend_assets_lock:
        _backend_assets_instance = None


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
    normalized_tag = canonical_annotation_tag(tag)
    return PLACEHOLDER_TAG_NAMES.get(normalized_tag, normalized_tag.upper())


def canonical_annotation_tag(tag: str) -> str:
    return tag.strip().casefold().replace("-", "_").replace(" ", "_")


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
