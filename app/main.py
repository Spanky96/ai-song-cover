import mimetypes
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import audio
from .config import PROJECT_ROOT, Settings, free_bytes
from .database import Database, new_id, now
from .engines import Engines
from .jobs import ACTIVE_STATES, JobManager

KINDS = {"voice": "voices", "song": "songs", "instrumental": "instrumentals", "vocal": "vocals"}
EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".opus", ".aac", ".aiff", ".webm"}


class LyricRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=100000)

    @field_validator("name", "content")
    @classmethod
    def strip_text(cls, value):
        if not value.strip():
            raise ValueError("内容不能为空。")
        return value.strip()


class RenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class JobRequest(BaseModel):
    name: str = Field(default="", max_length=120)
    job_type: Literal["cover", "mix", "separate"] = "cover"
    source_id: str
    voice_id: str | None = None
    instrumental_id: str | None = None
    lyric_id: str | None = None
    diffusion_steps: int = Field(default=30, ge=10, le=100)
    cfg_rate: float = Field(default=0.7, ge=0, le=1)
    pitch_shift: int = Field(default=0, ge=-12, le=12)
    vocal_gain_db: float = Field(default=0, ge=-24, le=12)
    instrumental_gain_db: float = Field(default=-3, ge=-24, le=12)
    offset_ms: int = Field(default=0, ge=-10000, le=10000)
    start_seconds: float = Field(default=0, ge=0, le=1800)
    preview_seconds: float = Field(default=0, ge=0, le=1800)
    formats: list[Literal["wav", "mp3"]] = Field(default=["wav", "mp3"], min_length=1)


