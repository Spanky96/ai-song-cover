import array
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path


class AudioError(ValueError):
    pass


def probe(path: Path) -> dict:
    if not shutil.which("ffprobe"):
        raise AudioError("未安装 FFmpeg / ffprobe。")
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        info = json.loads(result.stdout)
        stream = next(s for s in info["streams"] if s["codec_type"] == "audio")
        duration = float(info.get("format", {}).get("duration") or stream.get("duration", 0))
        if not math.isfinite(duration) or duration <= 0 or duration > 1800:
            raise AudioError("音频时长必须大于 0 且不超过 30 分钟。")
        return {
            "duration": duration,
            "sample_rate": int(stream["sample_rate"]),
            "channels": int(stream["channels"]),
            "size": path.stat().st_size,
        }
    except (subprocess.SubprocessError, KeyError, StopIteration, ValueError) as error:
        if isinstance(error, AudioError):
            raise
        raise AudioError("无法读取音频，请上传有效的 WAV、MP3、FLAC、M4A 或 OGG 文件。") from error


def waveform(path: Path, bins: int = 100) -> list[float]:
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "1000",
                "-f",
                "s16le",
                "pipe:1",
            ],
            capture_output=True,
            timeout=60,
            check=True,
        )
        samples = array.array("h", result.stdout)
        if sys.byteorder != "little":
            samples.byteswap()
        stride = max(1, len(samples) // bins)
        peaks = [
            max((abs(s) for s in samples[i : i + stride]), default=0) / 32768
            for i in range(0, len(samples), stride)
        ][:bins]
        return [round(p, 4) for p in peaks]
    except subprocess.SubprocessError as error:
        raise AudioError("音频解码失败，请检查文件是否损坏。") from error


def normalize_command(source: Path, target: Path) -> list[str]:
    return [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-v",
        "error",
        "-i",
        str(source),
        "-vn",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        str(target),
    ]


def mix_command(
    vocal: Path,
    instrumental: Path | None,
    target: Path,
    duration: float,
    vocal_db: float = 0,
    instrumental_db: float = -3,
    offset_ms: int = 0,
) -> list[str]:
    # Keep the source timeline; negative offsets trim, positive offsets add silence.
    vocal_filter = "aresample=44100"
    if offset_ms < 0:
        vocal_filter += f",atrim=start={-offset_ms / 1000:.3f},asetpts=PTS-STARTPTS"
    elif offset_ms > 0:
        vocal_filter += f",adelay={offset_ms}:all=1"
    vocal_filter += f",volume={vocal_db}dB,apad"
    command = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(vocal)]
    if instrumental:
        command += ["-i", str(instrumental)]
        filters = (
            f"[0:a]{vocal_filter}[v];"
            f"[1:a]aresample=44100,volume={instrumental_db}dB,apad[b];"
            "[v][b]amix=inputs=2:duration=longest:normalize=0,"
            "alimiter=limit=0.95:level=0:latency=1[out]"
        )
    else:
        filters = f"[0:a]{vocal_filter},alimiter=limit=0.95:level=0:latency=1[out]"
    return command + [
        "-filter_complex",
        filters,
        "-map",
        "[out]",
        "-t",
        f"{duration:.6f}",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-c:a",
        "pcm_s24le",
        str(target),
    ]


def mp3_command(source: Path, target: Path) -> list[str]:
    return [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-v",
        "error",
        "-i",
        str(source),
        "-codec:a",
        "libmp3lame",
        "-b:a",
        "320k",
        str(target),
    ]
