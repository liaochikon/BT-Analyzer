"""Headless Audio Input processor contracts."""

import numpy as np
import pytest


def test_audio_input_processor_decodes_mono_to_channel_first():
    from nodes.audio_input_node.processor import AudioInputProcessor

    observed = {}
    progress = []

    def read(path, *, dtype, always_2d):
        observed.update(path=path, dtype=dtype, always_2d=always_2d)
        return np.arange(4, dtype=np.float32)[:, None], 44100

    data = AudioInputProcessor().decode(
        "song.mp3",
        sample_rate=44100,
        read=read,
        progress=progress.append,
    )

    assert observed == {"path": "song.mp3", "dtype": "float32", "always_2d": True}
    assert data.shape == (1, 4)
    assert data.dtype == np.float32
    assert progress == [0.2, 0.4]


def test_audio_input_processor_preserves_multichannel_and_decode_errors():
    from nodes.audio_input_node.processor import AudioInputProcessor

    processor = AudioInputProcessor()
    stereo = np.ones((2, 8), dtype=np.float32)
    data = processor.decode(
        "song.wav",
        sample_rate=44100,
        read=lambda *_args, **_kwargs: (stereo.T, 44100),
    )

    np.testing.assert_array_equal(data, stereo)
    assert data.dtype == np.float32
    assert data.flags.c_contiguous
    with pytest.raises(RuntimeError, match="decode failed"):
        processor.decode(
            "broken.wav",
            sample_rate=44100,
            read=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("decode failed")),
        )


def test_audio_input_processor_resamples_frames_first_multichannel():
    from nodes.audio_input_node.processor import AudioInputProcessor

    observed = {}

    def resample(frames, source_rate, target_rate, *, quality):
        observed.update(
            shape=frames.shape,
            source_rate=source_rate,
            target_rate=target_rate,
            quality=quality,
        )
        return frames[::2]

    data = AudioInputProcessor().decode(
        "song.wav",
        sample_rate=24000,
        read=lambda *_args, **_kwargs: (np.ones((8, 2), dtype=np.float32), 48000),
        resample=resample,
    )

    assert observed == {
        "shape": (8, 2),
        "source_rate": 48000,
        "target_rate": 24000,
        "quality": "HQ",
    }
    assert data.shape == (2, 4)