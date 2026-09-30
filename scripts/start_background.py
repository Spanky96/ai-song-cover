"""Start the local app in a separate process and report its URL after it is reachable."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

PROJECT = Path(__file__).resolve().parent.parent


def start():
    first_port = int(os.environ.get("COVER_PORT", "8765"))
    for port in range(first_port, min(first_port + 20, 65536)):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
        break
    else:
        raise SystemExit("没有找到可用端口。")
    log_path = PROJECT / "artifacts/server.log"
    log_path.parent.mkdir(exist_ok=True)
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=PROJECT,
            env={**os.environ, "COVER_PORT": str(port)},
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    url = f"http://127.0.0.1:{port}"
    for _ in range(40):
        if process.poll() is not None:
            raise SystemExit(f"服务启动失败，详见 {log_path}")
        try:
            with urlopen(url, timeout=1):
                print(f"URL: {url}\nPID: {process.pid}\nLog: {log_path}")
                return
        except (URLError, TimeoutError):
            time.sleep(0.25)
    process.terminate()
    process.wait(timeout=10)
    raise SystemExit(f"服务启动超时，详见 {log_path}")


if __name__ == "__main__":
    start()
