import json
import os
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GIB = 1024**3


class Settings:
    def __init__(self, root: Path | None = None):
        self.project_root = root or PROJECT_ROOT
        self.config_path = self.project_root / "settings.json"
        self.data_dir = (
            Path(os.environ.get("COVER_DATA_DIR", str(self.project_root / "data")))
            .expanduser()
            .resolve()
        )
        self.values = {}
        if self.config_path.exists():
            self.values = json.loads(self.config_path.read_text())
        self.ensure_data_dirs()

    def ensure_data_dirs(self):
        for folder in (
            "database",
            "jobs",
            "assets/voices",
            "assets/songs",
            "assets/instrumentals",
            "assets/vocals",
            "assets/outputs",
            "assets/lyrics",
            "uploads",
        ):
            (self.data_dir / folder).mkdir(parents=True, exist_ok=True)

    @property
    def models_dir(self) -> Path:
        return (
            Path(
                os.environ.get(
                    "COVER_MODELS_DIR",
                    self.values.get("models_dir", str(self.project_root / "models")),
                )
            )
            .expanduser()
            .resolve()
        )

    @property
    def seed_repo(self) -> Path:
        return (
            Path(self.values.get("seed_repo", self.models_dir / "seed-vc")).expanduser().resolve()
        )

    @property
    def seed_python(self) -> Path:
        return Path(
            os.path.abspath(
                Path(
                    self.values.get("seed_python", self.models_dir / "runtime/bin/python")
                ).expanduser()
            )
        )

    @property
    def demucs_python(self) -> Path:
        return Path(
            os.path.abspath(
                Path(
                    self.values.get("demucs_python", self.models_dir / "runtime/bin/python")
                ).expanduser()
            )
        )

    @property
    def demucs_device(self) -> str:
        return self.values.get("demucs_device", "cpu")

    def save(self, values: dict):
        for key in ("models_dir", "seed_repo", "seed_python", "demucs_python"):
            if key in values and values[key]:
                values[key] = os.path.abspath(Path(values[key]).expanduser())
        self.values.update(values)
        temporary = self.config_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.values, ensure_ascii=False, indent=2))
        temporary.replace(self.config_path)

    def public(self) -> dict:
        return {
            "data_dir": str(self.data_dir),
            "models_dir": str(self.models_dir),
            "seed_repo": str(self.seed_repo),
            "seed_python": str(self.seed_python),
            "demucs_python": str(self.demucs_python),
            "demucs_device": self.demucs_device,
        }

    @property
    def model_weights_ready(self) -> bool:
        manifest = self.models_dir / "install-manifest.json"
        try:
            files = json.loads(manifest.read_text())["files"]
            if len(files) != 10:
                return False
            for file in files:
                path = (self.models_dir / file["path"]).resolve()
                if not path.is_relative_to(self.models_dir) or path.stat().st_size != file["bytes"]:
                    return False
            return True
        except (OSError, ValueError, KeyError, TypeError):
            return False

    @property
    def model_free_requirement(self) -> int:
        return 5 * GIB + (0 if self.model_weights_ready else 2_570_455_151)

    def model_environment(self) -> dict:
        environment = {
            **os.environ,
            "HF_HOME": str(self.models_dir / "huggingface"),
            "HF_HUB_CACHE": str(self.models_dir / "huggingface/hub"),
            "TORCH_HOME": str(self.models_dir / "torch"),
            "PYTORCH_ENABLE_MPS_FALLBACK": "1",
            "PYTHONUNBUFFERED": "1",
            "HF_ENDPOINT": os.environ.get(
                "HF_ENDPOINT", self.values.get("hf_endpoint", "https://huggingface.co")
            ),
            "HF_HUB_DISABLE_XET": "1",
        }
        if self.model_weights_ready:
            environment.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        return environment


def free_bytes(path: Path) -> int:
    while not path.exists():
        path = path.parent
    return shutil.disk_usage(path).free
