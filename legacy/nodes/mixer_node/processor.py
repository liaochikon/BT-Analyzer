"""Headless multi-channel mixer processor."""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class MixerChannelParameters:
    gain: float
    muted: bool
    soloed: bool


@dataclass(frozen=True, slots=True)
class MixerParameters:
    channels: tuple[MixerChannelParameters, ...]


class MixerProcessor:
    """Render connected channel sources through an immutable channel snapshot."""

    @staticmethod
    def db_to_linear(db: float, *, minimum_db: float) -> float:
        if db <= minimum_db:
            return 0.0
        return float(10.0 ** (db / 20.0))

    @staticmethod
    def linear_to_db(gain: float, *, minimum_db: float) -> float:
        if gain <= 0.0:
            return minimum_db
        return max(minimum_db, 20.0 * np.log10(gain))

    @staticmethod
    def render(sources, start: int, frames: int, parameters: MixerParameters):
        output = np.zeros((frames, 2), dtype=np.float32)
        maximum_length = 0
        active = False
        any_solo = any(channel.soloed for channel in parameters.channels)

        for index, source in sources:
            if index >= len(parameters.channels):
                continue
            channel = parameters.channels[index]
            if channel.muted or (any_solo and not channel.soloed):
                continue
            block = source(start, frames)
            if block is None or len(block) == 0:
                continue
            active = True
            length = min(len(block), frames)
            output[:length] += block[:length] * np.float32(channel.gain)
            maximum_length = max(maximum_length, length)

        if not active:
            return None
        output = output[:maximum_length]
        np.clip(output, -1.0, 1.0, out=output)
        return output