from __future__ import annotations

from html import escape
from html.parser import HTMLParser
import re
from datetime import date, datetime
import hashlib

from open_anonymizer.models import (
    AnonymizationSettings,
    ImportedDocument,
    ProcessedDocument,
)
from open_anonymizer.services.configured_matching import (
    address_filename_patterns,
    address_text_patterns,
    person_filename_patterns,
    person_text_patterns,
)
from open_anonymizer.services.deduce_backend import (
    deidentify_text_with_references as backend_deidentify_text_with_references,
)
from open_anonymizer.services.smart_pseudonymizer import SmartPseudonymizer


MONTH_VARIANTS = {
    1: ("january", "jan", "janvier", "janv", "januari"),
    2: ("february", "feb", "fevrier", "février", "fevr", "févr", "februari"),
    3: ("march", "mar", "mars", "maart", "mrt"),
    4: ("april", "apr", "avril"),
    5: ("may", "mai", "mei"),
    6: ("june", "jun", "juin", "juni"),
    7: ("july", "jul", "juillet", "juli"),
    8: ("august", "aug", "aout", "août", "augustus"),
    9: ("september", "sep", "septembre", "sept"),
    10: ("october", "oct", "octobre", "oktober", "okt"),
    11: ("november", "nov", "novembre"),
    12: ("december", "dec", "decembre", "décembre"),
}
SUPPORTED_BIRTHDATE_FORMATS = (
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
)
HTML_FILE_SUFFIXES = {".html", ".htm"}
HTML_RAW_TEXT_TAGS = {"script", "style"}
HTML_TEXT_ATTRIBUTE_NAMES = {"alt", "aria-description", "aria-label", "placeholder", "title"}
GUARANTEED_CLEANUP_WARNING = (
    "Guaranteed cleanup replaced configured literals that remained after backend processing."
)
PDF_PAGE_BREAK_TOKEN = "OPENANONYMIZERPAGEBREAKTOKEN"
FILENAME_LITERAL_TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)


class ProcessingError(Exception):
    pass


def normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]

    compacted: list[str] = []
    blank_run = 0
    for line in lines:
        if line:
            blank_run = 0
            compacted.append(line)
            continue

        if blank_run == 0:
            compacted.append("")
        blank_run += 1

    return "\n".join(compacted).strip()


def parse_birthdate(value: str) -> date | None:
    stripped = value.strip()
    if not stripped:
        return None

    for fmt in SUPPORTED_BIRTHDATE_FORMATS:
        try:
            return datetime.strptime(stripped, fmt).date()
        except ValueError:
            continue

    raise ProcessingError("Birthdate must use DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY, or YYYY-MM-DD.")


def build_birthdate_variants(birthdate: date) -> set[str]:
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

    for month_name in MONTH_VARIANTS[month]:
        for day_value in day_options:
            for year_value in year_options:
                variants.add(f"{day_value} {month_name} {year_value}")
                variants.add(f"{day_value} {month_name}. {year_value}")

    return variants


def _build_literal_pattern(literal: str) -> re.Pattern[str]:
    body = r"\s+".join(re.escape(part) for part in literal.split())
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


def _build_filename_literal_pattern(literal: str) -> re.Pattern[str]:
    tokens = FILENAME_LITERAL_TOKEN_PATTERN.findall(literal)
    if tokens:
        body = r"[\s._/-]+".join(re.escape(token) for token in tokens)
    else:
        body = re.escape(literal)
    return re.compile(rf"(?<![^\W_]){body}(?![^\W_])", re.IGNORECASE)


def _replace_literals(
    text: str,
    literals: list[str],
    replacement: str,
) -> tuple[str, list[str]]:
    matches: list[str] = []

    for literal in sorted({item.strip() for item in literals if item.strip()}, key=len, reverse=True):
        pattern = _build_literal_pattern(literal)

        def replace_match(match: re.Match[str]) -> str:
            matches.append(match.group(0))
            return replacement

        text = pattern.sub(replace_match, text)

    return text, matches


