from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from .settings import WHISPER_COMPUTE_TYPE, WHISPER_DEVICE, WHISPER_MODEL_SIZE, Settings
from .storage import ProjectStore, validate_project_id

logger = logging.getLogger(__name__)


class AudioProcessor:
    def __init__(self):
        self.model = None

    def transcribe(self, path: Path, progress):
        from faster_whisper import WhisperModel

        from .transcription import transcribe_audio

        if self.model is None:
            self.model = WhisperModel(
                WHISPER_MODEL_SIZE, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE_TYPE
            )
        return transcribe_audio(self.model, path, progress)

    def diarize(self, path: Path, segments):
        from .diarization import diarize_with_mfcc

        return diarize_with_mfcc(path, segments)


@dataclass
class JobState:
    job_id: str
    original_name: str
    file_path: Path
    status: str = "queued"
    stage: str = "처리 대기 중"
    progress: int = 0
    result: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    finished_at: float | None = None
    subscribers: set[asyncio.Queue] = field(default_factory=set)

    def snapshot(self) -> dict[str, Any]:
        return {
            "type": "status",
            "job_id": self.job_id,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "error": self.error,
            "result_count": len(self.result),
        }

    def emit(self, event: dict[str, Any]) -> None:
        for queue in tuple(self.subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

    def update(self, stage: str, progress: int) -> None:
        self.status = "processing"
        self.stage = stage
        self.progress = max(self.progress, min(99, int(progress)))
        self.emit(self.snapshot())

    def subscribe(self) -> asyncio.Queue:
        if len(self.subscribers) >= 16:
            raise HTTPException(429, "이 작업의 상태 연결이 너무 많습니다.")
        queue = asyncio.Queue(maxsize=64)
        self.subscribers.add(queue)
        return queue


class JobManager:
    def __init__(self, settings: Settings, store: ProjectStore, processor=None):
        self.settings = settings
        self.store = store
        self.processor = processor or AudioProcessor()
        self.jobs: dict[str, JobState] = {}
        self.tasks: set[asyncio.Task] = set()
        self.project_locks: dict[str, asyncio.Lock] = {}
        self._capacity_lock = asyncio.Lock()
        self._reserved = 0
        self._worker = asyncio.Semaphore(1)
        self.closing = False

    async def reserve(self) -> None:
        async with self._capacity_lock:
            if self.closing or self._reserved >= self.settings.max_pending_jobs:
                raise HTTPException(
                    503,
                    "처리 대기열이 가득 찼습니다. 잠시 후 다시 시도해주세요.",
                    headers={"Retry-After": "10"},
                )
            self._reserved += 1

    async def release(self) -> None:
        async with self._capacity_lock:
            self._reserved -= 1

    def start(self, job: JobState) -> None:
        self.jobs[job.job_id] = job
        task = asyncio.create_task(self.run(job))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def get(self, job_id: str) -> JobState:
        validate_project_id(job_id)
        if job_id in self.jobs:
            return self.jobs[job_id]
        meta = await asyncio.to_thread(self.store.meta, job_id)
        return JobState(
            job_id,
            meta["original_name"],
            Path(),
            status="done",
            stage="완료",
            progress=100,
            result=meta.get("segments", []),
        )

    def project_lock(self, job_id: str) -> asyncio.Lock:
        validate_project_id(job_id)
        if job_id not in self.jobs and not (self.store.project_dir(job_id) / "meta.json").is_file():
            raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
        return self.project_locks.setdefault(job_id, asyncio.Lock())

    async def run(self, job: JobState) -> None:
        try:
            async with self._worker:
                if self.closing:
                    raise RuntimeError("Server shutting down")
                loop = asyncio.get_running_loop()
                job.update("모델 로딩·텍스트 변환 중...", 10)

                def progress(value):
                    def apply():
                        if job.status == "processing" and int(value) > job.progress:
                            job.update("텍스트 변환 중...", int(value))

                    loop.call_soon_threadsafe(apply)

                segments, duration = await asyncio.to_thread(
                    self.processor.transcribe, job.file_path, progress
                )
                job.emit(
                    {
                        "type": "transcript_info",
                        "duration_sec": round(duration, 2),
                        "segment_count": len(segments),
                    }
                )
                job.update("화자 구분 중...", 75)
                result = await asyncio.to_thread(self.processor.diarize, job.file_path, segments)
                await asyncio.to_thread(
                    self.store.create,
                    job.job_id,
                    job.original_name,
                    job.file_path,
                    result,
                    duration,
                )
                # Completion is published only after the project is fully persisted.
                job.result = result
                job.status, job.stage, job.progress = "done", "완료", 100
                job.emit({**job.snapshot(), "type": "done", "result": result})
        except Exception:
            logger.exception("Audio processing failed for job %s", job.job_id)
            job.status, job.stage = "error", "실패"
            job.error = (
                "음성 처리에 실패했습니다. 지원 형식·음성 내용·모델 실행 환경을 확인해주세요."
            )
            job.emit({**job.snapshot(), "type": "error"})
        finally:
            job.finished_at = time.monotonic()
            try:
                job.file_path.unlink(missing_ok=True)
            finally:
                await self.release()

    def expire(self) -> None:
        now = time.monotonic()
        for job_id, job in list(self.jobs.items()):
            if (
                job.finished_at is not None
                and now - job.finished_at > self.settings.job_ttl_seconds
                and not job.subscribers
            ):
                self.jobs.pop(job_id, None)
                lock = self.project_locks.get(job_id)
                if lock is not None and not lock.locked():
                    self.project_locks.pop(job_id, None)

    async def cleanup(self) -> None:
        while True:
            await asyncio.sleep(60)
            self.expire()

    async def close(self) -> None:
        self.closing = True
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
