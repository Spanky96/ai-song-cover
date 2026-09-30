import array
import io
import math
import wave

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_wav(seconds=2, frequency=440, volume=0.2, sample_rate=16000):
    samples = array.array(
        "h",
        (
            int(volume * 32767 * math.sin(2 * math.pi * frequency * i / sample_rate))
            for i in range(int(sample_rate * seconds))
        ),
    )
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(sample_rate)
        file.writeframes(samples.tobytes())
    return buffer.getvalue()


@pytest.fixture
def wav():
    return make_wav()


@pytest.fixture
def application(tmp_path, monkeypatch):
    monkeypatch.setenv("COVER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("COVER_MODELS_DIR", raising=False)
    return create_app(Settings(tmp_path))


@pytest.fixture
def client(application):
    with TestClient(application) as test_client:
        yield test_client


def upload(client, data, kind="vocal", name="测试音频", filename="audio.wav"):
    response = client.post(
        "/api/assets",
        data={"kind": kind, "name": name},
        files={"file": (filename, data, "audio/wav")},
    )
    assert response.status_code == 201, response.text
    return response.json()
