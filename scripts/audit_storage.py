"""Read file sizes and wheel ZIP directories without installing packages or model weights."""

import argparse
import io
import json
import subprocess
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import httpx
from packaging.tags import compatible_tags, cpython_tags, mac_platforms
from packaging.utils import parse_wheel_filename

PROJECT = Path(__file__).resolve().parent.parent
SEED_REVISION = "51383efd921027683c89e5348211d93ff12ac2a8"
SEED_WEIGHT = "DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema_v2.pth"
MODEL_FILES = {
    "Plachta/Seed-VC": [SEED_WEIGHT, "config_dit_mel_seed_uvit_whisper_base_f0_44k.yml"],
    "lj1995/VoiceConversionWebUI": ["rmvpe.pt"],
    "funasr/campplus": ["campplus_cn_common.bin"],
    "nvidia/bigvgan_v2_44khz_128band_512x": ["bigvgan_generator.pt", "config.json"],
    "openai/whisper-small": ["model.safetensors", "config.json", "preprocessor_config.json"],
}


def get_json(client, url):
    response = client.get(url)
    response.raise_for_status()
    return response.json()


class RemoteWheel(io.RawIOBase):
    def __init__(self, client, url, size):
        self.client, self.url, self.size = client, url, size
        self.position = 0
        self.bytes_fetched = 0

    def seekable(self):
        return True

    def seek(self, offset, whence=0):
        self.position = (
            offset
            if whence == 0
            else (self.position + offset if whence == 1 else self.size + offset)
        )
        return self.position

    def tell(self):
        return self.position

    def read(self, size=-1):
        size = self.size - self.position if size < 0 else size
        size = min(size, self.size - self.position)
        if size <= 0:
            return b""
        if size > 4 * 1024**2:
            raise ValueError("ZIP directory exceeds metadata download limit")
        start, end = self.position, self.position + size - 1
        with self.client.stream(
            "GET", self.url, headers={"Range": f"bytes={start}-{end}"}
        ) as response:
            if response.status_code != 206:
                raise ValueError(f"Server did not honor Range: {response.status_code}")
            content = response.read()
        if len(content) != size:
            raise ValueError("Incomplete ZIP range response")
        self.position += size
        self.bytes_fetched += size
        return content


def wheel_tags():
    platforms = list(mac_platforms(version=(15, 3), arch="arm64"))
    tags = list(cpython_tags(python_version=(3, 10), platforms=platforms))
    tags += list(compatible_tags(python_version=(3, 10), interpreter="cp310", platforms=platforms))
    return {tag: index for index, tag in enumerate(tags)}


def audit_package(client, requirement, priorities):
    name, version = requirement.split("==", 1)
    row = {"name": name, "version": version}
    try:
        info = get_json(client, f"https://pypi.org/pypi/{name}/{version}/json")
        candidates = []
        for file in info["urls"]:
            if file["packagetype"] != "bdist_wheel":
                continue
            tags = parse_wheel_filename(file["filename"])[3]
            ranks = [priorities[tag] for tag in tags if tag in priorities]
            if ranks:
                candidates.append((min(ranks), file))
        if not candidates:
            archive = next((f for f in info["urls"] if f["packagetype"] == "sdist"), None)
            if archive:
                row.update(
                    filename=archive["filename"],
                    download_bytes=archive["size"],
                    url=archive["url"],
                    format="sdist",
                    sha256=archive["digests"]["sha256"],
                )
            row["uncompressed_bytes"] = None
            row["note"] = "Source build required; installed size is unmeasured"
            return row
        file = min(candidates, key=lambda entry: entry[0])[1]
        row.update(
            filename=file["filename"],
            download_bytes=file["size"],
            url=file["url"],
            format="wheel",
            sha256=file["digests"]["sha256"],
        )
        remote = RemoteWheel(client, file["url"], file["size"])
        try:
            with zipfile.ZipFile(remote) as archive:
                row["uncompressed_bytes"] = sum(entry.file_size for entry in archive.infolist())
                row["file_count"] = len(archive.infolist())
            row["metadata_range_bytes"] = remote.bytes_fetched
        except (httpx.HTTPError, ValueError, zipfile.BadZipFile) as error:
            row["uncompressed_bytes"] = None
            row["note"] = str(error)
    except (httpx.HTTPError, ValueError, KeyError) as error:
        row["error"] = str(error)
    return row


def audit_models(client, endpoint):
    results = []
    for repo, paths in MODEL_FILES.items():
        row = {"repo": repo, "files": []}
        try:
            metadata = get_json(client, f"{endpoint}/api/models/{repo}?blobs=true")
            row["revision"] = metadata["sha"]
            files = {entry["rfilename"]: entry for entry in metadata["siblings"]}
            for path in paths:
                entry = files[path]
                row["files"].append(
                    {
                        "path": path,
                        "bytes": entry["size"],
                        "sha256": entry.get("lfs", {}).get("sha256"),
                        "url": f"https://huggingface.co/{repo}/blob/{metadata['sha']}/{path}",
                    }
                )
            row["total_bytes"] = sum(entry["bytes"] for entry in row["files"])
        except (httpx.HTTPError, KeyError, ValueError) as error:
            row["error"] = str(error)
        results.append(row)
    return results


def local_usage(path):
    if not path.exists():
        return {"path": str(path), "installed": False, "allocated_bytes": 0}
    result = subprocess.run(
        ["du", "-sk", str(path.resolve())], capture_output=True, text=True, check=True
    )
    return {
        "path": str(path),
        "installed": True,
        "allocated_bytes": int(result.stdout.split()[0]) * 1024,
    }


