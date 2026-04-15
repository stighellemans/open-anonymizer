from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from open_anonymizer import __version__


APP_NAME = "Open Anonymizer"
DEFAULT_MACOS_ARCHIVE_NAME = "open-anonymizer-macos.dmg"


@dataclass
class ReleaseArtifact:
    platform: str
    label: str
    filename: str
    install_hint: str
    source_path: str | None = None
    relative_path: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    available: bool = False


def default_windows_installer_name() -> str:
    return f"OpenAnonymizer-{__version__}-Setup.exe"


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a web-ready release bundle from built installers.")
    parser.add_argument(
        "--release-dir",
        type=Path,
        default=REPO_ROOT / "release",
        help="Directory containing built release artifacts.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "release" / "web-ready" / f"v{__version__}",
        help="Directory where the web-ready bundle will be assembled.",
    )
    parser.add_argument(
        "--macos-archive",
        type=Path,
        help=f"Optional path to the macOS DMG. Defaults to release/{DEFAULT_MACOS_ARCHIVE_NAME}.",
    )
    parser.add_argument(
        "--windows-installer",
        type=Path,
        help=f"Optional path to the Windows installer. Defaults to release/{default_windows_installer_name()}.",
    )
    return parser


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_release_artifacts(args: argparse.Namespace) -> list[ReleaseArtifact]:
    release_dir = args.release_dir.resolve()
    macos_archive = args.macos_archive.resolve() if args.macos_archive else release_dir / DEFAULT_MACOS_ARCHIVE_NAME
    windows_installer = (
        args.windows_installer.resolve()
        if args.windows_installer
        else release_dir / default_windows_installer_name()
    )

    return [
        ReleaseArtifact(
            platform="macos",
            label="Download for macOS",
            filename=macos_archive.name,
            install_hint="Open the disk image, then drag OpenAnonymizer.app into Applications.",
            source_path=str(macos_archive),
        ),
        ReleaseArtifact(
            platform="windows",
            label="Download for Windows",
            filename=windows_installer.name,
            install_hint="Run the setup executable and follow the installer prompts.",
            source_path=str(windows_installer),
        ),
    ]


def stage_artifacts(artifacts: list[ReleaseArtifact], output_dir: Path) -> list[ReleaseArtifact]:
    output_dir.mkdir(parents=True, exist_ok=True)
    staged: list[ReleaseArtifact] = []

    for artifact in artifacts:
        source_path = Path(artifact.source_path) if artifact.source_path else None
        if source_path is None or not source_path.exists():
            staged.append(artifact)
            continue

        destination_path = output_dir / artifact.filename
        if source_path.resolve() != destination_path.resolve():
            shutil.copy2(source_path, destination_path)

        artifact.available = True
        artifact.relative_path = artifact.filename
        artifact.size_bytes = destination_path.stat().st_size
        artifact.sha256 = sha256_file(destination_path)
        staged.append(artifact)

    return staged


def write_checksums(artifacts: list[ReleaseArtifact], output_dir: Path) -> None:
    lines = [f"{artifact.sha256}  {artifact.filename}" for artifact in artifacts if artifact.available]
    content = "\n".join(lines)
    if content:
        content += "\n"
    (output_dir / "SHA256SUMS.txt").write_text(content, encoding="utf-8")


