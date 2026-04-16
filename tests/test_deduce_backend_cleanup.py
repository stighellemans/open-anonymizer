from __future__ import annotations

import pickle
import sys
import threading
from types import SimpleNamespace

from open_anonymizer.models import RecognitionFlags
from open_anonymizer.services import deduce_backend


def test_backend_assets_load_once_until_release(monkeypatch) -> None:
    deduce_backend.release_backend_resources()
    built_assets: list[object] = []

    def fake_build_backend_assets() -> object:
        asset = object()
        built_assets.append(asset)
        return asset

    monkeypatch.setattr(deduce_backend, "_build_backend_assets", fake_build_backend_assets)

    first = deduce_backend._backend_assets()
    second = deduce_backend._backend_assets()
    deduce_backend.release_backend_resources()
    third = deduce_backend._backend_assets()

    assert first is second
    assert third is not first
    assert built_assets == [first, third]


def test_backend_model_loads_once_per_flags_until_release(monkeypatch) -> None:
    deduce_backend.release_backend_resources()
    built_models: list[tuple[bool, ...]] = []

    def fake_build_backend_model(flags_key: tuple[bool, ...]) -> object:
        built_models.append(flags_key)
        return object()

    monkeypatch.setattr(deduce_backend, "_build_backend_model", fake_build_backend_model)

    default_flags = RecognitionFlags().as_key()
    alternate_flags = RecognitionFlags(names=False).as_key()

    first = deduce_backend._backend_model(default_flags)
    second = deduce_backend._backend_model(default_flags)
    third = deduce_backend._backend_model(alternate_flags)
    deduce_backend.release_backend_resources()
    fourth = deduce_backend._backend_model(default_flags)

    assert first is second
    assert third is not first
    assert fourth is not first
    assert built_models == [default_flags, alternate_flags, default_flags]


def test_prime_backend_resources_loads_assets_and_default_model(monkeypatch) -> None:
    events: list[object] = []

    monkeypatch.setattr(
        deduce_backend,
        "_backend_assets",
        lambda: events.append("assets") or object(),
    )
    monkeypatch.setattr(
        deduce_backend,
        "_backend_model",
        lambda flags_key: events.append(("model", flags_key)) or object(),
    )

    deduce_backend.prime_backend_resources()

    assert events == ["assets", ("model", RecognitionFlags().as_key())]


def test_release_backend_resources_clears_model_and_assets_cache() -> None:
    flags_key = RecognitionFlags().as_key()
    fake_assets = object()
    fake_model = object()

    deduce_backend.release_backend_resources()
    deduce_backend._backend_assets_instance = fake_assets
    deduce_backend._backend_models[flags_key] = fake_model

    deduce_backend.release_backend_resources()

    assert deduce_backend._backend_assets_instance is None
    assert deduce_backend._backend_models == {}


def test_backend_model_single_flights_concurrent_same_flag_builds(monkeypatch) -> None:
    deduce_backend.release_backend_resources()
    flags_key = RecognitionFlags().as_key()
    build_count = 0
    entered_build = threading.Event()
    release_build = threading.Event()

    def fake_build_backend_model(requested_flags_key: tuple[bool, ...]) -> object:
        nonlocal build_count
        assert requested_flags_key == flags_key
        build_count += 1
        entered_build.set()
        release_build.wait(timeout=2)
        return object()

    monkeypatch.setattr(deduce_backend, "_build_backend_model", fake_build_backend_model)

    results: list[object | None] = [None, None]

    def load_model(index: int) -> None:
        results[index] = deduce_backend._backend_model(flags_key)

    first_thread = threading.Thread(target=load_model, args=(0,))
    second_thread = threading.Thread(target=load_model, args=(1,))

    first_thread.start()
    assert entered_build.wait(timeout=2)
    second_thread.start()
    release_build.set()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)

    assert build_count == 1
    assert results[0] is not None
    assert results[1] is results[0]
    deduce_backend.release_backend_resources()


