from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "medium")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "auto")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "ko")
WHISPER_BEAM_SIZE = env_int("WHISPER_BEAM_SIZE", 6)
WHISPER_BEST_OF = env_int("WHISPER_BEST_OF", 6)
WHISPER_TEMPERATURE = env_float("WHISPER_TEMPERATURE", 0.0)
WHISPER_VAD_MIN_SILENCE_MS = env_int("WHISPER_VAD_MIN_SILENCE_MS", 500)
WHISPER_VAD_SPEECH_PAD_MS = env_int("WHISPER_VAD_SPEECH_PAD_MS", 350)
WHISPER_INITIAL_PROMPT = os.getenv(
    "WHISPER_INITIAL_PROMPT",
    "다음은 한국어 회의 대화입니다. 화자 발화를 정확하게 받아쓰고 반복 어절을 최소화하세요.",
)
WHISPER_CONDITION_ON_PREV_TEXT = os.getenv(
    "WHISPER_CONDITION_ON_PREV_TEXT", "false"
).strip().lower() in {"1", "true", "yes", "y"}
WHISPER_REPETITION_FILTER = os.getenv("WHISPER_REPETITION_FILTER", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "y",
}
DIARIZATION_NUM_SPEAKERS = env_int("DIARIZATION_NUM_SPEAKERS", 0)
DIARIZATION_MIN_SPEAKERS = env_int("DIARIZATION_MIN_SPEAKERS", 1)
DIARIZATION_MAX_SPEAKERS = env_int("DIARIZATION_MAX_SPEAKERS", 8)


def positive_int(name: str, default: int) -> int:
    return max(1, env_int(name, default))


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(
        default_factory=lambda: (
            Path(os.getenv("MEETING_DATA_DIR", str(BASE_DIR / "data"))).expanduser().resolve()
        )
    )
    max_upload_bytes: int = field(
        default_factory=lambda: positive_int("MAX_UPLOAD_SIZE_MB", 500) * 1024 * 1024
    )
    max_pending_jobs: int = field(default_factory=lambda: positive_int("MAX_PENDING_JOBS", 8))
    job_ttl_seconds: int = field(default_factory=lambda: positive_int("JOB_TTL_SECONDS", 1800))
    max_document_chars: int = 200_000
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""), repr=False)
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-3.8-flash"))
    gemini_max_output_tokens: int = field(
        default_factory=lambda: positive_int("GEMINI_MAX_OUTPUT_TOKENS", 8192)
    )
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            x.strip() for x in os.getenv("CORS_ORIGINS", "").split(",") if x.strip()
        )
    )

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", self.gemini_model):
            raise ValueError("GEMINI_MODEL must be a model identifier.")
        if "*" in self.cors_origins:
            raise ValueError("CORS_ORIGINS must list explicit origins.")
