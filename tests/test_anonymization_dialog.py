from PySide6.QtCore import QPoint, Qt

from datetime import date

from open_anonymizer.models import AnonymizationSettings, RecognitionFlags
from open_anonymizer.ui.anonymization_dialog import (
    AnonymizationDialog,
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

    dialog.birthdate_input.setText("12/31/1980")
    dialog.handle_save()

    assert dialog.result() == 0
    assert "Birthdate must use" in dialog.error_label.text()


def test_dialog_uses_plain_language_for_people_and_addresses(qtbot) -> None:
    dialog = AnonymizationDialog(AnonymizationSettings())
    qtbot.addWidget(dialog)

    label_texts = {label.text() for label in dialog.findChildren(type(dialog._field_label("")))}

    assert "General recognition" in {
        label.text() for label in dialog.findChildren(type(dialog._section_title("")))
    }
    assert "Hide specific information" in {
        label.text() for label in dialog.findChildren(type(dialog._section_title("")))
    }
    assert "First name patient" in label_texts
    assert "Last name patient" in label_texts
    assert "Birthdate patient" in label_texts
    assert "Other people" in label_texts
    assert dialog.other_person_rows[0].first_name_input.placeholderText() == "First name"
    assert dialog.other_person_rows[0].last_name_input.placeholderText() == "Last name"
    assert dialog.address_rows[0].street_input.placeholderText() == "Street"
    assert dialog.address_rows[0].number_input.placeholderText() == "Number"
    assert dialog.add_other_person_button.text() == "Add other person"
    assert dialog.add_address_button.text() == "Add address"


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
    assert dialog.date_shift_days_input.text() == "21"

    dialog.smart_mode_toggle.setChecked(False)

    assert dialog.current_settings().mode == "placeholders"
    assert dialog.current_settings().date_shift_days == 21
    assert dialog.smart_date_shift_row.isHidden() is True
    assert dialog.smart_mode_toggle.toolTip().startswith("Uses stable fake names")


def test_dialog_shows_resolved_auto_date_shift_placeholder(qtbot, monkeypatch) -> None:
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

    assert dialog.date_shift_days_input.text() == ""
    assert dialog.date_shift_days_input.placeholderText().startswith("Auto ")
    assert "Auto currently resolves to" in dialog.date_shift_days_input.toolTip()


def test_dialog_uses_preview_document_for_auto_date_shift_placeholder(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "open_anonymizer.services.smart_pseudonymizer._local_secret_bytes",
        lambda: b"\x22" * 32,
    )

    dialog = AnonymizationDialog(
        AnonymizationSettings(mode="smart_pseudonyms"),
        preview_document_key="preview-doc",
    )
    qtbot.addWidget(dialog)

    assert dialog.date_shift_days_input.placeholderText().startswith("Auto ")
    assert "Auto currently resolves to" in dialog.date_shift_days_input.toolTip()


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
