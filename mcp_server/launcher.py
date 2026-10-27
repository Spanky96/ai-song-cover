"""确保翻唱工作室服务可用；不可达时按需后台拉起。"""

import subprocess
import sys
import time
from pathlib import Path

from .client import StudioClient, StudioError, StudioUnavailableError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = PROJECT_ROOT / "artifacts/server.log"
START_TIMEOUT_SECONDS = 90.0
POLL_INTERVAL_SECONDS = 1.0


def ensure_running(client: StudioClient, *, timeout_seconds: float = START_TIMEOUT_SECONDS) -> dict:
    try:
        return client.status()
    except StudioUnavailableError:
        start_server(client)
        deadline = time.monotonic() + timeout_seconds
        last_error = None
        while time.monotonic() < deadline:
            time.sleep(POLL_INTERVAL_SECONDS)
            try:
                return client.status()
            except StudioUnavailableError as error:
                last_error = error
        raise StudioError(
            f"后台启动翻唱工作室超时（{timeout_seconds:.0f} 秒），"
            f"日志见 {LOG_PATH}。原始错误：{last_error}"
        ) from last_error


def start_server(client: StudioClient) -> int:
    """与 scripts/start_background.py 相同的启动方式，但固定使用客户端端口。"""
    LOG_PATH.parent.mkdir(exist_ok=True)
    with LOG_PATH.open("ab") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(client.port()),
            ],
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return process.pid
