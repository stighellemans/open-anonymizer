from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DESTINATION = REPO_ROOT / "vendor" / "tesseract_runtime"
MACOS_HOMEBREW_PREFIX = Path("/opt/homebrew")
DEFAULT_LANGS = ["eng", "fra", "nld", "osd"]


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def _adhoc_sign(path: Path) -> None:
    _run(["codesign", "--force", "--sign", "-", str(path)])


def _otool_dependency_entries(binary_path: Path) -> list[tuple[str, Path]]:
    result = subprocess.run(
        ["otool", "-L", str(binary_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    dependencies: list[tuple[str, Path]] = []
    for line in result.stdout.splitlines()[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        dependency = stripped.split(" (compatibility version", 1)[0]
        if dependency.startswith(("/System/", "/usr/lib/")):
            continue
        if dependency.startswith("@"):
            continue
        dependency_path = Path(dependency).resolve()
        dependencies.append((dependency, dependency_path))
    return dependencies


def _otool_load_paths(binary_path: Path) -> list[str]:
    result = subprocess.run(
        ["otool", "-L", str(binary_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    dependencies: list[str] = []
    for line in result.stdout.splitlines()[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        dependency = stripped.split(" (compatibility version", 1)[0]
        if dependency.startswith(("/System/", "/usr/lib/")):
            continue
        dependencies.append(dependency)
    return dependencies


def _install_name(binary_path: Path) -> str:
    result = subprocess.run(
        ["otool", "-D", str(binary_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(lines) >= 2:
        return lines[1]
    return str(binary_path)


def _macos_binary_name() -> str:
    return "tesseract.exe" if os.name == "nt" else "tesseract"


def _resolve_homebrew_dependency(homebrew_prefix: Path, dependency_load_path: str) -> Path | None:
    dependency_name = Path(dependency_load_path).name
    candidate_paths = [
        homebrew_prefix / "lib" / dependency_name,
    ]
    candidate_paths.extend(sorted((homebrew_prefix / "opt").glob(f"*/lib/{dependency_name}")))
    candidate_paths.extend(sorted((homebrew_prefix / "Cellar").glob(f"*/*/lib/{dependency_name}")))

    for candidate in candidate_paths:
        if candidate.exists():
            return candidate.resolve()
    return None


def _homebrew_binary(homebrew_prefix: Path) -> Path:
    return (homebrew_prefix / "bin" / _macos_binary_name()).resolve()


def _copy_homebrew_runtime(
    destination_root: Path,
    homebrew_prefix: Path,
    langs: list[str],
) -> None:
    if destination_root.exists():
        shutil.rmtree(destination_root)

    binary_source = _homebrew_binary(homebrew_prefix)
    if not binary_source.exists():
        raise FileNotFoundError(f"Tesseract binary not found at {binary_source}")

    binary_destination = destination_root / "bin" / binary_source.name
    _copy_file(binary_source, binary_destination)

    copied_libraries: dict[Path, Path] = {}
    install_name_map: dict[str, str] = {}
    pending = [binary_source]
    seen: set[Path] = set()

    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)

        for dependency_load_path in _otool_load_paths(current):
            if dependency_load_path.startswith(("/System/", "/usr/lib/")):
                continue
            if dependency_load_path.startswith("@"):
                dependency_path = _resolve_homebrew_dependency(homebrew_prefix, dependency_load_path)
                if dependency_path is None:
                    continue
            else:
                dependency_path = Path(dependency_load_path).resolve()
            if dependency_path in copied_libraries:
                continue
            destination = destination_root / "lib" / dependency_path.name
            _copy_file(dependency_path, destination)
            copied_libraries[dependency_path] = destination
            install_name_map[str(dependency_path)] = f"@loader_path/{dependency_path.name}"
            install_name_map[dependency_load_path] = f"@loader_path/{dependency_path.name}"
            pending.append(dependency_path)

    for source_path, destination_path in copied_libraries.items():
        current_install_name = _install_name(source_path)
        if current_install_name:
            install_name_map[current_install_name] = f"@loader_path/{destination_path.name}"
            install_name_map[Path(current_install_name).name] = f"@loader_path/{destination_path.name}"
        _run(["install_name_tool", "-id", f"@loader_path/{destination_path.name}", str(destination_path)])
        for dependency_load_path in _otool_load_paths(source_path):
            replacement = (
                install_name_map.get(dependency_load_path)
                or install_name_map.get(Path(dependency_load_path).name)
            )
            if replacement:
                _run(["install_name_tool", "-change", dependency_load_path, replacement, str(destination_path)])
        if current_install_name in install_name_map and current_install_name != install_name_map[current_install_name]:
            # no-op guard for completeness
            pass

    for dependency_load_path in _otool_load_paths(binary_source):
        replacement = (
            install_name_map.get(dependency_load_path)
            or install_name_map.get(Path(dependency_load_path).name)
        )
        if replacement:
            binary_replacement = replacement.replace("@loader_path/", "@executable_path/../lib/")
            _run(["install_name_tool", "-change", dependency_load_path, binary_replacement, str(binary_destination)])

    for path in list((destination_root / "lib").glob("*.dylib")) + [binary_destination]:
        _adhoc_sign(path)

    tessdata_root = homebrew_prefix / "share" / "tessdata"
    if not tessdata_root.exists():
        raise FileNotFoundError(f"Tessdata directory not found at {tessdata_root}")

    for lang in langs:
        traineddata = (tessdata_root / f"{lang}.traineddata").resolve()
        if not traineddata.exists():
            raise FileNotFoundError(f"Missing traineddata file: {traineddata}")
        _copy_file(traineddata, destination_root / "share" / "tessdata" / traineddata.name)

    configs_dir = tessdata_root / "configs"
    if configs_dir.exists():
        shutil.copytree(configs_dir, destination_root / "share" / "tessdata" / "configs", dirs_exist_ok=True)

    for extra_name in ("LICENSE", "README.md"):
        source_extra = tessdata_root / extra_name
        if source_extra.exists():
            _copy_file(source_extra.resolve(), destination_root / "share" / "tessdata" / extra_name)


def _copy_self_contained_runtime(
    source_root: Path,
    destination_root: Path,
    langs: list[str],
) -> None:
    executable_name = _macos_binary_name()
    source_binary = source_root / "bin" / executable_name
    source_tessdata = source_root / "share" / "tessdata"

    if not source_binary.exists():
        raise FileNotFoundError(f"Tesseract binary not found: {source_binary}")
    if not source_tessdata.exists():
        raise FileNotFoundError(f"Tessdata directory not found: {source_tessdata}")

    if destination_root.exists():
        shutil.rmtree(destination_root)

    _copy_file(source_binary, destination_root / "bin" / executable_name)

    source_lib_dir = source_root / "lib"
    if source_lib_dir.exists():
        shutil.copytree(source_lib_dir, destination_root / "lib", dirs_exist_ok=True)

    for lang in langs:
        traineddata = source_tessdata / f"{lang}.traineddata"
        if not traineddata.exists():
            raise FileNotFoundError(f"Missing traineddata file: {traineddata}")
        _copy_file(traineddata, destination_root / "share" / "tessdata" / traineddata.name)

    configs_dir = source_tessdata / "configs"
    if configs_dir.exists():
        shutil.copytree(configs_dir, destination_root / "share" / "tessdata" / "configs", dirs_exist_ok=True)

    for extra_name in ("LICENSE", "COPYING", "README.md"):
        source_extra = source_root / extra_name
        if source_extra.exists():
            _copy_file(source_extra, destination_root / extra_name)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage a bundled Tesseract runtime into vendor/tesseract_runtime."
    )
    parser.add_argument(
        "source",
        nargs="?",
        type=Path,
        help="Path to a self-contained Tesseract runtime. Omit when using --from-homebrew.",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=DEFAULT_DESTINATION,
        help="Destination runtime directory inside the repo.",
    )
    parser.add_argument(
        "--langs",
        nargs="+",
        default=DEFAULT_LANGS,
        help="Traineddata files to copy into the bundled runtime.",
    )
    parser.add_argument(
        "--from-homebrew",
        action="store_true",
        help="Stage a relocatable runtime from the local Homebrew Tesseract installation.",
    )
    parser.add_argument(
        "--homebrew-prefix",
        type=Path,
        default=MACOS_HOMEBREW_PREFIX,
        help="Homebrew prefix to use with --from-homebrew.",
    )
    args = parser.parse_args()

    destination_root = args.destination.resolve()

    if args.from_homebrew:
        _copy_homebrew_runtime(destination_root, args.homebrew_prefix.resolve(), args.langs)
    else:
        if args.source is None:
            raise SystemExit("Provide a source runtime path or use --from-homebrew.")
        _copy_self_contained_runtime(args.source.resolve(), destination_root, args.langs)

    print(destination_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