class SettingsRequest(BaseModel):
    models_dir: str = Field(min_length=1, max_length=4096)
    seed_repo: str = Field(min_length=1, max_length=4096)
    seed_python: str = Field(min_length=1, max_length=4096)
    demucs_python: str = Field(min_length=1, max_length=4096)
    demucs_device: Literal["cpu", "mps", "cuda"] = "cpu"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    db = Database(settings.data_dir / "database/studio.sqlite3")
    engines = Engines(settings)
    manager = JobManager(settings, db, engines)

    @asynccontextmanager
    async def lifespan(app):
        manager.start()
        yield
        manager.close()

    application = FastAPI(title="AI 翻唱工作室", lifespan=lifespan)
    application.state.settings = settings
    application.state.db = db
    application.state.engines = engines
    application.state.manager = manager

    @application.middleware("http")
    async def local_requests(request: Request, call_next):
        host = request.url.hostname
        if host not in ("127.0.0.1", "localhost", "::1", "testserver"):
            return JSONResponse({"detail": "仅允许本机访问。"}, status_code=403)
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin:
            origin_url = urlsplit(origin)
            if (
                origin_url.netloc != request.headers.get("host")
                or origin_url.scheme != request.url.scheme
            ):
                return JSONResponse({"detail": "拒绝跨站写入请求。"}, status_code=403)
        return await call_next(request)

    def get_asset(asset_id):
        asset = db.get("assets", asset_id)
        if not asset:
            raise HTTPException(404, "音频资产不存在。")
        return asset

    def active_jobs():
        return [j for j in db.all("jobs") if j["state"] in ACTIVE_STATES]

    @application.get("/api/status")
    def status():
        return {
            **engines.status(),
            "storage": {
                "data_dir": str(settings.data_dir),
                "models_dir": str(settings.models_dir),
                "data_free_bytes": free_bytes(settings.data_dir),
                "models_free_bytes": free_bytes(settings.models_dir),
                "model_space_ok": free_bytes(settings.models_dir)
                >= settings.model_free_requirement,
            },
        }

    @application.get("/api/assets")
    def list_assets():
        return db.all("assets")

    @application.post("/api/assets", status_code=201)
    def upload_asset(kind: str = Form(...), name: str = Form(""), file: UploadFile = File(...)):
        if kind not in KINDS:
            raise HTTPException(422, "无效的资产类型。")
        suffix = Path(file.filename or "audio").suffix.lower()
        if suffix not in EXTENSIONS:
            raise HTTPException(422, "不支持此音频格式。")
        title = (name.strip() or Path(file.filename or "音频").stem)[:120]
        asset_id = new_id()
        target = settings.data_dir / "assets" / KINDS[kind] / f"{asset_id}{suffix}"
        size = 0
        try:
            with target.open("wb") as out:
                while chunk := file.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > 250 * 1024**2:
                        raise HTTPException(413, "单个音频不得超过 250 MB。")
                    if free_bytes(settings.data_dir) < 512 * 1024**2:
                        raise HTTPException(507, "存储空间不足，请先释放磁盘空间。")
                    out.write(chunk)
            metadata = audio.probe(target)
            if kind == "voice" and not 1 <= metadata["duration"] <= 30.1:
                raise HTTPException(422, "参考音色需为 1 至 30 秒的干净人声录音。")
            asset = {
                "id": asset_id,
                "kind": kind,
                "name": title,
                "path": str(target.relative_to(settings.data_dir)),
                **metadata,
                "waveform": audio.waveform(target),
                "metadata": {},
                "created_at": now(),
            }
            db.insert("assets", asset)
            return asset
        except BaseException as error:
            target.unlink(missing_ok=True)
            if isinstance(error, audio.AudioError):
                raise HTTPException(422, str(error)) from error
            raise
        finally:
            file.file.close()

    @application.get("/api/assets/{asset_id}/audio")
    def serve_audio(asset_id: str, download: bool = False):
        asset = get_asset(asset_id)
        path = settings.data_dir / asset["path"]
        if not path.is_file():
            raise HTTPException(404, "音频文件已不存在。")
        return FileResponse(
            path,
            media_type=mimetypes.guess_type(path)[0] or "application/octet-stream",
            filename=f"{asset['name']}{path.suffix}" if download else None,
        )

    @application.patch("/api/assets/{asset_id}")
    def rename_asset(asset_id: str, request: RenameRequest):
        get_asset(asset_id)
        if not request.name.strip():
            raise HTTPException(422, "名称不能为空。")
        with db.connection() as connection:
            connection.execute(
                "UPDATE assets SET name=? WHERE id=?", (request.name.strip(), asset_id)
            )
        return get_asset(asset_id)

    @application.delete("/api/assets/{asset_id}", status_code=204)
    def delete_asset(asset_id: str):
        asset = get_asset(asset_id)
        for job in active_jobs():
            if asset_id in (
                job["params"].get(key) for key in ("source_id", "voice_id", "instrumental_id")
            ):
                raise HTTPException(409, "正在执行的任务使用了此资产，请先取消任务。")
        with db.connection() as connection:
            connection.execute("DELETE FROM assets WHERE id=?", (asset_id,))
        (settings.data_dir / asset["path"]).unlink(missing_ok=True)

    @application.get("/api/lyrics")
    def list_lyrics():
        return db.all("lyrics")

    @application.post("/api/lyrics", status_code=201)
    def create_lyric(request: LyricRequest):
        lyric = {
            "id": new_id(),
            **request.model_dump(),
            "version": 1,
            "created_at": now(),
            "updated_at": now(),
        }
        db.insert("lyrics", lyric)
        return lyric

    @application.put("/api/lyrics/{lyric_id}")
    def edit_lyric(lyric_id: str, request: LyricRequest):
        with db.connection() as connection:
            row = connection.execute("SELECT * FROM lyrics WHERE id=?", (lyric_id,)).fetchone()
            if not row:
                raise HTTPException(404, "歌词不存在。")
            connection.execute(
                "INSERT INTO lyric_versions VALUES (?,?,?,?,?)",
                (lyric_id, row["version"], row["name"], row["content"], row["updated_at"]),
            )
            connection.execute(
                "UPDATE lyrics SET name=?,content=?,version=version+1,updated_at=? WHERE id=?",
                (request.name, request.content, now(), lyric_id),
            )
        return db.get("lyrics", lyric_id)

    @application.get("/api/lyrics/{lyric_id}/versions")
    def lyric_versions(lyric_id: str):
        lyric = db.get("lyrics", lyric_id)
        if not lyric:
            raise HTTPException(404, "歌词不存在。")
        with db.connection() as connection:
            old = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM lyric_versions WHERE lyric_id=? ORDER BY version DESC",
                    (lyric_id,),
                )
            ]
        return [lyric] + old

    @application.delete("/api/lyrics/{lyric_id}", status_code=204)
    def delete_lyric(lyric_id: str):
        if not db.get("lyrics", lyric_id):
            raise HTTPException(404, "歌词不存在。")
        with db.connection() as connection:
            connection.execute("DELETE FROM lyrics WHERE id=?", (lyric_id,))

    @application.get("/api/jobs")
    def list_jobs():
        return db.all("jobs")

    def submit(request: JobRequest, lyric_snapshot: dict | None = None):
        source = get_asset(request.source_id)
        expected = ("song",) if request.job_type == "separate" else ("song", "vocal")
        if source["kind"] not in expected:
            raise HTTPException(422, "请选择原唱歌曲或干声录唱作为来源。")
        if request.job_type == "mix" and source["kind"] != "vocal":
            raise HTTPException(422, "直接混音的来源需为干声录唱。")
        if request.job_type == "cover":
            if not request.voice_id or get_asset(request.voice_id)["kind"] != "voice":
                raise HTTPException(422, "请选择参考音色。")
        if request.job_type == "separate" and (request.start_seconds or request.preview_seconds):
            raise HTTPException(422, "人声分离不支持试听片段参数。")
        if request.instrumental_id and get_asset(request.instrumental_id)["kind"] != "instrumental":
            raise HTTPException(422, "请选择伴奏资产。")
        errors = engines.validate(request.job_type, source["kind"])
        if errors:
            raise HTTPException(409, " ".join(errors))
        params = request.model_dump()
        params["source_name"] = source["name"]
        if request.voice_id:
            params["voice_name"] = get_asset(request.voice_id)["name"]
        if lyric_snapshot:
            params["lyric_snapshot"] = lyric_snapshot
        elif request.lyric_id:
            lyric = db.get("lyrics", request.lyric_id)
            if not lyric:
                raise HTTPException(404, "歌词不存在。")
            params["lyric_snapshot"] = {
                "id": lyric["id"],
                "name": lyric["name"],
                "version": lyric["version"],
                "content": lyric["content"],
            }
        name = (
            request.name.strip()
            or f"{source['name']} · "
            + {
                "cover": "AI 翻唱",
                "mix": "混音",
                "separate": "人声分离",
            }[request.job_type]
        )
        job = {
            "id": new_id(),
            "name": name,
            "state": "queued",
            "stage": "等待处理",
            "progress": 0,
            "params": params,
            "outputs": [],
            "error": None,
            "created_at": now(),
            "updated_at": now(),
        }
        db.insert("jobs", job)
        manager.enqueue(job["id"])
        return job

    @application.post("/api/jobs", status_code=201)
    def create_job(request: JobRequest):
        return submit(request)

    @application.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        if not db.get("jobs", job_id):
            raise HTTPException(404, "任务不存在。")
        manager.cancel(job_id)
        return db.get("jobs", job_id)

    @application.post("/api/jobs/{job_id}/retry", status_code=201)
    def retry_job(job_id: str):
        job = db.get("jobs", job_id)
        if not job:
            raise HTTPException(404, "任务不存在。")
        if job["state"] in ACTIVE_STATES:
            raise HTTPException(409, "任务仍在执行。")
        return submit(
            JobRequest.model_validate(job["params"]),
            lyric_snapshot=job["params"].get("lyric_snapshot"),
        )

    @application.get("/api/jobs/{job_id}/log")
    def job_log(job_id: str):
        if not db.get("jobs", job_id):
            raise HTTPException(404, "任务不存在。")
        path = settings.data_dir / "jobs" / job_id / "task.log"
        if not path.exists():
            return {"log": "任务尚未开始。"}
        with path.open("rb") as file:
            file.seek(max(0, path.stat().st_size - 64000))
            return {"log": file.read().decode("utf-8", errors="replace")}

    @application.delete("/api/jobs/{job_id}", status_code=204)
    def delete_job(job_id: str):
        job = db.get("jobs", job_id)
        if not job:
            raise HTTPException(404, "任务不存在。")
        if job["state"] in ACTIVE_STATES:
            raise HTTPException(409, "请先取消任务。")
        with db.connection() as connection:
            connection.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        shutil.rmtree(settings.data_dir / "jobs" / job_id, ignore_errors=True)

    @application.get("/api/settings")
    def get_settings():
        return settings.public()

    @application.put("/api/settings")
    def save_settings(request: SettingsRequest):
        if active_jobs():
            raise HTTPException(409, "请等待当前任务完成再修改推理配置。")
        settings.save(request.model_dump())
        engines.invalidate()
        return settings.public()

    application.mount("/static", StaticFiles(directory=PROJECT_ROOT / "app/static"), name="static")

    @application.get("/")
    def index():
        return FileResponse(PROJECT_ROOT / "app/static/index.html")

    return application


app = create_app()
