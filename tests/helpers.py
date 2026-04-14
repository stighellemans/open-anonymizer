from __future__ import annotations

from pathlib import Path


def escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_pdf_pages(path: Path, streams: list[bytes]) -> None:
    page_count = len(streams)
    if page_count == 0:
        raise ValueError("At least one page stream is required.")

    page_object_numbers = [3 + index * 2 for index in range(page_count)]
    content_object_numbers = [number + 1 for number in page_object_numbers]
    font_object_number = 3 + page_count * 2

    kids = " ".join(f"{number} 0 R" for number in page_object_numbers).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(page_count).encode("ascii") + b" >>",
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
            b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream"
        )

    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    chunks = [b"%PDF-1.4\n"]
    offsets = [0]
    current_offset = len(chunks[0])

    for index, obj in enumerate(objects, start=1):
        offsets.append(current_offset)
        chunk = f"{index} 0 obj\n".encode("ascii") + obj + b"\nendobj\n"
        chunks.append(chunk)
        current_offset += len(chunk)

    xref_offset = current_offset
    xref_lines = [b"xref\n", f"0 {len(objects) + 1}\n".encode("ascii"), b"0000000000 65535 f \n"]
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


def write_pdf_stream(path: Path, stream: bytes) -> None:
    write_pdf_pages(path, [stream])


def write_text_pdf(path: Path, text: str) -> None:
    stream = f"BT\n/F1 18 Tf\n72 720 Td\n({escape_pdf_text(text)}) Tj\nET\n".encode("latin-1")
    write_pdf_stream(path, stream)


def write_positioned_words_pdf(path: Path, words: list[tuple[str, int, int]]) -> None:
    stream_lines = ["BT", "/F1 18 Tf"]
    for text, x, y in words:
        stream_lines.append(f"1 0 0 1 {x} {y} Tm")
        stream_lines.append(f"({escape_pdf_text(text)}) Tj")
    stream_lines.append("ET")
    stream = ("\n".join(stream_lines) + "\n").encode("latin-1")
    write_pdf_stream(path, stream)


def write_visible_graphics_pdf(path: Path) -> None:
    stream = b"0 0 0 rg\n72 700 200 50 re\nf\n"
    write_pdf_stream(path, stream)
