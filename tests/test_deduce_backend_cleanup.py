from __future__ import annotations

from open_anonymizer.services import deduce_backend


def test_release_backend_resources_clears_backend_model_cache(monkeypatch) -> None:
    events: list[str] = []

    class FakeCachedModel:
        def cache_clear(self) -> None:
            events.append("cache-cleared")

    monkeypatch.setattr(deduce_backend, "_backend_model", FakeCachedModel())

    deduce_backend.release_backend_resources()

    assert events == ["cache-cleared"]