def _replace_patterns(
    text: str,
    patterns: tuple[re.Pattern[str], ...],
    replacement: str,
) -> tuple[str, list[str]]:
    matches: list[str] = []

    for pattern in patterns:
        def replace_match(match: re.Match[str]) -> str:
            matches.append(match.group(0))
            return replacement

        text = pattern.sub(replace_match, text)

    return text, matches


def _replace_filename_literals(
    text: str,
    literals: list[str],
    replacement: str,
) -> tuple[str, list[str]]:
    matches: list[str] = []

    for literal in sorted({item.strip() for item in literals if item.strip()}, key=len, reverse=True):
        pattern = _build_filename_literal_pattern(literal)

        def replace_match(match: re.Match[str]) -> str:
            matches.append(match.group(0))
            return replacement

        text = pattern.sub(replace_match, text)

    return text, matches


def _next_numbered_placeholder(text: str, tag_name: str) -> str:
    indices = [
        int(match.group(1))
        for match in re.finditer(rf"\[{re.escape(tag_name)}-(\d+)\]", text)
    ]
    return f"[{tag_name}-{max(indices, default=0) + 1}]"


def _configured_patient_literals(
    anonymization_settings: AnonymizationSettings,
) -> list[str]:
    return [
        literal
        for literal in [
            f"{anonymization_settings.first_name} {anonymization_settings.last_name}".strip(),
            anonymization_settings.first_name,
            anonymization_settings.last_name,
        ]
        if literal
    ]


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


def _append_placeholder_references(
    target: dict[str, list[str]],
    placeholder: str,
    values: list[str],
) -> None:
    if not values:
        return

    existing = target.setdefault(placeholder, [])
    for value in values:
        if value not in existing:
            existing.append(value)


def apply_guaranteed_cleanup(
    text: str,
    anonymization_settings: AnonymizationSettings,
) -> tuple[str, bool, dict[str, tuple[str, ...]]]:
    changed = False
    cleanup_references: dict[str, list[str]] = {}

    cleaned, replaced = _replace_literals(
        text,
        _configured_patient_literals(anonymization_settings),
        "[PATIENT]",
    )
    _append_placeholder_references(cleanup_references, "[PATIENT]", replaced)
    changed = changed or bool(replaced)

    if anonymization_settings.birthdate:
        date_placeholder = _next_numbered_placeholder(cleaned, "DATE")
        cleaned, replaced = _replace_literals(
            cleaned,
            list(build_birthdate_variants(anonymization_settings.birthdate)),
            date_placeholder,
        )
        _append_placeholder_references(cleanup_references, date_placeholder, replaced)
        changed = changed or bool(replaced)

    for other_name in anonymization_settings.other_names:
        person_placeholder = _next_numbered_placeholder(cleaned, "PERSON")
        cleaned, replaced = _replace_patterns(
            cleaned,
            person_text_patterns(other_name),
            person_placeholder,
        )
        _append_placeholder_references(cleanup_references, person_placeholder, replaced)
        changed = changed or bool(replaced)

    for custom_address in anonymization_settings.custom_addresses:
        location_placeholder = _next_numbered_placeholder(cleaned, "LOCATION")
        cleaned, replaced = _replace_patterns(
            cleaned,
            address_text_patterns(custom_address),
            location_placeholder,
        )
        _append_placeholder_references(cleanup_references, location_placeholder, replaced)
        changed = changed or bool(replaced)

    return cleaned, changed, _merge_placeholder_references(cleanup_references)