def write_manifest(artifacts: list[ReleaseArtifact], output_dir: Path) -> None:
    manifest = {
        "app_name": APP_NAME,
        "version": __version__,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "downloads": [asdict(artifact) for artifact in artifacts],
    }
    (output_dir / "release-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _download_card(artifact: ReleaseArtifact) -> str:
    button_html: str
    status_html: str
    if artifact.available and artifact.relative_path:
        button_html = f'<a class="button" href="{escape(artifact.relative_path)}">{escape(artifact.label)}</a>'
        status_html = (
            f'<p class="status ok">Ready: {escape(artifact.filename)}</p>'
            f'<p class="meta">SHA256: <code>{escape(artifact.sha256 or "")}</code></p>'
        )
    else:
        button_html = f'<span class="button disabled">{escape(artifact.label)}</span>'
        status_html = (
            f'<p class="status pending">Pending: build {escape(artifact.filename)} on its native platform.</p>'
        )

    return f"""
    <article class="card">
      <h2>{escape(artifact.label)}</h2>
      <p>{escape(artifact.install_hint)}</p>
      {button_html}
      {status_html}
    </article>
    """.strip()


def write_download_page(artifacts: list[ReleaseArtifact], output_dir: Path) -> None:
    cards = "\n".join(_download_card(artifact) for artifact in artifacts)
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(APP_NAME)} Downloads</title>
  <style>
    :root {{
      --bg: #f7f3ea;
      --panel: rgba(255, 252, 245, 0.88);
      --ink: #1f1a14;
      --muted: #6b6257;
      --accent: #0d6b57;
      --accent-2: #d88b2d;
      --border: rgba(31, 26, 20, 0.12);
    }}

    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: "Avenir Next", "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(216, 139, 45, 0.16), transparent 28%),
        radial-gradient(circle at bottom right, rgba(13, 107, 87, 0.16), transparent 30%),
        linear-gradient(180deg, #fcfaf5 0%, var(--bg) 100%);
    }}
    main {{
      max-width: 980px;
      margin: 0 auto;
      padding: 56px 24px 72px;
    }}
    .hero {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 28px;
      padding: 32px;
      backdrop-filter: blur(12px);
      box-shadow: 0 22px 60px rgba(31, 26, 20, 0.08);
    }}
    h1 {{
      margin: 0 0 12px;
      font-size: clamp(2.2rem, 4vw, 4.2rem);
      line-height: 0.95;
      letter-spacing: -0.04em;
    }}
    .eyebrow {{
      margin: 0 0 16px;
      text-transform: uppercase;
      letter-spacing: 0.16em;
      font-size: 0.82rem;
      color: var(--accent);
      font-weight: 700;
    }}
    .lede {{
      max-width: 42rem;
      color: var(--muted);
      font-size: 1.05rem;
      line-height: 1.6;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 18px;
      margin-top: 28px;
    }}
    .card {{
      border: 1px solid var(--border);
      border-radius: 22px;
      padding: 22px;
      background: rgba(255, 255, 255, 0.68);
    }}
    .card h2 {{
      margin-top: 0;
      margin-bottom: 10px;
      font-size: 1.2rem;
    }}
    .card p {{
      margin: 0 0 14px;
      color: var(--muted);
      line-height: 1.55;
    }}
    .button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 48px;
      padding: 0 18px;
      border-radius: 999px;
      font-weight: 700;
      text-decoration: none;
      color: white;
      background: linear-gradient(135deg, var(--accent), #0a5b4a);
    }}
    .button.disabled {{
      background: #b8b1a5;
      color: white;
      cursor: not-allowed;
    }}
    .status {{
      margin-top: 14px;
      font-weight: 700;
    }}
    .status.ok {{
      color: var(--accent);
    }}
    .status.pending {{
      color: var(--accent-2);
    }}
    .meta {{
      font-size: 0.94rem;
      word-break: break-word;
    }}
    .footer {{
      margin-top: 24px;
      color: var(--muted);
      font-size: 0.95rem;
    }}
    code {{
      font-family: "SFMono-Regular", "Cascadia Code", monospace;
      font-size: 0.9em;
    }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <p class="eyebrow">Version {escape(__version__)}</p>
      <h1>{escape(APP_NAME)}</h1>
      <p class="lede">
        Fully local desktop anonymization for medical text. Upload this folder as-is to your website,
        or merge the download links into your existing site and keep <code>SHA256SUMS.txt</code> next to the installers.
      </p>
      <div class="grid">
        {cards}
      </div>
      <p class="footer">
        Generated bundle contents: <a href="SHA256SUMS.txt">SHA256SUMS.txt</a> and
        <a href="release-manifest.json">release-manifest.json</a>.
      </p>
    </section>
  </main>
</body>
</html>
"""
    (output_dir / "index.html").write_text(page, encoding="utf-8")


def main() -> int:
    args = build_argument_parser().parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    artifacts = build_release_artifacts(args)
    staged_artifacts = stage_artifacts(artifacts, output_dir)

    if not any(artifact.available for artifact in staged_artifacts):
        raise FileNotFoundError(
            "No release artifacts were found. Build at least one installer before preparing the web bundle."
        )

    write_checksums(staged_artifacts, output_dir)
    write_manifest(staged_artifacts, output_dir)
    write_download_page(staged_artifacts, output_dir)

    print(output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
