import hashlib
import json
from types import SimpleNamespace

import pytest

from app.config import GIB, Settings
from scripts import download_models, setup_ai


def test_setup_rejects_low_disk_space_before_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_ai.shutil, "disk_usage", lambda _: SimpleNamespace(free=5 * GIB))
    monkeypatch.setattr(setup_ai, "run", lambda *_: pytest.fail("Installation must not start"))
    models = tmp_path / "models"
    with pytest.raises(SystemExit, match="可用空间"):
        setup_ai.setup(models, source_archive=True)
    assert not models.exists()
    assert not (tmp_path / "settings.json").exists()


def test_offline_mode_requires_all_manifest_files(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    monkeypatch.delenv("COVER_MODELS_DIR", raising=False)
    monkeypatch.setenv("COVER_DATA_DIR", str(tmp_path / "data"))
    settings = Settings(tmp_path)
    settings.models_dir.mkdir()
    files = []
    for index in range(10):
        path = settings.models_dir / f"weight-{index}"
        path.write_bytes(b"model")
        files.append({"path": path.name, "bytes": 5})
    (settings.models_dir / "install-manifest.json").write_text(json.dumps({"files": files}))
    assert settings.model_weights_ready
    assert settings.model_environment()["HF_HUB_OFFLINE"] == "1"
    assert settings.model_free_requirement == 5 * GIB

    (settings.models_dir / "weight-9").unlink()
    assert not settings.model_weights_ready
    assert "HF_HUB_OFFLINE" not in settings.model_environment()
    assert settings.model_free_requirement > 5 * GIB


def test_manifest_rejects_paths_outside_model_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("COVER_MODELS_DIR", raising=False)
    monkeypatch.setenv("COVER_DATA_DIR", str(tmp_path / "data"))
    settings = Settings(tmp_path)
    settings.models_dir.mkdir()
    (tmp_path / "unrelated-file").write_bytes(b"model")
    files = [{"path": "../unrelated-file", "bytes": 5}] * 10
    (settings.models_dir / "install-manifest.json").write_text(json.dumps({"files": files}))
    assert not settings.model_weights_ready


def test_download_rejects_corrupt_model(tmp_path):
    path = tmp_path / "weight"
    path.write_bytes(b"corrupt")
    expected = hashlib.sha256(b"correct").hexdigest()
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        download_models.verify_file(path, 7, expected)
    with pytest.raises(ValueError, match="File size mismatch"):
        download_models.verify_file(path, 100, expected)
