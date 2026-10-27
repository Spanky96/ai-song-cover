"""MCP 工具编排：把翻唱工作室的主要流程暴露为可调用工具。

典型用法是 voice_swap 一键换声；细粒度工具用于试听片段、直接混音、
人声分离、排查任务日志等进阶场景。
"""

import os
import time
from pathlib import Path
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .client import (
    StudioClient,
    StudioError,
    summarize_asset,
)
from .launcher import ensure_running

TERMINAL_STATES = {"completed", "failed", "cancelled", "interrupted"}
POLL_SECONDS = 5.0
DEFAULT_WAIT_SECONDS = 1800.0


def default_output_dir() -> Path:
    return Path(os.environ.get("COVER_MCP_OUTPUT_DIR", "~/Downloads")).expanduser()


def resolve_voice(assets: list[dict], voice: str | None) -> dict:
    """按资产 ID 或名称关键词解析参考音色；voice 为空时匹配「我的声音」。"""
    voices = [asset for asset in assets if asset["kind"] == "voice"]
    if not voices:
        raise StudioError("音色库为空，请先用 upload_audio(kind=\"voice\") 上传 1-30 秒干净人声。")
    keyword = voice or "我的声音"
    matched = [asset for asset in voices if asset["id"] == keyword]
    if not matched:
        matched = [asset for asset in voices if keyword in asset["name"]]
    if len(matched) == 1:
        return matched[0]
    listing = "\n".join(
        f"- {asset['name']}（{asset.get('duration', 0):.1f}s, id={asset['id']}）"
        for asset in voices
    )
    if not matched:
        raise StudioError(f"没有找到匹配「{keyword}」的音色。现有音色：\n{listing}")
    raise StudioError(
        f"「{keyword}」匹配到 {len(matched)} 个音色，请指定更精确的名称或资产 ID。现有音色：\n{listing}"
    )


def summarize_job(job: dict) -> dict:
    params = job.get("params") or {}
    return {
        "id": job["id"],
        "name": job["name"],
        "state": job["state"],
        "stage": job.get("stage"),
        "progress": job.get("progress"),
        "error": job.get("error"),
        "outputs": job.get("outputs") or [],
        "job_type": params.get("job_type"),
        "source_name": params.get("source_name"),
        "voice_name": params.get("voice_name"),
        "preview": bool(params.get("preview_seconds")),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
    }


