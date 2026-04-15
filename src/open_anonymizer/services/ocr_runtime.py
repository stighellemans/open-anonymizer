from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Iterable


BUNDLED_TESSERACT_DIRNAME = "tesseract_runtime"


def _unique_paths(paths: Iterable[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _runtime_root_candidates() -> list[Path]:
    candidates: list[Path] = []

    explicit_bundle_root = os.getenv("OPEN_ANONYMIZER_BUNDLE_ROOT", "").strip()
    if explicit_bundle_root:
        candidates.append(Path(explicit_bundle_root).expanduser())

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass))

    executable_path = Path(sys.executable).resolve()
    candidates.append(executable_path.parent)
    candidates.extend(list(executable_path.parents)[:3])
    repo_root = Path(__file__).resolve().parents[3]
    candidates.append(repo_root)
    candidates.append(repo_root / "vendor")

    return _unique_paths(candidates)


def _tesseract_executable_name() -> str:
    return "tesseract.exe" if os.name == "nt" else "tesseract"


def find_tesseract_binary() -> Path | None:
    configured_binary = os.getenv("OPEN_ANONYMIZER_TESSERACT_BIN", "").strip()
    if configured_binary:
        configured_path = Path(configured_binary).expanduser()
        if configured_path.exists():
            return configured_path.resolve()
        resolved = shutil.which(configured_binary)
        if resolved:
            return Path(resolved).resolve()

    executable_name = _tesseract_executable_name()
    for root in _runtime_root_candidates():
        for candidate in (
            root / BUNDLED_TESSERACT_DIRNAME / "bin" / executable_name,
            root / "Frameworks" / BUNDLED_TESSERACT_DIRNAME / "bin" / executable_name,
            root / "Resources" / BUNDLED_TESSERACT_DIRNAME / "bin" / executable_name,
        ):
            if candidate.exists():
                return candidate.resolve()

    resolved = shutil.which(executable_name)
    if resolved:
        return Path(resolved).resolve()
    return None


def find_tessdata_dir(binary_path: Path | None = None) -> Path | None:
    configured_dir = os.getenv("OPEN_ANONYMIZER_TESSDATA_DIR", "").strip()
    if configured_dir:
        candidate = Path(configured_dir).expanduser()
        if candidate.exists():
            return candidate.resolve()

    candidate_dirs: list[Path] = []
    if binary_path is not None:
        candidate_dirs.extend(
            [
                binary_path.parent.parent / "share" / "tessdata",
                binary_path.parent.parent / "tessdata",
            ]
        )

    for root in _runtime_root_candidates():
        candidate_dirs.extend(
            [
                root / BUNDLED_TESSERACT_DIRNAME / "share" / "tessdata",
                root / BUNDLED_TESSERACT_DIRNAME / "tessdata",
                root / "Frameworks" / BUNDLED_TESSERACT_DIRNAME / "share" / "tessdata",
                root / "Frameworks" / BUNDLED_TESSERACT_DIRNAME / "tessdata",
                root / "Resources" / BUNDLED_TESSERACT_DIRNAME / "share" / "tessdata",
                root / "Resources" / BUNDLED_TESSERACT_DIRNAME / "tessdata",
            ]
        )

    for candidate in _unique_paths(candidate_dirs):
        if candidate.exists():
            return candidate.resolve()
    return None


def build_tesseract_subprocess_env(binary_path: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    if binary_path is None:
        binary_path = find_tesseract_binary()

    path_entries: list[str] = []
    lib_entries: list[str] = []

    if binary_path is not None:
        path_entries.append(str(binary_path.parent))
        bundled_lib_dir = binary_path.parent.parent / "lib"
        if bundled_lib_dir.exists():
            path_entries.append(str(bundled_lib_dir))
            lib_entries.append(str(bundled_lib_dir))

    if path_entries:
        existing_path = env.get("PATH", "")
        env["PATH"] = os.pathsep.join(path_entries + ([existing_path] if existing_path else []))

    if lib_entries:
        for key in ("DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH", "LD_LIBRARY_PATH"):
            existing = env.get(key, "")
            env[key] = os.pathsep.join(lib_entries + ([existing] if existing else []))

    tessdata_dir = find_tessdata_dir(binary_path)
    if tessdata_dir is not None:
        if tessdata_dir.name == "tessdata":
            env["TESSDATA_PREFIX"] = str(tessdata_dir.parent)
        else:
            env["TESSDATA_PREFIX"] = str(tessdata_dir)

    return env
