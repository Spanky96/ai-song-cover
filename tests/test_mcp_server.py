import asyncio
import json

import httpx
import pytest

from mcp_server import launcher
from mcp_server.client import StudioClient, StudioError, StudioUnavailableError, summarize_asset
from mcp_server.server import (
    download_outputs,
    mcp,
    resolve_voice,
    run_voice_swap,
    wait_for_job,
)


def transport(handler):
    return httpx.MockTransport(handler)


def make_asset(asset_id="a1", kind="voice", name="我的声音", duration=6.4):
    return {
        "id": asset_id,
        "kind": kind,
        "name": name,
        "path": f"assets/{kind}s/{asset_id}.wav",
        "duration": duration,
        "sample_rate": 44100,
        "metadata": {},
        "created_at": "2026-10-27T10:00:00",
    }


def make_job(job_id="j1", state="completed", outputs=("o1",), params=None):
    return {
        "id": job_id,
        "name": "测试任务",
        "state": state,
        "stage": "已完成" if state == "completed" else "等待处理",
        "progress": 100 if state == "completed" else 0,
        "params": {"job_type": "cover", "source_name": "老爷爷真小气", **(params or {})},
        "outputs": list(outputs),
        "error": None,
        "created_at": "2026-10-27T10:00:00",
        "updated_at": "2026-10-27T10:20:00",
    }


class FakeClient:
    def __init__(self, assets=None, jobs=None):
        self.assets_list = assets if assets is not None else []
        self.jobs_list = jobs if jobs is not None else []
        self.uploaded = []
        self.created = []
        self.downloaded = []
        self.status_calls = 0

    def status(self):
        self.status_calls += 1
        if self.status_calls == 1:
            raise StudioUnavailableError("connection refused")
        return {"platform": "Darwin"}

    def port(self):
        return 8765

    def assets(self):
        return self.assets_list

    def asset(self, asset_id):
        for item in self.assets_list:
            if item["id"] == asset_id:
                return item
        raise StudioError(f"音频资产 {asset_id} 不存在。")

    def upload(self, path, kind, name=""):
        asset = make_asset(asset_id=f"new-{len(self.uploaded)}", kind=kind, name=name or "上传")
        self.uploaded.append((path, kind))
        self.assets_list = [*self.assets_list, asset]
        return asset

    def create_job(self, params):
        self.created.append(params)
        return make_job(state="queued", outputs=())

    def job(self, job_id):
        return self.job_results.pop(0)

    def download_asset(self, asset_id, destination):
        self.downloaded.append((asset_id, destination))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"audio")
        return destination


class TestClient:
    def test_api_error_detail_is_surfaced(self, tmp_path):
        def handler(request):
            return httpx.Response(422, json={"detail": "参考音色需为 1 至 30 秒的干净人声录音。"})

        path = tmp_path / "ref.wav"
        path.write_bytes(b"wav")
        with pytest.raises(StudioError, match="干净人声"):
            StudioClient(transport=transport(handler)).upload(path, "voice")

    def test_connection_failure_raises_unavailable(self):
        def handler(request):
            raise httpx.ConnectError("refused")

        with pytest.raises(StudioUnavailableError):
            StudioClient(transport=transport(handler)).status()

    def test_upload_rejects_bad_kind_extension_and_missing_file(self, tmp_path):
        client = StudioClient(transport=transport(lambda request: httpx.Response(200, json={})))
        existing = tmp_path / "a.xyz"
        existing.write_bytes(b"x")
        with pytest.raises(StudioError, match="资产类型"):
            client.upload(tmp_path / "a.wav", "bad-kind")
        with pytest.raises(StudioError, match="不支持的音频格式"):
            client.upload(existing, "song")
        with pytest.raises(StudioError, match="不存在"):
            client.upload(tmp_path / "missing.wav", "song")

    def test_upload_posts_multipart_and_parses_asset(self, tmp_path):
        seen = {}

        def handler(request):
            seen["path"] = request.url.path
            seen["content_type"] = request.headers["content-type"]
            seen["body"] = request.content.decode("utf-8", "replace")
            return httpx.Response(201, json=make_asset(asset_id="up1", kind="song"))

        path = tmp_path / "song.mp3"
        path.write_bytes(b"id3data")
        asset = StudioClient(transport=transport(handler)).upload(path, "song", "老爷爷")
        assert seen["path"] == "/api/assets"
        assert "multipart/form-data" in seen["content_type"]
        assert 'filename="song.mp3"' in seen["body"]
        assert asset["id"] == "up1"

    def test_download_asset_writes_file(self, tmp_path):
        def handler(request):
            assert request.url.path == "/api/assets/o1/audio"
            return httpx.Response(200, content=b"mp3-bytes")

        destination = tmp_path / "out" / "成品.mp3"
        StudioClient(transport=transport(handler)).download_asset("o1", destination)
        assert destination.read_bytes() == b"mp3-bytes"

    def test_summarize_asset_strips_waveform(self):
        asset = {**make_asset(), "waveform": [0.1] * 1000}
        summary = summarize_asset(asset)
        assert "waveform" not in summary
        assert summary["name"] == "我的声音"