def apply_guaranteed_filename_cleanup(
    text: str,
    anonymization_settings: AnonymizationSettings,
) -> tuple[str, bool, dict[str, tuple[str, ...]]]:
    changed = False
    cleanup_references: dict[str, list[str]] = {}

    cleaned, replaced = _replace_filename_literals(
        text,
        _configured_patient_literals(anonymization_settings),
        "[PATIENT]",
    )
    _append_placeholder_references(cleanup_references, "[PATIENT]", replaced)
    changed = changed or bool(replaced)

    if anonymization_settings.birthdate:
        date_placeholder = _next_numbered_placeholder(cleaned, "DATE")
        cleaned, replaced = _replace_filename_literals(
            cleaned,
            list(build_birthdate_variants(anonymization_settings.birthdate)),
            date_placeholder,
        )
        _append_placeholder_references(cleanup_references, date_placeholder, replaced)
        changed = changed or bool(replaced)

    for other_name in anonymization_settings.other_names:
        person_placeholder = _next_numbered_placeholder(cleaned, "PERSON")
        cleaned, replaced = _replace_patterns(
            cleaned,
            person_filename_patterns(other_name),
            person_placeholder,
        )
        _append_placeholder_references(cleanup_references, person_placeholder, replaced)
        changed = changed or bool(replaced)

    for custom_address in anonymization_settings.custom_addresses:
        location_placeholder = _next_numbered_placeholder(cleaned, "LOCATION")
        cleaned, replaced = _replace_patterns(
            cleaned,
            address_filename_patterns(custom_address),
            location_placeholder,
        )
        _append_placeholder_references(cleanup_references, location_placeholder, replaced)
        changed = changed or bool(replaced)

    return cleaned, changed, _merge_placeholder_references(cleanup_references)


def _deidentify_text(
    text: str,
    anonymization_settings: AnonymizationSettings,
    *,
    normalize: bool,
    smart_pseudonymizer: SmartPseudonymizer | None = None,
) -> tuple[str, list[str], dict[str, tuple[str, ...]]]:
    if not text or not text.strip():
        return text, [], {}

    source_text = normalize_whitespace(text) if normalize else text
    if not source_text.strip():
        return text, [], {}

    if smart_pseudonymizer is not None:
        return smart_pseudonymizer.deidentify_text(source_text)

    backend_result = backend_deidentify_text_with_references(
        source_text,
        anonymization_settings,
    )
    cleaned, replaced_configured_literals, cleanup_references = apply_guaranteed_cleanup(
        backend_result.deidentified_text,
        anonymization_settings,
    )

    warnings: list[str] = []
    if replaced_configured_literals:
        warnings.append(GUARANTEED_CLEANUP_WARNING)

    return cleaned, warnings, _merge_placeholder_references(
        backend_result.placeholder_references,
        cleanup_references,
    )


def _deidentify_html_fragment(
    text: str,
    anonymization_settings: AnonymizationSettings,
    *,
    smart_pseudonymizer: SmartPseudonymizer | None = None,
) -> tuple[str, list[str], dict[str, tuple[str, ...]]]:
    if not text or not text.strip():
        return text, [], {}

    match = re.match(r"^(\s*)(.*?)(\s*)$", text, flags=re.DOTALL)
    if not match:
        return _deidentify_text(
            text,
            anonymization_settings,
            normalize=False,
            smart_pseudonymizer=smart_pseudonymizer,
        )

    leading, core, trailing = match.groups()
    if not core:
        return text, [], {}

    cleaned, warnings, placeholder_references = _deidentify_text(
        core,
        anonymization_settings,
        normalize=False,
        smart_pseudonymizer=smart_pseudonymizer,
    )
    return f"{leading}{cleaned}{trailing}", warnings, placeholder_references


def _join_pdf_page_texts(page_texts: list[str]) -> str:
    return "\n\n".join(page for page in page_texts if page.strip()).strip()


