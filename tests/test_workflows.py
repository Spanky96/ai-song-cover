import array
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from app import audio
from app.config import Settings
from app.database import Database, new_id, now
from app.engines import Engines
from app.jobs import Cancelled, JobManager
from tests.conftest import make_wav, upload


def wait_job(client, job_id):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = next(j for j in client.get("/api/jobs").json() if j["id"] == job_id)
        if job["state"] not in ("queued", "running", "cancelling"):
            return job
        time.sleep(0.05)
    raise AssertionError("Task did not finish")


def test_upload_preview_download_and_delete(client, wav):
    asset = upload(client, wav, filename="../../outside.wav")
    assert asset["duration"] == pytest.approx(2)
    assert asset["sample_rate"] == 16000
    assert len(asset["waveform"]) == 100
    assert max(asset["waveform"]) > 0
    assert ".." not in asset["path"]
    response = client.get(f"/api/assets/{asset['id']}/audio?download=true")
    assert response.content == wav
    assert "attachment" in response.headers["content-disposition"]
    renamed = client.patch(f"/api/assets/{asset['id']}", json={"name": "新名称"}).json()
    assert renamed["name"] == "新名称"
    assert client.delete(f"/api/assets/{asset['id']}").status_code == 204
    assert client.get(f"/api/assets/{asset['id']}/audio").status_code == 404


def test_invalid_audio_is_not_retained(client, application):
    response = client.post(
        "/api/assets",
        data={"kind": "song"},
        files={"file": ("broken.wav", b"invalid", "audio/wav")},
    )
    assert response.status_code == 422
    assert client.get("/api/assets").json() == []
    assert not list((application.state.settings.data_dir / "assets/songs").iterdir())


@pytest.mark.parametrize("seconds", [0.5, 31])
def test_voice_reference_duration(client, seconds):
    response = client.post(
        "/api/assets",
        data={"kind": "voice"},
        files={"file": ("voice.wav", make_wav(seconds), "audio/wav")},
    )
    assert response.status_code == 422
    assert client.get("/api/assets").json() == []


def test_lyric_version_history(client):
    lyric = client.post("/api/lyrics", json={"name": "歌曲", "content": "第一版歌词"}).json()
    updated = client.put(
        f"/api/lyrics/{lyric['id']}", json={"name": "歌曲", "content": "第二版歌词"}
    ).json()
    assert updated["version"] == 2
    versions = client.get(f"/api/lyrics/{lyric['id']}/versions").json()
    assert [v["content"] for v in versions] == ["第二版歌词", "第一版歌词"]


def test_actual_ffmpeg_mix_export_snapshot_and_cleanup(client, application, wav):
    vocal = upload(client, wav, name="干声")
    instrumental = upload(client, make_wav(frequency=220, volume=0.1), "instrumental")
    lyric = client.post("/api/lyrics", json={"name": "歌词", "content": "原始歌词"}).json()
    response = client.post(
        "/api/jobs",
        json={
            "job_type": "mix",
            "source_id": vocal["id"],
            "instrumental_id": instrumental["id"],
            "lyric_id": lyric["id"],
            "name": "完成作品",
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()
    client.put(f"/api/lyrics/{lyric['id']}", json={"name": "歌词", "content": "后来的歌词"})
    job = wait_job(client, created["id"])
    assert job["state"] == "completed", job["error"]
    assert job["progress"] == 100
    assert job["params"]["lyric_snapshot"]["content"] == "原始歌词"
    outputs = [a for a in client.get("/api/assets").json() if a["kind"] == "output"]
    assert {Path(a["path"]).suffix for a in outputs} == {".wav", ".mp3"}
    for output in outputs:
        assert output["duration"] == pytest.approx(2, abs=0.05)
        assert output["sample_rate"] == 44100
        assert output["channels"] == 2
        assert max(output["waveform"]) > 0
    work = application.state.settings.data_dir / "jobs" / job["id"]
    assert (work / "lyrics.txt").read_text() == "原始歌词"
    assert not list(work.glob("*.wav"))
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 204
    assert not work.exists()
    assert len([a for a in client.get("/api/assets").json() if a["kind"] == "output"]) == 2


def test_mix_rejects_song_instead_of_dry_vocal(client, wav):
    song = upload(client, wav, "song")
    response = client.post("/api/jobs", json={"job_type": "mix", "source_id": song["id"]})
    assert response.status_code == 422


def test_ai_missing_environment_does_not_enqueue_fake_output(client, wav):
    song, voice = upload(client, wav, "song"), upload(client, wav, "voice")
    response = client.post("/api/jobs", json={"source_id": song["id"], "voice_id": voice["id"]})
    assert response.status_code == 409
    assert "推理环境未安装" in response.json()["detail"]
    assert client.get("/api/jobs").json() == []
    assert not any(a["kind"] == "output" for a in client.get("/api/assets").json())


@pytest.mark.parametrize("offset", [-200, 200])
def test_mix_offset_and_limiter_preserve_timeline(tmp_path, offset):
    vocal = tmp_path / "vocal.wav"
    vocal.write_bytes(make_wav(volume=0.8))
    output = tmp_path / "output.wav"
    subprocess.run(
        audio.mix_command(vocal, None, output, 2, vocal_db=12, offset_ms=offset),
        check=True,
        capture_output=True,
    )
    assert audio.probe(output)["duration"] == pytest.approx(2, abs=0.001)
    decoded = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(output), "-f", "s16le", "-ac", "1", "pipe:1"],
        capture_output=True,
        check=True,
    )
    samples = array.array("h", decoded.stdout)
    assert max(abs(sample) for sample in samples) / 32768 <= 0.951
    if offset > 0:
        assert max(abs(sample) for sample in samples[:4410]) == 0