class TestResolveVoice:
    def test_default_keyword_matches_single_voice(self):
        asset = resolve_voice([make_asset(name="我的声音 · 原始录音"), make_asset("v2", name="上游示例")], None)
        assert asset["name"] == "我的声音 · 原始录音"

    def test_ambiguous_match_lists_candidates(self):
        voices = [make_asset("v1", name="我的歌声音色 · 如愿"), make_asset("v2", name="我的歌声音色 · 庐州月")]
        with pytest.raises(StudioError, match="2 个音色"):
            resolve_voice(voices, "我的歌声音色")

    def test_id_takes_priority(self):
        voices = [make_asset("v1", name="示例"), make_asset("v2", name="示例")]
        assert resolve_voice(voices, "v2")["id"] == "v2"

    def test_no_voice_assets(self):
        with pytest.raises(StudioError, match="音色库为空"):
            resolve_voice([make_asset(kind="song")], None)


class TestWaitForJob:
    def test_returns_immediately_for_terminal_job(self):
        client = FakeClient()
        client.job_results = [make_job(state="failed")]
        assert wait_for_job(client, "j1", timeout_seconds=1)["state"] == "failed"

    def test_timeout_reports_stage(self):
        client = FakeClient()
        client.job_results = [make_job(state="running", outputs=())] * 5
        with pytest.raises(StudioError, match="running · 等待处理"):
            wait_for_job(client, "j1", timeout_seconds=0, poll_seconds=0)

    def test_polls_until_completed(self):
        client = FakeClient()
        client.job_results = [make_job(state="running", outputs=()), make_job(state="completed")]
        assert wait_for_job(client, "j1", timeout_seconds=5, poll_seconds=0)["state"] == "completed"


class TestVoiceSwap:
    def test_requires_source(self):
        with pytest.raises(StudioError, match="audio_path"):
            run_voice_swap(FakeClient())

    def test_full_flow_uploads_converts_and_downloads(self, tmp_path, monkeypatch):
        monkeypatch.setattr("mcp_server.server.ensure_running", lambda client: {"ok": True})
        client = FakeClient(assets=[make_asset(name="我的声音 · 原始录音")])
        client.job_results = [make_job(state="completed", outputs=("o1",))]
        client.assets_list = [
            *client.assets_list,
            make_asset("o1", kind="output", name="老爷爷真小气 · 换声"),
        ]
        result = run_voice_swap(
            client,
            audio_path="/tmp/老爷爷.mp3",
            output_dir=tmp_path,
            timeout_seconds=10,
        )
        assert client.uploaded == [("/tmp/老爷爷.mp3", "song")]
        assert client.created[0]["voice_id"] == "a1"
        assert client.created[0]["job_type"] == "cover"
        assert client.created[0]["auto_f0_adjust"] is False
        assert result["state"] == "completed"
        assert result["files"] == [str(tmp_path / "老爷爷真小气 · 换声.wav")]
        assert (tmp_path / "老爷爷真小气 · 换声.wav").read_bytes() == b"audio"

    def test_wait_false_returns_hint(self, monkeypatch):
        monkeypatch.setattr("mcp_server.server.ensure_running", lambda client: {"ok": True})
        client = FakeClient(assets=[make_asset(name="我的声音 · 原始录音")])
        result = run_voice_swap(
            client, audio_path="/tmp/a.mp3", wait=False, auto_f0_adjust=True
        )
        assert result["state"] == "queued"
        assert client.created[0]["auto_f0_adjust"] is True
        assert "wait_job" in result["hint"]

    def test_failed_job_reports_hint(self, monkeypatch, tmp_path):
        monkeypatch.setattr("mcp_server.server.ensure_running", lambda client: {"ok": True})
        client = FakeClient(assets=[make_asset(name="我的声音 · 原始录音")])
        client.job_results = [make_job(state="failed", outputs=())]
        result = run_voice_swap(client, audio_path="/tmp/a.mp3", output_dir=tmp_path)
        assert result["state"] == "failed"
        assert "get_job_log" in result["hint"]

    def test_existing_file_is_not_overwritten(self, tmp_path):
        client = FakeClient(
            assets=[
                make_asset("o1", kind="output", name="成品"),
                make_asset("o2", kind="output", name="成品"),
            ]
        )
        (tmp_path / "成品.wav").write_bytes(b"old")
        files = download_outputs(client, make_job(outputs=("o1", "o2")), tmp_path)
        assert files[0] == str(tmp_path / "o1.wav")
        assert (tmp_path / "成品.wav").read_bytes() == b"old"


class TestLauncher:
    def test_ensure_running_starts_server_once(self, monkeypatch):
        client = FakeClient()
        spawned = []
        monkeypatch.setattr(launcher, "start_server", lambda c: spawned.append(c.port()) or 123)
        monkeypatch.setattr(launcher.time, "sleep", lambda _: None)
        assert launcher.ensure_running(client) == {"platform": "Darwin"}
        assert spawned == [8765]

    def test_ensure_running_timeout_mentions_log(self, monkeypatch):
        class DeadClient:
            def status(self):
                raise StudioUnavailableError("down")

        monkeypatch.setattr(launcher, "start_server", lambda client: 1)
        monkeypatch.setattr(launcher.time, "sleep", lambda _: None)
        with pytest.raises(StudioError, match="超时"):
            launcher.ensure_running(DeadClient(), timeout_seconds=0)


class TestToolRegistration:
    def test_expected_tools_are_registered(self):
        names = {tool.name for tool in asyncio.run(mcp.list_tools())}
        expected = {
            "studio_status",
            "list_assets",
            "upload_audio",
            "voice_swap",
            "create_job",
            "get_job",
            "list_jobs",
            "wait_job",
            "get_job_log",
            "cancel_job",
            "retry_job",
            "download_asset",
            "delete_asset",
        }
        assert expected <= names

    def test_voice_swap_schema_has_validations(self):
        tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
        schema = json.dumps(tools["voice_swap"].inputSchema)
        assert "pitch_shift" in schema and "preview_seconds" in schema
