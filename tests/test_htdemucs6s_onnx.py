"""Host-side contract for the fixed-segment htdemucs_6s ONNX graph."""

import numpy as np
import pytest

from htdemucs6s_onnx.contract import SEGMENT_SAMPLES, STEMS, contract_document
from htdemucs6s_onnx.separate import (
    center_trim,
    load_wav,
    pad_segment,
    prepare_mix,
    separate,
    triangular_weight,
)


def _identity_segment(mix):
    stems = np.broadcast_to(mix[:, None, :, :], (1, len(STEMS), 2, mix.shape[-1]))
    return np.array(stems, dtype=np.float32, copy=True)


def test_contract_matches_the_static_htdemucs_6s_graph():
    document = contract_document()

    assert document["segment_samples"] == 343980
    assert document["segment_samples"] == SEGMENT_SAMPLES
    assert document["input"]["shape"] == [1, 2, 343980]
    assert document["output"]["shape"] == [1, 6, 2, 343980]
    assert document["stems"] == ["drums", "bass", "other", "vocals", "guitar", "piano"]


def test_prepare_mix_makes_stereo_and_drops_extra_channels():
    mono = prepare_mix(np.ones(4, dtype=np.float32))
    multi = prepare_mix(np.arange(12, dtype=np.float32).reshape(3, 4))

    assert mono.shape == (2, 4)
    assert np.array_equal(mono[0], mono[1])
    assert multi.shape == (2, 4)
    assert np.array_equal(multi, np.arange(8, dtype=np.float32).reshape(2, 4))


def test_pad_segment_borrows_context_and_center_trim_recovers_the_chunk():
    mix = np.arange(20, dtype=np.float32).reshape(2, 10)
    padded, chunk_length = pad_segment(mix, offset=6, segment_samples=8)
    recovered = center_trim(padded, chunk_length)

    assert chunk_length == 4
    assert padded.shape == (2, 8)
    assert np.array_equal(recovered, mix[:, 6:10])


def test_short_mix_is_center_padded_inside_the_static_segment():
    mix = np.arange(10, dtype=np.float32).reshape(2, 5)
    padded, chunk_length = pad_segment(mix, offset=0, segment_samples=8)

    assert chunk_length == 5
    assert np.array_equal(center_trim(padded, chunk_length), mix)


def test_identity_segment_survives_overlap_add_and_file_normalization():
    samples = np.linspace(-0.4, 0.8, 100, dtype=np.float32)
    mix = np.stack((samples, samples * 0.5))

    stems = separate(mix, _identity_segment, overlap=0.25, segment_samples=32)

    assert list(stems) == list(STEMS)
    for stem in stems.values():
        assert stem.shape == mix.shape
        assert stem.dtype == np.float32
        np.testing.assert_allclose(stem, mix, atol=1e-5)


def test_triangular_weight_peaks_in_the_middle_and_covers_the_segment():
    weight = triangular_weight(8)

    assert weight.shape == (8,)
    assert weight[0] < weight[3]
    assert weight[-1] < weight[4]
    assert weight.max() == pytest.approx(1)


def test_load_wav_resamples_48k_audio_to_the_model_rate(tmp_path):
    pytest.importorskip("soundfile")
    pytest.importorskip("soxr")
    import soundfile as sf

    source_rate = 48000
    frames = source_rate // 2
    tone = np.sin(2 * np.pi * 440 * np.arange(frames) / source_rate).astype(np.float32)
    stereo = np.stack((tone, tone * 0.5), axis=1)
    path = tmp_path / "tone.wav"
    sf.write(path, stereo, source_rate)

    mix = load_wav(path)

    assert mix.shape[0] == 2
    assert mix.shape[1] == pytest.approx(round(frames * 44100 / source_rate), abs=2)


def test_separate_rejects_an_empty_mix_and_a_bad_overlap():
    with pytest.raises(ValueError):
        separate(np.zeros(0, dtype=np.float32), _identity_segment, segment_samples=8)
    with pytest.raises(ValueError):
        separate(np.ones(8, dtype=np.float32), _identity_segment, overlap=1, segment_samples=8)
