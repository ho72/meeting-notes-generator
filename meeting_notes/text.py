from __future__ import annotations

import re
from collections import Counter
from typing import Any


def tokenized_text(text: str) -> list[str]:
    return re.findall(r"[0-9A-Za-z가-힣']+", text)


def is_repetitive_text(text: str) -> bool:
    tokens = tokenized_text(text)
    if len(tokens) < 6:
        return False
    lowered = [t.lower() for t in tokens]
    token_counter = Counter(lowered)
    most_common_count = token_counter.most_common(1)[0][1]
    dominant_ratio = most_common_count / len(lowered)
    unique_ratio = len(token_counter) / len(lowered)

    bigrams = [f"{lowered[i]} {lowered[i + 1]}" for i in range(len(lowered) - 1)]
    bigram_ratio = 0.0
    if bigrams:
        bigram_ratio = Counter(bigrams).most_common(1)[0][1] / len(bigrams)
    return dominant_ratio >= 0.58 or unique_ratio <= 0.36 or bigram_ratio >= 0.5


def normalize_for_compare(text: str) -> str:
    tokens = tokenized_text(text.lower())
    return " ".join(tokens)


def remove_excessive_duplicates(
    segments: list[dict[str, Any]], max_repeat: int = 2
) -> list[dict[str, Any]]:
    if not segments:
        return segments
    cleaned: list[dict[str, Any]] = []
    previous = ""
    repeat_count = 0
    for seg in segments:
        norm = normalize_for_compare(seg["text"])
        if norm and norm == previous:
            repeat_count += 1
        else:
            previous = norm
            repeat_count = 1
        if norm and repeat_count > max_repeat:
            continue
        cleaned.append(seg)
    return cleaned


def merge_consecutive_segments(
    diarized: list[dict[str, Any]], gap_threshold: float = 1.2
) -> list[dict[str, Any]]:
    if not diarized:
        return diarized
    merged: list[dict[str, Any]] = [diarized[0].copy()]
    for item in diarized[1:]:
        last = merged[-1]
        should_merge = (
            item["speaker"] == last["speaker"] and item["start"] - last["end"] <= gap_threshold
        )
        if should_merge:
            last["text"] = f"{last['text']} {item['text']}".strip()
            last["end"] = item["end"]
            continue
        merged.append(item.copy())
    return merged


def segments_to_transcript(segments: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for seg in segments:
        h = int(seg["start"]) // 3600
        m = (int(seg["start"]) % 3600) // 60
        s = int(seg["start"]) % 60
        start = f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
        h = int(seg["end"]) // 3600
        m = (int(seg["end"]) % 3600) // 60
        s = int(seg["end"]) % 60
        end = f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
        lines.append(f"[{seg['speaker']}] ({start} - {end})\n{seg['text']}")
    return "\n\n".join(lines)
