import json
import queue
import shutil
import signal
import subprocess
import threading
from pathlib import Path

from . import audio
from .config import Settings
from .database import Database, new_id, now
from .engines import Engines

ACTIVE_STATES = {"queued", "running", "cancelling"}


class Cancelled(Exception):
    pass


class JobManager:
    def __init__(self, settings: Settings, db: Database, engines: Engines):
        self.settings, self.db, self.engines = settings, db, engines
        self.queue = queue.Queue()
        self.events = {}
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="cover-worker", daemon=True)

    def start(self):
        for job in reversed(self.db.all("jobs")):
            if job["state"] in ("running", "cancelling"):
                self.db.update_job(
                    job["id"],
                    state="interrupted",
                    stage="任务中断",
                    error="应用退出导致任务中断，可以重新执行。",
                )
            elif job["state"] == "queued":
                self.enqueue(job["id"])
        self.thread.start()

    def close(self):
        self.stopping.set()
        with self.lock:
            for event in self.events.values():
                event.set()
        self.queue.put(None)
        self.thread.join(timeout=15)

    def enqueue(self, job_id: str):
        with self.lock:
            self.events[job_id] = threading.Event()
        self.queue.put(job_id)

    def cancel(self, job_id: str):
        with self.lock:
            event = self.events.get(job_id)
            if event:
                event.set()
            job = self.db.get("jobs", job_id)
            if job and job["state"] in ACTIVE_STATES:
                if job["state"] == "queued":
                    self.db.update_job(job_id, state="cancelled", stage="已取消")
                else:
                    self.db.update_job(job_id, state="cancelling", stage="正在取消")

    def _loop(self):
        while not self.stopping.is_set():
            job_id = self.queue.get()
            if job_id is None:
                break
            with self.lock:
                event = self.events.get(job_id)
            job = self.db.get("jobs", job_id)
            if not job or not event:
                continue
            if event.is_set() or job["state"] != "queued":
                with self.lock:
                    self.events.pop(job_id, None)
                continue
            try:
                self.db.update_job(job_id, state="running", stage="准备音频", progress=5)
                self._process(job, event)
            except Cancelled:
                self.db.update_job(job_id, state="cancelled", stage="已取消")
            except Exception as error:
                self.db.update_job(
                    job_id, state="failed", stage="执行失败", error=str(error)[-4000:]
                )
            finally:
                with self.lock:
                    self.events.pop(job_id, None)

    def _run(
        self, job_id: str, command: list[str], event: threading.Event, cwd: Path | None = None
    ):
        if event.is_set():
            raise Cancelled()
        log_path = self.settings.data_dir / "jobs" / job_id / "task.log"
        with log_path.open("a", encoding="utf-8") as log:
            log.write("\n" + json.dumps(command, ensure_ascii=False) + "\n")
            log.flush()
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=self.settings.model_environment(),
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                while True:
                    if event.is_set() or self.stopping.is_set():
                        raise Cancelled()
                    try:
                        return_code = process.wait(timeout=0.2)
                        break
                    except subprocess.TimeoutExpired:
                        continue
            except BaseException:
                self._terminate(process)
                raise
        if event.is_set():
            raise Cancelled()
        if return_code:
            with log_path.open("rb") as file:
                file.seek(max(0, log_path.stat().st_size - 3500))
                tail = file.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"音频处理失败（退出码 {return_code}）：\n{tail}")

    @staticmethod
    def _terminate(process):
        if process.poll() is not None:
            return
        try:
            import os

            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()

    def _asset_path(self, asset_id: str) -> Path:
        asset = self.db.get("assets", asset_id)
        if not asset:
            raise ValueError("任务引用的音频资产已不存在。")
        return self.settings.data_dir / asset["path"]

    def _publish(self, path: Path, kind: str, name: str, job_id: str) -> str:
        folder = {"output": "outputs", "vocal": "vocals", "instrumental": "instrumentals"}[kind]
        asset_id = new_id()
        destination = self.settings.data_dir / "assets" / folder / f"{asset_id}{path.suffix}"
        shutil.copy2(path, destination)
        try:
            self.db.insert(
                "assets",
                {
                    "id": asset_id,
                    "kind": kind,
                    "name": name,
                    "path": str(destination.relative_to(self.settings.data_dir)),
                    **audio.probe(destination),
                    "waveform": audio.waveform(destination),
                    "metadata": {"job_id": job_id},
                    "created_at": now(),
                },
            )
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        return asset_id

    def _process(self, job: dict, event: threading.Event):
        job_id, params = job["id"], job["params"]
        job_type = params["job_type"]
        source_asset = self.db.get("assets", params["source_id"])
        if not source_asset:
            raise ValueError("任务引用的演唱来源已不存在。")
        errors = self.engines.validate(job_type, source_asset["kind"])
        if errors:
            raise ValueError(" ".join(errors))
        work = self.settings.data_dir / "jobs" / job_id
        work.mkdir(parents=True, exist_ok=True)
        (work / "request.json").write_text(json.dumps(params, ensure_ascii=False, indent=2))
        if params.get("lyric_snapshot"):
            (work / "lyrics.txt").write_text(params["lyric_snapshot"]["content"], encoding="utf-8")
        source = work / "source.wav"
        self._run(
            job_id, audio.normalize_command(self._asset_path(params["source_id"]), source), event
        )
        instrumental = (
            self._asset_path(params["instrumental_id"]) if params.get("instrumental_id") else None
        )
        vocal = source
        if source_asset["kind"] == "song":
            self.db.update_job(job_id, stage="分离人声与伴奏", progress=15)
            separation = work / "separated"
            self._run(job_id, self.engines.demucs_command(source, separation), event)
            vocal = separation / "htdemucs/source/vocals.wav"
            generated_instrumental = separation / "htdemucs/source/no_vocals.wav"
            if not vocal.is_file() or not generated_instrumental.is_file():
                raise RuntimeError("Demucs 未产生完整的人声和伴奏文件。")
            instrumental = instrumental or generated_instrumental
            self._publish(vocal, "vocal", f"{source_asset['name']} · 分离人声", job_id)
            self._publish(
                generated_instrumental, "instrumental", f"{source_asset['name']} · 分离伴奏", job_id
            )
        if job_type == "separate":
            output_ids = [
                asset["id"]
                for asset in self.db.all("assets")
                if asset["metadata"].get("job_id") == job_id
            ]
        else:
            if job_type == "cover":
                self.db.update_job(job_id, stage="转换歌声音色", progress=45)
                converted = work / "converted"
                converted.mkdir(exist_ok=True)
                self._run(
                    job_id,
                    self.engines.seed_command(
                        vocal, self._asset_path(params["voice_id"]), converted, params
                    ),
                    event,
                    cwd=self.settings.seed_repo,
                )
                candidates = list(converted.glob("vc_*.wav"))
                if len(candidates) != 1:
                    raise RuntimeError("Seed-VC 未产生唯一的转换结果。")
                vocal = candidates[0]
            self.db.update_job(job_id, stage="混音与同步", progress=80)
            duration = source_asset["duration"]
            if instrumental:
                duration = max(duration, audio.probe(instrumental)["duration"])
            mixed = work / "mix.wav"
            self._run(
                job_id,
                audio.mix_command(
                    vocal,
                    instrumental,
                    mixed,
                    duration,
                    params["vocal_gain_db"],
                    params["instrumental_gain_db"],
                    params["offset_ms"],
                ),
                event,
            )
            files = [mixed] if "wav" in params["formats"] else []
            if "mp3" in params["formats"]:
                self.db.update_job(job_id, stage="导出 MP3", progress=90)
                mp3 = work / "mix.mp3"
                self._run(job_id, audio.mp3_command(mixed, mp3), event)
                files.append(mp3)
            output_ids = []
            try:
                for file in files:
                    if event.is_set():
                        raise Cancelled()
                    output_ids.append(self._publish(file, "output", job["name"], job_id))
                if event.is_set():
                    raise Cancelled()
            except BaseException:
                for asset_id in output_ids:
                    self._remove_asset(asset_id)
                raise
        self.db.update_job(
            job_id, state="completed", stage="已完成", progress=100, outputs=output_ids
        )
        # Published stems and outputs are retained; duplicate working audio is disposable.
        for child in work.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            elif child.suffix.lower() in (".wav", ".mp3"):
                child.unlink()

    def _remove_asset(self, asset_id):
        asset = self.db.get("assets", asset_id)
        if asset:
            (self.settings.data_dir / asset["path"]).unlink(missing_ok=True)
            with self.db.connection() as db:
                db.execute("DELETE FROM assets WHERE id=?", (asset_id,))
