from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException

AUDIO_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".m4a",
    ".flac",
    ".ogg",
    ".opus",
    ".aac",
    ".webm",
    ".aiff",
    ".aif",
    ".wma",
}


def normalize_filename(filename: str) -> str:
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    if not name or len(name) > 240 or Path(name).suffix.lower() not in AUDIO_EXTENSIONS:
        raise HTTPException(400, "지원하는 음성 파일을 선택해주세요.")
    return name


def validate_project_id(project_id: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", project_id):
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
    return project_id


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as f:
        temp = Path(f.name)
        try:
            f.write(content)
            f.flush()
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


class ProjectStore:
    def __init__(self, data_dir: Path):
        self.root = data_dir.resolve()
        self.uploads = self.root / "uploads"
        self.projects = self.root / "projects"

    def initialize(self) -> None:
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.projects.mkdir(parents=True, exist_ok=True)

    def project_dir(self, project_id: str) -> Path:
        path = self.projects / validate_project_id(project_id)
        if path.is_symlink() or path.resolve().parent != self.projects.resolve():
            raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
        return path

    def meta(self, project_id: str) -> dict[str, Any]:
        path = self.project_dir(project_id) / "meta.json"
        if not path.is_file() or path.is_symlink():
            raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(500, "프로젝트 메타데이터를 읽을 수 없습니다.") from exc
        if not isinstance(data, dict) or data.get("job_id") != project_id:
            raise HTTPException(500, "프로젝트 메타데이터가 올바르지 않습니다.")
        return data

    def save_meta(self, project_id: str, meta: dict[str, Any]) -> None:
        atomic_write(
            self.project_dir(project_id) / "meta.json",
            json.dumps(meta, ensure_ascii=False, indent=2),
        )

    def create(
        self,
        project_id: str,
        name: str,
        upload: Path,
        segments: list[dict[str, Any]],
        duration: float,
    ) -> None:
        destination = self.project_dir(project_id)
        pending = Path(tempfile.mkdtemp(prefix=".pending-", dir=self.projects))
        try:
            (pending / "audio").mkdir()
            (pending / "docs").mkdir()
            shutil.move(str(upload), pending / "audio" / ("source" + Path(name).suffix.lower()))
            meta = {
                "job_id": project_id,
                "original_name": name,
                "title": Path(name).stem,
                "created_at": datetime.now(UTC).isoformat(),
                "duration_sec": round(duration, 2),
                "segments": segments,
                "has_transcript": False,
                "has_notes": False,
                "schema_version": 1,
            }
            atomic_write(pending / "meta.json", json.dumps(meta, ensure_ascii=False, indent=2))
            pending.rename(destination)
        finally:
            if pending.exists():
                shutil.rmtree(pending)

    def read_document(self, project_id: str, kind: str) -> str | None:
        self.meta(project_id)
        if kind not in {"transcript", "meeting-notes", "generated-notes"}:
            raise ValueError("Unknown document kind")
        path = self.project_dir(project_id) / "docs" / (kind + ".md")
        if path.is_symlink() or path.parent.is_symlink():
            raise HTTPException(500, "프로젝트 문서 경로가 올바르지 않습니다.")
        return path.read_text(encoding="utf-8") if path.exists() else None

    def save_document(self, project_id: str, kind: str, content: str) -> str:
        if kind not in {"transcript", "meeting-notes"}:
            raise ValueError("Unknown document kind")
        meta = self.meta(project_id)
        path = self.project_dir(project_id) / "docs" / (kind + ".md")
        if path.is_symlink() or path.parent.is_symlink():
            raise HTTPException(500, "프로젝트 문서 경로가 올바르지 않습니다.")
        atomic_write(path, content)
        meta["has_transcript" if kind == "transcript" else "has_notes"] = True
        self.save_meta(project_id, meta)
        return str(path.relative_to(self.root))

    @staticmethod
    def source_hash(transcript: str, signature: str = "") -> str:
        return hashlib.sha256((signature + "\0" + transcript).encode()).hexdigest()

    def cached_notes(self, project_id: str, transcript: str, signature: str = "") -> str | None:
        meta = self.meta(project_id)
        if meta.get("generated_source_hash") != self.source_hash(transcript, signature):
            return None
        return self.read_document(project_id, "generated-notes")

    def cache_notes(
        self, project_id: str, transcript: str, notes: str, signature: str = ""
    ) -> None:
        meta = self.meta(project_id)
        atomic_write(self.project_dir(project_id) / "docs/generated-notes.md", notes)
        meta["generated_source_hash"] = self.source_hash(transcript, signature)
        self.save_meta(project_id, meta)

    def detail(self, project_id: str) -> dict[str, Any]:
        meta = self.meta(project_id)
        return {
            **meta,
            "transcript": self.read_document(project_id, "transcript"),
            "notes": self.read_document(project_id, "meeting-notes"),
        }

    def list(self) -> list[dict[str, Any]]:
        records = []
        for path in self.projects.iterdir():
            if (
                not path.is_dir()
                or path.is_symlink()
                or not re.fullmatch(r"[0-9a-f]{32}", path.name)
            ):
                continue
            try:
                meta = self.meta(path.name)
            except HTTPException:
                continue
            records.append(
                {
                    key: meta.get(key)
                    for key in (
                        "job_id",
                        "title",
                        "original_name",
                        "created_at",
                        "duration_sec",
                        "has_transcript",
                        "has_notes",
                    )
                }
            )
        return sorted(records, key=lambda item: item.get("created_at") or "", reverse=True)
