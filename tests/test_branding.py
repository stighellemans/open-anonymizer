from __future__ import annotations

from pathlib import Path

from open_anonymizer import branding


def test_asset_path_prefers_pyinstaller_bundle(monkeypatch, tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle"
    bundled_asset = bundle_root / "open_anonymizer" / "assets" / "fingerprint.png"
    bundled_asset.parent.mkdir(parents=True)
    bundled_asset.write_bytes(b"png")

    monkeypatch.setattr(branding.sys, "_MEIPASS", str(bundle_root), raising=False)

    assert branding._asset_path("fingerprint.png") == bundled_asset


def test_asset_path_falls_back_to_source_tree(monkeypatch) -> None:
    monkeypatch.delattr(branding.sys, "_MEIPASS", raising=False)

    asset_path = branding._asset_path("fingerprint.png")

    assert asset_path.name == "fingerprint.png"
    assert asset_path.exists()
    assert asset_path.parent.name == "assets"
