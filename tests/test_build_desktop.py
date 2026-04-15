from __future__ import annotations

import ast
import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_build_desktop_module():
    module_path = REPO_ROOT / "scripts" / "build_desktop.py"
    spec = importlib.util.spec_from_file_location("build_desktop_test_module", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_pyinstaller_args_adds_custom_hooks_and_qt_exclusions(monkeypatch) -> None:
    build_desktop = _load_build_desktop_module()
    monkeypatch.setattr(build_desktop, "_build_macos_icns", lambda svg_path: None)
    monkeypatch.setattr(build_desktop, "_build_windows_ico", lambda svg_path: None)

    args = build_desktop.build_argument_parser().parse_args([])
    pyinstaller_args = build_desktop.build_pyinstaller_args(args)

    hooks_index = pyinstaller_args.index("--additional-hooks-dir")
    assert pyinstaller_args[hooks_index + 1] == str(build_desktop.CUSTOM_HOOKS_DIR)

    excluded_modules = {
        pyinstaller_args[index + 1]
        for index, value in enumerate(pyinstaller_args)
        if value == "--exclude-module"
    }
    assert excluded_modules == set(build_desktop.DEFAULT_EXCLUDED_PYSIDE6_MODULES)
    assert "belgian_deduce" not in {
        pyinstaller_args[index + 1]
        for index, value in enumerate(pyinstaller_args)
        if value == "--collect-data"
    }


def test_collect_package_data_args_skips_nested_lookup_cache(monkeypatch) -> None:
    build_desktop = _load_build_desktop_module()
    monkeypatch.setattr(
        build_desktop,
        "collect_data_files",
        lambda package_name: [
            (
                "/tmp/site-packages/belgian_deduce/base_config.json",
                "belgian_deduce",
            ),
            (
                "/tmp/site-packages/belgian_deduce/data/lookup/cache/lookup_structs.pickle",
                "belgian_deduce/data/lookup/cache",
            ),
            (
                "/tmp/site-packages/belgian_deduce/data/lookup/cache/cache/lookup_structs.pickle",
                "belgian_deduce/data/lookup/cache/cache",
            ),
        ],
    )

    args = build_desktop._collect_package_data_args(
        "belgian_deduce",
        excluded_fragments=(build_desktop.NESTED_LOOKUP_CACHE_FRAGMENT,),
    )

    assert args == [
        "--add-data",
        "/tmp/site-packages/belgian_deduce/base_config.json"
        f"{build_desktop.os.pathsep}belgian_deduce",
        "--add-data",
        "/tmp/site-packages/belgian_deduce/data/lookup/cache/lookup_structs.pickle"
        f"{build_desktop.os.pathsep}belgian_deduce/data/lookup/cache",
    ]


def test_build_pyinstaller_args_adds_macos_options_only_on_macos(monkeypatch, tmp_path: Path) -> None:
    build_desktop = _load_build_desktop_module()
    icon_path = tmp_path / "OpenAnonymizer.icns"
    icon_path.write_bytes(b"icns")
    monkeypatch.setattr(build_desktop, "_build_macos_icns", lambda source_path: icon_path)
    monkeypatch.setattr(build_desktop, "_build_windows_ico", lambda source_path: tmp_path / "OpenAnonymizer.ico")

    args = build_desktop.build_argument_parser().parse_args([])

    monkeypatch.setattr(build_desktop.sys, "platform", "darwin")
    macos_args = build_desktop.build_pyinstaller_args(args)
    assert "--osx-bundle-identifier" in macos_args
    assert args.bundle_identifier in macos_args
    assert "--icon" in macos_args
    assert str(icon_path) in macos_args

    monkeypatch.setattr(build_desktop.sys, "platform", "win32")
    windows_args = build_desktop.build_pyinstaller_args(args)
    assert "--osx-bundle-identifier" not in windows_args
    assert "--icon" in windows_args
    assert str(tmp_path / "OpenAnonymizer.ico") in windows_args


def test_runtime_source_only_imports_expected_qt_modules() -> None:
    imported_qt_modules: set[str] = set()

    for path in sorted((REPO_ROOT / "src" / "open_anonymizer").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module is None or not node.module.startswith("PySide6."):
                continue
            imported_qt_modules.add(node.module.removeprefix("PySide6."))

    assert imported_qt_modules == {"QtCore", "QtGui", "QtWidgets"}
