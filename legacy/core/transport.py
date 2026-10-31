"""Central playback transport (the "conductor").

The Transport is a tiny shared state object that represents the playhead on a
single, global sample-based timeline. It does NOT play audio and it does NOT
own any DearPyGui widgets. It only holds:

  - where the playhead is        (current_index, in samples)
  - how long the timeline is      (length, in samples)
  - whether we are playing         (state)
  - a pending seek request         (set by anyone, executed by the player)

Ownership rules (important):
  - The ENGINE PRODUCER THREAD is the single writer of _index / _state / _length.
    Every state change is expressed as a command posted to a thread-safe queue; the
    producer drains and applies them in order (see _apply_* below). The audio callback
    only reports how many frames it played via note_consumed().
  - Everyone else (UI buttons, monitor node, audio callback) is a POSTER or a READER.
    They enqueue commands (play/pause/stop/seek/length/consumed) and read current_index
    every frame to draw the cursor. They never mutate the playhead directly.

Because everything is aligned to one sample rate and starts at index 0, a single
integer sample index is a universal coordinate shared by every node.

Thread safety: a queue.SimpleQueue carries commands from any thread to the single
owner, so _index / _state / _length each have exactly one writer. Reads of these
scalars from other threads are atomic under CPython's GIL, so no lock is needed.
"""

from dataclasses import dataclass
import queue
from enum import Enum, auto

from core.bus import BusMessageResult, Message
from core.constants import GLOBAL_SAMPLE_RATE
from core.transport_contracts import (
    CancelCountIn,
    ClearLoop,
    Pause,
    Play,
    Resync,
    Seek,
    SetLength,
    SetBeatTrim,
    SetLoopEnabled,
    SetLoopPoint,
    SetLoopRange,
    SetSnapEnabled,
    SetRate,
    Stop,
    TogglePlayback,
    TransportEndpoint,
    TransportTopic,
)

# Shipped beat-grid default (used when no Beat Grid node drives the tempo, and restored by
# reset_beat_grid when one is removed). One source of truth for both.
_DEFAULT_BPM = 120.0
_DEFAULT_OFFSET = 0.0


class PlaybackState(Enum):
    STOPPED = auto()
    PLAYING = auto()
    PAUSED = auto()


@dataclass(frozen=True, slots=True)
class TransportSnapshot:
    sample_rate: int
    current_index: int
    length: int
    state: PlaybackState
    rate: float
    position_seconds: float
    length_seconds: float
    source_position_seconds: float
    source_length_seconds: float
    progress: float
    loop_a: float | None
    loop_b: float | None
    loop_enabled: bool
    loop_active: bool
    bpm: float
    beat_offset: float
    bpm_trim: float
    offset_trim: float
    beat_grid_from_node: bool
    snap_enabled: bool
    waveform: object
    waveform_version: int


