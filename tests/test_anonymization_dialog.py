from PySide6.QtCore import QPoint, Qt

from datetime import date, timedelta

from open_anonymizer.models import AnonymizationSettings, RecognitionFlags
from open_anonymizer.services.smart_pseudonymizer import effective_date_shift_days
from open_anonymizer.ui.anonymization_dialog import (
    DATE_SHIFT_SAFE_MIN_DAYS,
    INFO_HINTS,
    AnonymizationDialog,
    InfoHintButton,
    _date_shift_slider_position,
    load_saved_anonymization_settings,
    save_anonymization_settings,
)


def test_save_and_load_anonymization_settings_round_trip() -> None:
    save_anonymization_settings(
        AnonymizationSettings(
            first_name="Jean",
            last_name="Dupont",
            birthdate=date(1980, 3, 12),
            date_shift_days=14,
            other_names=["Sophie Martin"],
            custom_addresses=["Rue de la Loi 12, 1000 Bruxelles"],
            deidentify_filenames=False,
            mode="smart_pseudonyms",
            recognition_flags=RecognitionFlags(dates=False, ages=False),
        )
    )

    loaded = load_saved_anonymization_settings()

    assert loaded == AnonymizationSettings(
        first_name="Jean",
        last_name="Dupont",
        birthdate=date(1980, 3, 12),
        date_shift_days=14,
        other_names=["Sophie Martin"],
        custom_addresses=["Rue de la Loi 12, 1000 Bruxelles"],
        deidentify_filenames=False,
        mode="smart_pseudonyms",
        recognition_flags=RecognitionFlags(dates=False, ages=False),
    )


def test_dialog_blocks_invalid_birthdate(qtbot) -> None:
    dialog = AnonymizationDialog(AnonymizationSettings())
    qtbot.addWidget(dialog)

    assert dialog.error_label.isHidden() is True

    dialog.birthdate_input.setText("12/31/1980")
    dialog.handle_save()

    assert dialog.result() == 0
    assert "Birthdate must use" in dialog.error_label.text()
    assert dialog.error_label.isHidden() is False


def test_dialog_hides_error_row_again_after_clearing_input(qtbot) -> None:
    dialog = AnonymizationDialog(AnonymizationSettings())
    qtbot.addWidget(dialog)

    dialog.birthdate_input.setText("12/31/1980")
    dialog.handle_save()

    assert dialog.error_label.isHidden() is False

    dialog.birthdate_input.setText("12/03/1980")

    assert dialog.error_label.text() == ""
    assert dialog.error_label.isHidden() is True


def test_dialog_uses_plain_language_for_people_and_addresses(qtbot) -> None:
    dialog = AnonymizationDialog(AnonymizationSettings())
    qtbot.addWidget(dialog)

    label_texts = {label.text() for label in dialog.findChildren(type(dialog._field_label("")))}

    assert "Which categories do you want the system to detect and anonymize?" in {
        label.text() for label in dialog.findChildren(type(dialog._section_title("")))
    }
    assert "Patient" in {
        label.text() for label in dialog.findChildren(type(dialog._section_title("")))
    }
    assert "Other information you want to anonymize" in {
        label.text() for label in dialog.findChildren(type(dialog._section_title("")))
    }
    assert "Other options" in {
        label.text() for label in dialog.findChildren(type(dialog._section_title("")))
    }
    assert (
        "What is your first name and last name you want the anonymizer to recognize?"
        in label_texts
    )
    assert "What is your birthdate?" in label_texts
    assert "Shift detected dates (days)" in label_texts
    assert "Other people" in label_texts
    assert "Addresses" in label_texts
    assert dialog.first_name_input.placeholderText() == "First name"
    assert dialog.last_name_input.placeholderText() == "Last name"
    assert dialog.other_person_rows[0].first_name_input.placeholderText() == "First name"
    assert dialog.other_person_rows[0].last_name_input.placeholderText() == "Last name"
    assert dialog.address_rows[0].street_input.placeholderText() == "Street"
    assert dialog.address_rows[0].number_input.placeholderText() == "Number"
    assert dialog.add_other_person_button.text() == "Add other person"
    assert dialog.add_address_button.text() == "Add address"
    example_texts = {
        label.text()
        for label in dialog.findChildren(type(dialog._field_label("")))
        if label.objectName() in {"smartPlaceholderExampleHeader", "smartPlaceholderExampleValue"}
    }
    assert "Original" in example_texts
    assert "Default placeholder" in example_texts
    assert "Smart placeholder" in example_texts
    assert "Marie Dupont" in example_texts
    assert "[PATIENT]" in example_texts
    assert "Sophie Martin" in example_texts
    assert "12/03/1980" in example_texts
    assert "[DATE-1]" in example_texts
    shifted_date_label = dialog.findChild(type(dialog._field_label("")), "smartPlaceholderExampleShiftedDate")
    assert shifted_date_label is not None
    assert "(" in shifted_date_label.text()
    assert ")" in shifted_date_label.text()
    filename_example_texts = {
        label.text()
        for label in dialog.findChildren(type(dialog._field_label("")))
        if label.objectName() in {"filenameExampleHeader", "filenameExampleValue"}
    }
    assert "Original" in filename_example_texts
    assert "Default placeholder" in filename_example_texts
    assert "Smart placeholder" in filename_example_texts
    assert "Jean_Dupont_report.txt" in filename_example_texts
    assert "8f3a91c2de_deid.txt" in filename_example_texts
    assert "12-03-1980_lab_results.pdf" in filename_example_texts
    assert "4b72c1a8f0_deid.pdf" in filename_example_texts


