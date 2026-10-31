"""Headless audio export processor contracts."""

import threading

import numpy as np


def test_audio_export_processor_renders_locked_blocks_and_writes_wav():
    from nodes.audio_export_node.processor import AudioExportProcessor, AudioExportStatus

    lock = threading.Lock()
    render_calls = []
    writes = []
    progress = []

    def render(start, block_size):
        assert lock.locked()
        render_calls.append((start, block_size))
        remaining = 5 - start
        return np.ones((min(block_size, remaining), 2), dtype=np.float32)

    def write(path, data, sample_rate, subtype):
        writes.append((path, data.copy(), sample_rate, subtype))

    result = AudioExportProcessor().export(
        render,
        5,
        "mix.wav",
        render_lock=lock,
        sample_rate=44100,
        block_size=2,
        progress=progress.append,
        write=write,
    )

    assert result.status is AudioExportStatus.EXPORTED
    assert result.frames == 5
    assert render_calls == [(0, 2), (2, 2), (4, 2)]
    assert writes[0][0] == "mix.wav"
    assert writes[0][1].shape == (5, 2)
    assert writes[0][2:] == (44100, "PCM_16")
    assert progress == [0.0, 0.4, 0.8, 1.0, 1.0]


def test_audio_export_processor_reports_empty_and_write_failure():
    from nodes.audio_export_node.processor import AudioExportProcessor, AudioExportStatus

    processor = AudioExportProcessor()
    lock = threading.Lock()

    no_input = processor.export(
        None, 0, "mix.wav", render_lock=lock,
        sample_rate=44100, block_size=2)
    empty = processor.export(
        lambda *_args: np.empty((0, 2), dtype=np.float32),
        4,
        "mix.wav",
        render_lock=lock,
        sample_rate=44100,
        block_size=2,
    )
    failed = processor.export(
        lambda *_args: np.ones((2, 2), dtype=np.float32),
        2,
        "mix.wav",
        render_lock=lock,
        sample_rate=44100,
        block_size=2,
        write=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    assert no_input.status is AudioExportStatus.NO_INPUT
    assert empty.status is AudioExportStatus.EMPTY
    assert failed.status is AudioExportStatus.WRITE_FAILED
    assert failed.error == "disk full"