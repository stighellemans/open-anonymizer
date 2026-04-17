from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import time


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_PATH = REPO_ROOT / "docs" / "images" / "open-anonymizer-demo.png"
DEFAULT_PDF_PATH = REPO_ROOT / "build" / "readme-demo" / "open-anonymizer-demo.pdf"
DEFAULT_WINDOW_SIZE = (1160, 664)
DEFAULT_SPLITTER_SIZES = (400, 720)
LINES_PER_PAGE = 26
POLL_INTERVAL_SECONDS = 0.05

sys.path.insert(0, str(REPO_ROOT / "src"))


@dataclass(frozen=True)
class CapturePaths:
    output_png: Path
    demo_pdf: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a deterministic medical-demo PDF, load it in Open "
            "Anonymizer, and capture a README screenshot."
        )
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=(
            "Output PNG path. Relative paths are resolved from the repository "
            "root."
        ),
    )
    parser.add_argument(
        "--pdf",
        type=Path,
        help=(
            "Optional output path for the generated demo PDF. Defaults to a "
            "sibling of the PNG."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Maximum number of seconds to wait for import and processing.",
    )
    return parser.parse_args()


def resolve_repo_path(path: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded
    return REPO_ROOT / expanded


def build_capture_paths(args: argparse.Namespace) -> CapturePaths:
    output_png = resolve_repo_path(args.output)
    demo_pdf = (
        resolve_repo_path(args.pdf)
        if args.pdf is not None
        else DEFAULT_PDF_PATH
    )
    output_png.parent.mkdir(parents=True, exist_ok=True)
    demo_pdf.parent.mkdir(parents=True, exist_ok=True)
    return CapturePaths(output_png=output_png, demo_pdf=demo_pdf)


def escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_pdf_pages(path: Path, streams: list[bytes]) -> None:
    if not streams:
        raise ValueError("At least one PDF page stream is required.")

    page_count = len(streams)
    page_object_numbers = [3 + (index * 2) for index in range(page_count)]
    content_object_numbers = [number + 1 for number in page_object_numbers]
    font_object_number = 3 + (page_count * 2)
    kids = " ".join(f"{number} 0 R" for number in page_object_numbers).encode("ascii")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids ["
        + kids
        + b"] /Count "
        + str(page_count).encode("ascii")
        + b" >>",
    ]

    for content_object_number, stream in zip(content_object_numbers, streams):
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 "
            + f"{font_object_number} 0 R".encode("ascii")
            + b" >> >> /Contents "
            + f"{content_object_number} 0 R".encode("ascii")
            + b" >>"
        )
        objects.append(
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"endstream"
        )

    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    chunks = [b"%PDF-1.4\n"]
    offsets = [0]
    current_offset = len(chunks[0])

    for object_number, obj in enumerate(objects, start=1):
        offsets.append(current_offset)
        chunk = f"{object_number} 0 obj\n".encode("ascii") + obj + b"\nendobj\n"
        chunks.append(chunk)
        current_offset += len(chunk)

    xref_offset = current_offset
    xref_lines = [
        b"xref\n",
        f"0 {len(objects) + 1}\n".encode("ascii"),
        b"0000000000 65535 f \n",
    ]
    for offset in offsets[1:]:
        xref_lines.append(f"{offset:010d} 00000 n \n".encode("ascii"))

    trailer = (
        b"trailer\n"
        + f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode("ascii")
        + b"startxref\n"
        + str(xref_offset).encode("ascii")
        + b"\n%%EOF\n"
    )
    path.write_bytes(b"".join(chunks + xref_lines + [trailer]))


def build_pdf_stream(lines: list[str]) -> bytes:
    stream_lines = [
        "BT",
        "/F1 12 Tf",
        "14 TL",
        "72 740 Td",
    ]
    for index, line in enumerate(lines):
        if index:
            stream_lines.append("T*")
        stream_lines.append(f"({escape_pdf_text(line)}) Tj")
    stream_lines.append("ET")
    return ("\n".join(stream_lines) + "\n").encode("latin-1")


def demo_pdf_pages() -> list[list[str]]:
    lines = [
        "UNIVERSITAIR ZIEKENHUIS GENT",
        "Dienst cardiologie - consultatieverslag",
        "",
        "Patient: Marie De Smet",
        "Geboortedatum: 14/02/1978",
        "Rijksregisternummer: 78.02.14-123.45",
        "Adres: Korenmarkt 12, 9000 Gent",
        "Telefoon: 0471 23 45 67",
        "E-mail: marie.desmet@example.be",
        "Verwijzende arts: Dr. Pieter Van den Broeck",
        "Consultatiedatum: 03/04/2026",
        "",
        "Reden van aanmelding:",
        "Recidiverende hartkloppingen, wisselende inspanningsdyspneu en vermoeidheid",
        "sinds drie weken, zonder thoracale pijn of recente koorts.",
        "",
        "Voorgeschiedenis:",
        "Hypothyreoidie, arteriele hypertensie en appendectomie in 2001.",
        "",
        "Medicatie:",
        "Bisoprolol 2.5 mg 1x/dag, levothyroxine 100 mcg 1x/dag en",
        "sporadisch ibuprofen 400 mg bij hoofdpijn.",
        "",
        "Klinisch onderzoek:",
        "Bloeddruk 148/92 mmHg, pols 104/min, saturatie 98% in rust.",
        "Auscultatie hart en longen zonder acute afwijkingen.",
        "",
        "ECG toont sinustachycardie zonder ischemische veranderingen.",
        "Laboratorium op 02/04/2026: Hb 13.2 g/dL, creatinine 0.82 mg/dL,",
        "TSH 5.8 mU/L en CRP < 5 mg/L.",
        "",
        "Bespreking:",
        "Klachten en beleid besproken met patient en echtgenoot Luc De Smet.",
        "Er is geen vermoeden van acuut coronair syndroom.",
        "",
        "Plan:",
        "Holtermonitor, transthoracale echocardiografie en controle binnen",
        "drie weken in Campus Sint-Lucas.",
        "",
        "Advies:",
        "Cafeine beperken, vochtinname verhogen en huisarts contacteren bij",
        "syncope, toenemende dyspneu of persisterende palpaties.",
        "",
        "Resume francophone:",
        "Palpitations intermittentes, pas de douleur thoracique, examen",
        "neurologique normal et pas de perte de connaissance rapporte.",
        "",
        "Volgende afspraak: 24/04/2026 om 09:30.",
        "Contact secretariaat: 09 332 45 67 of cardio.secretariaat@uzgent.example.",
        "Vorige opname: Clinique Saint-Luc, Avenue Hippocrate 10, 1200 Bruxelles.",
        "Document opgesteld door Dr. Elise Vermeulen op 03/04/2026.",
    ]
    return [
        lines[index : index + LINES_PER_PAGE]
        for index in range(0, len(lines), LINES_PER_PAGE)
    ]


def write_demo_pdf(path: Path) -> None:
    streams = [build_pdf_stream(page_lines) for page_lines in demo_pdf_pages()]
    write_pdf_pages(path, streams)


def configure_reproducible_environment() -> tempfile.TemporaryDirectory[str]:
    sandbox = tempfile.TemporaryDirectory(prefix="open-anonymizer-readme-")
    sandbox_root = Path(sandbox.name)

    for variable, relative in (
        ("HOME", "."),
        ("USERPROFILE", "."),
        ("APPDATA", "AppData/Roaming"),
        ("LOCALAPPDATA", "AppData/Local"),
        ("XDG_CONFIG_HOME", ".config"),
        ("XDG_CACHE_HOME", ".cache"),
        ("XDG_DATA_HOME", ".local/share"),
    ):
        path = sandbox_root / relative
        path.mkdir(parents=True, exist_ok=True)
        os.environ[variable] = str(path)

    os.environ.setdefault("QT_SCALE_FACTOR", "1")
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "0")
    os.environ.setdefault("OPEN_ANONYMIZER_MAX_CONCURRENT_IMPORTS", "1")
    return sandbox


