"""Headless Mixer processor contracts."""

from dataclasses import FrozenInstanceError

import numpy as np
import pytest


def _parameters(*channels):
    from nodes.mixer_node.processor import MixerChannelParameters, MixerParameters

    return MixerParameters(tuple(MixerChannelParameters(*channel) for channel in channels))


def test_mixer_processor_respects_solo_gain_partial_length_and_clipping():
    from nodes.mixer_node.processor import MixerProcessor

    sources = [
        (0, lambda _start, frames: np.full((frames, 2), 0.75, dtype=np.float32)),
        (1, lambda _start, _frames: np.full((4, 2), 0.75, dtype=np.float32)),
    ]
    output = MixerProcessor.render(
        sources,
        0,
        8,
        _parameters((2.0, False, True), (1.0, False, False)),
    )

    assert output.shape == (8, 2)
    assert np.all(output == 1.0)


def test_mixer_processor_returns_none_without_active_sources_and_params_are_immutable():
    from nodes.mixer_node.processor import MixerProcessor

    parameters = _parameters((1.0, True, False))
    output = MixerProcessor.render(
        [(0, lambda *_args: np.ones((4, 2), dtype=np.float32))],
        0,
        4,
        parameters,
    )

    assert output is None
    with pytest.raises(FrozenInstanceError):
        parameters.channels = ()


def test_mixer_processor_db_conversion_preserves_true_mute_floor():
    from nodes.mixer_node.processor import MixerProcessor

    assert MixerProcessor.db_to_linear(-60.0, minimum_db=-60.0) == 0.0
    gain = MixerProcessor.db_to_linear(-6.0, minimum_db=-60.0)
    assert MixerProcessor.linear_to_db(gain, minimum_db=-60.0) == pytest.approx(-6.0)