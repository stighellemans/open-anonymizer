from __future__ import annotations

from html import escape
from html.parser import HTMLParser
import re
from datetime import date, datetime

import deduce

from open_anonymizer.models import ImportedDocument, PatientContext, ProcessedDocument

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


def _replace_literals(text: str, literals: list[str], replacement: str) -> str:
    for literal in sorted({item.strip() for item in literals if item.strip()}, key=len, reverse=True):
        text = _build_literal_pattern(literal).sub(replacement, text)
    return text


def apply_guaranteed_cleanup(text: str, patient_context: PatientContext) -> str:
    name_literals: list[str] = []
    if patient_context.first_name and patient_context.last_name:
        name_literals.append(f"{patient_context.first_name} {patient_context.last_name}")
    if patient_context.first_name:
        name_literals.append(patient_context.first_name)
    if patient_context.last_name:
        name_literals.append(patient_context.last_name)

    cleaned = _replace_literals(text, name_literals, "<PATIENT>")
    cleaned = re.sub(r"(?:<PATIENT>\s+){2,}<PATIENT>", "<PATIENT>", cleaned)

    if patient_context.birthdate:
        cleaned = _replace_literals(cleaned, list(build_birthdate_variants(patient_context.birthdate)), "<DATE>")

    return cleaned


def _deidentify_text(
    text: str,
    patient_context: PatientContext,
    *,
    normalize: bool,
) -> tuple[str, list[str]]:
    if not text or not text.strip():
        return text, []

    source_text = normalize_whitespace(text) if normalize else text
    if not source_text.strip():
        return text, []

    patient_first_name = patient_context.first_name.strip()
    patient_last_name = patient_context.last_name.strip()

    annotated = deduce.annotate_text(
        source_text,
        patient_first_names=patient_first_name,
        patient_initials=patient_first_name[:1],
        patient_surname=patient_last_name,
        patient_given_name=patient_first_name.split(" ")[0] if patient_first_name else "",
    )
    deidentified = deduce.deidentify_annotations(annotated)
    cleaned = apply_guaranteed_cleanup(deidentified, patient_context)

    warnings: list[str] = []
    if cleaned != deidentified:
        warnings.append("Guaranteed cleanup replaced literals that remained after backend processing.")

    return cleaned, warnings


def _deidentify_html_fragment(text: str, patient_context: PatientContext) -> tuple[str, list[str]]:
    if not text or not text.strip():
        return text, []

    match = re.match(r"^(\s*)(.*?)(\s*)$", text, flags=re.DOTALL)
    if not match:
        return _deidentify_text(text, patient_context, normalize=False)

    leading, core, trailing = match.groups()
    if not core:
        return text, []

    cleaned, warnings = _deidentify_text(core, patient_context, normalize=False)
    return f"{leading}{cleaned}{trailing}", warnings


def _serialize_html_tag(
    tag: str,
    attrs: list[tuple[str, str | None]],
    patient_context: PatientContext,
    *,
    self_closing: bool,
) -> tuple[str, list[str]]:
    warnings: list[str] = []
    serialized_parts = [f"<{tag}"]

    for name, value in attrs:
        if value is None:
            serialized_parts.append(f" {name}")
            continue

        if name in HTML_TEXT_ATTRIBUTE_NAMES:
            value, attr_warnings = _deidentify_html_fragment(value, patient_context)
            warnings.extend(attr_warnings)

        serialized_parts.append(f' {name}="{escape(value, quote=True)}"')

    serialized_parts.append(" />" if self_closing else ">")
    return "".join(serialized_parts), warnings


class _HtmlDeidentifier(HTMLParser):
    def __init__(self, patient_context: PatientContext):
        super().__init__(convert_charrefs=False)
        self.patient_context = patient_context
        self.parts: list[str] = []
        self.warnings: list[str] = []
        self._raw_text_tag_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        serialized, warnings = _serialize_html_tag(
            tag,
            attrs,
            self.patient_context,
            self_closing=False,
        )
        self.parts.append(serialized)
        self.warnings.extend(warnings)
        if tag in HTML_RAW_TEXT_TAGS:
            self._raw_text_tag_stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        serialized, warnings = _serialize_html_tag(
            tag,
            attrs,
            self.patient_context,
            self_closing=True,
        )
        self.parts.append(serialized)
        self.warnings.extend(warnings)

    def handle_endtag(self, tag: str) -> None:
        self.parts.append(f"</{tag}>")
        if self._raw_text_tag_stack and self._raw_text_tag_stack[-1] == tag:
            self._raw_text_tag_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._raw_text_tag_stack:
            self.parts.append(data)
            return

        cleaned, warnings = _deidentify_html_fragment(data, self.patient_context)
        self.parts.append(escape(cleaned, quote=False))
        self.warnings.extend(warnings)

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
    return bool(document.path and document.path.suffix.lower() in HTML_FILE_SUFFIXES)


def deidentify_document(document: ImportedDocument, patient_context: PatientContext) -> ProcessedDocument:
    if not document.raw_text or not document.raw_text.strip():
        raise ProcessingError("No text content found to de-identify.")

    if _is_html_document(document):
        parser = _HtmlDeidentifier(patient_context)
        parser.feed(document.raw_text)
        parser.close()
        warnings = list(dict.fromkeys(parser.warnings))
        return ProcessedDocument(
            document_id=document.id,
            output_text=parser.get_output(),
            warnings=warnings,
        )

    cleaned, warnings = _deidentify_text(document.raw_text, patient_context, normalize=True)
    return ProcessedDocument(document_id=document.id, output_text=cleaned, warnings=warnings)
