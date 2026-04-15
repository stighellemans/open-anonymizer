from __future__ import annotations

import sys
from types import SimpleNamespace

from open_anonymizer.services import deduce_backend


def test_release_backend_resources_clears_backend_model_cache(monkeypatch) -> None:
    events: list[str] = []

    class FakeCachedModel:
        def cache_clear(self) -> None:
            events.append("cache-cleared")

    monkeypatch.setattr(deduce_backend, "_backend_model", FakeCachedModel())

    deduce_backend.release_backend_resources()

    assert events == ["cache-cleared"]


def test_lookup_cache_dir_prefers_bundled_cache(monkeypatch, tmp_path) -> None:
    bundled_cache_dir = tmp_path / "bundled-cache"
    bundled_cache_dir.mkdir()

    monkeypatch.setattr(
        deduce_backend,
        "_bundled_lookup_cache_dir",
        lambda: bundled_cache_dir,
    )

    assert deduce_backend._lookup_cache_dir() == bundled_cache_dir


def test_bundled_lookup_cache_dir_returns_lookup_root(monkeypatch, tmp_path) -> None:
    package_dir = tmp_path / "belgian_deduce"
    cache_file = package_dir / "data" / "lookup" / "cache" / "lookup_structs.pickle"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_bytes(b"cache")

    monkeypatch.setitem(
        sys.modules,
        "belgian_deduce",
        SimpleNamespace(__file__=str(package_dir / "__init__.py")),
    )

    assert deduce_backend._bundled_lookup_cache_dir() == package_dir / "data" / "lookup"


def test_lookup_cache_dir_falls_back_to_user_cache(monkeypatch, tmp_path) -> None:
    user_cache_dir = tmp_path / "user-cache"
    user_cache_dir.mkdir()

    monkeypatch.setattr(deduce_backend, "_bundled_lookup_cache_dir", lambda: None)
    monkeypatch.setattr(deduce_backend, "_backend_cache_dir", lambda: user_cache_dir)

    assert deduce_backend._lookup_cache_dir() == user_cache_dir
