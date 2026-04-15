from __future__ import annotations

import re


TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)
NAME_TEXT_SEPARATOR_PATTERN = r"(?:[\s,.'’`-]+)"
ADDRESS_TEXT_SEPARATOR_PATTERN = r"(?:[\W_]+)"
FILENAME_SEPARATOR_PATTERN = r"[\s,._/-]+"
POSTAL_CODE_PATTERN = re.compile(r"\d{4,5}")
SURNAME_PARTICLES = {
    "al",
    "bin",
    "da",
    "de",
    "del",
    "della",
    "den",
    "der",
    "di",
    "du",
    "el",
    "la",
    "le",
    "st",
    "ter",
    "ten",
    "van",
    "vande",
    "vanden",
    "von",
}


def person_metadata_variants(value: str) -> tuple[str, ...]:
    stripped = _normalize_spacing(value)
    if not stripped:
        return ()

    variants = [stripped]
    given_tokens, family_tokens = _person_name_parts(stripped)
    if given_tokens and family_tokens:
        variants.extend(
            [
                " ".join(given_tokens + family_tokens),
                " ".join(family_tokens + given_tokens),
                f"{' '.join(family_tokens)}, {' '.join(given_tokens)}",
            ]
        )

    return _dedupe_strings(variants)


def parse_person_components(value: str) -> tuple[str, str]:
    stripped = _normalize_spacing(value)
    if not stripped:
        return "", ""

    given_tokens, family_tokens = _person_name_parts(stripped)
    if given_tokens and family_tokens:
        return " ".join(given_tokens), " ".join(family_tokens)

    return stripped, ""


def address_metadata_variants(value: str) -> tuple[str, ...]:
    stripped = _normalize_spacing(value)
    if not stripped:
        return ()

    variants = [stripped]
    for tokens in _address_token_sequences(stripped):
        variants.append(" ".join(tokens))

    return _dedupe_strings(variants)


def parse_address_components(value: str) -> tuple[str, str, str, str]:
    stripped = _normalize_spacing(value)
    if not stripped:
        return "", "", "", ""

    tokens = tuple(_tokens(stripped))
    if not tokens:
        return "", "", "", ""

    street_tokens, house_number_tokens, locality_tokens = _address_parts(tokens)
    postal_code = ""
    city_tokens: tuple[str, ...] = ()
    if locality_tokens:
        if POSTAL_CODE_PATTERN.fullmatch(locality_tokens[0]):
            postal_code = locality_tokens[0]
            city_tokens = locality_tokens[1:]
        else:
            city_tokens = locality_tokens

    return (
        " ".join(street_tokens),
        " ".join(house_number_tokens),
        postal_code,
        " ".join(city_tokens),
    )


def person_text_patterns(value: str) -> tuple[re.Pattern[str], ...]:
    return _compile_patterns(
        _person_token_sequences(value),
        NAME_TEXT_SEPARATOR_PATTERN,
        left_boundary=r"(?<!\w)",
        right_boundary=r"(?!\w)",
    )


def person_filename_patterns(value: str) -> tuple[re.Pattern[str], ...]:
    return _compile_patterns(
        _person_token_sequences(value),
        FILENAME_SEPARATOR_PATTERN,
        left_boundary=r"(?<![^\W_])",
        right_boundary=r"(?![^\W_])",
    )


def address_text_patterns(value: str) -> tuple[re.Pattern[str], ...]:
    return _compile_patterns(
        _address_token_sequences(value),
        ADDRESS_TEXT_SEPARATOR_PATTERN,
        left_boundary=r"(?<!\w)",
        right_boundary=r"(?!\w)",
    )


def address_filename_patterns(value: str) -> tuple[re.Pattern[str], ...]:
    return _compile_patterns(
        _address_token_sequences(value),
        FILENAME_SEPARATOR_PATTERN,
        left_boundary=r"(?<![^\W_])",
        right_boundary=r"(?![^\W_])",
    )


def _person_token_sequences(value: str) -> tuple[tuple[str, ...], ...]:
    stripped = _normalize_spacing(value)
    tokens = tuple(_tokens(stripped))
    if not tokens:
        return ()

    sequences = [tokens]
    given_tokens, family_tokens = _person_name_parts(stripped)
    if given_tokens and family_tokens:
        sequences.extend(
            [
                given_tokens + family_tokens,
                family_tokens + given_tokens,
            ]
        )

    return _dedupe_token_sequences(sequences)


