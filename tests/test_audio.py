from types import SimpleNamespace

import numpy as np
import pytest

from meeting_notes import diarization
from meeting_notes.text import merge_consecutive_segments, remove_excessive_duplicates
from meeting_notes.transcription import transcribe_audio


@pytest.mark.parametrize("count", [1, 2, 3, 4, 8])
def test_speaker_estimation_never_scores_one_cluster_per_sample(count):
    features = np.random.default_rng(42).normal(size=(count, 12))
    result = diarization.estimate_speaker_count(features)
    assert 1 <= result <= count


def test_identical_features_fall_back_to_one_speaker():
    assert diarization.estimate_speaker_count(np.zeros((4, 12))) == 1


def test_explicit_speaker_count_is_clamped(monkeypatch):
    monkeypatch.setattr(diarization, "DIARIZATION_NUM_SPEAKERS", 8)
    assert diarization.estimate_speaker_count(np.ones((2, 12))) == 2


def test_zero_feature_rows_do_not_crash_cosine_clustering(tmp_path, monkeypatch):
    monkeypatch.setattr(diarization, "DIARIZATION_NUM_SPEAKERS", 2)
    monkeypatch.setattr(diarization.librosa, "load", lambda *a, **k: (np.zeros(48000), 16000))
    features = iter([np.array([0.0, 0.0]), np.array([1.0, 0.0]), np.array([2.0, 0.0])])
    monkeypatch.setattr(diarization, "extract_speaker_feature", lambda *a: next(features))
    segments = [{"start": i, "end": i + 0.9, "text": str(i)} for i in range(3)]
    assert diarization.diarize_with_mfcc(tmp_path / "fixture.wav", segments)


def test_real_feature_extraction_on_synthetic_audio(tmp_path, audio_bytes):
    path = tmp_path / "tone.wav"
    path.write_bytes(audio_bytes)
    result = diarization.diarize_with_mfcc(path, [{"start": 0, "end": 1, "text": "합성 입력"}])
    assert result == [{"speaker": "Speaker 1", "start": 0, "end": 1, "text": "합성 입력"}]


def test_duplicate_filter_preserves_at_most_two_consecutive_copies():
    segments = [{"start": i, "end": i + 1, "text": "같은 발화"} for i in range(4)]
    assert len(remove_excessive_duplicates(segments)) == 2


def test_merging_preserves_separate_speakers_and_large_gaps():
    segments = [
        {"speaker": "Speaker 1", "start": 0, "end": 1, "text": "첫 말"},
        {"speaker": "Speaker 1", "start": 1.2, "end": 2, "text": "다음 말"},
        {"speaker": "Speaker 2", "start": 2, "end": 3, "text": "다른 사람"},
        {"speaker": "Speaker 2", "start": 8, "end": 9, "text": "긴 간격"},
    ]
    assert len(merge_consecutive_segments(segments)) == 3


def test_zero_log_probability_is_not_replaced_with_negative_default(tmp_path):
    class Model:
        def transcribe(self, path):
            seg = SimpleNamespace(
                start=0,
                end=1,
                text="확인 확인 확인 확인 확인 확인 확인",
                avg_logprob=0.0,
                no_speech_prob=0.0,
                compression_ratio=1.0,
            )
            return iter([seg, seg, seg]), SimpleNamespace(duration=1)

    result, _ = transcribe_audio(Model(), tmp_path / "fixture.wav")
    assert len(result) == 2