def test_load_bundled_lookup_structs_reads_packaged_cache(monkeypatch, tmp_path) -> None:
    bundled_cache_dir = tmp_path / "bundled-cache"
    bundled_cache_file = bundled_cache_dir / "cache" / "lookup_structs.pickle"
    bundled_cache_file.parent.mkdir(parents=True)
    expected_lookup_structs = {"loaded": True}
    with bundled_cache_file.open("wb") as handle:
        pickle.dump(
            {
                "deduce_version": "1.2.3",
                "lookup_structs": expected_lookup_structs,
            },
            handle,
        )

    monkeypatch.setattr(
        deduce_backend,
        "_bundled_lookup_cache_file",
        lambda: bundled_cache_file,
    )

    assert deduce_backend._load_bundled_lookup_structs("1.2.3") == expected_lookup_structs


def test_load_bundled_lookup_structs_rejects_wrong_version(monkeypatch, tmp_path) -> None:
    bundled_cache_dir = tmp_path / "bundled-cache"
    bundled_cache_file = bundled_cache_dir / "cache" / "lookup_structs.pickle"
    bundled_cache_file.parent.mkdir(parents=True)
    with bundled_cache_file.open("wb") as handle:
        pickle.dump(
            {
                "deduce_version": "old-version",
                "lookup_structs": {"loaded": True},
            },
            handle,
        )

    monkeypatch.setattr(
        deduce_backend,
        "_bundled_lookup_cache_file",
        lambda: bundled_cache_file,
    )

    assert deduce_backend._load_bundled_lookup_structs("1.2.3") is None


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


def test_build_backend_assets_uses_bundled_lookup_structs_without_rebuilding(
    monkeypatch,
    tmp_path,
) -> None:
    lookup_data_path = tmp_path / "lookup-data"
    tokenizer = object()
    bundled_lookup_structs = {"loaded": True}

    def fake_get_lookup_structs(**kwargs):
        raise AssertionError(f"Rebuild should not run: {kwargs}")

    fake_deduce = SimpleNamespace(
        _initialize_tokenizer=lambda path: tokenizer if path == lookup_data_path else None
    )

    monkeypatch.setattr(deduce_backend, "_lookup_data_dir", lambda: lookup_data_path)
    monkeypatch.setattr(
        deduce_backend,
        "_load_bundled_lookup_structs",
        lambda deduce_version: bundled_lookup_structs if deduce_version == "1.2.3" else None,
    )
    monkeypatch.setitem(
        sys.modules,
        "belgian_deduce",
        SimpleNamespace(Deduce=fake_deduce, __version__="1.2.3"),
    )
    monkeypatch.setitem(
        sys.modules,
        "belgian_deduce.lookup_structs",
        SimpleNamespace(get_lookup_structs=fake_get_lookup_structs),
    )

    assets = deduce_backend._build_backend_assets()

    assert assets.lookup_data_path == lookup_data_path
    assert assets.cache_path == lookup_data_path
    assert assets.tokenizer is tokenizer
    assert assets.lookup_structs == bundled_lookup_structs


def test_build_backend_assets_rebuilds_without_writing_duplicate_cache(
    monkeypatch,
    tmp_path,
) -> None:
    lookup_data_path = tmp_path / "lookup-data"
    tokenizer = object()
    rebuilt_lookup_structs = {"rebuilt": True}
    get_lookup_structs_calls: list[dict[str, object]] = []

    def fake_get_lookup_structs(**kwargs):
        get_lookup_structs_calls.append(kwargs)
        return rebuilt_lookup_structs

    fake_deduce = SimpleNamespace(
        _initialize_tokenizer=lambda path: tokenizer if path == lookup_data_path else None
    )

    monkeypatch.setattr(deduce_backend, "_lookup_data_dir", lambda: lookup_data_path)
    monkeypatch.setattr(deduce_backend, "_load_bundled_lookup_structs", lambda deduce_version: None)
    monkeypatch.setitem(
        sys.modules,
        "belgian_deduce",
        SimpleNamespace(Deduce=fake_deduce, __version__="1.2.3"),
    )
    monkeypatch.setitem(
        sys.modules,
        "belgian_deduce.lookup_structs",
        SimpleNamespace(get_lookup_structs=fake_get_lookup_structs),
    )

    assets = deduce_backend._build_backend_assets()

    assert assets.lookup_data_path == lookup_data_path
    assert assets.cache_path == lookup_data_path
    assert assets.tokenizer is tokenizer
    assert assets.lookup_structs == rebuilt_lookup_structs
    assert get_lookup_structs_calls == [
        {
            "lookup_path": lookup_data_path,
            "cache_path": lookup_data_path,
            "tokenizer": tokenizer,
            "deduce_version": "1.2.3",
            "build": True,
            "save_cache": False,
        }
    ]