def _person_name_parts(value: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if "," in value:
        family_part, given_part = value.split(",", 1)
        family_tokens = tuple(_tokens(family_part))
        given_tokens = tuple(_tokens(given_part))
        if family_tokens and given_tokens:
            return given_tokens, family_tokens

    tokens = tuple(_tokens(value))
    if len(tokens) < 2:
        return (), ()

    surname_start = len(tokens) - 1
    while surname_start > 1 and tokens[surname_start - 1].casefold() in SURNAME_PARTICLES:
        surname_start -= 1

    given_tokens = tokens[:surname_start]
    family_tokens = tokens[surname_start:]
    if not given_tokens or not family_tokens:
        return (), ()

    return given_tokens, family_tokens


def _address_token_sequences(value: str) -> tuple[tuple[str, ...], ...]:
    tokens = tuple(_tokens(value))
    if not tokens:
        return ()

    sequences = [tokens]
    street_tokens, house_number_tokens, locality_tokens = _address_parts(tokens)
    if house_number_tokens and street_tokens:
        sequences.extend(
            [
                street_tokens + house_number_tokens + locality_tokens,
                house_number_tokens + street_tokens + locality_tokens,
            ]
        )
        if locality_tokens:
            sequences.extend(
                [
                    street_tokens + house_number_tokens,
                    house_number_tokens + street_tokens,
                ]
            )

    return _dedupe_token_sequences(sequences)


def _address_parts(
    tokens: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    if len(tokens) < 2:
        return tokens, (), ()

    postal_code_index = next(
        (
            index
            for index, token in enumerate(tokens)
            if index < len(tokens) - 1 and POSTAL_CODE_PATTERN.fullmatch(token)
        ),
        None,
    )
    street_segment = tokens[:postal_code_index] if postal_code_index is not None else tokens
    locality_tokens = tokens[postal_code_index:] if postal_code_index is not None else ()

    if len(street_segment) < 2:
        return street_segment, (), locality_tokens

    if _is_house_number_token(street_segment[0]) and any(
        _has_letters(token) for token in street_segment[1:]
    ):
        return street_segment[1:], (street_segment[0],), locality_tokens

    if _is_house_number_token(street_segment[-1]) and any(
        _has_letters(token) for token in street_segment[:-1]
    ):
        return street_segment[:-1], (street_segment[-1],), locality_tokens

    return street_segment, (), locality_tokens


def _compile_patterns(
    sequences: tuple[tuple[str, ...], ...],
    separator_pattern: str,
    *,
    left_boundary: str,
    right_boundary: str,
) -> tuple[re.Pattern[str], ...]:
    patterns: list[re.Pattern[str]] = []

    for tokens in sorted(sequences, key=lambda item: (len(item), item), reverse=True):
        body = separator_pattern.join(re.escape(token) for token in tokens)
        patterns.append(
            re.compile(
                rf"{left_boundary}{body}{right_boundary}",
                re.IGNORECASE,
            )
        )

    return tuple(patterns)


def _dedupe_token_sequences(
    sequences: list[tuple[str, ...]],
) -> tuple[tuple[str, ...], ...]:
    deduped: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()

    for sequence in sequences:
        if not sequence:
            continue

        normalized = tuple(token.casefold() for token in sequence)
        if normalized in seen:
            continue

        seen.add(normalized)
        deduped.append(sequence)

    return tuple(deduped)


def _dedupe_strings(values: list[str]) -> tuple[str, ...]:
    deduped: list[str] = []
    seen: set[str] = set()

    for value in values:
        stripped = value.strip()
        if not stripped:
            continue

        normalized = stripped.casefold()
        if normalized in seen:
            continue

        seen.add(normalized)
        deduped.append(stripped)

    return tuple(deduped)


def _tokens(value: str) -> list[str]:
    return TOKEN_PATTERN.findall(value)


def _normalize_spacing(value: str) -> str:
    return " ".join(value.split())


def _has_letters(token: str) -> bool:
    return any(character.isalpha() for character in token)


def _is_house_number_token(token: str) -> bool:
    return any(character.isdigit() for character in token)
