"""Headless rolling-window FFT processor shared by the EQ and Spectrum nodes.

Both feed short producer-thread render blocks into a large fixed rolling buffer (so the low end
keeps fine frequency resolution -- 44100/8192 ~= 5.4 Hz/bin), then once per GUI frame compute a
Hann-windowed magnitude spectrum and resample it onto a log-frequency grid for an even visual
weight per octave on a log-scale x-axis.

Threading: ``push`` runs on the producer thread and ``snapshot`` runs on the GUI thread. A short
lock protects only the rolling-buffer handoff; FFT work happens after copying and outside the lock.
"""

from dataclasses import dataclass
from threading import Lock

import numpy as np


@dataclass(frozen=True, slots=True)
class SpectrumSnapshot:
    frequencies: tuple[float, ...]
    levels_db: tuple[float, ...]


class RollingSpectrum:
    def __init__(self, sample_rate, fft_size: int = 8192, db_floor: float = -90.0):
        self.sample_rate = sample_rate
        self.fft_size = fft_size
        self.db_floor = db_floor
        self._buf = np.zeros(fft_size, dtype=np.float64)
        self._window = np.hanning(fft_size)
        self._lock = Lock()
        self.has_audio = False

    def push(self, block):
        """Roll a producer-thread audio block (mono or (n, ch)) into the buffer, newest at the end."""
        if block is None or len(block) == 0:
            return
        mono = block.mean(axis=1) if getattr(block, "ndim", 1) == 2 else block
        mono = np.asarray(mono, dtype=np.float64)
        k = len(mono)
        with self._lock:
            if k >= self.fft_size:
                self._buf[:] = mono[-self.fft_size:]
            elif k > 0:
                self._buf[:-k] = self._buf[k:]
                self._buf[-k:] = mono
            self.has_audio = True

    def snapshot(self, n_points: int = 200, f_min: float = 20.0,
                 f_max: float = 20000.0) -> SpectrumSnapshot | None:
        """Return an immutable log-frequency snapshot, or None before the first audio block.

        Levels are clipped to ``db_floor``..0. ``f_max`` is capped just below Nyquist.
        """
        with self._lock:
            if not self.has_audio:
                return None
            buffer = self._buf.copy()
        mag = np.abs(np.fft.rfft(buffer * self._window)) / (self.fft_size * 0.5)
        db = 20.0 * np.log10(mag + 1e-9)
        freqs = np.fft.rfftfreq(self.fft_size, 1.0 / self.sample_rate)
        fmax = min(f_max, self.sample_rate * 0.5 * 0.999)
        log_freqs = np.logspace(np.log10(f_min), np.log10(fmax), n_points)
        log_db = np.interp(log_freqs, freqs, db)
        levels_db = np.clip(log_db, self.db_floor, 0.0)
        return SpectrumSnapshot(
            frequencies=tuple(float(value) for value in log_freqs),
            levels_db=tuple(float(value) for value in levels_db),
        )
