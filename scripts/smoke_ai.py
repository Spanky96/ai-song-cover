"""Run a real local API cover job with provided audio or verified upstream examples."""

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path

import httpx

PROJECT = Path(__file__).resolve().parent.parent
FIXTURES = [
    (
        "song.mp3",
        "https://cdn.jsdelivr.net/gh/facebookresearch/demucs@v4.0.1/test.mp3",
        "668604d876880d39913ef73bcf9df4b4308a9d70",
    ),
    (
        "reference.wav",
        "https://cdn.jsdelivr.net/gh/Plachtaa/seed-vc@"
        "51383efd921027683c89e5348211d93ff12ac2a8/examples/reference/teio_0.wav",
        "1968dc484cac879175c1133651e363990b6aa98d",
    ),
]


def prepare_examples(folder):
    folder.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=httpx.Timeout(30, connect=5), follow_redirects=True) as client:
        for name, url, expected in FIXTURES:
            path = folder / name
            if path.exists():
                content = path.read_bytes()
                if (
                    hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()
                    == expected
                ):
                    continue
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
            if digest != expected:
                raise ValueError(f"Upstream example Git blob hash mismatch: {name}")
            path.write_bytes(content)
            print(f"上游示例已校验：{name} ({len(content)} 字节)", flush=True)


def upload(client, path, kind, name):
    with path.open("rb") as file:
        response = client.post(
            "/api/assets",
            data={"kind": kind, "name": name},
            files={"file": (path.name, file, "audio/wav")},
        )
    response.raise_for_status()
    return response.json()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--seconds", type=float, default=6)
    parser.add_argument("--steps", type=int, default=10)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if bool(args.source) != bool(args.reference):
        raise SystemExit("演唱来源与参考音色需同时提供。")
    if not 1 <= args.seconds <= 30 or not 10 <= args.steps <= 100:
        raise SystemExit("验收时长需为 1 至 30 秒，推理步数需为 10 至 100。")
    root = PROJECT / "artifacts/ai-validation"
    root.mkdir(parents=True, exist_ok=True)
    fixtures = root / "fixtures"
    upstream = args.source is None
    if upstream:
        prepare_examples(fixtures)
    if args.prepare_only:
        return
    source = args.source or fixtures / "song.mp3"
    reference = args.reference or fixtures / "reference.wav"
    inputs = []
    for original, name in ((source, "source.wav"), (reference, "voice.wav")):
        path = root / name
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-v",
                "error",
                "-i",
                str(original),
                "-t",
                str(args.seconds),
                "-ar",
                "44100",
                "-ac",
                "2",
                "-c:a",
                "pcm_s16le",
                str(path),
            ],
            check=True,
        )
        inputs.append(path)
    label = "上游示例" if upstream else "个人素材"
    with httpx.Client(base_url=args.url, timeout=60) as client:
        status_response = client.get("/api/status")
        status_response.raise_for_status()
        status = status_response.json()
        if not (
            status["seed"]["ready"] and status["demucs"]["ready"] and status["model_weights_ready"]
        ):
            raise SystemExit("真实推理环境或权重未就绪。")
        song = upload(client, inputs[0], "song", f"安装验收 · {label}歌曲")
        voice = upload(client, inputs[1], "voice", f"安装验收 · {label}参考音色")
        response = client.post(
            "/api/jobs",
            json={
                "source_id": song["id"],
                "voice_id": voice["id"],
                "name": f"安装验收 · {label} AI 翻唱",
                "diffusion_steps": args.steps,
            },
        )
        response.raise_for_status()
        job_id = response.json()["id"]
        (root / "latest-job.json").write_text(json.dumps({"job_id": job_id}))
        print(f"真实任务：{job_id}", flush=True)
        deadline = time.monotonic() + 900
        previous = None
        while time.monotonic() < deadline:
            response = client.get("/api/jobs")
            response.raise_for_status()
            job = next(job for job in response.json() if job["id"] == job_id)
            stage = (job["state"], job["stage"])
            if stage != previous:
                print(f"{job['state']}：{job['stage']} ({job['progress']}%)", flush=True)
                previous = stage
            if job["state"] == "completed":
                break
            if job["state"] not in ("queued", "running", "cancelling"):
                raise RuntimeError(job.get("error") or job["state"])
            time.sleep(1)
        else:
            client.post(f"/api/jobs/{job_id}/cancel").raise_for_status()
            raise RuntimeError("真实推理验收超时，已请求取消。")
        response = client.get("/api/assets")
        response.raise_for_status()
        generated = [
            asset for asset in response.json() if asset["metadata"].get("job_id") == job_id
        ]
        if sorted(asset["kind"] for asset in generated) != [
            "instrumental",
            "output",
            "output",
            "vocal",
            "vocal",
        ]:
            raise AssertionError("Missing separation or export assets")
        for asset in generated:
            if abs(asset["duration"] - song["duration"]) > 0.15 or max(asset["waveform"]) <= 0:
                raise AssertionError(f"Invalid audio duration or silent result: {asset['name']}")
            response = client.get(f"/api/assets/{asset['id']}/audio?download=true")
            response.raise_for_status()
            path = root / (asset["kind"] + "-" + asset["id"] + Path(asset["path"]).suffix)
            path.write_bytes(response.content)
            if not response.content or "attachment" not in response.headers.get(
                "content-disposition", ""
            ):
                raise AssertionError("Audio download failed")
        report = {
            "used_upstream_examples": upstream,
            "job": job,
            "generated": generated,
            "input_duration": song["duration"],
            "environment": status,
        }
        (root / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "job_id": job_id,
                    "state": job["state"],
                    "used_upstream_examples": upstream,
                    "outputs": [
                        {
                            "kind": asset["kind"],
                            "duration": asset["duration"],
                            "path": asset["path"],
                        }
                        for asset in generated
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
