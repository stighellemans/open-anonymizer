from __future__ import annotations

import platform
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def build_archive_name() -> str:
    system = platform.system().lower()
    if "darwin" in system:
        system = "macos"
    return f"open-anonymizer-{system}.zip"


def main() -> int:
    dist_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dist")
    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("release") / build_archive_name()

    if not dist_dir.exists():
        raise FileNotFoundError(f"Distribution directory does not exist: {dist_dir}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output_path, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(dist_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(dist_dir))

    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
