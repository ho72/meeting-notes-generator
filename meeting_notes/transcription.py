from __future__ import annotations

import re
from collections.abc import Callable
from inspect import signature
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .settings import (
    WHISPER_BEAM_SIZE,
    WHISPER_BEST_OF,
    WHISPER_CONDITION_ON_PREV_TEXT,
    WHISPER_INITIAL_PROMPT,
    WHISPER_LANGUAGE,
    WHISPER_REPETITION_FILTER,
    WHISPER_TEMPERATURE,
    WHISPER_VAD_MIN_SILENCE_MS,
    WHISPER_VAD_SPEECH_PAD_MS,
)
from .text import is_repetitive_text, remove_excessive_duplicates

if TYPE_CHECKING:
    from faster_whisper import WhisperModel


def build_transcribe_kwargs(model: WhisperModel) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "task": "transcribe",
        "language": WHISPER_LANGUAGE if WHISPER_LANGUAGE else None,
        "beam_size": max(1, WHISPER_BEAM_SIZE),
        "best_of": max(1, WHISPER_BEST_OF),
        "temperature": WHISPER_TEMPERATURE,
        "condition_on_previous_text": WHISPER_CONDITION_ON_PREV_TEXT,
        "initial_prompt": WHISPER_INITIAL_PROMPT if WHISPER_INITIAL_PROMPT else None,
        "patience": 1.2,
        "repetition_penalty": 1.12,
        "no_repeat_ngram_size": 3,
        "word_timestamps": False,
        "vad_filter": True,
        "vad_parameters": {
            "min_silence_duration_ms": WHISPER_VAD_MIN_SILENCE_MS,
            "speech_pad_ms": WHISPER_VAD_SPEECH_PAD_MS,
        },
        "compression_ratio_threshold": 2.4,
        "log_prob_threshold": -1.0,
        "no_speech_threshold": 0.6,
    }
    valid_params = set(signature(model.transcribe).parameters.keys())
    return {
        key: value for key, value in candidate.items() if key in valid_params and value is not None
    }


def transcribe_audio(
    model: WhisperModel,
    audio_path: Path,
    progress_cb: Callable[[float], None] | None = None,
) -> tuple[list[dict[str, Any]], float]:
    segments_iter, info = model.transcribe(str(audio_path), **build_transcribe_kwargs(model))
    duration = float(getattr(info, "duration", 0.0) or 0.0)
    raw_segments: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    for seg in segments_iter:
        if progress_cb and duration > 0:
            ratio = min(max(float(seg.end), 0.0), duration) / duration
            progress_cb(40 + ratio * 35)
        text = re.sub(r"\s+", " ", seg.text).strip()
        if not text:
            continue
        item = {
            "start": float(seg.start),
            "end": float(seg.end),
            "text": text,
        }
        raw_segments.append(item)
        avg_logprob = float(
            getattr(seg, "avg_logprob", -0.6)
            if getattr(seg, "avg_logprob", None) is not None
            else -0.6
        )
        no_speech_prob = float(getattr(seg, "no_speech_prob", 0.0) or 0.0)
        compression_ratio = float(getattr(seg, "compression_ratio", 0.0) or 0.0)

        if no_speech_prob > 0.7 and avg_logprob < -0.8:
            continue
        if compression_ratio > 2.6 and avg_logprob < -0.8:
            continue
        if WHISPER_REPETITION_FILTER and is_repetitive_text(text) and avg_logprob < -0.2:
            continue

        segments.append(item)
    if raw_segments and len(segments) < max(3, int(len(raw_segments) * 0.15)):
        segments = raw_segments
    segments = remove_excessive_duplicates(segments, max_repeat=2)
    return segments, duration
