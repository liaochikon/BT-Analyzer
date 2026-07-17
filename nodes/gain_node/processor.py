"""Headless gain signal processor."""

import numpy as np


class GainProcessor:
    """Apply a live dB gain or bypass one audio block."""

    def process_block(self, block, *, gain_db: float, bypass: bool):
        if bypass:
            return block
        return block * np.float32(10.0 ** (gain_db / 20.0))
