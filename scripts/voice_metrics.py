"""Compare local vocal experiments using the installed pitch and speaker models."""

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np
import torch
import torchaudio

PROJECT = Path(__file__).resolve().parent.parent
ROOT = PROJECT / "artifacts/personal-cover"
FINISHED_VOICES = ("voice-像我这样的人", "voice-如愿", "voice-离别开出花")
sys.path.insert(0, str(PROJECT / "models/seed-vc"))
from modules.campplus.DTDNN import CAMPPlus  # noqa: E402
from modules.rmvpe import RMVPE  # noqa: E402


def decode(path, rate=16000, mono=True):
    channels = []
    if mono:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=channels",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        count = int(probe.stdout.strip())
        matrix = "+".join(f"{1 / count}*c{index}" for index in range(count))
        channels = ["-af", f"pan=mono|c0={matrix}"]
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            *channels,
            "-ar",
            str(rate),
            "-f",
            "f32le",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    return np.frombuffer(result.stdout, dtype="<f4").copy()


def cosine(a, b):
    return float(np.dot(a, b) / max(np.linalg.norm(a) * np.linalg.norm(b), 1e-9))


def envelope(wave):
    frames = wave[: len(wave) // 160 * 160].reshape(-1, 160)
    return np.sqrt(np.mean(frames * frames, axis=1))


def pitch_comparison(source, converted, semitones):
    n = min(len(source), len(converted))
    source, converted = source[:n], converted[:n]
    mask = (source > 50) & (converted > 50)
    if not mask.any():
        return {"joint_voiced_frames": 0}
    errors = 1200 * np.log2(converted[mask] / source[mask]) - semitones * 100
    return {
        "joint_voiced_frames": int(mask.sum()),
        "median_absolute_cents": float(np.median(np.abs(errors))),
        "within_100_cents_fraction": float(np.mean(np.abs(errors) < 100)),
        "voiced_recall": float(mask.sum() / max((source > 50).sum(), 1)),
    }


class Analyzer:
    def __init__(self, models):
        manifest = json.loads((models / "install-manifest.json").read_text())
        paths = {f["filename"]: models / f["path"] for f in manifest["files"]}
        self.pitch = RMVPE(str(paths["rmvpe.pt"]), is_half=False, device="cpu")
        self.speaker = CAMPPlus(feat_dim=80, embedding_size=192).eval()
        self.speaker.load_state_dict(
            torch.load(paths["campplus_cn_common.bin"], map_location="cpu", weights_only=True)
        )
        self.cache = ROOT / "metrics-cache"
        self.cache.mkdir(parents=True, exist_ok=True)

    @torch.inference_mode()
    def analyze(self, path):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        wave = decode(path)
        embedding_start = 35 if len(wave) > 60 * 16000 else 0
        cache = self.cache / f"{digest}-at{embedding_start}-average.npz"
        if cache.exists():
            with np.load(cache) as data:
                f0, embedding = data["f0"], data["embedding"]
        else:
            pieces = []
            for start in range(0, len(wave), 16000 * 20):
                clip = wave[start : start + 16000 * 20]
                if len(clip) < 1024:
                    clip = np.pad(clip, (0, 1024 - len(clip)))
                f0 = self.pitch.infer_from_audio(clip, thred=0.03)
                pieces.append(f0[: int(np.ceil(len(wave[start : start + 16000 * 20]) / 160))])
            f0 = np.concatenate(pieces)
            # Speaker comparisons use the same Kaldi features as the pinned Seed-VC model.
            reference = torch.from_numpy(
                wave[embedding_start * 16000 : (embedding_start + 18) * 16000]
            ).unsqueeze(0)
            features = torchaudio.compliance.kaldi.fbank(
                reference,
                num_mel_bins=80,
                dither=0,
                sample_frequency=16000,
            )
            features -= features.mean(dim=0, keepdim=True)
            embedding = self.speaker(features.unsqueeze(0))[0].numpy()
            np.savez_compressed(cache, f0=f0, embedding=embedding)
        voiced = f0[f0 > 50]
        window_frames = min(800, len(f0))
        starts = range(0, len(f0) - window_frames + 1, 25)
        best_start = max(starts, key=lambda start: np.mean(f0[start : start + window_frames] > 50))
        rms = float(np.sqrt(np.mean(wave * wave)))
        native = decode(path, rate=44100, mono=False)
        return (
            {
                "sha256": digest,
                "duration": len(wave) / 16000,
                "speaker_analysis_start_seconds": embedding_start,
                "peak_per_channel": float(np.max(np.abs(native))),
                "rms_dbfs": float(20 * np.log10(max(rms, 1e-10))),
                "near_full_scale_channel_samples": int((np.abs(native) >= 0.9999).sum()),
                "voiced_fraction": float(len(voiced) / len(f0)),
                "suggested_8s_reference_start": best_start / 100,
                "suggested_window_voiced_fraction": float(
                    np.mean(f0[best_start : best_start + window_frames] > 50)
                ),
                "pitch_hz_p10_p50_p90": np.percentile(voiced, [10, 50, 90]).tolist()
                if len(voiced)
                else [],
            },
            f0,
            embedding,
            envelope(wave),
        )


def asset_from_database(asset_id):
    """Read-only fallback for assets that were overwritten in the session file."""
    database = PROJECT / "data/database/studio.sqlite3"
    if not database.is_file():
        return None
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                "SELECT id, kind, name, path, duration FROM assets WHERE id=?", (asset_id,)
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    return dict(row) if row else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", type=Path, default=ROOT / "session.json")
    args = parser.parse_args()
    session = json.loads(args.session.read_text())
    analyzer = Analyzer(PROJECT / "models")
    index = {asset["id"]: asset for asset in session["assets"].values()}
    for job in session["jobs"].values():
        for asset in job.get("assets", []):
            index.setdefault(asset["id"], asset)
    paths = {
        key: Path(session["data_dir"]) / asset["path"]
        for key, asset in session["assets"].items()
        if key == "recording" or key.startswith("voice-")
    }
    converted_jobs = {}
    for key, job in session["jobs"].items():
        converted = next(
            (a for a in job.get("assets", []) if a["metadata"].get("role") == "converted_vocal"),
            None,
        )
        if not converted:
            continue
        converted_jobs[key] = converted
        paths[key] = Path(session["data_dir"]) / converted["path"]
        source = index.get(job["params"].get("source_id")) or asset_from_database(
            job["params"].get("source_id")
        )
        if source:
            paths[f"{key} · 来源人声"] = Path(session["data_dir"]) / source["path"]
        else:
            print(f"警告：{key} 的来源资产已不存在，跳过来源对比。", flush=True)
    report, arrays = {}, {}
    for key, path in paths.items():
        print(f"分析：{key}", flush=True)
        report[key], *arrays[key] = analyzer.analyze(path)
    if "recording" in arrays:
        for key, (_, embedding, _) in arrays.items():
            report[key]["similarity_to_recording"] = cosine(embedding, arrays["recording"][1])
            report[key]["similarity_to_finished_vocals"] = {
                name: cosine(embedding, entry[1])
                for name, entry in arrays.items()
                if name in FINISHED_VOICES
            }
    for key in converted_jobs:
        source_key = f"{key} · 来源人声"
        if key not in arrays or source_key not in arrays:
            continue
        f0, embedding, env = arrays[key]
        source_f0, source_embedding, source_envelope = arrays[source_key]
        semitones = session["jobs"][key]["params"]["pitch_shift"]
        report[key]["similarity_to_source"] = cosine(embedding, source_embedding)
        report[key]["pitch_preservation"] = pitch_comparison(source_f0, f0, semitones)
        correlations = []
        for lag in range(-20, 21):
            a = source_envelope[max(0, -lag) :]
            b = env[max(0, lag) :]
            n = min(len(a), len(b))
            correlations.append((float(np.corrcoef(a[:n], b[:n])[0, 1]), lag * 10))
        corr, lag = max(correlations)
        report[key]["envelope_correlation"] = corr
        report[key]["estimated_lag_ms"] = lag
        if len(source_f0) > 3000:
            report[key]["intro_vocal_rms_dbfs"] = float(
                20 * np.log10(max(np.sqrt(np.mean(env[:2400] ** 2)), 1e-10))
            )
            report[key]["outro_vocal_rms_dbfs"] = float(
                20 * np.log10(max(np.sqrt(np.mean(env[-600:] ** 2)), 1e-10))
            )
            report[key]["section_pitch_preservation"] = {
                str(start): pitch_comparison(
                    source_f0[start * 100 : (start + 18) * 100],
                    f0[start * 100 : (start + 18) * 100],
                    semitones,
                )
                for start in (35, 85, 135)
            }
        singing = [
            cosine(embedding, entry[1])
            for name, entry in arrays.items()
            if name in FINISHED_VOICES
        ]
        if singing:
            report[key]["mean_similarity_to_finished_vocals"] = float(np.mean(singing))
    output = ROOT / "voice-metrics.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"分析结果：{output}", flush=True)
    for key in session["jobs"]:
        if key in report:
            summary = {"name": key, **report[key]}
            summary.pop("similarity_to_finished_vocals", None)
            print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    torch.set_num_threads(4)
    main()