def test_dialog_exposes_info_hints_for_key_titles(qtbot) -> None:
    dialog = AnonymizationDialog(AnonymizationSettings())
    qtbot.addWidget(dialog)

    expected_buttons = {
        "smartPlaceholdersInfoButton": INFO_HINTS["smart_pseudonyms"],
        "deidentifyFilenamesInfoButton": INFO_HINTS["deidentify_filenames"],
        "generalRecognitionInfoButton": INFO_HINTS["general_recognition"],
        "hideSpecificInformationInfoButton": INFO_HINTS["specific_information"],
    }

    for object_name, tooltip in expected_buttons.items():
        info_button = dialog.findChild(InfoHintButton, object_name)

        assert info_button is not None
        assert info_button.hint_text() == tooltip
        assert "max-width" in info_button.toolTip()


def test_info_hint_button_click_shows_wrapped_tooltip(qtbot, monkeypatch) -> None:
    dialog = AnonymizationDialog(AnonymizationSettings())
    qtbot.addWidget(dialog)
    dialog.show()

    info_button = dialog.findChild(InfoHintButton, "generalRecognitionInfoButton")
    show_calls: list[tuple[str, object | None]] = []

    assert info_button is not None

    def capture_show_text(position, text, widget=None, *args) -> None:
        del position, args
        show_calls.append((text, widget))

    monkeypatch.setattr(
        "open_anonymizer.ui.anonymization_dialog.QToolTip.showText",
        capture_show_text,
    )

    qtbot.mouseClick(info_button, Qt.MouseButton.LeftButton)

    assert show_calls
    assert INFO_HINTS["general_recognition"] in show_calls[-1][0]
    assert "max-width" in show_calls[-1][0]
    assert show_calls[-1][1] is info_button


def test_dialog_loads_legacy_strings_into_structured_rows(qtbot) -> None:
    dialog = AnonymizationDialog(
        AnonymizationSettings(
            other_names=["Martin, Sophie"],
            custom_addresses=["12 Rue de la Loi, 1000 Bruxelles"],
        )
    )
    qtbot.addWidget(dialog)

    assert dialog.other_person_rows[0].first_name_input.text() == "Sophie"
    assert dialog.other_person_rows[0].last_name_input.text() == "Martin"
    assert dialog.address_rows[0].street_input.text() == "Rue de la Loi"
    assert dialog.address_rows[0].number_input.text() == "12"
    assert dialog.address_rows[0].postal_code_input.text() == "1000"
    assert dialog.address_rows[0].city_input.text() == "Bruxelles"


def test_dialog_collects_structured_people_and_addresses(qtbot) -> None:
    dialog = AnonymizationDialog(AnonymizationSettings())
    qtbot.addWidget(dialog)

    dialog.first_name_input.setText("Jean")
    dialog.last_name_input.setText("Dupont")
    dialog.birthdate_input.setText("12/03/1980")
    dialog.other_person_rows[0].first_name_input.setText("Sophie")
    dialog.other_person_rows[0].last_name_input.setText("Martin")
    dialog.address_rows[0].street_input.setText("Rue de la Loi")
    dialog.address_rows[0].number_input.setText("12")
    dialog.address_rows[0].postal_code_input.setText("1000")
    dialog.address_rows[0].city_input.setText("Bruxelles")

    settings = dialog.current_settings()

    assert settings.first_name == "Jean"
    assert settings.last_name == "Dupont"
    assert settings.other_names == ["Sophie Martin"]
    assert settings.custom_addresses == ["Rue de la Loi 12, 1000 Bruxelles"]


def test_dialog_add_buttons_append_rows(qtbot) -> None:
    dialog = AnonymizationDialog(AnonymizationSettings())
    qtbot.addWidget(dialog)

    assert len(dialog.other_person_rows) == 1
    assert len(dialog.address_rows) == 1

    dialog.add_other_person_button.click()
    dialog.add_address_button.click()

    assert len(dialog.other_person_rows) == 2
    assert len(dialog.address_rows) == 2