def _deidentify_pdf_document(
    document: ImportedDocument,
    anonymization_settings: AnonymizationSettings,
    *,
    smart_pseudonymizer: SmartPseudonymizer | None = None,
) -> ProcessedDocument:
    page_texts = [page.text for page in document.pdf_pages]
    combined_text = f"\n\n{PDF_PAGE_BREAK_TOKEN}\n\n".join(page_texts)
    cleaned, warnings, placeholder_references = _deidentify_text(
        combined_text,
        anonymization_settings,
        normalize=True,
        smart_pseudonymizer=smart_pseudonymizer,
    )

    processed_page_texts = [page.strip() for page in cleaned.split(PDF_PAGE_BREAK_TOKEN)]
    if len(processed_page_texts) != len(page_texts):
        processed_page_texts = []
        cleaned_output = cleaned.replace(PDF_PAGE_BREAK_TOKEN, "\n\n").strip()
    else:
        cleaned_output = _join_pdf_page_texts(processed_page_texts)

    return ProcessedDocument(
        document_id=document.id,
        output_text=cleaned_output,
        pdf_page_texts=processed_page_texts,
        placeholder_references=placeholder_references,
        warnings=warnings,
    )


def _serialize_html_tag(
    tag: str,
    attrs: list[tuple[str, str | None]],
    anonymization_settings: AnonymizationSettings,
    *,
    self_closing: bool,
    smart_pseudonymizer: SmartPseudonymizer | None = None,
) -> tuple[str, list[str], dict[str, tuple[str, ...]]]:
    warnings: list[str] = []
    placeholder_references: dict[str, tuple[str, ...]] = {}
    serialized_parts = [f"<{tag}"]

    for name, value in attrs:
        if value is None:
            serialized_parts.append(f" {name}")
            continue

        if name in HTML_TEXT_ATTRIBUTE_NAMES:
            value, attr_warnings, attr_references = _deidentify_html_fragment(
                value,
                anonymization_settings,
                smart_pseudonymizer=smart_pseudonymizer,
            )
            warnings.extend(attr_warnings)
            placeholder_references = _merge_placeholder_references(
                placeholder_references,
                attr_references,
            )

        serialized_parts.append(f' {name}="{escape(value, quote=True)}"')

    serialized_parts.append(" />" if self_closing else ">")
    return "".join(serialized_parts), warnings, placeholder_references


class _HtmlDeidentifier(HTMLParser):
    def __init__(
        self,
        anonymization_settings: AnonymizationSettings,
        *,
        smart_pseudonymizer: SmartPseudonymizer | None = None,
    ):
        super().__init__(convert_charrefs=False)
        self.anonymization_settings = anonymization_settings
        self.smart_pseudonymizer = smart_pseudonymizer
        self.parts: list[str] = []
        self.warnings: list[str] = []
        self.placeholder_references: dict[str, tuple[str, ...]] = {}
        self._raw_text_tag_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        serialized, warnings, placeholder_references = _serialize_html_tag(
            tag,
            attrs,
            self.anonymization_settings,
            self_closing=False,
            smart_pseudonymizer=self.smart_pseudonymizer,
        )
        self.parts.append(serialized)
        self.warnings.extend(warnings)
        self.placeholder_references = _merge_placeholder_references(
            self.placeholder_references,
            placeholder_references,
        )
        if tag in HTML_RAW_TEXT_TAGS:
            self._raw_text_tag_stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        serialized, warnings, placeholder_references = _serialize_html_tag(
            tag,
            attrs,
            self.anonymization_settings,
            self_closing=True,
            smart_pseudonymizer=self.smart_pseudonymizer,
        )
        self.parts.append(serialized)
        self.warnings.extend(warnings)
        self.placeholder_references = _merge_placeholder_references(
            self.placeholder_references,
            placeholder_references,
        )

    def handle_endtag(self, tag: str) -> None:
        self.parts.append(f"</{tag}>")
        if self._raw_text_tag_stack and self._raw_text_tag_stack[-1] == tag:
            self._raw_text_tag_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._raw_text_tag_stack:
            self.parts.append(data)
            return

        cleaned, warnings, placeholder_references = _deidentify_html_fragment(
            data,
            self.anonymization_settings,
            smart_pseudonymizer=self.smart_pseudonymizer,
        )
        self.parts.append(escape(cleaned, quote=False))
        self.warnings.extend(warnings)
        self.placeholder_references = _merge_placeholder_references(
            self.placeholder_references,
            placeholder_references,
        )

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self.parts.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self.parts.append(f"<!{decl}>")

    def unknown_decl(self, data: str) -> None:
        self.parts.append(f"<!{data}>")

    def handle_pi(self, data: str) -> None:
        self.parts.append(f"<?{data}>")

    def get_output(self) -> str:
        return "".join(self.parts)


