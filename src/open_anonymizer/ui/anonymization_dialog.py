from __future__ import annotations

from html import escape

from PySide6.QtCore import QPoint, QSettings, QSignalBlocker, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPaintEvent, QPainter, QPen
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
    QSlider,
    QStyle,
    QStyleOptionSlider,
    QToolButton,
    QToolTip,
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
INFO_HINTS = {
    "smart_pseudonyms": (
        "Keeps the text readable by swapping real details for consistent fake ones. "
        "Example: 'Marie Dupont' becomes 'Sophie Martin' instead of '[PATIENT]'."
    ),
    "deidentify_filenames": (
        "Turn this on when the filename itself might contain personal information, "
        "like a patient's name."
    ),
    "general_recognition": "Turn off the categories you do not want to hide.",
    "specific_information": (
        "Add personal information here when you want to make sure it gets removed."
    ),
}
DATE_SHIFT_SAFE_MIN_DAYS = 10
DATE_SHIFT_MAX_ABS_DAYS = 104 * 7
_DATE_SHIFT_NEGATIVE_STEP_COUNT = (
    DATE_SHIFT_MAX_ABS_DAYS - DATE_SHIFT_SAFE_MIN_DAYS + 1
)
DATE_SHIFT_SLIDER_MAX = (_DATE_SHIFT_NEGATIVE_STEP_COUNT * 2) - 1


def _format_tooltip_html(text: str) -> str:
    escaped_text = escape(text).replace("\n", "<br/>")
    return (
        "<div style='max-width: 260px; white-space: normal; line-height: 1.35;'>"
        f"{escaped_text}"
        "</div>"
    )


def _coerce_safe_date_shift_days(days: int | None) -> int | None:
    if days is None:
        return None
    if days == 0:
        return None

    bounded_days = max(-DATE_SHIFT_MAX_ABS_DAYS, min(DATE_SHIFT_MAX_ABS_DAYS, days))
    if -DATE_SHIFT_SAFE_MIN_DAYS < bounded_days < DATE_SHIFT_SAFE_MIN_DAYS:
        return (
            -DATE_SHIFT_SAFE_MIN_DAYS
            if bounded_days < 0
            else DATE_SHIFT_SAFE_MIN_DAYS
        )
    return bounded_days


def _date_shift_slider_position(days: int) -> int:
    safe_days = _coerce_safe_date_shift_days(days)
    if safe_days is None:
        raise ValueError("Date shift slider requires a non-zero day shift.")
    if safe_days < 0:
        return safe_days + DATE_SHIFT_MAX_ABS_DAYS
    return _DATE_SHIFT_NEGATIVE_STEP_COUNT + (safe_days - DATE_SHIFT_SAFE_MIN_DAYS)


def _date_shift_days_from_slider_position(position: int) -> int:
    bounded_position = max(0, min(DATE_SHIFT_SLIDER_MAX, position))
    if bounded_position < _DATE_SHIFT_NEGATIVE_STEP_COUNT:
        return -DATE_SHIFT_MAX_ABS_DAYS + bounded_position
    return DATE_SHIFT_SAFE_MIN_DAYS + (
        bounded_position - _DATE_SHIFT_NEGATIVE_STEP_COUNT
    )


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
    date_shift_days = _coerce_safe_date_shift_days(date_shift_days)

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


