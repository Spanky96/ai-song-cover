"""Download only the audited model files into the caches used by the inference engines."""

import argparse
import hashlib
import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

PROJECT = Path(__file__).resolve().parent.parent
GIB = 1024**3


def verify_file(path: Path, size: int, expected_hash: str | None = None) -> str:
    if path.stat().st_size != size:
        raise ValueError(f"File size mismatch: {path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(4 * 1024**2):
            digest.update(chunk)
    actual = digest.hexdigest()
    if expected_hash and not actual.startswith(expected_hash):
        raise ValueError(f"SHA256 mismatch: {path.name}")
    return actual


def check_space(root: Path, pending_bytes: int, reserve_bytes: int):
    available = shutil.disk_usage(root).free
    if available < pending_bytes + reserve_bytes:
        raise SystemExit(
            f"可用空间 {available / GIB:.2f} GiB，待下载 {pending_bytes / GIB:.2f} GiB，"
            f"需保留 {reserve_bytes / GIB:.2f} GiB；已暂停下载。"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models-dir", type=Path, default=PROJECT / "models")
    parser.add_argument("--hf-endpoint", default="https://huggingface.co")
    parser.add_argument("--reserve-gib", type=float, default=5)
    args = parser.parse_args()
    if args.reserve_gib < 1:
        raise SystemExit("系统余量不得小于 1 GiB。")
    root = args.models_dir.expanduser().resolve()
    if not (root / "seed-vc/inference.py").is_file():
        raise SystemExit("请先完成推理环境安装。")
    audit = json.loads((PROJECT / "scripts/storage-audit.json").read_text())
    if not audit["completeness"]["model_inventory_complete"]:
        raise SystemExit("模型文件清单不完整，未开始下载。")

    os.environ.update(
        HF_ENDPOINT=args.hf_endpoint.rstrip("/"),
        HF_HOME=str(root / "huggingface"),
        HF_HUB_DISABLE_XET="1",
        HF_HUB_DOWNLOAD_TIMEOUT="60",
        HF_HUB_ETAG_TIMEOUT="20",
        HF_HUB_OFFLINE="0",
    )
    from huggingface_hub import HfApi, hf_hub_download

    api = HfApi(endpoint=os.environ["HF_ENDPOINT"])
    reserve = int(args.reserve_gib * GIB)
    records = []
    pending = audit["totals"]["model_files_verified_bytes"]
    for model in audit["models"]:
        info = api.model_info(model["repo"], revision="main", timeout=30)
        if info.sha != model["revision"]:
            raise SystemExit(f"模型仓库已更新，请重新核对清单：{model['repo']}")
        cache = root / "seed-vc/checkpoints"
        if model["repo"] in ("openai/whisper-small", "nvidia/bigvgan_v2_44khz_128band_512x"):
            cache /= "hf_cache"
        for file in model["files"]:
            # A cached file uses no additional space; interrupted downloads are resumable by the hub.
            try:
                local = Path(
                    hf_hub_download(
                        model["repo"],
                        file["path"],
                        cache_dir=cache,
                        revision="main",
                        local_files_only=True,
                    )
                )
            except FileNotFoundError:
                check_space(root, pending, reserve)
                print(f"下载：{model['repo']}/{file['path']} ({file['bytes']} 字节)", flush=True)
                local = Path(
                    hf_hub_download(
                        model["repo"],
                        file["path"],
                        cache_dir=cache,
                        revision="main",
                    )
                )
            actual = verify_file(local, file["bytes"], file.get("sha256"))
            records.append(
                {
                    "repo": model["repo"],
                    "filename": file["path"],
                    "bytes": file["bytes"],
                    "sha256": actual,
                    "path": str(local.resolve().relative_to(root)),
                    "revision": model["revision"],
                }
            )
            pending -= file["bytes"]
            print(f"校验通过：{file['path']}；剩余 {pending} 字节。", flush=True)

    demucs = audit["demucs"]
    local = root / "torch/hub/checkpoints" / demucs["file"]
    if not local.exists():
        check_space(root, pending, reserve)
        local.parent.mkdir(parents=True, exist_ok=True)
        partial = local.with_suffix(".part")
        downloaded = 0
        last_print = time.monotonic()
        print(f"下载：Demucs/{demucs['file']} ({demucs['bytes']} 字节)", flush=True)
        with urlopen(demucs["url"], timeout=60) as response, partial.open("wb") as target:
            while chunk := response.read(1024**2):
                check_space(root, demucs["bytes"] - downloaded, reserve)
                target.write(chunk)
                downloaded += len(chunk)
                if time.monotonic() - last_print >= 15:
                    print(f"Demucs：{downloaded}/{demucs['bytes']} 字节", flush=True)
                    last_print = time.monotonic()
        verify_file(partial, demucs["bytes"], "8726e21a")
        partial.replace(local)
    actual = verify_file(local, demucs["bytes"], "8726e21a")
    records.append(
        {
            "repo": "facebookresearch/demucs",
            "filename": demucs["file"],
            "bytes": demucs["bytes"],
            "sha256": actual,
            "path": str(local.relative_to(root)),
        }
    )
    check_space(root, 0, reserve)
    manifest = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "seed_revision": audit["seed_repository"]["revision"],
        "hf_endpoint": os.environ["HF_ENDPOINT"],
        "files": records,
        "total_bytes": sum(record["bytes"] for record in records),
    }
    temporary = root / "install-manifest.tmp"
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(root / "install-manifest.json")
    config = PROJECT / "settings.json"
    values = json.loads(config.read_text())
    if Path(values["models_dir"]).resolve() != root:
        raise SystemExit("当前应用模型目录与下载目录不同；未启用离线推理。")
    values.update(hf_endpoint=os.environ["HF_ENDPOINT"])
    temporary = config.with_suffix(".tmp")
    temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(config)
    print(f"全部 {len(records)} 个文件已校验，合计 {manifest['total_bytes']} 字节。", flush=True)


if __name__ == "__main__":
    main()
