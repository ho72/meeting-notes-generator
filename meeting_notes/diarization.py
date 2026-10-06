from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

import librosa
import numpy as np
from sklearn.cluster import AgglomerativeClustering, KMeans
from sklearn.exceptions import ConvergenceWarning
from sklearn.metrics import silhouette_score

from .settings import DIARIZATION_MAX_SPEAKERS, DIARIZATION_MIN_SPEAKERS, DIARIZATION_NUM_SPEAKERS
from .text import merge_consecutive_segments


def estimate_speaker_count(features: np.ndarray) -> int:
    sample_count = len(features)
    if sample_count <= 1:
        return 1

    if DIARIZATION_NUM_SPEAKERS > 0:
        return max(1, min(DIARIZATION_NUM_SPEAKERS, sample_count))

    min_k = max(1, DIARIZATION_MIN_SPEAKERS)
    max_k = max(min_k, DIARIZATION_MAX_SPEAKERS)
    max_k = min(max_k, sample_count - 1)
    if sample_count < 3:
        return min(max_k, max(1, min_k))

    start_k = max(2, min_k)
    candidates: list[tuple[int, float]] = []
    for k in range(start_k, max_k + 1):
        kmeans = KMeans(n_clusters=k, random_state=42, n_init=10)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ConvergenceWarning)
            labels = kmeans.fit_predict(features)
        if not 2 <= len(set(labels)) < sample_count:
            continue
        score = silhouette_score(features, labels)
        candidates.append((k, score))

    if not candidates:
        return 1

    best_score = max(score for _, score in candidates)
    near_best = [k for k, score in candidates if (best_score - score) <= 0.025]
    best_k = max(near_best) if near_best else max(candidates, key=lambda x: x[1])[0]
    if best_score < 0.03 and min_k <= 1:
        return 1
    return max(min_k, best_k)


def extract_speaker_feature(clip: np.ndarray, sr: int) -> np.ndarray:
    mfcc = librosa.feature.mfcc(y=clip, sr=sr, n_mfcc=20)
    delta = librosa.feature.delta(mfcc)
    delta2 = librosa.feature.delta(mfcc, order=2)
    chroma = librosa.feature.chroma_stft(y=clip, sr=sr)
    contrast = librosa.feature.spectral_contrast(y=clip, sr=sr)
    centroid = librosa.feature.spectral_centroid(y=clip, sr=sr)
    rms = librosa.feature.rms(y=clip)
    zcr = librosa.feature.zero_crossing_rate(y=clip)

    f0 = librosa.yin(clip, fmin=librosa.note_to_hz("C2"), fmax=librosa.note_to_hz("C7"), sr=sr)
    voiced = f0[f0 > 0]
    pitch_features = np.array([voiced.mean(), voiced.std()]) if len(voiced) > 0 else np.zeros(2)

    return np.concatenate(
        [
            mfcc.mean(axis=1),
            mfcc.std(axis=1),
            delta.mean(axis=1),
            delta.std(axis=1),
            delta2.mean(axis=1),
            delta2.std(axis=1),
            chroma.mean(axis=1),
            contrast.mean(axis=1),
            centroid.mean(axis=1),
            centroid.std(axis=1),
            rms.mean(axis=1),
            rms.std(axis=1),
            zcr.mean(axis=1),
            zcr.std(axis=1),
            pitch_features,
        ]
    )


def smooth_speaker_labels(
    labels: np.ndarray, segments: list[dict[str, Any]], min_duration: float = 1.5
) -> np.ndarray:
    """짧은 고립 발화의 화자 레이블을 양쪽 이웃과 일치하면 수정한다."""
    smoothed = labels.copy()
    n = len(smoothed)
    for i in range(1, n - 1):
        duration = segments[i]["end"] - segments[i]["start"]
        if (
            duration < min_duration
            and smoothed[i] != smoothed[i - 1]
            and smoothed[i - 1] == smoothed[i + 1]
        ):
            smoothed[i] = smoothed[i - 1]
    return smoothed


def diarize_with_mfcc(audio_path: Path, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not segments:
        return []
    audio, sr = librosa.load(str(audio_path), sr=16000, mono=True)
    features: list[np.ndarray] = []
    valid_indices: list[int] = []
    labels = np.zeros(len(segments), dtype=int)

    for index, seg in enumerate(segments):
        start_sample = max(0, int(seg["start"] * sr))
        end_sample = min(len(audio), int(seg["end"] * sr))
        clip = audio[start_sample:end_sample]
        if len(clip) < int(0.7 * sr):
            continue
        try:
            feature = extract_speaker_feature(clip, sr)
            if not np.isfinite(feature).all():
                continue
            features.append(feature)
            valid_indices.append(index)
        except Exception:
            continue

    if features:
        matrix = np.vstack(features).astype(np.float32)
        matrix = (matrix - matrix.mean(axis=0, keepdims=True)) / (
            matrix.std(axis=0, keepdims=True) + 1e-6
        )
        speaker_count = estimate_speaker_count(matrix)
        if speaker_count <= 1:
            valid_labels = np.zeros(len(matrix), dtype=int)
        else:
            cluster = AgglomerativeClustering(
                n_clusters=speaker_count,
                metric="euclidean" if np.any(np.linalg.norm(matrix, axis=1) < 1e-8) else "cosine",
                linkage="average",
            )
            valid_labels = cluster.fit_predict(matrix)
        for idx, seg_idx in enumerate(valid_indices):
            labels[seg_idx] = int(valid_labels[idx])
        labels = smooth_speaker_labels(labels, segments)
        valid_set = set(valid_indices)
        for idx in range(len(segments)):
            if idx in valid_set:
                continue
            labels[idx] = labels[idx - 1] if idx > 0 else 0

    diarized: list[dict[str, Any]] = []
    for idx, seg in enumerate(segments):
        diarized.append(
            {
                "speaker": f"Speaker {labels[idx] + 1}",
                "start": seg["start"],
                "end": seg["end"],
                "text": seg["text"],
            }
        )
    return merge_consecutive_segments(diarized)
