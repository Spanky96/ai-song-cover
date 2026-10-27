"""翻唱工作室 HTTP API 的轻量客户端，供 MCP 工具复用。"""

import os
from pathlib import Path

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8765"
ASSET_KINDS = ("voice", "song", "vocal", "instrumental", "output")
AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".opus", ".aac", ".aiff", ".webm"}
VOICE_KIND = "voice"


class StudioError(RuntimeError):
    """业务错误，消息面向模型与用户，可直接展示。"""


class StudioUnavailableError(StudioError):
    """工作室服务未启动或暂时不可达。"""


def default_base_url() -> str:
    return (os.environ.get("COVER_URL") or DEFAULT_BASE_URL).rstrip("/")


class StudioClient:
    """无状态 HTTP 客户端；transport 参数仅供测试注入。"""

    def __init__(self, base_url: str | None = None, transport: httpx.BaseTransport | None = None):
        self.base_url = (base_url or default_base_url()).rstrip("/")
        self._transport = transport

    def port(self) -> int:
        return httpx.URL(self.base_url).port or 80

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        data: dict | None = None,
        files: dict | None = None,
    ):
        try:
            with httpx.Client(base_url=self.base_url, transport=self._transport, timeout=60) as client:
                response = client.request(method, path, json=json_body, data=data, files=files)
        except httpx.HTTPError as error:
            raise StudioUnavailableError(
                f"无法连接翻唱工作室（{self.base_url}）：{error}"
            ) from error
        if response.status_code >= 400:
            raise StudioError(_error_detail(response))
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    def status(self) -> dict:
        return self._request("GET", "/api/status")

    def assets(self) -> list[dict]:
        return self._request("GET", "/api/assets")

    def asset(self, asset_id: str) -> dict:
        assets = self.assets()
        for item in assets:
            if item["id"] == asset_id:
                return item
        raise StudioError(f"音频资产 {asset_id} 不存在。")

    def upload(self, path: str | Path, kind: str, name: str = "") -> dict:
        if kind not in ASSET_KINDS:
            raise StudioError(f"资产类型需为 {ASSET_KINDS} 之一，收到 {kind!r}。")
        file_path = Path(path).expanduser()
        if not file_path.is_file():
            raise StudioError(f"音频文件不存在：{file_path}")
        if file_path.suffix.lower() not in AUDIO_EXTENSIONS:
            raise StudioError(f"不支持的音频格式 {file_path.suffix}，可用：{sorted(AUDIO_EXTENSIONS)}")
        with file_path.open("rb") as handle:
            return self._request(
                "POST",
                "/api/assets",
                data={"kind": kind, "name": name},
                files={"file": (file_path.name, handle, None)},
            )

    def delete_asset(self, asset_id: str) -> dict:
        return self._request("DELETE", f"/api/assets/{asset_id}")

    def jobs(self) -> list[dict]:
        return self._request("GET", "/api/jobs")

    def job(self, job_id: str) -> dict:
        jobs = self.jobs()
        for item in jobs:
            if item["id"] == job_id:
                return item
        raise StudioError(f"任务 {job_id} 不存在。")

    def create_job(self, params: dict) -> dict:
        return self._request("POST", "/api/jobs", json_body=params)

    def cancel_job(self, job_id: str) -> dict:
        return self._request("POST", f"/api/jobs/{job_id}/cancel")

    def retry_job(self, job_id: str) -> dict:
        return self._request("POST", f"/api/jobs/{job_id}/retry")

    def job_log(self, job_id: str) -> str:
        return self._request("GET", f"/api/jobs/{job_id}/log").get("log", "")

    def download_asset(self, asset_id: str, destination: Path) -> Path:
        try:
            with httpx.Client(
                base_url=self.base_url, transport=self._transport, timeout=120
            ) as client:
                response = client.get(f"/api/assets/{asset_id}/audio")
        except httpx.HTTPError as error:
            raise StudioUnavailableError(
                f"下载音频失败（{self.base_url}）：{error}"
            ) from error
        if response.status_code >= 400:
            raise StudioError(_error_detail(response))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(response.content)
        return destination


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    detail = payload.get("detail") if isinstance(payload, dict) else None
    return f"工作室返回 {response.status_code}：{detail or response.text[:200]}"


def summarize_asset(asset: dict) -> dict:
    """去掉波形大数组，只保留模型需要的摘要字段。"""
    return {
        "id": asset["id"],
        "kind": asset["kind"],
        "name": asset["name"],
        "duration": asset.get("duration"),
        "sample_rate": asset.get("sample_rate"),
        "role": (asset.get("metadata") or {}).get("role"),
        "job_id": (asset.get("metadata") or {}).get("job_id"),
        "created_at": asset.get("created_at"),
    }