def test_cancel_terminates_running_process(application, tmp_path):
    manager = application.state.manager
    job_id = new_id()
    (manager.settings.data_dir / "jobs" / job_id).mkdir()
    event = threading.Event()
    timer = threading.Timer(0.3, event.set)
    timer.start()
    started = time.monotonic()
    with pytest.raises(Cancelled):
        manager._run(job_id, [sys.executable, "-c", "import time; time.sleep(60)"], event)
    timer.join()
    assert time.monotonic() - started < 8


def test_restart_marks_running_job_interrupted(tmp_path, monkeypatch):
    monkeypatch.setenv("COVER_DATA_DIR", str(tmp_path / "data"))
    settings = Settings(tmp_path)
    db = Database(settings.data_dir / "database/test.sqlite3")
    job_id = new_id()
    db.insert(
        "jobs",
        {
            "id": job_id,
            "name": "中断任务",
            "state": "running",
            "stage": "处理中",
            "progress": 45,
            "params": {},
            "created_at": now(),
            "updated_at": now(),
        },
    )
    manager = JobManager(settings, db, Engines(settings))
    manager.start()
    manager.close()
    assert db.get("jobs", job_id)["state"] == "interrupted"


def test_pre_cancelled_queue_entry_never_runs(application):
    manager = application.state.manager
    job_id = new_id()
    application.state.db.insert(
        "jobs",
        {
            "id": job_id,
            "name": "取消任务",
            "state": "queued",
            "stage": "等待",
            "params": {},
            "created_at": now(),
            "updated_at": now(),
        },
    )
    manager.enqueue(job_id)
    manager.cancel(job_id)
    manager.start()
    manager.close()
    assert application.state.db.get("jobs", job_id)["state"] == "cancelled"


def test_cross_site_writes_and_invalid_hosts_rejected(client):
    response = client.post(
        "/api/lyrics",
        json={"name": "x", "content": "x"},
        headers={"Origin": "https://external.example"},
    )
    assert response.status_code == 403
    assert client.get("/api/assets", headers={"Host": "external.example"}).status_code == 403
    assert (
        client.post(
            "/api/lyrics",
            json={"name": "x", "content": "x"},
            headers={"Origin": "http://testserver"},
        ).status_code
        == 201
    )


def test_executable_symlink_keeps_virtual_environment_path(tmp_path, monkeypatch):
    monkeypatch.setenv("COVER_DATA_DIR", str(tmp_path / "data"))
    virtual_python = tmp_path / "runtime/bin/python"
    virtual_python.parent.mkdir(parents=True)
    virtual_python.symlink_to(sys.executable)
    settings = Settings(tmp_path)
    settings.save({"seed_python": str(virtual_python), "demucs_python": str(virtual_python)})
    assert settings.seed_python == virtual_python
    assert settings.demucs_python == virtual_python
    assert json.loads((tmp_path / "settings.json").read_text())["seed_python"] == str(
        virtual_python
    )


def test_seed_singing_command_retains_pitch_and_duration(application):
    command = application.state.engines.seed_command(
        Path("source.wav"),
        Path("voice.wav"),
        Path("result"),
        {"diffusion_steps": 30, "cfg_rate": 0.7, "pitch_shift": 2},
    )
    assert command[1].endswith("scripts/seed_inference.py")
    assert command[2] == str(application.state.settings.seed_repo)
    for key, expected in [
        ("--f0-condition", "True"),
        ("--length-adjust", "1.0"),
        ("--auto-f0-adjust", "False"),
        ("--semi-tone-shift", "2"),
    ]:
        assert command[command.index(key) + 1] == expected


def test_seed_command_maps_auto_f0_adjust(application):
    command = application.state.engines.seed_command(
        Path("source.wav"),
        Path("voice.wav"),
        Path("result"),
        {"diffusion_steps": 30, "cfg_rate": 0.7, "pitch_shift": 0, "auto_f0_adjust": True},
    )
    assert command[command.index("--auto-f0-adjust") + 1] == "True"


