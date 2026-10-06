from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .jobs import JobManager, JobState
from .llm import NotesClient
from .settings import BASE_DIR, Settings
from .storage import ProjectStore, normalize_filename
from .text import segments_to_transcript


class DocumentBody(BaseModel):
    content: str = Field(max_length=200_000)


class FormatBody(BaseModel):
    transcript: str | None = Field(default=None, max_length=200_000)


def create_app(settings: Settings | None = None, processor=None, notes_client=None) -> FastAPI:
    settings = settings or Settings()
    store = ProjectStore(settings.data_dir)
    manager = JobManager(settings, store, processor)
    notes_client = notes_client or NotesClient(settings)

    @asynccontextmanager
    async def lifespan(app):
        store.initialize()
        cleanup = asyncio.create_task(manager.cleanup())
        try:
            yield
        finally:
            cleanup.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup
            await manager.close()

    app = FastAPI(title="Meeting Notes Generator API", lifespan=lifespan)
    app.state.manager, app.state.store, app.state.notes_client = manager, store, notes_client
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
        )

    @app.middleware("http")
    async def check_write_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        allowed = {str(request.base_url).rstrip("/"), *settings.cors_origins}
        if (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and origin
            and origin not in allowed
        ):
            return JSONResponse({"detail": "허용되지 않은 웹 Origin입니다."}, status_code=403)
        return await call_next(request)

    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

    @app.get("/")
    async def index():
        return FileResponse(BASE_DIR / "static/index.html")

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "notes_enabled": bool(settings.gemini_api_key),
            "max_upload_size_mb": settings.max_upload_bytes // (1024 * 1024),
            "max_pending_jobs": settings.max_pending_jobs,
        }

    @app.post("/upload", status_code=202)
    async def upload(file: UploadFile = File(...)):
        path = None
        reserved = False
        submitted = False
        try:
            name = normalize_filename(file.filename or "")
            await manager.reserve()
            reserved = True
            job_id = uuid.uuid4().hex
            path = store.uploads / (job_id + Path(name).suffix.lower())
            size = 0
            with path.open("xb") as output:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise HTTPException(413, "파일 용량 제한을 초과했습니다.")
                    await asyncio.to_thread(output.write, chunk)
            if not size:
                raise HTTPException(400, "빈 파일은 처리할 수 없습니다.")
            manager.start(JobState(job_id, name, path))
            submitted = True
            return {"job_id": job_id, "status": "queued"}
        finally:
            await file.close()
            if not submitted:
                if path:
                    path.unlink(missing_ok=True)
                if reserved:
                    await manager.release()

    @app.get("/status/{job_id}")
    async def status(job_id: str):
        return (await manager.get(job_id)).snapshot()

    @app.get("/result/{job_id}")
    async def result(job_id: str):
        job = await manager.get(job_id)
        if job.status == "error":
            raise HTTPException(422, job.error)
        if job.status != "done":
            return JSONResponse(job.snapshot(), status_code=202)
        return {"job_id": job_id, "status": "done", "result": job.result}

    @app.get("/events/{job_id}")
    async def events(job_id: str, request: Request):
        job = await manager.get(job_id)
        queue = job.subscribe()

        async def stream():
            try:
                yield "data: " + json.dumps(job.snapshot(), ensure_ascii=False) + "\n\n"
                if job.status in {"done", "error"}:
                    terminal = {**job.snapshot(), "type": job.status}
                    if job.status == "done":
                        terminal["result"] = job.result
                    yield "data: " + json.dumps(terminal, ensure_ascii=False) + "\n\n"
                    return
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                    if event["type"] in {"done", "error"}:
                        break
            finally:
                job.subscribers.discard(queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/projects")
    async def list_projects():
        return await asyncio.to_thread(store.list)

    @app.get("/projects/{project_id}")
    async def get_project(project_id: str):
        async with manager.project_lock(project_id):
            return await asyncio.to_thread(store.detail, project_id)

    async def save(project_id: str, body: DocumentBody, kind: str):
        async with manager.project_lock(project_id):
            path = await asyncio.to_thread(store.save_document, project_id, kind, body.content)
        return {"ok": True, "path": path}

    @app.post("/projects/{project_id}/save-transcript")
    async def save_transcript(project_id: str, body: DocumentBody):
        return await save(project_id, body, "transcript")

    @app.post("/projects/{project_id}/save-notes")
    async def save_notes(project_id: str, body: DocumentBody):
        return await save(project_id, body, "meeting-notes")

    @app.post("/format/{job_id}")
    async def format_notes(job_id: str, body: FormatBody | None = None):
        job = await manager.get(job_id)
        if job.status != "done":
            raise HTTPException(409, "음성 처리가 아직 완료되지 않았습니다.")
        async with manager.project_lock(job_id):
            transcript = (
                body.transcript
                if body and body.transcript is not None
                else await asyncio.to_thread(store.read_document, job_id, "transcript")
            )
            if transcript is None:
                transcript = segments_to_transcript(job.result)
            transcript = transcript.strip()
            if not transcript:
                raise HTTPException(422, "회의록으로 정리할 전사문이 없습니다.")
            if len(transcript) > settings.max_document_chars:
                raise HTTPException(413, "전사문이 너무 깁니다. 나눠서 처리해주세요.")
            cached = await asyncio.to_thread(
                store.cached_notes, job_id, transcript, notes_client.cache_signature
            )
            if cached is not None:
                return {"job_id": job_id, "notes": cached, "cached": True}
            notes = await notes_client.generate(transcript)
            await asyncio.to_thread(
                store.cache_notes, job_id, transcript, notes, notes_client.cache_signature
            )
            return {"job_id": job_id, "notes": notes, "cached": False}

    return app


app = create_app()
