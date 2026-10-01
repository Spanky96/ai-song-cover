"""Import the supplied personal library and keep reproducible cover experiments."""

import argparse
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

import httpx

PROJECT = Path(__file__).resolve().parent.parent
ROOT = PROJECT / "artifacts/personal-cover"
SESSION = ROOT / "session.json"


def save(session):
    ROOT.mkdir(parents=True, exist_ok=True)
    temporary = SESSION.with_suffix(".tmp")
    temporary.write_text(json.dumps(session, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(SESSION)


def assets(client):
    response = client.get("/api/assets")
    response.raise_for_status()
    return {asset["id"]: asset for asset in response.json()}


def asset_index(session):
    index = {asset["id"]: asset for asset in session.get("assets", {}).values()}
    for job in session.get("jobs", {}).values():
        for asset in job.get("assets", []):
            index.setdefault(asset["id"], asset)
    return index


def job_asset(session, job_key, kind, role=None):
    candidates = session["jobs"][job_key].get("assets", [])
    if role:
        return next(asset for asset in candidates if asset["metadata"].get("role") == role)
    return next(asset for asset in candidates if asset["kind"] == kind)


def max_volume_db(path):
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=True,
    )
    for line in result.stderr.splitlines():
        if "max_volume:" in line:
            return float(line.split("max_volume:")[1].replace("dB", "").strip())
    raise ValueError(f"volumedetect 未报告峰值：{path}")


def duration_of(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def import_audio(client, session, key, path, kind, name):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    existing = session.setdefault("assets", {}).get(key)
    library = assets(client)
    if existing and existing["sha256"] == digest and existing["id"] in library:
        if library[existing["id"]]["name"] != name:
            response = client.patch(f"/api/assets/{existing['id']}", json={"name": name})
            response.raise_for_status()
            existing["name"] = name
            save(session)
        return existing
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    info = json.loads(result.stdout)["format"]
    suffix = ".m4a" if "m4a" in info["format_name"] else path.suffix
    with path.open("rb") as audio:
        response = client.post(
            "/api/assets",
            data={"kind": kind, "name": name},
            files={"file": (path.stem + suffix, audio, "application/octet-stream")},
        )
    response.raise_for_status()
    asset = response.json()
    session["assets"][key] = {
        "id": asset["id"],
        "sha256": digest,
        "original_path": str(path),
        "path": asset["path"],
        "name": asset["name"],
        "duration": asset["duration"],
    }
    save(session)
    print(f"导入：{name}，{asset['duration']:.2f} 秒，{asset['id']}", flush=True)
    return session["assets"][key]


def wait_job(client, job_id, timeout=3600):
    deadline = time.monotonic() + timeout
    previous, last_print = None, 0
    while time.monotonic() < deadline:
        response = client.get("/api/jobs")
        response.raise_for_status()
        job = next(job for job in response.json() if job["id"] == job_id)
        state = (job["state"], job["stage"])
        if state != previous or time.monotonic() - last_print > 30:
            print(f"{job['name']}：{job['state']} / {job['stage']}", flush=True)
            previous, last_print = state, time.monotonic()
        if job["state"] == "completed":
            return job
        if job["state"] not in ("queued", "running", "cancelling"):
            raise RuntimeError(job["error"] or job["state"])
        time.sleep(1)
    client.post(f"/api/jobs/{job_id}/cancel").raise_for_status()
    raise TimeoutError(f"Job timed out: {job_id}")


def run_job(client, session, key, params):
    record = session.setdefault("jobs", {}).get(key)
    if record and record["params"] != params:
        raise ValueError(f"Experiment key already uses different parameters: {key}")
    if not record:
        response = client.post("/api/jobs", json=params)
        response.raise_for_status()
        record = {"id": response.json()["id"], "params": params}
        session["jobs"][key] = record
        save(session)
    job = wait_job(client, record["id"])
    record["result"] = job
    record["assets"] = [
        asset for asset in assets(client).values() if asset["metadata"].get("job_id") == job["id"]
    ]
    save(session)
    return record


def trim(path, target, start, seconds):
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-v",
            "error",
            "-ss",
            str(start),
            "-i",
            str(path),
            "-t",
            str(seconds),
            "-ar",
            "44100",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(target),
        ],
        check=True,
    )


