from __future__ import annotations

from PySide6.QtCore import QPoint, QSettings, QSize, Qt
from PySide6.QtGui import QColor, QIntValidator, QPaintEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from open_anonymizer.models import (
    AnonymizationSettings,
    RECOGNITION_GROUPS,
    RecognitionFlags,
)
from open_anonymizer.services.configured_matching import (
    parse_address_components,
    parse_person_components,
)
from open_anonymizer.services.deidentifier import ProcessingError, parse_birthdate
from open_anonymizer.services.smart_pseudonymizer import (
    effective_date_shift_days,
    format_date_shift_days,
)


SETTINGS_ORGANIZATION = "Open Anonymizer"
SETTINGS_APPLICATION = "Open Anonymizer"
SETTINGS_PREFIX = "anonymization"
MODE_LABELS = {
    "placeholders": "Placeholders",
    "smart_pseudonyms": "Smart placeholders",
}
MODE_HINTS = {
    "placeholders": "Uses bracketed tags like [PATIENT], [DATE-1], and [PERSON-1].",
    "smart_pseudonyms": (
        "Uses stable fake names, institutions, and shifted dates."
    ),
}
RECOGNITION_LABELS = {
    "names": "Names",
    "locations": "Locations",
    "institutions": "Institutions",
    "dates": "Dates",
    "ages": "Ages",
    "identifiers": "Identifiers",
    "phone_numbers": "Phone numbers",
    "email_addresses": "Email addresses",
    "urls": "URLs",
}


def anonymization_qsettings() -> QSettings:
    return QSettings(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        SETTINGS_ORGANIZATION,
        SETTINGS_APPLICATION,
    )


def _dedupe_lines(raw_text: str) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()

    for line in raw_text.splitlines():
        stripped = line.strip()
        if stripped[:1] in {"-", "*", "•"}:
            stripped = stripped[1:].strip()
        if not stripped:
            continue

        normalized = stripped.casefold()
        if normalized in seen:
            continue

        seen.add(normalized)
        lines.append(stripped)

    return lines


def _dedupe_values(values: list[str]) -> list[str]:
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

    return deduped


def _saved_mode(value: str) -> str:
    return value if value in MODE_LABELS else "placeholders"


def load_saved_anonymization_settings() -> AnonymizationSettings:
    settings = anonymization_qsettings()
    birthdate_value = settings.value(f"{SETTINGS_PREFIX}/birthdate", "", str) or ""
    raw_date_shift = settings.value(f"{SETTINGS_PREFIX}/date_shift_days", "", str) or ""

    try:
        birthdate = parse_birthdate(birthdate_value)
    except ProcessingError:
        birthdate = None

    try:
        date_shift_days = int(raw_date_shift.strip()) if raw_date_shift.strip() else None
    except ValueError:
        date_shift_days = None

    recognition_flags = RecognitionFlags(
        **{
            name: settings.value(
                f"{SETTINGS_PREFIX}/recognition/{name}",
                True,
                bool,
            )
            for name in RECOGNITION_GROUPS
        }
    )

    return AnonymizationSettings(
        first_name=(settings.value(f"{SETTINGS_PREFIX}/first_name", "", str) or "").strip(),
        last_name=(settings.value(f"{SETTINGS_PREFIX}/last_name", "", str) or "").strip(),
        birthdate=birthdate,
        date_shift_days=date_shift_days,
        other_names=_dedupe_lines(
            settings.value(f"{SETTINGS_PREFIX}/other_names", "", str) or ""
        ),
        custom_addresses=_dedupe_lines(
            settings.value(f"{SETTINGS_PREFIX}/custom_addresses", "", str) or ""
        ),
        deidentify_filenames=settings.value(
            f"{SETTINGS_PREFIX}/deidentify_filenames",
            True,
            bool,
        ),
        mode=_saved_mode(
            settings.value(
                f"{SETTINGS_PREFIX}/mode",
                "placeholders",
                str,
            )
            or "placeholders"
        ),
        recognition_flags=recognition_flags,
    )