def test_dialog_exposes_smart_placeholder_toggle(qtbot) -> None:
    dialog = AnonymizationDialog(
        AnonymizationSettings(
            mode="smart_pseudonyms",
            date_shift_days=21,
        )
    )
    qtbot.addWidget(dialog)

    assert dialog.smart_mode_toggle.isChecked() is True
    assert dialog.smart_date_shift_row.isHidden() is False
    assert dialog.date_shift_days_slider.days() == 21
    assert dialog.date_shift_days_slider.findChild(type(dialog._field_label("")), "dateShiftBubble").text() == "+21"
    shifted_date_label = dialog.findChild(type(dialog._field_label("")), "smartPlaceholderExampleShiftedDate")
    assert shifted_date_label is not None
    assert shifted_date_label.text() == "02/04/1980 (+21 days)"

    dialog.smart_mode_toggle.setChecked(False)

    assert dialog.current_settings().mode == "placeholders"
    assert dialog.current_settings().date_shift_days == 21
    assert dialog.smart_date_shift_row.isHidden() is True
    assert dialog.smart_mode_toggle.toolTip().startswith("Uses stable fake names")


def test_dialog_shows_resolved_auto_date_shift_slider_value(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "open_anonymizer.services.smart_pseudonymizer._local_secret_bytes",
        lambda: b"\x66" * 32,
    )

    dialog = AnonymizationDialog(
        AnonymizationSettings(
            first_name="Jean",
            last_name="Dupont",
            birthdate=date(1980, 3, 12),
            mode="smart_pseudonyms",
        )
    )
    qtbot.addWidget(dialog)

    auto_shift_days, _ = effective_date_shift_days(dialog.current_settings())

    assert dialog.current_settings().date_shift_days is None
    assert dialog.date_shift_days_slider.days() == auto_shift_days
    assert (
        dialog.date_shift_days_slider.findChild(
            type(dialog._field_label("")),
            "dateShiftBubble",
        ).text()
        == f"{auto_shift_days:+d}"
    )
    assert "Auto currently resolves to" in dialog.date_shift_days_slider.toolTip()


def test_dialog_shows_auto_date_shift_slider_value_without_preview_document(
    qtbot,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "open_anonymizer.services.smart_pseudonymizer._local_secret_bytes",
        lambda: b"\x22" * 32,
    )

    dialog = AnonymizationDialog(AnonymizationSettings(mode="smart_pseudonyms"))
    qtbot.addWidget(dialog)

    auto_shift_days, _ = effective_date_shift_days(dialog.current_settings())

    assert dialog.current_settings().date_shift_days is None
    assert dialog.date_shift_days_slider.days() == auto_shift_days
    assert "Auto currently resolves to" in dialog.date_shift_days_slider.toolTip()


def test_dialog_marks_date_shift_as_manual_after_slider_change(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "open_anonymizer.services.smart_pseudonymizer._local_secret_bytes",
        lambda: b"\x33" * 32,
    )

    dialog = AnonymizationDialog(AnonymizationSettings(mode="smart_pseudonyms"))
    qtbot.addWidget(dialog)

    dialog.date_shift_days_slider.set_days(84)

    assert dialog.current_settings().date_shift_days == 84
    assert "Custom date shift override" in dialog.date_shift_days_slider.toolTip()
    shifted_date_label = dialog.findChild(type(dialog._field_label("")), "smartPlaceholderExampleShiftedDate")
    assert shifted_date_label is not None
    expected_date = (date(1980, 3, 12) + timedelta(days=84)).strftime("%d/%m/%Y")
    assert shifted_date_label.text() == f"{expected_date} (+84 days)"


def test_date_shift_slider_skips_directly_over_unsafe_center_gap() -> None:
    assert (
        _date_shift_slider_position(-DATE_SHIFT_SAFE_MIN_DAYS) + 1
        == _date_shift_slider_position(DATE_SHIFT_SAFE_MIN_DAYS)
    )


def test_dialog_enables_filename_deidentification_by_default(qtbot) -> None:
    dialog = AnonymizationDialog(AnonymizationSettings())
    qtbot.addWidget(dialog)

    assert dialog.deidentify_filenames_toggle.isChecked() is True

    dialog.deidentify_filenames_toggle.setChecked(False)

    assert dialog.current_settings().deidentify_filenames is False


def test_dialog_uses_compact_scrollable_layout(qtbot) -> None:
    dialog = AnonymizationDialog(AnonymizationSettings())
    qtbot.addWidget(dialog)

    assert dialog.height() <= 480
    assert dialog.minimumHeight() < dialog.sizeHint().height()


def test_toggle_switch_is_clickable_across_entire_track(qtbot) -> None:
    dialog = AnonymizationDialog(AnonymizationSettings(mode="placeholders"))
    qtbot.addWidget(dialog)
    dialog.show()

    toggle = dialog.smart_mode_toggle
    qtbot.mouseClick(
        toggle,
        Qt.MouseButton.LeftButton,
        pos=QPoint(toggle.width() - 3, toggle.height() // 2),
    )

    assert toggle.isChecked() is True