def prepare(client, session, folder):
    import_audio(
        client, session, "recording", folder / "我的声音.m4a", "voice", "我的声音 · 原始录音"
    )
    for name in ("像我这样的人", "如愿", "离别开出花"):
        import_audio(
            client,
            session,
            name,
            folder / "我翻唱的歌曲" / f"{name}.mp3",
            "song",
            f"我的成品 · {name}",
        )
    target = import_audio(
        client,
        session,
        "target",
        folder / "目标翻唱/我和我的祖国.m4a",
        "song",
        "目标原曲 · 我和我的祖国",
    )
    run_job(
        client,
        session,
        "target-separation",
        {
            "job_type": "separate",
            "source_id": target["id"],
            "name": "我和我的祖国 · 原曲人声分离",
        },
    )
    for index, name in enumerate(("像我这样的人", "如愿", "离别开出花")):
        clip = ROOT / f"reference-song-{index}.wav"
        trim(Path(session["data_dir"]) / session["assets"][name]["path"], clip, 35, 18)
        source = import_audio(
            client, session, f"clip-{name}", clip, "song", f"音色提取 · {name} · 35–53秒"
        )
        separated = run_job(
            client,
            session,
            f"separate-{name}",
            {
                "job_type": "separate",
                "source_id": source["id"],
                "name": f"音色提取 · {name}",
            },
        )
        vocal = next(asset for asset in separated["assets"] if asset["kind"] == "vocal")
        import_audio(
            client,
            session,
            f"voice-{name}",
            Path(session["data_dir"]) / vocal["path"],
            "voice",
            f"我的歌声音色 · {name}",
        )


def prepare_targets(client, session, folder):
    targets = {
        "永不失联的爱": folder / "目标翻唱/永不失联的爱.m4a",
        "庐州月": folder / "目标翻唱/庐州月mp3.mp3",
        "好久不见": folder / "目标翻唱/好久不见.m4a",
    }
    for name, path in targets.items():
        source = import_audio(client, session, f"target-{name}", path, "song", f"目标原曲 · {name}")
        run_job(
            client,
            session,
            f"target-separation-{name}",
            {
                "job_type": "separate",
                "source_id": source["id"],
                "name": f"{name} · 原曲人声分离",
            },
        )


def excerpt(client, session, start, seconds):
    separated = session["jobs"]["target-separation"]["assets"]
    for kind in ("vocal", "instrumental"):
        asset = next(asset for asset in separated if asset["kind"] == kind)
        path = ROOT / f"target-{kind}-{start:g}-{seconds:g}.wav"
        trim(Path(session["data_dir"]) / asset["path"], path, start, seconds)
        label = {"vocal": "人声", "instrumental": "伴奏"}[kind]
        import_audio(
            client,
            session,
            f"excerpt-{kind}",
            path,
            kind,
            f"我和我的祖国 · 调优{label} · {start:g}秒起",
        )
    session["excerpt"] = {"start": start, "seconds": seconds}
    save(session)


def target_excerpt(client, session, target_key, start, seconds):
    separated = session["jobs"][f"target-separation-{target_key}"]["assets"]
    for kind in ("vocal", "instrumental"):
        asset = next(asset for asset in separated if asset["kind"] == kind)
        path = ROOT / f"target-{target_key}-{kind}-{start:g}-{seconds:g}.wav"
        trim(Path(session["data_dir"]) / asset["path"], path, start, seconds)
        label = {"vocal": "人声", "instrumental": "伴奏"}[kind]
        import_audio(
            client,
            session,
            f"excerpt-{target_key}-{kind}",
            path,
            kind,
            f"{target_key} · 调优{label} · {start:g}秒起",
        )
    session[f"excerpt-{target_key}"] = {"start": start, "seconds": seconds}
    save(session)


def lookup_asset(client, session, asset_id):
    return assets(client).get(asset_id) or asset_index(session).get(asset_id)


