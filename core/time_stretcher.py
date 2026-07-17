"""Real-time, pitch-preserving time stretcher (WSOLA).

WSOLA (Waveform Similarity Overlap-Add) slows down / speeds up audio without changing
pitch. It is a time-domain method: it overlap-adds windowed analysis frames at a fixed
synthesis hop, while choosing each analysis frame's position (within a small search
window) to best match the natural waveform continuation of the previous frame -- this
waveform-similarity alignment is what avoids phase discontinuities (clicks).

Designed for the playback producer thread:
  - Pulls source audio on demand through a frozen ``source(start, frames) -> (n, ch)``
    callable (random access; ``None`` past the end).
  - Stateful, sequential synthesis; ``reset(source_pos)`` re-aligns after a seek.
  - ``set_rate(r)``: r < 1 slows down (longer), r > 1 speeds up (shorter), 1.0 = identity.

All processing stays on the calling (producer) thread; no DPG, no locks.
"""

import math
import numpy as np


class TimeStretcher:
    def __init__(self, channels: int = 2, frame: int = 2048,
                 synthesis_hop: int = 512, search: int = 256):
        self.channels = channels
        self.N = int(frame)
        self.Hs = int(synthesis_hop)
        self.search = int(search)
        # Periodic Hann window (good overlap-add behaviour at 75% overlap).
        self.win = np.hanning(self.N + 1)[:-1].astype(np.float32)

        self.rate = 1.0
        self.source = None        # callable(start, frames) -> (n, channels) | None

        self._a = 0.0             # analysis position (source samples, float)
        self._delta = 0           # current waveform-similarity offset (samples)
        self._eof = False

        # Streaming overlap-add accumulator (length N; we add a frame at offset 0,
        # emit the first Hs finalised samples, then shift left by Hs each step).
        self._acc = np.zeros((self.N, channels), dtype=np.float32)
        self._wsum = np.zeros(self.N, dtype=np.float32)

        # Finalised output waiting to be pulled.
        self._chunks: list[np.ndarray] = []
        self._buffered = 0

    # ----- Public API (producer thread only) ----- #

    def set_source(self, source):
        self.source = source

    def set_rate(self, rate: float):
        self.rate = max(0.05, float(rate))

    def reset(self, source_pos: float):
        """Re-align the analysis head after a seek / discontinuity and drop buffered output."""
        self._a = float(max(0.0, source_pos))
        self._delta = 0
        self._eof = False
        self._acc[:] = 0.0
        self._wsum[:] = 0.0
        self._chunks.clear()
        self._buffered = 0

    def pull(self, frames: int):
        """Return the next ``frames`` of stretched audio as (frames, channels) float32.

        Returns a shorter final block then ``None`` once the source is exhausted.
        """
        while self._buffered < frames and not self._eof:
            if not self._synthesis_step():
                break
        if self._buffered == 0:
            return None
        return self._take(frames)

    # ----- Internal ----- #

    def _synthesis_step(self) -> bool:
        frame = self._read_frame(self._a + self._delta)
        if frame is None:
            self._eof = True
            return False

        # Overlap-add the windowed analysis frame at the head of the accumulator.
        self._acc[:self.N] += frame * self.win[:, None]
        self._wsum[:self.N] += self.win

        # The first Hs samples can no longer be touched by future frames -> finalise them.
        out = self._acc[:self.Hs] / np.maximum(self._wsum[:self.Hs, None], 1e-6)
        self._chunks.append(np.ascontiguousarray(out, dtype=np.float32))
        self._buffered += self.Hs

        # Shift the accumulator left by Hs (keep length N).
        self._acc = np.concatenate(
            [self._acc[self.Hs:], np.zeros((self.Hs, self.channels), dtype=np.float32)])
        self._wsum = np.concatenate(
            [self._wsum[self.Hs:], np.zeros(self.Hs, dtype=np.float32)])

        # Choose the next frame's offset so it best continues the current waveform.
        natural = self._read_mono(self._a + self._delta + self.Hs, self.N)
        a_next = self._a + self.Hs * self.rate
        region = self._read_mono(a_next - self.search, self.N + 2 * self.search)
        self._delta = self._best_delta(natural, region)
        self._a = a_next
        return True

    def _best_delta(self, natural: np.ndarray, region: np.ndarray) -> int:
        # Normalised cross-correlation of `natural` against every length-N window of
        # `region` (offsets 0..2*search). Returns the best offset relative to the centre.
        if natural is None or region is None or len(region) < self.N:
            return 0
        windows = np.lib.stride_tricks.sliding_window_view(region, self.N)  # (2S+1, N)
        scores = windows @ natural
        norms = np.sqrt((windows * windows).sum(axis=1)) + 1e-6
        best = int(np.argmax(scores / norms))
        return best - self.search

    def _read_frame(self, pos: float):
        # One (N, channels) analysis frame; None only when fully past the source end.
        N = self.N
        start = int(math.floor(pos))
        if start < 0:
            out = np.zeros((N, self.channels), dtype=np.float32)
            avail = N + start  # = N - |start|
            if avail > 0:
                buf = self._src(0, avail)
                if buf is not None and len(buf):
                    m = min(len(buf), avail)
                    out[-start:-start + m] = buf[:m]
            return out
        buf = self._src(start, N)
        if buf is None or len(buf) == 0:
            return None
        out = np.zeros((N, self.channels), dtype=np.float32)
        m = min(len(buf), N)
        out[:m] = buf[:m]
        return out

    def _read_mono(self, pos: float, length: int) -> np.ndarray:
        # Mono read for correlation; zero-filled past the edges (never None).
        out = np.zeros(length, dtype=np.float32)
        start = int(math.floor(pos))
        dst, req, s = 0, length, start
        if s < 0:
            dst = -s
            if dst >= length:
                return out
            s, req = 0, length - dst
        buf = self._src(s, req)
        if buf is not None and len(buf):
            mono = buf.mean(axis=1) if buf.ndim == 2 else buf
            m = min(len(mono), req)
            out[dst:dst + m] = mono[:m]
        return out

    def _src(self, start: int, frames: int):
        if self.source is None or frames <= 0:
            return None
        return self.source(int(start), int(frames))

    def _take(self, frames: int) -> np.ndarray:
        buf = np.concatenate(self._chunks, axis=0)
        take = min(frames, len(buf))
        out = np.ascontiguousarray(buf[:take], dtype=np.float32)
        rest = buf[take:]
        self._chunks = [rest] if len(rest) else []
        self._buffered = len(rest)
        return out
