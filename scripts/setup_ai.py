"""Install an isolated, reproducible inference runtime without downloading model weights."""

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
SEED_REVISION = "51383efd921027683c89e5348211d93ff12ac2a8"
GIB = 1024**3


def run(command, environment):
    print("\n> " + " ".join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], env=environment, check=True)


def setup(models_dir: Path):
    models_dir = models_dir.expanduser().absolute()
    ancestor = models_dir
    while not ancestor.exists():
        ancestor = ancestor.parent
    available = shutil.disk_usage(ancestor).free
    if available < 30 * GIB:
        raise SystemExit(
            f"模型存储位置仅剩 {available / GIB:.1f} GB，安装需至少 30 GB。"
            "请提供外置 SSD 路径或释放空间后重试；未开始安装。"
        )
    if not shutil.which("uv") or not shutil.which("git"):
        raise SystemExit("需要先安装 uv 和 Git。")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("需要先安装 FFmpeg。")
    models_dir.mkdir(parents=True, exist_ok=True)
    environment = {
        **os.environ,
        "UV_CACHE_DIR": str(models_dir / "uv-cache"),
        "UV_PYTHON_INSTALL_DIR": str(models_dir / "python"),
        "HF_HOME": str(models_dir / "huggingface"),
        "TORCH_HOME": str(models_dir / "torch"),
    }
    repo = models_dir / "seed-vc"
    if not repo.exists():
        run(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                "https://github.com/Plachtaa/seed-vc.git",
                repo,
            ],
            environment,
        )
        run(["git", "-C", repo, "checkout", SEED_REVISION], environment)
    else:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        if result.stdout.strip() != SEED_REVISION:
            raise SystemExit("Seed-VC 目录版本与固定版本不同，请使用一个新的模型目录。")
    runtime = models_dir / "runtime"
    if not (runtime / "bin/python").exists():
        run(["uv", "venv", "--python", "3.10", runtime], environment)
    executable = runtime / "bin/python"
    run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            executable,
            "-r",
            PROJECT / "scripts/ai-requirements.lock",
        ],
        environment,
    )
    run(
        [
            executable,
            "-c",
            "import torch, torchaudio, demucs, librosa; "
            "from transformers import WhisperModel; "
            "print('torch:', torch.__version__); "
            "print('MPS:', torch.backends.mps.is_available())",
        ],
        environment,
    )
    # Import model modules before downloading weights to expose missing dependencies early.
    validation = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from modules.length_regulator import InterpolateRegulator; "
        "from modules.flow_matching import CFM; from modules.rmvpe import RMVPE; "
        "from modules.bigvgan.bigvgan import BigVGAN; "
        "print('Seed-VC imports: OK')"
    )
    run([executable, "-c", validation, repo], environment)
    config_path = PROJECT / "settings.json"
    existing = json.loads(config_path.read_text()) if config_path.exists() else {}
    existing.update(
        models_dir=str(models_dir),
        seed_repo=str(repo),
        seed_python=str(executable),
        demucs_python=str(executable),
        demucs_device="cpu",
    )
    temp = config_path.with_suffix(".tmp")
    temp.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(config_path)
    print("\n推理环境已安装，配置已保存。重启应用后可运行 AI 任务。")
    print("模型权重将在首次推理时下载，完成后同一模型可离线使用。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="安装 Demucs 与 Seed-VC 推理环境")
    parser.add_argument(
        "--models-dir",
        type=Path,
        default=Path(os.environ.get("COVER_MODELS_DIR", str(PROJECT / "models"))),
    )
    arguments = parser.parse_args()
    setup(arguments.models_dir)
