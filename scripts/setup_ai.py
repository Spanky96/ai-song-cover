"""Install an isolated, reproducible inference runtime without downloading model weights."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

PROJECT = Path(__file__).resolve().parent.parent
SEED_REVISION = "51383efd921027683c89e5348211d93ff12ac2a8"
GIB = 1024**3
AUDIT = PROJECT / "scripts/storage-audit.json"


def run(command, environment):
    print("\n> " + " ".join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], env=environment, check=True)


def check_storage(path: Path, required: int, reserve: int):
    ancestor = path
    while not ancestor.exists():
        ancestor = ancestor.parent
    available = shutil.disk_usage(ancestor).free
    if available < required + reserve:
        raise SystemExit(
            f"可用空间 {available / GIB:.2f} GiB，不足以容纳待写入文件 "
            f"{required / GIB:.2f} GiB 并保留 {reserve / GIB:.2f} GiB 余量；未继续安装。"
        )
    print(f"磁盘余量：{available / GIB:.2f} GiB；保留：{reserve / GIB:.2f} GiB。", flush=True)


def install_source_archive(repo: Path):
    url = f"https://codeload.github.com/Plachtaa/seed-vc/zip/{SEED_REVISION}"
    tree_url = (
        f"https://api.github.com/repos/Plachtaa/seed-vc/git/trees/{SEED_REVISION}?recursive=1"
    )
    with urlopen(tree_url, timeout=30) as response:
        tree = json.load(response)
    if tree.get("truncated"):
        raise SystemExit("上游文件清单不完整，未解压源码。")
    blobs = {entry["path"]: entry for entry in tree["tree"] if entry["type"] == "blob"}
    total = sum(entry["size"] for entry in blobs.values())
    with tempfile.TemporaryDirectory(dir=repo.parent, prefix="seed-source-") as temporary:
        staging = Path(temporary)
        archive_path = staging / "source.zip"
        with urlopen(url, timeout=60) as response, archive_path.open("wb") as target:
            shutil.copyfileobj(response, target)
        source = staging / "source"
        source.mkdir()
        prefix = f"seed-vc-{SEED_REVISION}/"
        with zipfile.ZipFile(archive_path) as archive:
            if sum(entry.file_size for entry in archive.infolist()) != total:
                raise SystemExit("源码压缩包与官方文件清单的大小不符。")
            for relative, metadata in blobs.items():
                destination = source / relative
                if not destination.resolve().is_relative_to(source.resolve()):
                    raise SystemExit("源码压缩包包含无效路径。")
                content = archive.read(prefix + relative)
                digest = hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()
                if digest != metadata["sha"]:
                    raise SystemExit(f"源码文件校验失败：{relative}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
                destination.chmod(0o755 if metadata["mode"] == "100755" else 0o644)
        (source / ".seed-revision").write_text(SEED_REVISION + "\n")
        source.rename(repo)
    print(f"固定版本源码已安装，{len(blobs)} 个文件的 Git 对象哈希均通过校验。", flush=True)


def install_source_cdn(repo: Path):
    import httpx

    url = f"https://api.github.com/repos/Plachtaa/seed-vc/git/trees/{SEED_REVISION}?recursive=1"
    with urlopen(url, timeout=30) as response:
        tree = json.load(response)
    if tree.get("truncated"):
        raise SystemExit("官方源码文件清单不完整。")
    files = [
        entry
        for entry in tree["tree"]
        if entry["type"] == "blob"
        and (
            entry["path"] in ("inference.py", "hf_utils.py", "LICENSE")
            or entry["path"].startswith(("modules/", "configs/"))
        )
    ]
    source = repo.parent / f".seed-source-{SEED_REVISION}"
    source.mkdir(exist_ok=True)
    with httpx.Client(
        timeout=httpx.Timeout(20, connect=5), transport=httpx.HTTPTransport(retries=2)
    ) as client:

        def download(entry):
            relative = entry["path"]
            destination = source / relative
            if not destination.resolve().is_relative_to(source.resolve()):
                raise ValueError("无效源码路径。")
            if destination.exists():
                content = destination.read_bytes()
                digest = hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()
                if digest == entry["sha"]:
                    return
            url = f"https://cdn.jsdelivr.net/gh/Plachtaa/seed-vc@{SEED_REVISION}/{quote(relative)}"
            for attempt in range(4):
                try:
                    response = client.get(url)
                    response.raise_for_status()
                    content = response.content
                    break
                except httpx.HTTPError:
                    if attempt == 3:
                        raise
                    time.sleep(attempt + 1)
            digest = hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()
            if len(content) != entry["size"] or digest != entry["sha"]:
                raise ValueError(f"官方 Git 对象校验失败：{relative}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            destination.chmod(0o755 if entry["mode"] == "100755" else 0o644)
            print(f"源码已校验：{relative}", flush=True)

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(download, files))
        (source / ".seed-revision").write_text(SEED_REVISION + "\n")
        (source / ".seed-source.json").write_text(
            json.dumps(
                {
                    "revision": SEED_REVISION,
                    "source": "jsdelivr-with-official-git-blob-verification",
                    "files": files,
                },
                indent=2,
            )
        )
        source.rename(repo)
    print(f"{len(files)} 个推理源码文件已安装，官方 Git 对象哈希均通过校验。", flush=True)


def setup(
    models_dir: Path, reserve_gib: float = 5, source_archive: bool = False, source_cdn: bool = False
):
    models_dir = models_dir.expanduser().absolute()
    if reserve_gib < 1:
        raise SystemExit("系统余量不得小于 1 GiB。")
    audit = json.loads(AUDIT.read_text())
    if not all(audit["completeness"].values()):
        raise SystemExit("存储清单不完整，请先运行 scripts/audit_storage.py。")
    totals = audit["totals"]
    reserve = int(reserve_gib * GIB)
    required = (
        totals["model_files_verified_bytes"]
        + totals["wheel_payload_verified_bytes"]
        + totals["package_download_verified_bytes"]
        + audit["seed_repository"]["tracked_file_bytes"]
        + 2 * GIB
    )
    # The extra 2 GiB is a working allowance for builds and copies, not measured package size.
    check_storage(models_dir, required, reserve)
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
        if source_cdn:
            install_source_cdn(repo)
        elif source_archive:
            install_source_archive(repo)
        else:
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
        marker = repo / ".seed-revision"
        if marker.exists():
            revision = marker.read_text().strip()
        else:
            result = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            )
            revision = result.stdout.strip()
        if revision != SEED_REVISION:
            raise SystemExit("Seed-VC 目录版本与固定版本不同，请使用一个新的模型目录。")
    runtime = models_dir / "runtime"
    if not (runtime / "bin/python").exists():
        python = subprocess.run(["uv", "python", "find", "3.10"], capture_output=True, text=True)
        interpreter = python.stdout.strip() if python.returncode == 0 else "3.10"
        run(["uv", "venv", "--python", interpreter, runtime], environment)
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
    check_storage(models_dir, totals["model_files_verified_bytes"], reserve)
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
    parser.add_argument("--reserve-gib", type=float, default=5)
    parser.add_argument("--source-archive", action="store_true")
    parser.add_argument("--source-cdn", action="store_true")
    arguments = parser.parse_args()
    setup(
        arguments.models_dir, arguments.reserve_gib, arguments.source_archive, arguments.source_cdn
    )
