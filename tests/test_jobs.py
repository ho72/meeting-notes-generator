import asyncio
import threading

from fastapi import HTTPException

from meeting_notes.jobs import JobManager, JobState
from meeting_notes.settings import Settings
from meeting_notes.storage import ProjectStore
from tests.conftest import FixtureProcessor


async def test_backpressure_and_single_audio_worker(tmp_path):
    started = threading.Event()
    release = threading.Event()

    class BlockingProcessor(FixtureProcessor):
        calls = 0

        def transcribe(self, path, progress):
            self.calls += 1
            started.set()
            assert release.wait(5)
            return super().transcribe(path, progress)

    store = ProjectStore(tmp_path)
    store.initialize()
    processor = BlockingProcessor()
    manager = JobManager(Settings(data_dir=tmp_path, max_pending_jobs=2), store, processor)
    for index in range(2):
        await manager.reserve()
        path = store.uploads / f"{index}.wav"
        path.write_bytes(b"fixture")
        manager.start(JobState(str(index) * 32, "fixture.wav", path))
    assert await asyncio.to_thread(started.wait, 5)
    try:
        assert processor.calls == 1
        assert manager.jobs["1" * 32].status == "queued"
        try:
            await manager.reserve()
        except HTTPException as exc:
            assert exc.status_code == 503
        else:
            raise AssertionError("Queue limit was not enforced")
    finally:
        release.set()
        await manager.close()
    assert manager._reserved == 0
    assert not list(store.uploads.iterdir())


async def test_progress_fanout_does_not_split_events_between_tabs():
    job = JobState("1" * 32, "fixture.wav", None)
    left, right = job.subscribe(), job.subscribe()
    for i in range(100):
        job.update("처리 중", i)
    assert left.qsize() == right.qsize() == 64
    assert await left.get() == await right.get()
    job.emit({"type": "done"})
    for queue in [left, right]:
        events = [queue.get_nowait() for _ in range(queue.qsize())]
        assert events[-1] == {"type": "done"}


async def test_failed_pipeline_cleans_upload_and_does_not_expose_exception(tmp_path):
    class FailingProcessor:
        def transcribe(self, *args):
            raise RuntimeError("private-internal-exception")

    store = ProjectStore(tmp_path)
    store.initialize()
    manager = JobManager(Settings(data_dir=tmp_path), store, FailingProcessor())
    path = store.uploads / "test.wav"
    path.write_bytes(b"fixture")
    await manager.reserve()
    job = JobState("a" * 32, "test.wav", path)
    await manager.run(job)
    assert job.status == "error"
    assert "private-internal-exception" not in job.error
    assert not path.exists() and manager._reserved == 0
    assert not store.list()