def wait_for_job(
    client: StudioClient, job_id: str, *, timeout_seconds: float, poll_seconds: float = POLL_SECONDS
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    job = client.job(job_id)
    while job["state"] not in TERMINAL_STATES:
        if time.monotonic() >= deadline:
            raise StudioError(
                f"等待任务 {job_id} 超时（{timeout_seconds:.0f} 秒），"
                f"当前 {job['state']} · {job.get('stage')} · {job.get('progress')}%。"
                f"可用 wait_job 继续等待，或 cancel_job 取消。"
            )
        time.sleep(poll_seconds)
        job = client.job(job_id)
    return job


def download_outputs(client: StudioClient, job: dict, directory: Path) -> list[str]:
    """把任务成品下载到指定目录；同名文件追加任务短 ID 避免覆盖。"""
    files = []
    for asset_id in job["outputs"]:
        asset = client.asset(asset_id)
        stem = asset["name"].replace("/", "／").strip() or asset_id
        suffix = Path(asset["path"]).suffix
        destination = directory / f"{stem}{suffix}"
        if destination.name != f"{stem}{suffix}" or destination.exists():
            destination = directory / f"{asset_id}{suffix}"
        files.append(str(client.download_asset(asset_id, destination)))
    return files


def run_voice_swap(
    client: StudioClient,
    *,
    audio_path: str | None = None,
    source_id: str | None = None,
    voice: str | None = None,
    voice_id: str | None = None,
    pitch_shift: int = 0,
    auto_f0_adjust: bool = False,
    cfg_rate: float = 0.7,
    diffusion_steps: int = 30,
    vocal_enhance: bool = False,
    vocal_gain_db: float = 0,
    instrumental_gain_db: float = -3,
    offset_ms: int = 0,
    start_seconds: float = 0,
    preview_seconds: float = 0,
    formats: list[str] | None = None,
    wait: bool = True,
    timeout_seconds: float = DEFAULT_WAIT_SECONDS,
    output_dir: Path | None = None,
    job_name: str = "",
) -> dict:
    """一键换声编排：上传/选源 → 解析音色 → 提交转换 → 等待 → 下载成品。"""
    if not audio_path and not source_id:
        raise StudioError("需要提供 audio_path（本地音频）或 source_id（已有资产）。")
    selected_formats = formats or ["mp3"]
    ensure_running(client)
    if source_id:
        source = client.asset(source_id)
        if source["kind"] not in ("song", "vocal"):
            raise StudioError(f"来源需为原唱歌曲（song）或干声（vocal），当前是 {source['kind']}。")
    else:
        source = client.upload(audio_path, "song")
    if voice_id:
        voice_asset = client.asset(voice_id)
        if voice_asset["kind"] != "voice":
            raise StudioError(f"资产 {voice_id} 不是音色（voice），而是 {voice_asset['kind']}。")
    else:
        voice_asset = resolve_voice(client.assets(), voice)
    params = {
        "name": job_name or f"{source['name']} · {voice_asset['name']} 换声",
        "job_type": "cover",
        "source_id": source["id"],
        "voice_id": voice_asset["id"],
        "diffusion_steps": diffusion_steps,
        "cfg_rate": cfg_rate,
        "pitch_shift": pitch_shift,
        "vocal_gain_db": vocal_gain_db,
        "instrumental_gain_db": instrumental_gain_db,
        "offset_ms": offset_ms,
        "start_seconds": start_seconds,
        "preview_seconds": preview_seconds,
        "vocal_enhance": vocal_enhance,
        "auto_f0_adjust": auto_f0_adjust,
        "formats": selected_formats,
    }
    job = client.create_job(params)
    summary = {
        **summarize_job(job),
        "source": summarize_asset(source),
        "voice": summarize_asset(voice_asset),
    }
    if not wait:
        return {
            **summary,
            "hint": f"任务已排队，用 wait_job(job_id=\"{job['id']}\") 或 get_job 跟踪；"
            "完成后用 download_asset(job['outputs'][0]) 取回成品。",
        }
    final_job = wait_for_job(client, job["id"], timeout_seconds=timeout_seconds)
    summary = {**summary, **summarize_job(final_job)}
    if final_job["state"] != "completed":
        summary["hint"] = f"任务未完成（{final_job['state']}），可用 get_job_log 排查或 retry_job 重试。"
        return summary
    destination_dir = output_dir or default_output_dir()
    summary["files"] = download_outputs(client, final_job, destination_dir)
    return summary


mcp = FastMCP(
    "cover-studio",
    instructions=(
        "AI 翻唱工作室：把一段音频里的人声替换成指定音色（本机 Demucs 分离 + Seed-VC 转换）。"
        "最常用 voice_swap 一键完成；长歌曲建议 wait=False 提交后用 wait_job/get_job 轮询。"
        "任务失败时用 get_job_log 查看日志，调参后可用 create_job 复用 source_id/voice_id 重跑。"
    ),
)


@mcp.tool()
def studio_status(auto_start: bool = True) -> dict:
    """查看工作室与推理环境状态（模型权重、FFmpeg、存储余量）。auto_start 为真时会在服务未启动的情况下后台拉起。"""
    client = StudioClient()
    if not auto_start:
        return client.status()
    return ensure_running(client)


@mcp.tool()
def list_assets(
    kind: Literal["voice", "song", "vocal", "instrumental", "output"] | None = None,
    keyword: str = "",
) -> dict:
    """浏览素材库资产。kind 过滤类型（voice=参考音色, song=原唱, vocal=干声/转换人声, instrumental=伴奏, output=成品）；keyword 按名称模糊匹配。"""
    assets = StudioClient().assets()
    filtered = [
        summarize_asset(asset)
        for asset in assets
        if (kind is None or asset["kind"] == kind) and (not keyword or keyword in asset["name"])
    ]
    return {"total": len(filtered), "assets": filtered}


@mcp.tool()
def upload_audio(
    path: str,
    kind: Literal["voice", "song", "vocal", "instrumental", "output"],
    name: str = "",
) -> dict:
    """导入本地音频到素材库。参考音色（voice）需为 1-30 秒干净人声；原唱歌曲用 song。"""
    return summarize_asset(StudioClient().upload(path, kind, name))


@mcp.tool()
def voice_swap(
    audio_path: Annotated[str, Field(description="本地音频路径，如一段语音或歌曲")],
    voice: Annotated[
        str | None, Field(description="音色名称关键词或资产 ID；留空时匹配「我的声音」")
    ] = None,
    pitch_shift: Annotated[int, Field(ge=-12, le=12, description="半音移调，人声不自然时调整")] = 0,
    auto_f0_adjust: Annotated[
        bool,
        Field(description="自动音域对齐：把源音高整体移到参考音色的音域。源是高音/变调音频或语音时开启，效果远胜手动移调"),
    ] = False,
    cfg_rate: Annotated[float, Field(ge=0, le=1, description="音色引导强度，越高越像参考音色")] = 0.7,
    diffusion_steps: Annotated[int, Field(ge=10, le=100, description="扩散步数，越大质量越好越慢")] = 30,
    vocal_enhance: Annotated[bool, Field(description="转换后人声增强")] = False,
    vocal_gain_db: Annotated[float, Field(ge=-24, le=12)] = 0,
    instrumental_gain_db: Annotated[float, Field(ge=-24, le=12)] = -3,
    offset_ms: Annotated[int, Field(ge=-10000, le=10000, description="人声整体前后微调")] = 0,
    start_seconds: Annotated[float, Field(ge=0, description="试听片段起点")] = 0,
    preview_seconds: Annotated[
        float, Field(ge=0, description="大于 0 时只转换该时长的片段用于快速试听调参")
    ] = 0,
    formats: list[Literal["wav", "mp3"]] | None = None,
    wait: Annotated[bool, Field(description="False 时只提交任务立即返回，稍后轮询")] = True,
    timeout_seconds: float = DEFAULT_WAIT_SECONDS,
    output_dir: Annotated[str | None, Field(description="成品下载目录，默认 ~/Downloads")] = None,
    job_name: str = "",
) -> dict:
    """一键换声：把 audio_path 中的人声替换成指定音色，完成后成品自动下载。整首歌约需 10-20 分钟；建议先 preview_seconds=15 试听验证音色与移调。"""
    client = StudioClient()
    directory = Path(output_dir).expanduser() if output_dir else None
    return run_voice_swap(
        client,
        audio_path=audio_path,
        voice=voice,
        pitch_shift=pitch_shift,
        auto_f0_adjust=auto_f0_adjust,
        cfg_rate=cfg_rate,
        diffusion_steps=diffusion_steps,
        vocal_enhance=vocal_enhance,
        vocal_gain_db=vocal_gain_db,
        instrumental_gain_db=instrumental_gain_db,
        offset_ms=offset_ms,
        start_seconds=start_seconds,
        preview_seconds=preview_seconds,
        formats=list(formats or ["mp3"]),
        wait=wait,
        timeout_seconds=timeout_seconds,
        output_dir=directory,
        job_name=job_name,
    )


@mcp.tool()
def create_job(
    job_type: Literal["cover", "mix", "separate"],
    source_id: str,
    voice_id: str | None = None,
    instrumental_id: str | None = None,
    diffusion_steps: Annotated[int, Field(ge=10, le=100)] = 30,
    cfg_rate: Annotated[float, Field(ge=0, le=1)] = 0.7,
    pitch_shift: Annotated[int, Field(ge=-12, le=12)] = 0,
    auto_f0_adjust: Annotated[bool, Field(description="自动音域对齐，高音/变调源建议开启")] = False,
    vocal_gain_db: float = 0,
    instrumental_gain_db: float = -3,
    offset_ms: int = 0,
    start_seconds: float = 0,
    preview_seconds: float = 0,
    vocal_enhance: bool = False,
    formats: list[Literal["wav", "mp3"]] | None = None,
    name: str = "",
) -> dict:
    """提交细粒度任务（不等待）：cover=音色转换（需 voice_id）、mix=干声直接混音、separate=仅人声分离。参数与 voice_swap 相同；适合复用已有 source_id/voice_id 调参重跑。"""
    params = {
        "name": name,
        "job_type": job_type,
        "source_id": source_id,
        "voice_id": voice_id,
        "instrumental_id": instrumental_id,
        "diffusion_steps": diffusion_steps,
        "cfg_rate": cfg_rate,
        "pitch_shift": pitch_shift,
        "vocal_gain_db": vocal_gain_db,
        "instrumental_gain_db": instrumental_gain_db,
        "offset_ms": offset_ms,
        "start_seconds": start_seconds,
        "preview_seconds": preview_seconds,
        "vocal_enhance": vocal_enhance,
        "auto_f0_adjust": auto_f0_adjust,
        "formats": list(formats or ["mp3"]),
    }
    return summarize_job(StudioClient().create_job(params))


@mcp.tool()
def get_job(job_id: str) -> dict:
    """查看任务详情：状态、阶段进度、输出资产 ID、错误信息。"""
    return summarize_job(StudioClient().job(job_id))


@mcp.tool()
def list_jobs(limit: int = 10) -> dict:
    """列出最近的任务（新的在前）。"""
    jobs = sorted(StudioClient().jobs(), key=lambda item: item.get("created_at", ""), reverse=True)
    recent = [summarize_job(job) for job in jobs[: max(1, limit)]]
    return {"total": len(recent), "jobs": recent}


@mcp.tool()
def wait_job(job_id: str, timeout_seconds: float = DEFAULT_WAIT_SECONDS) -> dict:
    """阻塞等待任务进入终态（completed/failed/cancelled/interrupted）并返回摘要；长任务注意设置足够超时。"""
    client = StudioClient()
    job = wait_for_job(client, job_id, timeout_seconds=timeout_seconds)
    summary = summarize_job(job)
    if job["state"] == "completed":
        summary["hint"] = f"输出资产：{job['outputs']}，可用 download_asset 下载。"
    return summary


@mcp.tool()
def get_job_log(job_id: str) -> str:
    """读取任务执行日志（尾部），用于排查失败原因。"""
    return StudioClient().job_log(job_id)


@mcp.tool()
def cancel_job(job_id: str) -> dict:
    """取消排队或执行中的任务。"""
    return summarize_job(StudioClient().cancel_job(job_id))


@mcp.tool()
def retry_job(job_id: str) -> dict:
    """以相同参数重新提交失败/取消的任务。"""
    return summarize_job(StudioClient().retry_job(job_id))


@mcp.tool()
def download_asset(
    asset_id: str,
    output_dir: Annotated[str | None, Field(description="下载目录，默认 ~/Downloads")] = None,
    filename: str = "",
) -> dict:
    """把资产（成品/干声/伴奏等）下载为本地文件，返回文件路径。"""
    client = StudioClient()
    asset = client.asset(asset_id)
    directory = Path(output_dir).expanduser() if output_dir else default_output_dir()
    stem = filename.strip() or asset["name"].replace("/", "／").strip() or asset_id
    suffix = Path(asset["path"]).suffix
    destination = directory / f"{stem}{suffix}"
    if destination.name != f"{stem}{suffix}":
        destination = directory / f"{asset_id}{suffix}"
    path = client.download_asset(asset_id, destination)
    return {"asset": summarize_asset(asset), "file": str(path)}


@mcp.tool()
def delete_asset(asset_id: str) -> dict:
    """删除素材库资产及音频文件（正在被任务使用的资产无法删除）。"""
    client = StudioClient()
    asset = client.asset(asset_id)
    client.delete_asset(asset_id)
    return {"deleted": asset_id, "name": asset["name"]}


if __name__ == "__main__":
    mcp.run()