def test_retry_preserves_original_lyric_after_edit_or_delete(client, wav):
    vocal = upload(client, wav)
    lyric = client.post("/api/lyrics", json={"name": "原稿", "content": "第一版歌词"}).json()
    created = client.post(
        "/api/jobs",
        json={
            "job_type": "mix",
            "source_id": vocal["id"],
            "lyric_id": lyric["id"],
        },
    ).json()
    assert wait_job(client, created["id"])["state"] == "completed"
    client.delete(f"/api/lyrics/{lyric['id']}")
    response = client.post(f"/api/jobs/{created['id']}/retry")
    assert response.status_code == 201, response.text
    retried = response.json()
    assert retried["params"]["lyric_snapshot"]["content"] == "第一版歌词"
    assert retried["params"]["lyric_snapshot"]["version"] == 1
    assert wait_job(client, retried["id"])["state"] == "completed"


def test_cover_orchestration_with_explicit_stub_engines(client, application, wav, monkeypatch):
    song = upload(client, wav, "song")
    voice = upload(client, wav, "voice")
    engines = application.state.engines
    calls = []
    monkeypatch.setattr(engines, "validate", lambda *_: [])

    def fake_demucs(source, output):
        calls.append("demucs")
        return [
            sys.executable,
            "-c",
            "import sys, shutil; from pathlib import Path; "
            "d=Path(sys.argv[2])/'htdemucs/source'; d.mkdir(parents=True); "
            "shutil.copyfile(sys.argv[1], d/'vocals.wav'); "
            "shutil.copyfile(sys.argv[1], d/'no_vocals.wav')",
            str(source),
            str(output),
        ]

    def fake_seed(source, reference, output, params):
        calls.append("seed")
        assert reference.is_file()
        assert params["pitch_shift"] == 0
        return [
            sys.executable,
            "-c",
            "import sys, shutil; from pathlib import Path; "
            "shutil.copyfile(sys.argv[1], Path(sys.argv[2])/'vc_stub.wav')",
            str(source),
            str(output),
        ]

    monkeypatch.setattr(engines, "demucs_command", fake_demucs)
    monkeypatch.setattr(engines, "seed_command", fake_seed)
    application.state.settings.seed_repo.mkdir(parents=True)
    created = client.post("/api/jobs", json={"source_id": song["id"], "voice_id": voice["id"]})
    assert created.status_code == 201
    finished = wait_job(client, created.json()["id"])
    assert finished["state"] == "completed", finished["error"]
    assert calls == ["demucs", "seed"]
    generated = [
        a for a in client.get("/api/assets").json() if a["metadata"].get("job_id") == finished["id"]
    ]
    assert sorted(a["kind"] for a in generated) == [
        "instrumental",
        "output",
        "output",
        "vocal",
        "vocal",
    ]
    converted = next(a for a in generated if a["metadata"].get("role") == "converted_vocal")
    assert converted["id"] not in finished["outputs"]
    assert client.get(f"/api/assets/{converted['id']}/audio").status_code == 200
    work = application.state.settings.data_dir / "jobs" / finished["id"]
    assert not (work / "converted").exists()
    remixed = client.post("/api/jobs", json={"job_type": "mix", "source_id": converted["id"]})
    assert remixed.status_code == 201
    assert wait_job(client, remixed.json()["id"])["state"] == "completed"
    assert calls == ["demucs", "seed"]

def test_preview_window_trims_cover_to_segment(client, application, monkeypatch):
    source = upload(client, make_wav(seconds=10), "vocal")
    voice = upload(client, make_wav(seconds=2), "voice")
    engines = application.state.engines
    monkeypatch.setattr(engines, "validate", lambda *_: [])
    trimmed_sources = []

    def fake_seed(source, reference, output, params):
        trimmed_sources.append(source)
        return [
            sys.executable,
            "-c",
            "import sys, shutil; from pathlib import Path; "
            "shutil.copyfile(sys.argv[1], Path(sys.argv[2])/'vc_stub.wav')",
            str(source),
            str(output),
        ]

    monkeypatch.setattr(engines, "seed_command", fake_seed)
    application.state.settings.seed_repo.mkdir(parents=True)
    created = client.post(
        "/api/jobs",
        json={
            "source_id": source["id"],
            "voice_id": voice["id"],
            "start_seconds": 2,
            "preview_seconds": 4,
        },
    )
    assert created.status_code == 201
    finished = wait_job(client, created.json()["id"])
    assert finished["state"] == "completed", finished["error"]
    assert len(trimmed_sources) == 1
    # The pipeline must convert the trimmed segment, not the full source; the
    # working file itself is cleaned up after the job finishes.
    assert trimmed_sources[0].parent.name == "preview"
    generated = [
        a for a in client.get("/api/assets").json() if a["metadata"].get("job_id") == finished["id"]
    ]
    outputs = [a for a in generated if a["kind"] == "output"]
    assert outputs
    assert all(a["metadata"].get("role") == "preview" for a in outputs)
    assert all(a["duration"] == pytest.approx(4, abs=0.5) for a in outputs)
    converted = next(a for a in generated if a["metadata"].get("role") == "converted_vocal")
    assert converted["duration"] == pytest.approx(4, abs=0.5)