def shift_instrumental(client, session, args):
    if args.from_id:
        source = lookup_asset(client, session, args.from_id)
        if not source:
            raise KeyError(f"Asset not found: {args.from_id}")
    elif args.from_asset:
        source = session["assets"][args.from_asset]
    elif args.from_job:
        source = job_asset(session, args.from_job, "instrumental")
    else:
        raise ValueError("shift-instrumental needs --from-id, --from-asset or --from-job")
    source_path = Path(session["data_dir"]) / source["path"]
    tag = args.out_key or f"{source['id']}-{args.semitones:+d}st"
    shifted = ROOT / f"instrumental-{tag}.wav"
    subprocess.run(
        ["rubberband", "-p", str(args.semitones), str(source_path), str(shifted)],
        check=True,
    )
    drift = abs(duration_of(shifted) - source["duration"])
    if drift > 0.05:
        raise ValueError(f"移调后时长偏差 {drift:.3f} 秒，超过 0.05 秒容差。")
    name = args.name or f"{source['name']} · 降{-args.semitones}半音"
    asset = import_audio(client, session, args.out_key or f"shifted-{tag}", shifted, "instrumental", name)
    print(f"移调完成：{name}，峰值 {max_volume_db(shifted):.2f} dBFS", flush=True)
    return asset


def remix(client, session, args):
    vocal = job_asset(session, args.vocal_job, "vocal", role="converted_vocal")
    if args.instrumental_key:
        instrumental = session["assets"][args.instrumental_key]
    elif args.match_source:
        instrumental_id = session["jobs"][args.vocal_job]["params"]["instrumental_id"]
        instrumental = lookup_asset(client, session, instrumental_id)
        if not instrumental:
            raise KeyError(f"Instrumental asset not found: {instrumental_id}")
    else:
        raise ValueError("remix needs --instrumental-key or --match-source")
    run_job(
        client,
        session,
        args.label,
        {
            "name": args.label,
            "job_type": "mix",
            "source_id": vocal["id"],
            "instrumental_id": instrumental["id"],
            "vocal_gain_db": args.vocal_gain,
            "instrumental_gain_db": args.instrumental_gain,
            "formats": ["wav", "mp3"],
        },
    )
    record = session["jobs"][args.label]
    mix = next(asset for asset in record["assets"] if asset["kind"] == "output")
    record["peak_check"] = {
        "converted_vocal_max_volume_db": max_volume_db(Path(session["data_dir"]) / vocal["path"]),
        "mix_max_volume_db": max_volume_db(Path(session["data_dir"]) / mix["path"]),
    }
    save(session)
    print(f"混音峰值：{json.dumps(record['peak_check'], ensure_ascii=False)}", flush=True)


def cover(client, session, args, full):
    if full:
        job_key = f"target-separation-{args.target_key}" if args.target_key else "target-separation"
        separated = session["jobs"][job_key]["assets"]
        vocal = next(a for a in separated if a["kind"] == "vocal")
        instrumental = next(a for a in separated if a["kind"] == "instrumental")
    else:
        target_key = args.target_key or ""
        vocal = (
            session["assets"][f"excerpt-{target_key}-vocal"]
            if target_key
            else session["assets"]["excerpt-vocal"]
        )
        instrumental = (
            session["assets"][f"excerpt-{target_key}-instrumental"]
            if target_key
            else session["assets"]["excerpt-instrumental"]
        )
    if args.instrumental_key:
        instrumental = session["assets"][args.instrumental_key]
    run_job(
        client,
        session,
        args.label,
        {
            "name": args.label,
            "source_id": vocal["id"],
            "instrumental_id": instrumental["id"],
            "voice_id": session["assets"][args.voice]["id"],
            "diffusion_steps": args.steps,
            "cfg_rate": args.cfg,
            "pitch_shift": args.pitch,
            "vocal_gain_db": args.vocal_gain,
            "instrumental_gain_db": args.instrumental_gain,
            "formats": ["wav", "mp3"],
        },
    )


def reference(client, session, args):
    source = session["assets"][args.voice]
    path = ROOT / f"reference-{source['id']}-{args.start:g}-{args.seconds:g}.wav"
    trim(Path(session["data_dir"]) / source["path"], path, args.start, args.seconds)
    asset = import_audio(client, session, "voice-selected", path, "voice", args.label)
    asset.update(reference_from=args.voice, clip_start=args.start, clip_seconds=args.seconds)
    save(session)