class Transport:
    def __init__(self, sample_rate: int = GLOBAL_SAMPLE_RATE):
        self.sample_rate = sample_rate

        self._index = 0            # current playhead position, in OUTPUT samples
        self._length = 0           # timeline length, in OUTPUT samples (0 = nothing loaded)
        self._state = PlaybackState.STOPPED

        # Playback speed. The timeline above is measured in OUTPUT samples (what the audio
        # callback actually plays). The underlying source has _base_length source samples;
        # at playback_rate r the output length is round(_base_length / r) and a source
        # position maps to output_index = source_pos / r  (i.e. source_pos = index * r).
        self._base_length = 0      # source length, in samples (rate-independent)
        self._rate = 1.0           # playback speed (r<1 slower, r>1 faster, 1.0 = normal)
        # Pre-computed source-timeline position (seconds), refreshed by the producer on every
        # mutation. Exposed as ONE atomic float (see source_position_seconds) so a GUI reader
        # never observes a torn (_index, _rate) pair captured mid-update.
        self._source_seconds = 0.0

        # A/B loop. Points are stored as NORMALISED fractions in [0, 1] (not samples) because a
        # fraction is rate-invariant -- (src/r)/(base/r) == src/base -- so a loop keeps marking
        # the same music at any playback speed, and the UI maps it straight to an x pixel. None
        # means "unset". _loop_enabled only takes effect once both A and B exist and A < B.
        self._loop_a = None
        self._loop_b = None
        self._loop_enabled = False

        # Beat grid (BPM + phase offset in seconds) used to draw the beat grid and time the
        # count-in / metronome. Split into a BASE and a TRIM:
        #   base  -- the source of truth, set by a Beat Grid sink node (or the shipped default).
        #   trim  -- a manual relative nudge from the transport bar, added on top of the base.
        # The effective value read everywhere (bpm / beat_offset properties) is base + trim, so a
        # node updating the base keeps the user's trim, and the trim survives node changes. Ships
        # with a sensible default so a grid appears immediately (no blank/startled state). Plain
        # scalars: base written by the pipeline thread (node publish), trim by the GUI, both read
        # by the GUI -- GIL-atomic, so no command queue is needed here.
        self._bpm_base = _DEFAULT_BPM
        self._offset_base = _DEFAULT_OFFSET
        self._bpm_trim = 0.0
        self._offset_trim = 0.0
        self._beat_grid_from_node = False   # True once a node has pushed a value this session

        # Snap-to-beat-grid toggle. When on, explicit seeks and A/B loop points are pulled to the
        # nearest beat. Only affects deliberate positioning (_apply_seek / _apply_loop_*); normal
        # playback advance, rate changes and loop-wrap write _index directly and are unaffected.
        self._snap_enabled = False

        # Waveform overview: a downsampled min/max envelope of the whole track on the SOURCE
        # timeline (so it aligns with the beat grid and never moves when the playback rate
        # changes). Computed on a worker thread when Audio Output sets a source and published
        # here as ONE tuple reference (min_arr, max_arr); the GUI reads it to draw the scrubber
        # waveform. Reference swap is atomic under the GIL, so no lock is needed. None = no
        # waveform yet (nothing loaded / still computing).
        self._waveform = None
        # Monotonic version, bumped on every set_waveform. The GUI redraws when it changes -- more
        # reliable than comparing id(_waveform), whose address can be reused after a tuple is freed
        # (id collision -> missed redraw).
        self._waveform_version = 0
        # Re-scan source: the (render, length) chain Audio Output published. The render closure
        # reads LIVE node params, so re-running it reflects the current gain/EQ/mix -- the GUI
        # polls _waveform_dirty and, when set, recomputes the envelope on a worker thread from
        # this. _waveform_dirty is flipped O(1) by any live-param change (cheap on a slider drag).
        self._waveform_source = None   # (render_callable, length) or None
        self._waveform_dirty = False

        # Commands from any thread -> applied in order by the engine producer thread.
        self._commands = queue.SimpleQueue()
        self._control_bus = None
        self._snapshot = self._make_snapshot()

    # ----- Read-only views (used by UI / monitor nodes every frame) ----- #

    @property
    def current_index(self) -> int:
        return self._index

    @property
    def length(self) -> int:
        return self._length

    @property
    def state(self) -> PlaybackState:
        return self._state

    def is_loaded(self) -> bool:
        return self._length > 0

    @property
    def rate(self) -> float:
        return self._rate

    @property
    def position_seconds(self) -> float:
        return self._index / self.sample_rate

    @property
    def source_position_seconds(self) -> float:
        # Position in the ORIGINAL (source) timeline, in seconds. Unlike position_seconds
        # (which follows the stretched OUTPUT timeline), this is what is actually being heard
        # right now, so playhead-following visualizers that align to source-time MIDI / audio
        # (e.g. the fretboard) must use this so they stay in sync at any playback rate. Returns
        # a single pre-computed float (refreshed by the producer) so the read is tear-free.
        return self._source_seconds

    @property
    def length_seconds(self) -> float:
        return self._length / self.sample_rate

    @property
    def source_length_seconds(self) -> float:
        # Length on the ORIGINAL (source) timeline, rate-independent. length_seconds follows the
        # stretched OUTPUT timeline; anything positioned in source time (the beat grid, which is
        # bpm + offset in source seconds) must divide by THIS, not length_seconds, or it drifts
        # when the playback rate != 1.0.
        return self._base_length / self.sample_rate

    @property
    def progress(self) -> float:
        # Normalised playhead in [0.0, 1.0]; handy for binding to a progress bar.
        if self._length <= 0:
            return 0.0
        return min(1.0, self._index / self._length)

    def snapshot(self) -> TransportSnapshot:
        return self._snapshot

    # ----- A/B loop read-only views (GUI reads every frame to draw markers) ----- #

    @property
    def loop_a(self):
        return self._loop_a

    @property
    def loop_b(self):
        return self._loop_b

    @property
    def loop_enabled(self) -> bool:
        return self._loop_enabled

    def loop_is_active(self) -> bool:
        # A loop only actually wraps playback when it is enabled AND both ends exist in order.
        return (self._loop_enabled and self._loop_a is not None
                and self._loop_b is not None and self._loop_a < self._loop_b)

    def _loop_a_index(self) -> int:
        # Output-timeline index of the loop's A point (fraction -> samples). Same conversion the
        # wrap uses, so "home to A" (stop / play-from-outside) lands exactly where a wrap does.
        return self._clamp(int(self._loop_a * self._length))

    def _loop_b_index(self) -> int:
        return self._clamp(int(self._loop_b * self._length))

    # ----- Beat grid (BPM + offset) -----
    # Direct scalar accessors (not command-queued): written by a Beat Grid node's publish on the
    # pipeline thread, read by the GUI each frame. Scalar float read/write is atomic under the
    # GIL, and a torn read only mis-places one frame of grid lines, self-correcting next frame.

    @property
    def bpm(self) -> float:
        # Effective tempo = node/default base + manual trim. Clamped > 0 so a big negative trim
        # can't produce a zero/negative interval that would break the grid / metronome.
        return max(1.0, self._bpm_base + self._bpm_trim)

    @property
    def beat_offset(self) -> float:
        # Effective phase = base + trim, clamped >= 0.
        return max(0.0, self._offset_base + self._offset_trim)

    @property
    def bpm_base(self) -> float:
        return self._bpm_base

    @property
    def offset_base(self) -> float:
        return self._offset_base

    @property
    def bpm_trim(self) -> float:
        return self._bpm_trim

    @property
    def offset_trim(self) -> float:
        return self._offset_trim

    @property
    def beat_grid_from_node(self) -> bool:
        return self._beat_grid_from_node

    def set_beat_grid(self, bpm: float, offset: float):
        """A Beat Grid node pushes detected/wired tempo + phase here (the BASE; pipeline thread)."""
        if bpm and bpm > 0:
            self._bpm_base = float(bpm)
        self._offset_base = max(0.0, float(offset))
        self._beat_grid_from_node = True
        self.publish_snapshot()

    def set_beat_trim(self, bpm_trim: float, offset_trim: float):
        """Manual relative nudge from the transport bar, added on top of the base (GUI thread)."""
        if self._control_bus is None:
            self._apply_beat_trim(bpm_trim, offset_trim)
            self.publish_snapshot()
            return BusMessageResult.ENQUEUED
        return self.submit(SetBeatTrim(float(bpm_trim), float(offset_trim)))

    def reset_beat_grid(self):
        """Fall back to the shipped default base (e.g. the Beat Grid node was removed). The user's
        trim is intentionally kept -- it is a separate, manual correction."""
        self._bpm_base = _DEFAULT_BPM
        self._offset_base = _DEFAULT_OFFSET
        self._beat_grid_from_node = False
        self.publish_snapshot()

    @property
    def waveform(self):
        """The current (min_arr, max_arr) envelope tuple, or None. Read by the GUI each frame."""
        return self._waveform

    @property
    def waveform_version(self) -> int:
        """Bumped on every set_waveform. The GUI redraws the waveform when this changes."""
        return self._waveform_version

    def set_waveform(self, envelope):
        """Publish a freshly computed (min_arr, max_arr) envelope (worker thread) or None to
        clear it. A single reference assignment -- atomic under the GIL, no lock needed."""
        self._waveform = envelope
        self._waveform_version += 1
        self.publish_snapshot()

    def set_waveform_source(self, render, length):
        """Record the render chain to re-scan for the waveform (Audio Output, compile thread).
        Setting a source marks the waveform dirty so the first envelope is computed. None clears
        both the source and any pending recompute."""
        if render is None or length <= 0:
            self._waveform_source = None
            self._waveform_dirty = False
        else:
            self._waveform_source = (render, int(length))
            self._waveform_dirty = True

    def mark_waveform_dirty(self):
        """Flag that a live param changed so the waveform should be recomputed. O(1) -- safe to
        call every frame during a slider drag; the GUI coalesces these via polling."""
        if self._waveform_source is not None:
            self._waveform_dirty = True

    def take_waveform_recompute(self):
        """GUI poll: if a recompute is pending, clear the flag and return (render, length) to
        scan; else None. Clearing here means a param changed DURING the scan re-sets the flag
        (see the worker's re-check), so the envelope converges to the latest params."""
        if self._waveform_dirty and self._waveform_source is not None:
            self._waveform_dirty = False
            return self._waveform_source
        return None

    def waveform_source_is(self, render) -> bool:
        """True if ``render`` is still the current waveform source chain. The worker checks this
        before publishing, so a scan that finished after the source was swapped/cleared (e.g. a
        disconnect set it to None) does not overwrite the fresh state with a stale envelope."""
        src = self._waveform_source   # single ref read, atomic
        return src is not None and src[0] is render

    @property
    def snap_enabled(self) -> bool:
        return self._snap_enabled

    def set_snap_enabled(self, enabled: bool):
        if self._control_bus is None:
            self._apply_snap_enabled(enabled)
            self.publish_snapshot()
            return BusMessageResult.ENQUEUED
        return self.submit(SetSnapEnabled(bool(enabled)))

    def _snap_src_seconds(self, src_t: float) -> float:
        """Pull a SOURCE-timeline position (seconds) to the nearest beat, if snapping is on.

        Beats are ``offset + n*(60/bpm)`` in source seconds (same grid the UI draws / the
        metronome ticks). Returns ``src_t`` unchanged when snapping is off or the tempo is
        unusable, and never returns a negative time.
        """
        if not self._snap_enabled:
            return src_t
        bpm = self.bpm
        if bpm <= 0:
            return src_t
        interval = 60.0 / bpm
        offset = self.beat_offset
        n = round((src_t - offset) / interval)
        return max(0.0, offset + n * interval)

    # ----- Command posting (callable from ANY thread) ----- #
    # These never mutate state directly; they enqueue a command that the engine producer
    # thread (the single owner of the playhead) applies in order.

    def bind_control_bus(self, bus) -> None:
        self._control_bus = bus

    def submit(self, command) -> BusMessageResult:
        if self._control_bus is None:
            self._commands.put(command)
            return BusMessageResult.ENQUEUED
        return self._control_bus.publish(Message(
            source="domain.transport.api",
            destination=TransportEndpoint.SERVICE,
            topic=TransportTopic.COMMAND,
            payload=command,
        ))

    def play(self):
        return self.submit(Play())

    def pause(self):
        return self.submit(Pause())

    def stop(self):
        return self.submit(Stop())

    def toggle(self):
        return self.submit(TogglePlayback())

    def request_seek(self, index: int):
        return self.submit(Seek(int(index)))

    def request_resync(self):
        # Re-anchor the engine's render clocks to the current playhead WITHOUT moving it. Used when
        # a live change (e.g. enabling the metronome mid-playback) needs the metronome's output
        # clock / ring realigned the same way a seek does, but without jumping position.
        return self.submit(Resync())

    def cancel_countin(self):
        # Abort an in-progress count-in WITHOUT moving the playhead (a second Space press during
        # the lead-in). Distinct from stop(), which would reset the position to 0.
        return self.submit(CancelCountIn())

    def set_length(self, length: int):
        return self.submit(SetLength(int(length)))

    def request_rate(self, rate: float):
        return self.submit(SetRate(float(rate)))

    def set_loop_a(self, frac: float):
        return self.submit(SetLoopPoint("A", float(frac)))

    def set_loop_b(self, frac: float):
        return self.submit(SetLoopPoint("B", float(frac)))

    def set_loop_range(self, a: float, b: float):
        # Set BOTH ends atomically, auto-ordered so a <= b. Used while dragging a marker: it
        # sidesteps the per-end A<B guard (which would reject an out-of-order intermediate set)
        # and gives clean swap behaviour when one handle is dragged past the other.
        return self.submit(SetLoopRange(float(a), float(b)))

    def clear_loop(self):
        return self.submit(ClearLoop())

    def set_loop_enabled(self, enabled: bool):
        return self.submit(SetLoopEnabled(bool(enabled)))

    def note_consumed(self, frames: int):
        # Posted by the audio callback after each block: "this many frames were played".
        self._commands.put(("CONSUMED", int(frames)))

    # ----- Command application (engine producer thread ONLY) ----- #
    # The producer is the single writer of _index / _state / _length. It drains the queue
    # once per loop and calls these. No other thread may call them.

    def drain_commands(self) -> list:
        cmds = []
        while True:
            try:
                cmds.append(self._commands.get_nowait())
            except queue.Empty:
                break
        return cmds

    def publish_snapshot(self) -> None:
        """Publish one coherent read model after the producer applies a command."""
        self._snapshot = self._make_snapshot()

    def _make_snapshot(self) -> TransportSnapshot:
        return TransportSnapshot(
            sample_rate=self.sample_rate,
            current_index=self._index,
            length=self._length,
            state=self._state,
            rate=self._rate,
            position_seconds=self.position_seconds,
            length_seconds=self.length_seconds,
            source_position_seconds=self._source_seconds,
            source_length_seconds=self.source_length_seconds,
            progress=self.progress,
            loop_a=self._loop_a,
            loop_b=self._loop_b,
            loop_enabled=self._loop_enabled,
            loop_active=self.loop_is_active(),
            bpm=self.bpm,
            beat_offset=self.beat_offset,
            bpm_trim=self._bpm_trim,
            offset_trim=self._offset_trim,
            beat_grid_from_node=self._beat_grid_from_node,
            snap_enabled=self._snap_enabled,
            waveform=self._waveform,
            waveform_version=self._waveform_version,
        )

    def _apply_play(self):
        if not self.is_loaded():
            return
        # With an active loop, the section IS the piece: starting playback from outside [A, B)
        # begins the loop cleanly at A instead of leaking through the pre-A / post-B region once.
        # Inside the loop, position is left alone (no jarring jump mid-section).
        if self.loop_is_active():
            if not (self._loop_a_index() <= self._index < self._loop_b_index()):
                self._index = self._loop_a_index()
                self._refresh_source_seconds()
        self._state = PlaybackState.PLAYING

    def _apply_pause(self):
        if self._state == PlaybackState.PLAYING:
            self._state = PlaybackState.PAUSED

    def _apply_stop(self):
        # Home = the loop's A point while a loop is active (the section's origin is A, not 0);
        # otherwise the absolute start. Clearing the loop restores stop -> 0.
        self._state = PlaybackState.STOPPED
        self._index = self._loop_a_index() if self.loop_is_active() else 0
        self._refresh_source_seconds()

    def _apply_toggle(self):
        if self._state == PlaybackState.PLAYING:
            self._apply_pause()
        else:
            self._apply_play()

    def _apply_seek(self, index: int):
        index = self._clamp(index)
        # Snap to the nearest beat when enabled. The beat grid is in SOURCE time, so convert the
        # output index -> source seconds, snap, then map back to an output index (src / rate).
        if self._snap_enabled:
            src_t = (index * self._rate) / self.sample_rate
            snapped_src = self._snap_src_seconds(src_t)
            index = self._clamp(round((snapped_src * self.sample_rate) / self._rate))
        self._index = index
        self._refresh_source_seconds()

    def _apply_length(self, length: int):
        # `length` is the SOURCE length; the output length depends on the playback rate.
        self._base_length = max(0, int(length))
        self._length = max(0, round(self._base_length / self._rate))
        self._index = self._clamp(self._index)
        self._refresh_source_seconds()

    def _apply_rate(self, rate: float):
        # Change playback speed while keeping the same SOURCE position playing, so the
        # output index is remapped and the output length is recomputed from the base.
        old = self._rate
        src = self._index * old                      # current source position (samples)
        self._rate = max(0.05, float(rate))
        if self._base_length > 0:
            self._length = max(0, round(self._base_length / self._rate))
        self._index = self._clamp(round(src / self._rate))
        self._refresh_source_seconds()

    def _apply_consumed(self, frames: int):
        # Advance the playhead by the frames the callback actually played. Ignored unless
        # we are PLAYING, so stale CONSUMED messages during pause/stop do not move it.
        if self._state != PlaybackState.PLAYING:
            return
        self._index = self._clamp(self._index + frames)
        # A/B loop: if playback reached the B point, jump back to A instead of stopping.
        # Returns True when it wrapped, so the caller (engine) knows to resync the render head.
        wrapped = self._maybe_loop_wrap()
        if not wrapped and self._length > 0 and self._index >= self._length:
            self._state = PlaybackState.STOPPED
        self._refresh_source_seconds()
        return wrapped

    def _maybe_loop_wrap(self) -> bool:
        # Producer thread only. When an active loop's B point is reached, rewind to A. Points
        # are fractions of the OUTPUT timeline, so convert with the current length here.
        if not self.loop_is_active() or self._length <= 0:
            return False
        b_index = int(self._loop_b * self._length)
        if self._index >= b_index:
            self._index = self._clamp(int(self._loop_a * self._length))
            return True
        return False

    def _snap_frac(self, frac: float) -> float:
        """Snap a loop-point fraction (of the OUTPUT timeline) to the nearest beat, if enabled.

        Loop points are fractions of the output length; the grid is in source time, so convert
        frac -> source seconds via the rate-independent source length, snap, then back to a frac.
        """
        if not self._snap_enabled:
            return frac
        src_len = self._base_length / self.sample_rate
        if src_len <= 0:
            return frac
        snapped_src = self._snap_src_seconds(frac * src_len)
        return min(1.0, max(0.0, snapped_src / src_len))

    def _apply_loop_a(self, frac: float):
        a = self._snap_frac(min(1.0, max(0.0, float(frac))))
        # Keep A strictly left of B if B exists; ignore an out-of-order set rather than swap.
        if self._loop_b is None or a < self._loop_b:
            self._loop_a = a

    def _apply_loop_b(self, frac: float):
        b = self._snap_frac(min(1.0, max(0.0, float(frac))))
        if self._loop_a is None or b > self._loop_a:
            self._loop_b = b

    def _apply_loop_range(self, a: float, b: float):
        a = self._snap_frac(min(1.0, max(0.0, float(a))))
        b = self._snap_frac(min(1.0, max(0.0, float(b))))
        self._loop_a = min(a, b)
        self._loop_b = max(a, b)

    def _apply_loop_clear(self):
        self._loop_a = None
        self._loop_b = None
        self._loop_enabled = False

    def _apply_loop_enable(self, enabled: bool):
        self._loop_enabled = bool(enabled)

    def _apply_beat_trim(self, bpm_trim: float, offset_trim: float):
        self._bpm_trim = float(bpm_trim)
        self._offset_trim = float(offset_trim)

    def _apply_snap_enabled(self, enabled: bool):
        self._snap_enabled = bool(enabled)

    def _refresh_source_seconds(self):
        # Producer thread only. Publish the source-timeline position as one atomic float so
        # cross-thread readers of source_position_seconds never see a half-updated state.
        self._source_seconds = (self._index * self._rate) / self.sample_rate

    # ----- Internal ----- #

    def _clamp(self, index: int) -> int:
        if self._length <= 0:
            return 0
        return max(0, min(int(index), self._length))


# Single shared instance used across the whole app.
TRANSPORT = Transport()