def save_anonymization_settings(anonymization_settings: AnonymizationSettings) -> None:
    settings = anonymization_qsettings()
    settings.setValue(f"{SETTINGS_PREFIX}/first_name", anonymization_settings.first_name)
    settings.setValue(f"{SETTINGS_PREFIX}/last_name", anonymization_settings.last_name)
    settings.setValue(
        f"{SETTINGS_PREFIX}/birthdate",
        anonymization_settings.birthdate.isoformat()
        if anonymization_settings.birthdate
        else "",
    )
    settings.setValue(
        f"{SETTINGS_PREFIX}/date_shift_days",
        ""
        if anonymization_settings.date_shift_days is None
        else str(anonymization_settings.date_shift_days),
    )
    settings.setValue(
        f"{SETTINGS_PREFIX}/other_names",
        "\n".join(anonymization_settings.other_names),
    )
    settings.setValue(
        f"{SETTINGS_PREFIX}/custom_addresses",
        "\n".join(anonymization_settings.custom_addresses),
    )
    settings.setValue(
        f"{SETTINGS_PREFIX}/deidentify_filenames",
        anonymization_settings.deidentify_filenames,
    )
    settings.setValue(f"{SETTINGS_PREFIX}/mode", anonymization_settings.mode)

    for name in RECOGNITION_GROUPS:
        settings.setValue(
            f"{SETTINGS_PREFIX}/recognition/{name}",
            getattr(anonymization_settings.recognition_flags, name),
        )

    settings.sync()