def export(client, session, args):
    job = session["jobs"][args.label]
    if job.get("result", {}).get("state") != "completed":
        raise ValueError("Only completed experiments can be exported")
    folder = ROOT / "deliverables"
    folder.mkdir(exist_ok=True)
    expected_duration = asset_index(session)[job["params"]["source_id"]]["duration"]
    records = []
    for asset in job["assets"]:
        if asset["kind"] != "output":
            continue
        path = Path(session["data_dir"]) / asset["path"]
        if abs(asset["duration"] - expected_duration) > 0.15:
            raise ValueError("Export duration does not match the full source")
        response = client.get(f"/api/assets/{asset['id']}/audio?download=true")
        response.raise_for_status()
        expected_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if hashlib.sha256(response.content).hexdigest() != expected_hash:
            raise ValueError("Downloaded asset hash mismatch")
        destination = folder / (args.label + path.suffix)
        shutil.copy2(path, destination)
        info = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            check=True,
        )
        loudness = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostats",
                "-i",
                str(path),
                "-af",
                "loudnorm=I=-16:TP=-1:LRA=11:print_format=json",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        measurement, _ = json.JSONDecoder().raw_decode(
            loudness.stderr[loudness.stderr.rfind("{") :]
        )
        records.append(
            {
                "asset_id": asset["id"],
                "path": str(destination),
                "sha256": expected_hash,
                "bytes": destination.stat().st_size,
                "duration": asset["duration"],
                "audio_info": json.loads(info.stdout),
                "integrated_lufs": float(measurement["input_i"]),
                "true_peak_dbfs": float(measurement["input_tp"]),
                "loudness_range_lu": float(measurement["input_lra"]),
            }
        )
        print(f"导出并校验：{destination}", flush=True)
    session.setdefault("deliverables_by_label", {})[args.label] = records
    save(session)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase",
        choices=(
            "prepare",
            "prepare-targets",
            "excerpt",
            "target-excerpt",
            "target",
            "reference",
            "compare",
            "full",
            "full-target",
            "shift-instrumental",
            "remix",
            "export",
        ),
    )
    parser.add_argument("--input-dir", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--start", type=float, default=35)
    parser.add_argument("--seconds", type=float, default=18)
    parser.add_argument("--target-key")
    parser.add_argument("--from-id")
    parser.add_argument("--from-asset")
    parser.add_argument("--from-job")
    parser.add_argument("--semitones", type=int)
    parser.add_argument("--out-key")
    parser.add_argument("--vocal-job")
    parser.add_argument("--match-source", action="store_true")
    parser.add_argument("--instrumental-key")
    parser.add_argument("--name")
    parser.add_argument("--voice", default="recording")
    parser.add_argument("--label", default="我和我的祖国 · 原始录音参考")
    parser.add_argument("--steps", type=int, default=30)
    parser.add_argument("--cfg", type=float, default=0.7)
    parser.add_argument("--pitch", type=int, default=0)
    parser.add_argument("--vocal-gain", type=float, default=0)
    parser.add_argument("--instrumental-gain", type=float, default=-3)
    args = parser.parse_args()
    session = json.loads(SESSION.read_text()) if SESSION.exists() else {}
    ROOT.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=args.url, timeout=120) as client:
        status = client.get("/api/status")
        status.raise_for_status()
        session["data_dir"] = status.json()["storage"]["data_dir"]
        if args.phase == "prepare":
            if not args.input_dir:
                parser.error("prepare requires --input-dir")
            prepare(client, session, args.input_dir)
        elif args.phase == "prepare-targets":
            if not args.input_dir:
                parser.error("prepare-targets requires --input-dir")
            prepare_targets(client, session, args.input_dir)
        elif args.phase == "excerpt":
            excerpt(client, session, args.start, args.seconds)
        elif args.phase == "target-excerpt":
            if not args.target_key:
                parser.error("target-excerpt requires --target-key")
            target_excerpt(client, session, args.target_key, args.start, args.seconds)
        elif args.phase == "reference":
            reference(client, session, args)
        elif args.phase == "target":
            if not args.target_key:
                parser.error("target requires --target-key")
            cover(client, session, args, False)
        elif args.phase == "full-target":
            if not args.target_key:
                parser.error("full-target requires --target-key")
            cover(client, session, args, True)
        elif args.phase == "shift-instrumental":
            if args.semitones is None:
                parser.error("shift-instrumental requires --semitones")
            shift_instrumental(client, session, args)
        elif args.phase == "remix":
            if not args.vocal_job:
                parser.error("remix requires --vocal-job")
            remix(client, session, args)
        elif args.phase == "export":
            export(client, session, args)
        else:
            cover(client, session, args, args.phase == "full")


if __name__ == "__main__":
    main()
