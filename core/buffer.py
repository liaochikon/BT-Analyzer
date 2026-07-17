import numpy as np


class RingBuffer:
    def __init__(self, capacity_frames: int, channels: int = 2, dtype=np.float32):
        self.capacity = int(capacity_frames)
        self.channels = channels
        self._buf = np.zeros((self.capacity, channels), dtype=dtype)
        self._write_pos = 0
        self._read_pos = 0
        # Producer-only, monotonically increasing. Marks the high-water point up to which
        # already-queued audio is stale; the consumer skips its read head forward past it.
        self._discard_boundary = 0

    @property
    def free_frames(self) -> int:
        return self.capacity - (self._write_pos - self._read_pos)

    def write(self, block: np.ndarray) -> int:
        frames = block.shape[0]
        free = self.free_frames
        n = frames if frames <= free else free
        if n <= 0:
            return 0
        start = self._write_pos % self.capacity
        if start + n <= self.capacity:
            self._buf[start:start + n] = block[:n]
        else:
            first = self.capacity - start
            self._buf[start:] = block[:first]
            self._buf[:n - first] = block[first:n]
        self._write_pos += n
        return n

    def read(self, out: np.ndarray):
        frames = out.shape[0]
        # Skip past any stale region the producer flagged (forward-only; never moves back).
        # This is the consumer's sole write to _read_pos, so single-writer is preserved and
        # write_pos >= read_pos always holds -> avail can never go negative.
        if self._read_pos < self._discard_boundary:
            self._read_pos = self._discard_boundary
        avail = self._write_pos - self._read_pos
        n = frames if frames <= avail else avail
        if n > 0:
            start = self._read_pos % self.capacity
            if start + n <= self.capacity:
                out[:n] = self._buf[start:start + n]
            else:
                first = self.capacity - start
                out[:first] = self._buf[start:]
                out[first:n] = self._buf[:n - first]
            self._read_pos += n
        if n < frames:
            out[n:] = 0.0
            return n, True
        return n, False

    def request_flush(self):
        # Producer-only: declare everything queued so far stale. We do NOT move write_pos
        # backwards (that would break the write_pos >= read_pos invariant against the
        # consumer's read_pos); instead we publish a forward-only boundary and let the
        # consumer advance its own read head past it on the next read().
        self._discard_boundary = self._write_pos