class ToggleSwitch(QCheckBox):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedSize(self.sizeHint())

    def sizeHint(self) -> QSize:
        return QSize(44, 28)

    def hitButton(self, pos: QPoint) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, event: QPaintEvent) -> None:
        del event

        track_color = QColor("#0a84ff") if self.isChecked() else QColor("#d1d5db")
        border_color = QColor("#0a84ff") if self.isChecked() else QColor("#c7cdd4")

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)

        track_rect = self.rect().adjusted(1, 2, -1, -2)
        painter.setBrush(track_color)
        painter.drawRoundedRect(track_rect, track_rect.height() / 2, track_rect.height() / 2)

        if not self.isChecked():
            painter.setPen(QPen(border_color, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                track_rect,
                track_rect.height() / 2,
                track_rect.height() / 2,
            )
            painter.setPen(Qt.PenStyle.NoPen)

        knob_diameter = track_rect.height() - 4
        knob_y = track_rect.top() + 2
        knob_x = (
            track_rect.right() - knob_diameter - 2
            if self.isChecked()
            else track_rect.left() + 2
        )
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(knob_x, knob_y, knob_diameter, knob_diameter)

        if self.hasFocus():
            focus_pen = QPen(QColor("#93c5fd"), 2)
            focus_pen.setCosmetic(True)
            painter.setPen(focus_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            focus_rect = self.rect().adjusted(0, 1, 0, -1)
            painter.drawRoundedRect(
                focus_rect,
                focus_rect.height() / 2,
                focus_rect.height() / 2,
            )

        painter.end()


class PersonEntryRow(QWidget):
    def __init__(
        self,
        *,
        first_name: str = "",
        last_name: str = "",
        on_remove=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._on_remove = on_remove

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.first_name_input = QLineEdit()
        self.first_name_input.setPlaceholderText("First name")
        self.last_name_input = QLineEdit()
        self.last_name_input.setPlaceholderText("Last name")
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(lambda checked=False: self._handle_remove())

        layout.addWidget(self.first_name_input, 1)
        layout.addWidget(self.last_name_input, 1)
        layout.addWidget(self.remove_button)

        self.first_name_input.setText(first_name)
        self.last_name_input.setText(last_name)

    def value(self) -> str:
        return " ".join(
            part
            for part in [
                self.first_name_input.text().strip(),
                self.last_name_input.text().strip(),
            ]
            if part
        )

    def _handle_remove(self) -> None:
        if self._on_remove is not None:
            self._on_remove(self)


class AddressEntryRow(QWidget):
    def __init__(
        self,
        *,
        street: str = "",
        number: str = "",
        postal_code: str = "",
        city: str = "",
        on_remove=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._on_remove = on_remove

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.street_input = QLineEdit()
        self.street_input.setPlaceholderText("Street")
        self.number_input = QLineEdit()
        self.number_input.setPlaceholderText("Number")
        self.number_input.setFixedWidth(110)
        self.postal_code_input = QLineEdit()
        self.postal_code_input.setPlaceholderText("Postal code")
        self.postal_code_input.setFixedWidth(120)
        self.city_input = QLineEdit()
        self.city_input.setPlaceholderText("City")
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(lambda checked=False: self._handle_remove())

        layout.addWidget(self.street_input, 2)
        layout.addWidget(self.number_input)
        layout.addWidget(self.postal_code_input)
        layout.addWidget(self.city_input, 1)
        layout.addWidget(self.remove_button)

        self.street_input.setText(street)
        self.number_input.setText(number)
        self.postal_code_input.setText(postal_code)
        self.city_input.setText(city)

    def value(self) -> str:
        street_line = " ".join(
            part
            for part in [
                self.street_input.text().strip(),
                self.number_input.text().strip(),
            ]
            if part
        )
        locality_line = " ".join(
            part
            for part in [
                self.postal_code_input.text().strip(),
                self.city_input.text().strip(),
            ]
            if part
        )
        if street_line and locality_line:
            return f"{street_line}, {locality_line}"
        return street_line or locality_line

    def _handle_remove(self) -> None:
        if self._on_remove is not None:
            self._on_remove(self)


class AnonymizationDialog(QDialog):
    def __init__(
        self,
        anonymization_settings: AnonymizationSettings,
        preview_document_key: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._accepted_settings: AnonymizationSettings | None = None
        self._checkboxes: dict[str, QCheckBox] = {}
        self.other_person_rows: list[PersonEntryRow] = []
        self.address_rows: list[AddressEntryRow] = []
        self.preview_document_key = preview_document_key

        self.setWindowTitle("Customize anonymization")
        self.resize(700, 480)
        self.setMinimumSize(620, 360)
        self._build_ui()
        self._load_settings(anonymization_settings)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content.setObjectName("dialogBody")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)
        scroll_area.setWidget(content)
        layout.addWidget(scroll_area, 1)

        content_layout.addWidget(self._section_title("Options"))
        options_layout = QVBoxLayout()
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.setSpacing(0)

        self.smart_mode_toggle = ToggleSwitch()
        self.smart_mode_toggle.setToolTip(MODE_HINTS["smart_pseudonyms"])
        self.smart_mode_toggle.toggled.connect(self._handle_smart_date_shift_state_changed)
        options_layout.addWidget(
            self._option_row(
                MODE_LABELS["smart_pseudonyms"],
                "Use realistic replacements for names, institutions, and dates.",
                self.smart_mode_toggle,
            )
        )
        self.date_shift_days_input = QLineEdit()
        self.date_shift_days_input.setObjectName("compactNumberInput")
        self.date_shift_days_input.setValidator(
            QIntValidator(-36500, 36500, self.date_shift_days_input)
        )
        self.date_shift_days_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.date_shift_days_input.setPlaceholderText("Auto")
        self.date_shift_days_input.setToolTip(
            "Optional signed day shift. Leave blank to keep automatic shifting."
        )
        self.date_shift_days_input.setFixedWidth(88)
        self.date_shift_days_input.textChanged.connect(self._update_date_shift_input_state)
        self.smart_date_shift_row = self._smart_date_shift_row()
        options_layout.addWidget(self.smart_date_shift_row)
        options_layout.addWidget(self._row_divider())

        self.deidentify_filenames_toggle = ToggleSwitch()
        self.deidentify_filenames_toggle.setChecked(True)
        options_layout.addWidget(
            self._option_row(
                "De-identify exported filenames",
                "Rename exported files with anonymized names.",
                self.deidentify_filenames_toggle,
            )
        )
        content_layout.addLayout(options_layout)
        content_layout.addWidget(self._section_divider())

        content_layout.addWidget(self._section_title("General recognition"))
        recognition_layout = QGridLayout()
        recognition_layout.setContentsMargins(0, 0, 0, 0)
        recognition_layout.setHorizontalSpacing(18)
        recognition_layout.setVerticalSpacing(8)

        for index, name in enumerate(RECOGNITION_GROUPS):
            checkbox = QCheckBox(RECOGNITION_LABELS[name])
            self._checkboxes[name] = checkbox
            recognition_layout.addWidget(checkbox, index // 3, index % 3)

        for column in range(3):
            recognition_layout.setColumnStretch(column, 1)

        content_layout.addLayout(recognition_layout)
        content_layout.addWidget(self._section_divider())

        content_layout.addWidget(self._section_title("Hide specific information"))
        patient_layout = QGridLayout()
        patient_layout.setContentsMargins(0, 0, 0, 0)
        patient_layout.setHorizontalSpacing(12)
        patient_layout.setVerticalSpacing(8)

        self.first_name_input = QLineEdit()
        self.first_name_input.textChanged.connect(self._update_date_shift_input_state)
        self.last_name_input = QLineEdit()
        self.last_name_input.textChanged.connect(self._update_date_shift_input_state)
        self.birthdate_input = QLineEdit()
        self.birthdate_input.setPlaceholderText("DD/MM/YYYY")
        self.birthdate_input.textChanged.connect(self.clear_error)
        self.birthdate_input.textChanged.connect(self._update_date_shift_input_state)
        patient_layout.addWidget(self._field_label("First name patient"), 0, 0)
        patient_layout.addWidget(self._field_label("Last name patient"), 0, 1)
        patient_layout.addWidget(self._field_label("Birthdate patient"), 0, 2)
        patient_layout.addWidget(self.first_name_input, 1, 0)
        patient_layout.addWidget(self.last_name_input, 1, 1)
        patient_layout.addWidget(self.birthdate_input, 1, 2)
        patient_layout.setColumnStretch(0, 1)
        patient_layout.setColumnStretch(1, 1)
        patient_layout.setColumnStretch(2, 1)
        content_layout.addLayout(patient_layout)

        content_layout.addWidget(self._field_label("Other people"))
        self.other_people_list = QWidget()
        self.other_people_layout = QVBoxLayout(self.other_people_list)
        self.other_people_layout.setContentsMargins(0, 0, 0, 0)
        self.other_people_layout.setSpacing(8)
        content_layout.addWidget(self.other_people_list)

        self.add_other_person_button = QPushButton("Add other person")
        self.add_other_person_button.clicked.connect(
            lambda checked=False: self._add_other_person_row()
        )
        content_layout.addWidget(
            self.add_other_person_button,
            0,
            Qt.AlignmentFlag.AlignLeft,
        )

        content_layout.addWidget(self._field_label("Addresses"))
        self.address_list = QWidget()
        self.address_layout = QVBoxLayout(self.address_list)
        self.address_layout.setContentsMargins(0, 0, 0, 0)
        self.address_layout.setSpacing(8)
        content_layout.addWidget(self.address_list)

        self.add_address_button = QPushButton("Add address")
        self.add_address_button.clicked.connect(
            lambda checked=False: self._add_address_row()
        )
        content_layout.addWidget(
            self.add_address_button,
            0,
            Qt.AlignmentFlag.AlignLeft,
        )

        content_layout.addStretch(1)

        self.setStyleSheet(
            """
            QDialog {
                background: #ffffff;
            }
            QScrollArea {
                border: none;
                background: transparent;
            }
            QWidget#dialogBody {
                background: transparent;
            }
            QLabel#sectionTitle {
                color: #111827;
                font-size: 12px;
                font-weight: 600;
            }
            QLabel#fieldLabel {
                color: #4b5563;
                font-size: 12px;
                font-weight: 500;
            }
            QLabel#optionTitle {
                color: #111827;
                font-size: 13px;
                font-weight: 600;
            }
            QLabel#optionDescription {
                color: #6b7280;
                font-size: 12px;
            }
            QLineEdit#compactNumberInput {
                border-radius: 8px;
                padding: 6px 8px;
            }
            QFrame#sectionDivider {
                background: #e5e7eb;
                min-height: 1px;
                max-height: 1px;
            }
            QFrame#rowDivider {
                background: #eef2f7;
                min-height: 1px;
                max-height: 1px;
            }
            QLineEdit {
                background: #ffffff;
                border: 1px solid #d1d5db;
                border-radius: 10px;
                color: #111827;
                padding: 7px 10px;
            }
            QLineEdit:focus {
                border-color: #111827;
            }
            QCheckBox {
                color: #111827;
                spacing: 8px;
            }
            QPushButton {
                background: #ffffff;
                border: 1px solid #d1d5db;
                border-radius: 10px;
                color: #111827;
                min-height: 32px;
                padding: 0 14px;
            }
            QPushButton:hover {
                background: #f9fafb;
            }
            QPushButton#primaryButton {
                background: #111827;
                border-color: #111827;
                color: #f9fafb;
            }
            QPushButton#primaryButton:hover {
                background: #1f2937;
            }
            """
        )

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #b91c1c;")
        self.error_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.error_label)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Save
        )
        self.cancel_button = button_box.button(QDialogButtonBox.StandardButton.Cancel)
        self.cancel_button.setObjectName("secondaryButton")
        self.save_button = button_box.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setObjectName("primaryButton")
        button_box.rejected.connect(self.reject)
        button_box.accepted.connect(self.handle_save)
        layout.addWidget(button_box)

    def _field_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    def _section_divider(self) -> QFrame:
        divider = QFrame()
        divider.setObjectName("sectionDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFrameShadow(QFrame.Shadow.Plain)
        return divider

    def _section_title(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    def _option_row(
        self,
        title: str,
        description: str,
        toggle: ToggleSwitch,
    ) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(10)

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("optionTitle")
        description_label = QLabel(description)
        description_label.setObjectName("optionDescription")
        description_label.setWordWrap(True)
        text_column.addWidget(title_label)
        text_column.addWidget(description_label)

        layout.addLayout(text_column, 1)
        layout.addWidget(toggle, 0, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        return row

    def _smart_date_shift_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 6)
        layout.setSpacing(8)

        label = self._field_label("Date shift")
        self.date_shift_days_suffix_label = self._field_label("days")

        layout.addWidget(label)
        layout.addWidget(self.date_shift_days_input, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.date_shift_days_suffix_label)
        layout.addStretch(1)
        return row

    def _row_divider(self) -> QFrame:
        divider = QFrame()
        divider.setObjectName("rowDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFrameShadow(QFrame.Shadow.Plain)
        return divider

    def _add_other_person_row(
        self,
        first_name: str = "",
        last_name: str = "",
    ) -> PersonEntryRow:
        row = PersonEntryRow(
            first_name=first_name,
            last_name=last_name,
            on_remove=self._remove_other_person_row,
        )
        self.other_person_rows.append(row)
        self.other_people_layout.addWidget(row)
        return row

    def _remove_other_person_row(self, row: PersonEntryRow) -> None:
        if row not in self.other_person_rows:
            return
        self.other_person_rows.remove(row)
        self.other_people_layout.removeWidget(row)
        row.deleteLater()

    def _clear_other_person_rows(self) -> None:
        for row in list(self.other_person_rows):
            self._remove_other_person_row(row)

    def _add_address_row(
        self,
        street: str = "",
        number: str = "",
        postal_code: str = "",
        city: str = "",
    ) -> AddressEntryRow:
        row = AddressEntryRow(
            street=street,
            number=number,
            postal_code=postal_code,
            city=city,
            on_remove=self._remove_address_row,
        )
        self.address_rows.append(row)
        self.address_layout.addWidget(row)
        return row

    def _remove_address_row(self, row: AddressEntryRow) -> None:
        if row not in self.address_rows:
            return
        self.address_rows.remove(row)
        self.address_layout.removeWidget(row)
        row.deleteLater()

    def _clear_address_rows(self) -> None:
        for row in list(self.address_rows):
            self._remove_address_row(row)

    def _update_smart_date_shift_visibility(self, checked: bool | None = None) -> None:
        del checked
        is_visible = self.smart_mode_toggle.isChecked()
        self.smart_date_shift_row.setVisible(is_visible)

    def _handle_smart_date_shift_state_changed(self, checked: bool) -> None:
        self._update_smart_date_shift_visibility(checked)
        self._update_date_shift_input_state()

    def _update_date_shift_input_state(self, value: str | None = None) -> None:
        del value
        if not self.smart_mode_toggle.isChecked():
            self.date_shift_days_input.setPlaceholderText("")
            self.date_shift_days_suffix_label.setText("days")
            self.date_shift_days_input.setToolTip(
                "Only used when Smart placeholders is enabled."
            )
            return

        if self.date_shift_days_input.text().strip():
            self.date_shift_days_suffix_label.setText("days")
            self.date_shift_days_input.setToolTip(
                "Filled value overrides auto date shifting."
            )
            return

        try:
            birthdate = parse_birthdate(self.birthdate_input.text())
        except ProcessingError:
            birthdate = None

        preview_settings = AnonymizationSettings(
            first_name=self.first_name_input.text().strip(),
            last_name=self.last_name_input.text().strip(),
            birthdate=birthdate,
            mode="smart_pseudonyms",
        )
        auto_shift_days, _ = effective_date_shift_days(
            preview_settings,
            document_key=self.preview_document_key,
        )
        if auto_shift_days is not None:
            self.date_shift_days_input.setPlaceholderText(
                f"Auto {auto_shift_days:+d}"
            )
            self.date_shift_days_suffix_label.setText("days")
            self.date_shift_days_input.setToolTip(
                f"Auto currently resolves to {format_date_shift_days(auto_shift_days)}."
            )
            return

        self.date_shift_days_input.setPlaceholderText("Per file")
        self.date_shift_days_suffix_label.setText("")
        self.date_shift_days_input.setToolTip(
            "Auto varies by document until patient details are configured."
        )

    def _load_settings(self, anonymization_settings: AnonymizationSettings) -> None:
        self.first_name_input.setText(anonymization_settings.first_name)
        self.last_name_input.setText(anonymization_settings.last_name)
        self.birthdate_input.setText(
            anonymization_settings.birthdate.strftime("%d/%m/%Y")
            if anonymization_settings.birthdate
            else ""
        )
        self.date_shift_days_input.setText(
            ""
            if anonymization_settings.date_shift_days is None
            else str(anonymization_settings.date_shift_days)
        )
        self._clear_other_person_rows()
        self._clear_address_rows()
        if anonymization_settings.other_names:
            for other_name in anonymization_settings.other_names:
                first_name, last_name = parse_person_components(other_name)
                self._add_other_person_row(first_name, last_name)
        else:
            self._add_other_person_row()

        if anonymization_settings.custom_addresses:
            for custom_address in anonymization_settings.custom_addresses:
                street, number, postal_code, city = parse_address_components(custom_address)
                self._add_address_row(street, number, postal_code, city)
        else:
            self._add_address_row()
        self.deidentify_filenames_toggle.setChecked(
            anonymization_settings.deidentify_filenames
        )
        self.smart_mode_toggle.setChecked(anonymization_settings.mode == "smart_pseudonyms")
        self._update_smart_date_shift_visibility()
        self._update_date_shift_input_state()

        for name, checkbox in self._checkboxes.items():
            checkbox.setChecked(getattr(anonymization_settings.recognition_flags, name))

    def clear_error(self) -> None:
        self.error_label.clear()

    def current_settings(self) -> AnonymizationSettings:
        birthdate = parse_birthdate(self.birthdate_input.text())
        date_shift_days_text = self.date_shift_days_input.text().strip()

        try:
            date_shift_days = int(date_shift_days_text) if date_shift_days_text else None
        except ValueError as exc:
            raise ProcessingError("Date shift must be a whole number of days.") from exc

        return AnonymizationSettings(
            first_name=self.first_name_input.text().strip(),
            last_name=self.last_name_input.text().strip(),
            birthdate=birthdate,
            date_shift_days=date_shift_days,
            other_names=_dedupe_values([row.value() for row in self.other_person_rows]),
            custom_addresses=_dedupe_values([row.value() for row in self.address_rows]),
            deidentify_filenames=self.deidentify_filenames_toggle.isChecked(),
            mode="smart_pseudonyms" if self.smart_mode_toggle.isChecked() else "placeholders",
            recognition_flags=RecognitionFlags(
                **{
                    name: checkbox.isChecked()
                    for name, checkbox in self._checkboxes.items()
                }
            ),
        )

    def settings(self) -> AnonymizationSettings:
        return self._accepted_settings or self.current_settings()

    def handle_save(self) -> None:
        try:
            self._accepted_settings = self.current_settings()
        except ProcessingError as exc:
            self.error_label.setText(str(exc))
            return

        self.accept()