def test_separate_rejects_preview_window(client, wav):
    song = upload(client, wav, "song")
    response = client.post(
        "/api/jobs",
        json={"job_type": "separate", "source_id": song["id"], "preview_seconds": 18},
    )
    assert response.status_code == 422

def test_preview_window_trims_song_before_separation(client, application, monkeypatch):
    song = upload(client, make_wav(seconds=10), "song")
    voice = upload(client, make_wav(seconds=2), "voice")
    engines = application.state.engines
    monkeypatch.setattr(engines, "validate", lambda *_: [])
    demucs_sources = []

    def fake_demucs(source, output):
        demucs_sources.append(source)
        return [
            sys.executable,
            "-c",
            "import sys, shutil; from pathlib import Path; "
            "d=Path(sys.argv[2])/'htdemucs/source'; d.mkdir(parents=True); "
            "shutil.copyfile(sys.argv[1], d/'vocals.wav'); "
            "shutil.copyfile(sys.argv[1], d/'no_vocals.wav')",
            str(source),
            str(output),
        ]

    def fake_seed(source, reference, output, params):
        return [
            sys.executable,
            "-c",
            "import sys, shutil; from pathlib import Path; "
            "shutil.copyfile(sys.argv[1], Path(sys.argv[2])/'vc_stub.wav')",
            str(source),
            str(output),
        ]

    monkeypatch.setattr(engines, "demucs_command", fake_demucs)
    monkeypatch.setattr(engines, "seed_command", fake_seed)
    application.state.settings.seed_repo.mkdir(parents=True)
    created = client.post(
        "/api/jobs",
        json={
            "source_id": song["id"],
            "voice_id": voice["id"],
            "start_seconds": 2,
            "preview_seconds": 4,
        },
    )
    assert created.status_code == 201
    finished = wait_job(client, created.json()["id"])
    assert finished["state"] == "completed", finished["error"]
    # Separation itself ran on the trimmed segment.
    assert demucs_sources[0].parent.name == "preview"
    generated = [
        a for a in client.get("/api/assets").json() if a["metadata"].get("job_id") == finished["id"]
    ]
    outputs = [a for a in generated if a["kind"] == "output"]
    assert all(a["duration"] == pytest.approx(4, abs=0.5) for a in outputs)
    assert all(a["metadata"].get("role") == "preview" for a in outputs)

def test_enhance_command_uses_polish_chain():
    command = audio.enhance_command(Path("in.wav"), Path("out.wav"))
    filters = command[command.index("-af") + 1]
    assert "highpass=f=70" in filters
    assert "deesser" in filters
    assert "acompressor" in filters
    assert "equalizer=f=3200" in filters
    assert "pcm_f32le" in command


def test_vocal_enhance_stage_keeps_pipeline_working(client, application, monkeypatch):
    source = upload(client, make_wav(seconds=3), "vocal")
    voice = upload(client, make_wav(seconds=2), "voice")
    engines = application.state.engines
    monkeypatch.setattr(engines, "validate", lambda *_: [])
    monkeypatch.setattr(
        engines,
        "seed_command",
        lambda source, reference, output, params: [
            sys.executable,
            "-c",
            "import sys, shutil; from pathlib import Path; "
            "shutil.copyfile(sys.argv[1], Path(sys.argv[2])/'vc_stub.wav')",
            str(source),
            str(output),
        ],
    )
    application.state.settings.seed_repo.mkdir(parents=True)
    created = client.post(
        "/api/jobs",
        json={"source_id": source["id"], "voice_id": voice["id"], "vocal_enhance": True},
    )
    assert created.status_code == 201
    finished = wait_job(client, created.json()["id"])
    assert finished["state"] == "completed", finished["error"]
    converted = next(
        a
        for a in client.get("/api/assets").json()
        if a["metadata"].get("job_id") == finished["id"]
        and a["metadata"].get("role") == "converted_vocal"
    )
    assert converted["duration"] == pytest.approx(3, abs=0.5)

def test_shell_and_static_send_revalidation_cache_headers(client):
    shell = client.get("/")
    assert shell.status_code == 200
    assert shell.headers["cache-control"] == "no-store"
    static = client.get("/static/app.js")
    assert static.status_code == 200
    assert static.headers["cache-control"] == "no-cache"
