import json
import platform
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .config import GIB, Settings, free_bytes


class Engines:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._cache = None
        self._cache_time = 0
        self._lock = threading.Lock()

    def invalidate(self):
        self._cache = None

    def status(self) -> dict:
        with self._lock:
            if self._cache and time.monotonic() - self._cache_time < 30:
                return self._cache
            settings = self.settings
            python_paths = {str(settings.seed_python), str(settings.demucs_python)}
            runtimes = {}
            for executable in python_paths:
                runtimes[executable] = self.inspect_python(Path(executable))
            seed_runtime = runtimes[str(settings.seed_python)]
            demucs_runtime = runtimes[str(settings.demucs_python)]
            script_ok = (settings.seed_repo / "inference.py").is_file()
            self._cache = {
                "platform": platform.system(),
                "architecture": platform.machine(),
                "ffmpeg": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
                "seed": {
                    "ready": script_ok and seed_runtime.get("seed", False),
                    "repository_found": script_ok,
                    "runtime": seed_runtime,
                    "model_weights": "首次推理按需加载",
                },
                "demucs": {
                    "ready": demucs_runtime.get("demucs", False),
                    "device": settings.demucs_device,
                    "runtime": demucs_runtime,
                },
            }
            self._cache_time = time.monotonic()
        return self._cache

    @staticmethod
    def inspect_python(executable: Path) -> dict:
        if not executable.is_file():
            return {"installed": False, "seed": False, "demucs": False, "mps": False}
        code = """
import importlib.util, json
result = {"installed": True, "seed": False, "demucs": False, "mps": False}
try:
    import torch, torchaudio
    result.update(torch=torch.__version__, mps=torch.backends.mps.is_available(),
                  cuda=torch.cuda.is_available())
    result['demucs'] = importlib.util.find_spec('demucs') is not None
    modules = ['librosa', 'transformers', 'yaml', 'munch', 'einops', 'scipy', 'huggingface_hub', 'dac']
    result['seed'] = all(importlib.util.find_spec(name) is not None for name in modules)
except Exception as e:
    result['error'] = str(e)
print(json.dumps(result))
"""
        try:
            result = subprocess.run(
                [str(executable), "-c", code],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            return json.loads(result.stdout.strip().splitlines()[-1])
        except (subprocess.SubprocessError, ValueError, IndexError) as error:
            return {"installed": True, "seed": False, "demucs": False, "error": str(error)}

    def validate(self, job_type: str, source_kind: str) -> list[str]:
        status = self.status()
        errors = []
        if not status["ffmpeg"]:
            errors.append("未安装 FFmpeg。")
        if job_type in ("cover", "separate"):
            if free_bytes(self.settings.models_dir) < 10 * GIB:
                errors.append("模型目录可用空间不足 10 GB，请先配置容量充足的存储目录。")
            if source_kind == "song" and not status["demucs"]["ready"]:
                errors.append("Demucs 推理环境未安装。")
        if job_type == "cover" and not status["seed"]["ready"]:
            errors.append("Seed-VC 推理环境未安装。")
        if free_bytes(self.settings.data_dir) < GIB:
            errors.append("音频存储目录可用空间不足 1 GB。")
        return errors

    def demucs_command(self, source: Path, output: Path) -> list[str]:
        return [
            str(self.settings.demucs_python),
            "-m",
            "demucs",
            "--two-stems",
            "vocals",
            "-n",
            "htdemucs",
            "-d",
            self.settings.demucs_device,
            "--segment",
            "7",
            "-o",
            str(output),
            str(source),
        ]

    def seed_command(self, source: Path, reference: Path, output: Path, params: dict) -> list[str]:
        return [
            str(self.settings.seed_python),
            str(self.settings.seed_repo / "inference.py"),
            "--source",
            str(source),
            "--target",
            str(reference),
            "--output",
            str(output),
            "--diffusion-steps",
            str(params["diffusion_steps"]),
            "--length-adjust",
            "1.0",
            "--inference-cfg-rate",
            str(params["cfg_rate"]),
            "--f0-condition",
            "True",
            "--auto-f0-adjust",
            "False",
            "--semi-tone-shift",
            str(params["pitch_shift"]),
            "--fp16",
            "False",
        ]
