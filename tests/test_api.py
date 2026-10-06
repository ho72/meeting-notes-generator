import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from meeting_notes.api import create_app
from meeting_notes.settings import Settings
from meeting_notes.storage import ProjectStore
from tests.conftest import FixtureNotes, FixtureProcessor, complete_upload


def test_health_and_static_assets(client):
    assert client.get("/health").json()["notes_enabled"] is False
    for route in ["/", "/static/app.js", "/static/styles.css"]:
        assert client.get(route).status_code == 200


def test_upload_persist_edit_format_save_and_reload(client, audio_bytes):
    job_id = complete_upload(client, audio_bytes)
    assert client.get("/result/" + job_id).json()["result"][0]["speaker"] == "Speaker 1"
    changed = "수정된 전사: 데모 제출 기한을 금요일로 변경합니다."
    response = client.post("/format/" + job_id, json={"transcript": changed})
    assert response.status_code == 200
    assert client.app.state.notes_client.inputs == [changed]
    assert client.post("/format/" + job_id, json={"transcript": changed}).json()["cached"]
    assert len(client.app.state.notes_client.inputs) == 1
    assert (
        client.post(f"/projects/{job_id}/save-transcript", json={"content": changed}).status_code
        == 200
    )
    assert (
        client.post(
            f"/projects/{job_id}/save-notes", json={"content": "사람이 검토한 회의록"}
        ).status_code
        == 200
    )
    detail = client.get("/projects/" + job_id).json()
    assert detail["transcript"] == changed and detail["notes"] == "사람이 검토한 회의록"
    assert detail["has_transcript"] and detail["has_notes"]
    client.app.state.manager.jobs.clear()  # Same behavior as a server restart or TTL expiry.
    assert client.get("/result/" + job_id).json()["status"] == "done"
    assert client.post("/format/" + job_id).json()["cached"]
    assert client.get("/projects").json()[0]["job_id"] == job_id


def test_changed_transcript_invalidates_cache_without_overwriting_reviewed_notes(
    client, audio_bytes
):
    job_id = complete_upload(client, audio_bytes)
    for transcript in ["첫 번째 수정", "두 번째 수정"]:
        assert (
            client.post("/format/" + job_id, json={"transcript": transcript}).json()["cached"]
            is False
        )
    assert len(client.app.state.notes_client.inputs) == 2
    client.post(f"/projects/{job_id}/save-notes", json={"content": "검토 완료"})
    client.post("/format/" + job_id, json={"transcript": "세 번째 수정"})
    assert client.get("/projects/" + job_id).json()["notes"] == "검토 완료"


def test_cache_signature_change_recalculates_notes(client, audio_bytes):
    job_id = complete_upload(client, audio_bytes)
    client.post("/format/" + job_id, json={"transcript": "같은 내용"})
    client.app.state.notes_client.cache_signature = "fixture-v2"
    assert (
        client.post("/format/" + job_id, json={"transcript": "같은 내용"}).json()["cached"] is False
    )


@pytest.mark.parametrize(
    "name,payload,status", [("meeting.wav", b"", 400), ("notes.txt", b"text", 400)]
)
def test_invalid_uploads_leave_no_files_or_capacity_reservations(client, name, payload, status):
    response = client.post("/upload", files={"file": (name, payload)})
    assert response.status_code == status
    assert not list(client.app.state.store.uploads.iterdir())
    assert client.app.state.manager._reserved == 0


def test_oversized_upload_is_removed(tmp_path):
    app = create_app(
        Settings(data_dir=tmp_path, max_upload_bytes=8), FixtureProcessor(), FixtureNotes()
    )
    with TestClient(app) as client:
        assert client.post("/upload", files={"file": ("meeting.wav", b"x" * 9)}).status_code == 413
        assert not list(app.state.store.uploads.iterdir())
        assert app.state.manager._reserved == 0


def test_filename_path_is_normalized_and_audio_stays_inside_data(client, audio_bytes):
    job_id = complete_upload(client, audio_bytes, "../../outside.wav")
    detail = client.get("/projects/" + job_id).json()
    assert detail["original_name"] == "outside.wav"
    assert (client.app.state.store.project_dir(job_id) / "audio/source.wav").is_file()


@pytest.mark.parametrize("project_id", ["not-an-id", "..", "a" * 32])
def test_invalid_or_missing_projects_cannot_be_created_by_save(client, project_id):
    assert (
        client.post(f"/projects/{project_id}/save-notes", json={"content": "text"}).status_code
        == 404
    )
    assert not list(client.app.state.store.projects.iterdir())
    assert not client.app.state.manager.project_locks


def test_malformed_and_oversized_documents_are_rejected(client, audio_bytes):
    job_id = complete_upload(client, audio_bytes)
    route = f"/projects/{job_id}/save-notes"
    assert client.post(route, json={"content": ["not", "text"]}).status_code == 422
    assert client.post(route, json={"content": "x" * 200_001}).status_code == 422
    assert client.post("/format/" + job_id, json={"transcript": "  "}).status_code == 422


def test_sse_replays_completed_result_and_releases_subscriber(client, audio_bytes):
    job_id = complete_upload(client, audio_bytes)
    response = client.get("/events/" + job_id)
    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    assert events[-1]["type"] == "done"
    assert events[-1]["result"][0]["text"]
    assert not client.app.state.manager.jobs[job_id].subscribers


def test_default_app_does_not_allow_arbitrary_cross_origin_requests(client):
    response = client.options(
        "/upload",
        headers={"Origin": "https://unrelated.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in response.headers


async def test_simultaneous_format_requests_only_generate_once(tmp_path):
    notes = FixtureNotes()
    app = create_app(Settings(data_dir=tmp_path), FixtureProcessor(), notes)
    async with app.router.lifespan_context(app):
        store = app.state.store
        upload = store.uploads / "fixture.wav"
        upload.write_bytes(b"fixture")
        project_id = "1" * 32
        store.create(
            project_id,
            "fixture.wav",
            upload,
            [{"speaker": "Speaker 1", "start": 0, "end": 1, "text": "text"}],
            1,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            responses = await asyncio.gather(
                *[
                    client.post("/format/" + project_id, json={"transcript": "수정 내용"})
                    for _ in range(2)
                ]
            )
        assert [r.status_code for r in responses] == [200, 200]
        assert len(notes.inputs) == 1


def test_corrupt_metadata_does_not_break_project_list(client):
    store: ProjectStore = client.app.state.store
    directory = store.project_dir("c" * 32)
    directory.mkdir()
    (directory / "meta.json").write_text("{not-json")
    assert client.get("/projects").json() == []


def test_cross_origin_form_post_is_rejected_before_processing(client, audio_bytes):
    response = client.post(
        "/upload",
        files={"file": ("test.wav", audio_bytes)},
        headers={"Origin": "https://unrelated.example"},
    )
    assert response.status_code == 403
    assert not client.app.state.manager.jobs
    assert not list(client.app.state.store.uploads.iterdir())


def test_same_origin_form_post_is_accepted(client, audio_bytes):
    assert (
        client.post(
            "/upload",
            files={"file": ("test.wav", audio_bytes)},
            headers={"Origin": "http://testserver"},
        ).status_code
        == 202
    )
