import io
import math
import struct
import time
import wave

import pytest
from fastapi.testclient import TestClient

from meeting_notes.api import create_app
from meeting_notes.settings import Settings


@pytest.fixture
def audio_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(
            b"".join(
                struct.pack("<h", int(2000 * math.sin(2 * math.pi * 220 * i / 16000)))
                for i in range(16000)
            )
        )
    return output.getvalue()


class FixtureProcessor:
    def transcribe(self, path, progress):
        assert path.is_file()
        progress(50)
        return [{"start": 0.0, "end": 1.0, "text": "다음 주까지 데모를 준비합니다."}], 1.0

    def diarize(self, path, segments):
        return [{**seg, "speaker": "Speaker 1"} for seg in segments]


class FixtureNotes:
    cache_signature = "fixture-v1"

    def __init__(self):
        self.inputs = []

    async def generate(self, transcript):
        self.inputs.append(transcript)
        return "# 테스트 회의록\n\n" + transcript


@pytest.fixture
def client(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    notes = FixtureNotes()
    app = create_app(settings, processor=FixtureProcessor(), notes_client=notes)
    with TestClient(app) as client:
        yield client


def complete_upload(client, audio_bytes, name="meeting.wav"):
    response = client.post("/upload", files={"file": (name, audio_bytes, "audio/wav")})
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    for _ in range(300):
        status = client.get("/status/" + job_id).json()
        if status["status"] in {"done", "error"}:
            assert status["status"] == "done", status
            return job_id
        time.sleep(0.01)
    raise AssertionError("Fixture pipeline did not finish")