class InfoHintButton(QToolButton):
    def __init__(self, tooltip: str, parent=None) -> None:
        super().__init__(parent)
        self._hint_text = tooltip
        self.setText("i")
        self.setToolTip(_format_tooltip_html(tooltip))
        self.setAutoRaise(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setProperty("infoHintButton", True)
        self.setFixedSize(16, 16)
        self.clicked.connect(self._show_tooltip_now)

    def hint_text(self) -> str:
        return self._hint_text

    def _show_tooltip_now(self) -> None:
        tooltip_pos = self.mapToGlobal(QPoint(self.width() + 6, self.height() // 2))
        QToolTip.showText(tooltip_pos, self.toolTip(), self)


class DateShiftSlider(QWidget):
    valueChanged = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setToolTip("")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._slider_wrap = QWidget()
        self._slider_wrap.setMinimumHeight(36)
        slider_layout = QVBoxLayout(self._slider_wrap)
        slider_layout.setContentsMargins(0, 12, 0, 0)
        slider_layout.setSpacing(0)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setObjectName("dateShiftSlider")
        self._slider.setRange(0, DATE_SHIFT_SLIDER_MAX)
        self._slider.setSingleStep(1)
        self._slider.setPageStep(28)
        self._slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self._slider.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._slider.setFixedWidth(220)
        self._slider.valueChanged.connect(self._handle_value_changed)
        slider_layout.addWidget(self._slider)

        self._bubble_label = QLabel(self._slider_wrap)
        self._bubble_label.setObjectName("dateShiftBubble")
        self._bubble_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(self._slider_wrap)

        limits_layout = QHBoxLayout()
        limits_layout.setContentsMargins(0, 0, 0, 0)
        limits_layout.setSpacing(0)
        self._lower_limit_label = QLabel(f"{-DATE_SHIFT_MAX_ABS_DAYS:+d}")
        self._lower_limit_label.setObjectName("dateShiftLimitLabel")
        self._upper_limit_label = QLabel(f"{DATE_SHIFT_MAX_ABS_DAYS:+d}")
        self._upper_limit_label.setObjectName("dateShiftLimitLabel")
        limits_layout.addWidget(self._lower_limit_label)
        limits_layout.addStretch(1)
        limits_layout.addWidget(self._upper_limit_label)
        layout.addLayout(limits_layout)

        self.set_days(_date_shift_days_from_slider_position(self._slider.value()))

    def value(self) -> int:
        return self._slider.value()

    def setValue(self, value: int) -> None:
        self._slider.setValue(value)

    def days(self) -> int:
        return _date_shift_days_from_slider_position(self._slider.value())

    def set_days(self, days: int) -> None:
        self._slider.setValue(_date_shift_slider_position(days))
        self._update_bubble_position()

    def setEnabled(self, enabled: bool) -> None:
        super().setEnabled(enabled)
        self._slider.setEnabled(enabled)
        self._bubble_label.setEnabled(enabled)
        self._lower_limit_label.setEnabled(enabled)
        self._upper_limit_label.setEnabled(enabled)

    def setToolTip(self, tooltip: str) -> None:
        super().setToolTip(tooltip)
        if hasattr(self, "_slider"):
            self._slider.setToolTip(tooltip)
            self._bubble_label.setToolTip(tooltip)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_bubble_position()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._update_bubble_position()

    def _handle_value_changed(self, value: int) -> None:
        self._update_bubble_position()
        self.valueChanged.emit(value)

    def _update_bubble_position(self) -> None:
        self._bubble_label.setText(f"{self.days():+d}")
        self._bubble_label.adjustSize()

        option = QStyleOptionSlider()
        self._slider.initStyleOption(option)
        handle_rect = self._slider.style().subControlRect(
            QStyle.ComplexControl.CC_Slider,
            option,
            QStyle.SubControl.SC_SliderHandle,
            self._slider,
        )
        bubble_x = (
            self._slider.geometry().x()
            + handle_rect.center().x()
            - (self._bubble_label.width() // 2)
        )
        bubble_x = max(0, min(self.width() - self._bubble_label.width(), bubble_x))
        bubble_y = max(0, self._slider.geometry().y() - self._bubble_label.height() - 2)
        self._bubble_label.move(bubble_x, bubble_y)


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
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._accepted_settings: AnonymizationSettings | None = None
        self._checkboxes: dict[str, QCheckBox] = {}
        self._date_shift_override_enabled = False
        self._syncing_date_shift_slider = False
        self.other_person_rows: list[PersonEntryRow] = []
        self.address_rows: list[AddressEntryRow] = []

        self.setWindowTitle("Customize anonymization")
        self.resize(700, 480)
        self.setMinimumSize(620, 320)
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
        scroll_area.viewport().setObjectName("dialogViewport")

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
                info_text=INFO_HINTS["smart_pseudonyms"],
                info_button_name="smartPlaceholdersInfoButton",
            )
        )
        self.date_shift_days_slider = DateShiftSlider()
        self.date_shift_days_slider.valueChanged.connect(
            self._handle_date_shift_slider_changed
        )
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
                info_text=INFO_HINTS["deidentify_filenames"],
                info_button_name="deidentifyFilenamesInfoButton",
            )
        )
        content_layout.addLayout(options_layout)
        content_layout.addWidget(self._section_divider())

        content_layout.addWidget(
            self._section_heading(
                "General recognition",
                INFO_HINTS["general_recognition"],
                info_button_name="generalRecognitionInfoButton",
            )
        )
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

        content_layout.addWidget(
            self._section_heading(
                "Hide specific information",
                INFO_HINTS["specific_information"],
                info_button_name="hideSpecificInformationInfoButton",
            )
        )
        patient_layout = QGridLayout()
        patient_layout.setContentsMargins(0, 0, 0, 0)
        patient_layout.setHorizontalSpacing(12)
        patient_layout.setVerticalSpacing(8)

        self.first_name_input = QLineEdit()
        self.first_name_input.textChanged.connect(self._update_date_shift_slider_state)
        self.last_name_input = QLineEdit()
        self.last_name_input.textChanged.connect(self._update_date_shift_slider_state)
        self.birthdate_input = QLineEdit()
        self.birthdate_input.setPlaceholderText("DD/MM/YYYY")
        self.birthdate_input.textChanged.connect(self.clear_error)
        self.birthdate_input.textChanged.connect(self._update_date_shift_slider_state)
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
                background: #ffffff;
            }
            QWidget#dialogViewport {
                background: #ffffff;
            }
            QWidget#dialogBody {
                background: #ffffff;
            }
            QToolTip {
                background: #111827;
                border: 1px solid #1f2937;
                border-radius: 7px;
                color: #f9fafb;
                font-size: 11px;
                padding: 4px 6px;
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
            QToolButton[infoHintButton="true"] {
                background: transparent;
                border: 1px solid #d1d5db;
                border-radius: 8px;
                color: #6b7280;
                font-size: 10px;
                font-weight: 700;
                padding: 0;
            }
            QToolButton[infoHintButton="true"]:hover {
                background: #f3f4f6;
                border-color: #9ca3af;
                color: #111827;
            }
            QToolButton[infoHintButton="true"]:pressed {
                background: #e5e7eb;
                border-color: #9ca3af;
                color: #111827;
            }
            QToolButton[infoHintButton="true"]:focus {
                outline: none;
            }
            QLabel#dateShiftBubble {
                background: #111827;
                border-radius: 8px;
                color: #f9fafb;
                font-size: 10px;
                font-weight: 600;
                min-height: 16px;
                padding: 0 6px;
            }
            QLabel#dateShiftLimitLabel {
                color: #9ca3af;
                font-size: 10px;
                font-weight: 500;
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
            QSlider#dateShiftSlider {
                min-height: 16px;
            }
            QSlider#dateShiftSlider::groove:horizontal {
                background: #e5e7eb;
                border-radius: 2px;
                height: 4px;
            }
            QSlider#dateShiftSlider::sub-page:horizontal {
                background: #111827;
                border-radius: 2px;
            }
            QSlider#dateShiftSlider::add-page:horizontal {
                background: #e5e7eb;
                border-radius: 2px;
            }
            QSlider#dateShiftSlider::handle:horizontal {
                background: #ffffff;
                border: 1px solid #111827;
                border-radius: 6px;
                height: 12px;
                margin: -4px 0;
                width: 12px;
            }
            QSlider#dateShiftSlider::handle:horizontal:hover {
                background: #f9fafb;
            }
            QSlider#dateShiftSlider::handle:horizontal:pressed {
                background: #f3f4f6;
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
        self.error_label.hide()
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

    def _label_with_info(
        self,
        label: QLabel,
        info_text: str,
        *,
        info_button_name: str,
    ) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        info_button = InfoHintButton(info_text)
        info_button.setObjectName(info_button_name)

        layout.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(info_button, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addStretch(1)
        return row

    def _section_heading(
        self,
        text: str,
        info_text: str,
        *,
        info_button_name: str,
    ) -> QWidget:
        return self._label_with_info(
            self._section_title(text),
            info_text,
            info_button_name=info_button_name,
        )

    def _option_row(
        self,
        title: str,
        description: str,
        toggle: ToggleSwitch,
        *,
        info_text: str | None = None,
        info_button_name: str | None = None,
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
        if info_text and info_button_name:
            text_column.addWidget(
                self._label_with_info(
                    title_label,
                    info_text,
                    info_button_name=info_button_name,
                )
            )
        else:
            text_column.addWidget(title_label)
        text_column.addWidget(description_label)

        layout.addLayout(text_column, 1)
        layout.addWidget(toggle, 0, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        return row

    def _smart_date_shift_row(self) -> QWidget:
        row = QWidget()
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 1, 0, 6)
        layout.setSpacing(4)

        layout.addWidget(self._field_label("Date shift (days)"))
        layout.addWidget(self.date_shift_days_slider, 0, Qt.AlignmentFlag.AlignLeft)
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
        self._update_date_shift_slider_state()

    def _preview_auto_date_shift_days(self) -> int | None:
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
        )
        return _coerce_safe_date_shift_days(auto_shift_days)

    def _set_date_shift_slider_days(self, days: int, *, is_manual: bool) -> None:
        safe_days = _coerce_safe_date_shift_days(days)
        if safe_days is None:
            safe_days = DATE_SHIFT_SAFE_MIN_DAYS

        self._date_shift_override_enabled = is_manual
        self._syncing_date_shift_slider = True
        blocker = QSignalBlocker(self.date_shift_days_slider)
        self.date_shift_days_slider.set_days(safe_days)
        del blocker
        self._syncing_date_shift_slider = False

    def _handle_date_shift_slider_changed(self, value: int) -> None:
        del value
        if not self._syncing_date_shift_slider:
            self._date_shift_override_enabled = True
        self._update_date_shift_slider_state()

    def _update_date_shift_slider_state(self, value: str | None = None) -> None:
        del value

        auto_shift_days = self._preview_auto_date_shift_days()
        if not self._date_shift_override_enabled and auto_shift_days is not None:
            self._set_date_shift_slider_days(auto_shift_days, is_manual=False)

        if not self.smart_mode_toggle.isChecked():
            self.date_shift_days_slider.setToolTip(
                "Only used when Smart placeholders is enabled."
            )
            return

        if self._date_shift_override_enabled:
            self.date_shift_days_slider.setToolTip(
                "Custom date shift override. The slider skips the unsafe zone between -10 and +10 days."
            )
            return

        if auto_shift_days is not None:
            self.date_shift_days_slider.setToolTip(
                f"Auto currently resolves to {format_date_shift_days(auto_shift_days)}. "
                "Moving the slider will save a custom value."
            )
            return

        self.date_shift_days_slider.setToolTip(
            "Moving the slider will save a custom date shift."
        )

    def _load_settings(self, anonymization_settings: AnonymizationSettings) -> None:
        self.first_name_input.setText(anonymization_settings.first_name)
        self.last_name_input.setText(anonymization_settings.last_name)
        self.birthdate_input.setText(
            anonymization_settings.birthdate.strftime("%d/%m/%Y")
            if anonymization_settings.birthdate
            else ""
        )
        saved_date_shift_days = _coerce_safe_date_shift_days(
            anonymization_settings.date_shift_days
        )
        auto_shift_days = self._preview_auto_date_shift_days() or DATE_SHIFT_SAFE_MIN_DAYS
        self._set_date_shift_slider_days(
            saved_date_shift_days or auto_shift_days,
            is_manual=saved_date_shift_days is not None,
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
        self._update_date_shift_slider_state()

        for name, checkbox in self._checkboxes.items():
            checkbox.setChecked(getattr(anonymization_settings.recognition_flags, name))

    def clear_error(self) -> None:
        self.error_label.clear()
        self.error_label.hide()

    def current_settings(self) -> AnonymizationSettings:
        birthdate = parse_birthdate(self.birthdate_input.text())
        date_shift_days = (
            self.date_shift_days_slider.days()
            if self._date_shift_override_enabled
            else None
        )

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
            self.error_label.show()
            return

        self.accept()