def build_demo_settings():
    from open_anonymizer.models import AnonymizationSettings

    return AnonymizationSettings(
        first_name="Marie",
        last_name="De Smet",
        birthdate=date(1978, 2, 14),
        other_names=[
            "Luc De Smet",
            "Pieter Van den Broeck",
            "Elise Vermeulen",
        ],
        custom_addresses=[
            "Korenmarkt 12, 9000 Gent",
            "Avenue Hippocrate 10, 1200 Bruxelles",
        ],
        mode="placeholders",
    )


def pump_events(app, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(POLL_INTERVAL_SECONDS)


def describe_window_state(window) -> str:
    current_document = window.current_document()
    current_status = current_document.status if current_document is not None else "none"
    document_states = ", ".join(
        f"{document.display_name}:{document.status}" for document in window.documents
    ) or "no-documents"
    return (
        f"current_document={current_status}; "
        f"processing_active={window.processing_active}; "
        f"pending_imports={len(window.active_import_workers)}; "
        f"queued_imports={len(window.queued_import_requests)}; "
        f"documents={document_states}"
    )


def wait_for(condition, *, timeout_seconds: float, app, description: str, window) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return
        time.sleep(POLL_INTERVAL_SECONDS)

    raise TimeoutError(
        f"Timed out while waiting for {description}. {describe_window_state(window)}"
    )


def wait_for_document_processing(window, *, timeout_seconds: float, app) -> None:
    def processing_finished() -> bool:
        document = window.current_document()
        if document is None:
            return False
        if window.active_import_workers or window.queued_import_requests:
            return False
        if window.processing_active:
            return False
        if document.status not in {"ready", "error"}:
            return False
        if document.status == "ready":
            return document.id in window.processed_documents
        return True

    wait_for(
        processing_finished,
        timeout_seconds=timeout_seconds,
        app=app,
        description="demo document processing",
        window=window,
    )


def configure_window(window) -> None:
    from PySide6.QtWidgets import QSplitter

    window.resize(*DEFAULT_WINDOW_SIZE)
    splitter = window.findChild(QSplitter)
    if splitter is not None:
        splitter.setSizes(list(DEFAULT_SPLITTER_SIZES))


def capture_window(window, output_path: Path) -> None:
    scrollbar = window.output_view.verticalScrollBar()
    if scrollbar is not None:
        scrollbar.setValue(scrollbar.minimum())
    window.statusBar().clearMessage()
    pixmap = window.grab()
    if pixmap.isNull() or not pixmap.save(str(output_path), "PNG"):
        raise RuntimeError(f"Failed to save screenshot to {output_path}")


def run_capture(paths: CapturePaths, *, timeout_seconds: float) -> None:
    sandbox = configure_reproducible_environment()
    try:
        from PySide6.QtCore import QStandardPaths
        from PySide6.QtWidgets import QApplication

        from open_anonymizer.branding import application_icon
        from open_anonymizer.main import APP_STYLESHEET
        from open_anonymizer.services.backend_runtime import shutdown_backend_runtime
        from open_anonymizer.services.deduce_backend import release_backend_resources
        from open_anonymizer.ui import MainWindow

        QStandardPaths.setTestModeEnabled(True)
        write_demo_pdf(paths.demo_pdf)

        app = QApplication([])
        app.setApplicationName("Open Anonymizer")
        app.setOrganizationName("Open Anonymizer")
        app.setStyleSheet(APP_STYLESHEET)
        app.setWindowIcon(application_icon())

        window = MainWindow()
        try:
            window.apply_anonymization_settings(
                build_demo_settings(),
                persist=False,
                reprocess=False,
            )
            configure_window(window)
            window.show()
            app.processEvents()
            pump_events(app, 0.2)

            window.handle_dropped_paths([paths.demo_pdf])
            wait_for_document_processing(
                window,
                timeout_seconds=timeout_seconds,
                app=app,
            )
            pump_events(app, 0.2)

            document = window.current_document()
            if document is None:
                raise RuntimeError("The demo PDF was not selected after import.")
            if document.status == "error":
                raise RuntimeError(
                    document.error_message
                    or f"Processing failed for {document.display_name}."
                )

            capture_window(window, paths.output_png)
        finally:
            window.close()
            app.processEvents()
            shutdown_backend_runtime()
            release_backend_resources()
    finally:
        sandbox.cleanup()


def main() -> int:
    multiprocessing.freeze_support()
    args = parse_args()
    paths = build_capture_paths(args)
    run_capture(paths, timeout_seconds=max(args.timeout, 1.0))
    print(f"Demo PDF: {paths.demo_pdf}")
    print(f"Screenshot: {paths.output_png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