def test_build_backend_assets_supports_legacy_lookup_structs_signature(
    monkeypatch,
    tmp_path,
) -> None:
    lookup_data_path = tmp_path / "lookup-data"
    tokenizer = object()
    rebuilt_lookup_structs = {"rebuilt": True}
    get_lookup_structs_calls: list[dict[str, object]] = []

    def fake_get_lookup_structs(
        lookup_path,
        cache_path,
        tokenizer,
        build=False,
        save_cache=True,
    ):
        get_lookup_structs_calls.append(
            {
                "lookup_path": lookup_path,
                "cache_path": cache_path,
                "tokenizer": tokenizer,
                "build": build,
                "save_cache": save_cache,
            }
        )
        return rebuilt_lookup_structs

    fake_deduce = SimpleNamespace(
        _initialize_tokenizer=lambda path: tokenizer if path == lookup_data_path else None
    )

    monkeypatch.setattr(deduce_backend, "_lookup_data_dir", lambda: lookup_data_path)
    monkeypatch.setattr(deduce_backend, "_load_bundled_lookup_structs", lambda deduce_version: None)
    monkeypatch.setitem(
        sys.modules,
        "belgian_deduce",
        SimpleNamespace(Deduce=fake_deduce, __version__="1.2.3"),
    )
    monkeypatch.setitem(
        sys.modules,
        "belgian_deduce.lookup_structs",
        SimpleNamespace(get_lookup_structs=fake_get_lookup_structs),
    )

    assets = deduce_backend._build_backend_assets()

    assert assets.lookup_structs == rebuilt_lookup_structs
    assert get_lookup_structs_calls == [
        {
            "lookup_path": lookup_data_path,
            "cache_path": lookup_data_path,
            "tokenizer": tokenizer,
            "build": True,
            "save_cache": False,
        }
    ]


def test_build_backend_assets_supports_package_version_lookup_structs_signature(
    monkeypatch,
    tmp_path,
) -> None:
    lookup_data_path = tmp_path / "lookup-data"
    tokenizer = object()
    rebuilt_lookup_structs = {"rebuilt": True}
    get_lookup_structs_calls: list[dict[str, object]] = []

    def fake_get_lookup_structs(
        lookup_path,
        cache_path,
        tokenizer,
        package_version,
        build=False,
        save_cache=True,
    ):
        get_lookup_structs_calls.append(
            {
                "lookup_path": lookup_path,
                "cache_path": cache_path,
                "tokenizer": tokenizer,
                "package_version": package_version,
                "build": build,
                "save_cache": save_cache,
            }
        )
        return rebuilt_lookup_structs

    fake_deduce = SimpleNamespace(
        _initialize_tokenizer=lambda path: tokenizer if path == lookup_data_path else None
    )

    monkeypatch.setattr(deduce_backend, "_lookup_data_dir", lambda: lookup_data_path)
    monkeypatch.setattr(deduce_backend, "_load_bundled_lookup_structs", lambda deduce_version: None)
    monkeypatch.setitem(
        sys.modules,
        "belgian_deduce",
        SimpleNamespace(Deduce=fake_deduce, __version__="1.2.3"),
    )
    monkeypatch.setitem(
        sys.modules,
        "belgian_deduce.lookup_structs",
        SimpleNamespace(get_lookup_structs=fake_get_lookup_structs),
    )

    assets = deduce_backend._build_backend_assets()

    assert assets.lookup_structs == rebuilt_lookup_structs
    assert get_lookup_structs_calls == [
        {
            "lookup_path": lookup_data_path,
            "cache_path": lookup_data_path,
            "tokenizer": tokenizer,
            "package_version": "1.2.3",
            "build": True,
            "save_cache": False,
        }
    ]