def _is_html_document(document: ImportedDocument) -> bool:
    return document.source_kind == "html"

def _normalize_filename_for_deidentification(value: str) -> str:
    return re.sub(r"(?<!\d)[._-]+|[._-]+(?!\d)", " ", value)


def _filename_hash_source(
    value: str,
    *,
    document: ImportedDocument | None = None,
) -> str:
    if document is not None:
        if document.raw_text:
            return document.raw_text
        if document.display_name:
            return document.display_name
    return value


def hashed_deidentified_filename_stem(
    value: str,
    *,
    document: ImportedDocument | None = None,
) -> str:
    digest = hashlib.sha256(_filename_hash_source(value, document=document).encode("utf-8"))
    return f"{digest.hexdigest()[:10]}_deid"


def deidentify_filename_stem_result(
    value: str,
    anonymization_settings: AnonymizationSettings,
    *,
    document: ImportedDocument | None = None,
) -> tuple[str, bool]:
    if not value or not value.strip():
        return value, False

    normalized_value = _normalize_filename_for_deidentification(value)
    smart_pseudonymizer = (
        SmartPseudonymizer(anonymization_settings)
        if anonymization_settings.mode == "smart_pseudonyms" and document is not None
        else None
    )
    cleaned, _, placeholder_references = _deidentify_text(
        normalized_value,
        anonymization_settings,
        normalize=False,
        smart_pseudonymizer=smart_pseudonymizer,
    )
    cleaned, _, filename_cleanup_references = apply_guaranteed_filename_cleanup(
        cleaned,
        anonymization_settings,
    )
    placeholder_references = _merge_placeholder_references(
        placeholder_references,
        filename_cleanup_references,
    )
    return (
        normalize_whitespace(cleaned).replace("\n", " ").strip(),
        bool(placeholder_references),
    )


def deidentify_filename_stem(
    value: str,
    anonymization_settings: AnonymizationSettings,
    *,
    document: ImportedDocument | None = None,
) -> str:
    cleaned, _ = deidentify_filename_stem_result(
        value,
        anonymization_settings,
        document=document,
    )
    return cleaned


def deidentify_document(
    document: ImportedDocument,
    anonymization_settings: AnonymizationSettings,
) -> ProcessedDocument:
    if not document.raw_text or not document.raw_text.strip():
        raise ProcessingError("No text content found to de-identify.")

    smart_pseudonymizer = (
        SmartPseudonymizer(anonymization_settings)
        if anonymization_settings.mode == "smart_pseudonyms"
        else None
    )

    if document.source_kind == "pdf" and document.pdf_pages:
        return _deidentify_pdf_document(
            document,
            anonymization_settings,
            smart_pseudonymizer=smart_pseudonymizer,
        )

    if _is_html_document(document):
        parser = _HtmlDeidentifier(
            anonymization_settings,
            smart_pseudonymizer=smart_pseudonymizer,
        )
        parser.feed(document.raw_text)
        parser.close()
        warnings = list(dict.fromkeys(parser.warnings))
        return ProcessedDocument(
            document_id=document.id,
            output_text=parser.get_output(),
            placeholder_references=parser.placeholder_references,
            warnings=warnings,
        )

    cleaned, warnings, placeholder_references = _deidentify_text(
        document.raw_text,
        anonymization_settings,
        normalize=True,
        smart_pseudonymizer=smart_pseudonymizer,
    )
    return ProcessedDocument(
        document_id=document.id,
        output_text=cleaned,
        placeholder_references=placeholder_references,
        warnings=warnings,
    )