def audit_repository(client):
    row = {"revision": SEED_REVISION}
    try:
        info = get_json(
            client,
            f"https://api.github.com/repos/Plachtaa/seed-vc/git/trees/{SEED_REVISION}?recursive=1",
        )
        if info.get("truncated"):
            raise ValueError("GitHub tree response was truncated")
        blobs = [entry for entry in info["tree"] if entry["type"] == "blob"]
        row.update(
            tracked_file_bytes=sum(entry["size"] for entry in blobs),
            file_count=len(blobs),
            largest_files=sorted(blobs, key=lambda entry: entry["size"], reverse=True)[:8],
        )
    except (httpx.HTTPError, KeyError, ValueError) as error:
        row["error"] = str(error)
    return row


def audit_demucs(client):
    url = "https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/955717e8-8726e21a.th"
    row = {"name": "htdemucs", "file": "955717e8-8726e21a.th", "url": url}
    try:
        response = client.head(url)
        response.raise_for_status()
        row["bytes"] = int(response.headers["content-length"])
    except (httpx.HTTPError, KeyError, ValueError) as error:
        row["error"] = str(error)
    return row


def write_package_inventory(report, path):
    lines = [
        "# AI runtime package file inventory",
        "",
        f"Checked at: {report['checked_at']}",
        f"Target: {report['target']}",
        "",
        "All sizes are exact bytes. Download sizes come from the PyPI release API.",
        "Wheel payload sizes are sums of uncompressed ZIP entries, not installed disk usage.",
        "Source distributions require a build; their installed size is unmeasured.",
        "Build dependencies, bytecode, caches and filesystem allocation are excluded.",
        "",
        "| Package | Version | Download bytes | Wheel payload bytes | Archive |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for package in report["packages"]:
        download = package.get("download_bytes")
        payload = package.get("uncompressed_bytes")
        archive = package.get("filename", "unavailable")
        if package.get("url"):
            archive = f"[{archive}]({package['url']})"
        lines.append(
            f"| {package['name']} | {package['version']} | "
            f"{download if download is not None else 'unmeasured'} | "
            f"{payload if payload is not None else 'unmeasured'} | {archive} |"
        )
    totals = report["totals"]
    lines += [
        "",
        f"Package count: {totals['package_count']}",
        f"Verified download total: {totals['package_download_verified_bytes']} bytes",
        f"Verified wheel payload total: {totals['wheel_payload_verified_bytes']} bytes",
        f"Unknown payload count: {totals['packages_with_unknown_uncompressed_size']}",
        "",
        "URLs, archive SHA256 values, model revisions and local disk measurements are in the JSON report.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=PROJECT / "artifacts/storage-audit.json")
    parser.add_argument("--hf-endpoint", default="https://huggingface.co")
    parser.add_argument("--packages-output", type=Path)
    args = parser.parse_args()
    started = time.monotonic()
    requirements = [
        line.strip()
        for line in (PROJECT / "scripts/ai-requirements.lock").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    priorities = wheel_tags()
    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "target": "CPython 3.10 / macOS 15.3 / arm64",
        "hf_metadata_endpoint": args.hf_endpoint,
        "units": "bytes",
        "wheel_payload_is_installed_disk_usage": False,
    }
    with httpx.Client(
        timeout=httpx.Timeout(20, connect=5),
        follow_redirects=True,
        headers={"User-Agent": "local-ai-cover-storage-audit/0.1"},
    ) as client:
        result["models"] = audit_models(client, args.hf_endpoint.rstrip("/"))
        result["demucs"] = audit_demucs(client)
        result["seed_repository"] = audit_repository(client)
        with ThreadPoolExecutor(max_workers=8) as pool:
            result["packages"] = list(
                pool.map(lambda req: audit_package(client, req, priorities), requirements)
            )
    result["local"] = [
        local_usage(PROJECT / name)
        for name in ("app", ".venv", "node_modules", "data", "artifacts", ".git", "models")
    ]
    result["shared_installed"] = [
        local_usage(Path(path))
        for path in (
            "/opt/homebrew/Cellar/ffmpeg/7.1.1_2",
            Path.home() / ".local/share/uv/python/cpython-3.10-macos-aarch64-none",
            Path.home() / "Library/Caches/ms-playwright",
        )
    ]
    result["completeness"] = {
        "model_inventory_complete": all("total_bytes" in row for row in result["models"])
        and "bytes" in result["demucs"],
        "package_download_inventory_complete": all(
            "download_bytes" in row for row in result["packages"]
        ),
    }
    result["totals"] = {
        "model_files_verified_bytes": sum(row.get("total_bytes", 0) for row in result["models"])
        + result["demucs"].get("bytes", 0),
        "package_download_verified_bytes": sum(
            row.get("download_bytes", 0) for row in result["packages"]
        ),
        "wheel_payload_verified_bytes": sum(
            row.get("uncompressed_bytes") or 0 for row in result["packages"]
        ),
        "package_count": len(result["packages"]),
        "packages_with_unknown_uncompressed_size": sum(
            row.get("uncompressed_bytes") is None for row in result["packages"]
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if args.packages_output:
        write_package_inventory(result, args.packages_output)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "seconds": round(time.monotonic() - started, 1),
                "totals": result["totals"],
                "models": [
                    {
                        "repo": row["repo"],
                        "bytes": row.get("total_bytes"),
                        "error": row.get("error"),
                    }
                    for row in result["models"]
                ],
                "demucs": result["demucs"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
