from __future__ import annotations

from pathlib import Path, PurePosixPath

from PyInstaller.utils.hooks.qt import add_qt6_dependencies


hiddenimports, binaries, datas = add_qt6_dependencies(__file__)

_EXCLUDED_PLUGIN_PATHS = {
    "generic/libqtuiotouchplugin.dylib",
    "imageformats/libqpdf.dylib",
}
_EXCLUDED_PLUGIN_PREFIXES = (
    "platforminputcontexts/",
)


def _plugin_relative_path(src: str, dst: str) -> str | None:
    destination = PurePosixPath(str(dst).replace("\\", "/"))
    if "plugins" not in destination.parts:
        return None

    plugin_index = destination.parts.index("plugins")
    plugin_parts = destination.parts[plugin_index + 1 :]
    return PurePosixPath(*plugin_parts, Path(src).name).as_posix()


def _keep_binary(binary: tuple[str, str]) -> bool:
    src, dst = binary
    relative_path = _plugin_relative_path(src, dst)
    if relative_path is None:
        return True

    if relative_path in _EXCLUDED_PLUGIN_PATHS:
        return False

    return not any(relative_path.startswith(prefix) for prefix in _EXCLUDED_PLUGIN_PREFIXES)


binaries = [binary for binary in binaries if _keep_binary(binary)]
