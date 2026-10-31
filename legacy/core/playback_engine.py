"""Central playback engine (the app-global audio output).

This is the single, app-wide owner of the sound device. It is a sibling of the
TRANSPORT (which holds *where* we are) and is responsible for *making sound*:

  - It owns one sounddevice.OutputStream (opened once, kept open = "Method A").
  - A real-time audio callback drains a RingBuffer into the hardware buffer and
    reports the frames played back to the TRANSPORT (via a command). The callback
    does the absolute minimum (a memcpy and a queue put) so it never blocks the
    real-time thread.
  - A normal producer thread is the SOLE owner of the playback truth: it drains
    TRANSPORT's command queue (play/pause/stop/seek/length/source/consumed),
    applies them in order, and fills the RingBuffer ahead of time by pulling audio
    blocks from a registered source. When no source is registered it outputs silence.

Thread roles (three threads, two shared points):
  - producer thread  -> sole writer of TRANSPORT state; writes audio into the ring
  - audio callback   -> reads the RingBuffer, posts frames-played to TRANSPORT
  - GUI thread       -> posts commands + reads TRANSPORT to draw the bar (elsewhere)

The RingBuffer (audio) and TRANSPORT (position) are the only shared points, so
adding UI/monitors never touches this audio lifeline.
"""

import logging
import queue
import threading
import time

import numpy as np
import sounddevice as sd

from core.constants import GLOBAL_SAMPLE_RATE
from core.transport import TRANSPORT, PlaybackState
from core.transport_contracts import (
    ConfigureMetronome,
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
    SetMasterGain,
    SetMetronomeGain,
    SetSnapEnabled,
    SetRate,
    SetSource,
    StartCountIn,
    Stop,
    TogglePlayback,
    TransportTopic,
)
from core.buffer import RingBuffer
from core.time_stretcher import TimeStretcher


logger = logging.getLogger(__name__)


class PlaybackEngine:
    # Click tone frequencies (Hz): the accented downbeat vs the other beats. Shared by the
    # count-in lead-in and the play-along metronome overlay (they differ only in amplitude).
    _CLICK_FREQ_ACCENT = 1500.0
    _CLICK_FREQ_NORMAL = 1000.0

    def __init__(self, block_frames: int = 512, channels: int = 2,
                 ring_depth: int = 4, sample_rate: int = GLOBAL_SAMPLE_RATE):
        self.block_frames = block_frames
        self.channels = channels
        self.sample_rate = sample_rate
        self.ring = RingBuffer(block_frames * ring_depth, channels)

        self._stream = None
        self._producer = None
        self._stop = threading.Event()
        self._underrun = False

        # Output device index for sounddevice; None = the system default output.
        self._device = None

        # Device output sample rate. The engine works internally at self.sample_rate (44100);
        # if the opened device runs at a different rate (e.g. a 48 kHz WASAPI endpoint, where
        # PortAudio will NOT auto-convert and hard-opening 44100 fails or plays pitched+glitchy),
        # the callback resamples internal audio to this rate on the fly. Equal to sample_rate
        # unless a device forces otherwise.
        self._device_rate = float(sample_rate)
        # Streaming linear-resampler state (audio-callback thread only): fractional read position
        # within the internal-rate audio, the previous internal sample (to interpolate across
        # callback boundaries), and a scratch buffer pulled from the ring.
        self._rs_phase = 0.0
        self._rs_prev = None          # (channels,) last internal sample from the previous block
        self._rs_scratch = None       # reused (n, channels) buffer for the internal pull

        self._source = None
        self._render_pos = 0
        self._control_mailbox = None

        # Serializes every call INTO the source render chain. Streamable effect nodes (EQ,
        # compressor, ...) keep per-instance DSP state (filter memory, envelopes) that is
        # read-modified-written inside their per-block transform, so two threads pulling the same
        # chain at once corrupt that state -> audible glitches on live playback. The producer's
        # per-block render and the waveform worker's whole-track scan both hold this lock, so they
        # never traverse the chain simultaneously. The producer runs on a normal thread (NOT the
        # RT callback, which only drains the ring), and the worker releases the lock between small
        # chunks, so the producer's wait is bounded to one chunk's processing and the ring's
        # ring_depth buffer absorbs it without an underrun.
        self._render_lock = threading.Lock()

        # Pitch-preserving slowdown (WSOLA). Bypassed entirely at rate == 1.0 so normal
        # playback pays zero cost; engaged only when the user lowers the playback speed.
        self._rate = 1.0
        self._stretcher = TimeStretcher(channels=self.channels)

        # Count-in: a metronome lead-in of N click blips at the beat interval, played BEFORE
        # playback resumes from the current playhead. Producer-thread-owned (like _render_pos):
        # only _apply_cmd and the producer loop touch these, so no lock is needed.
        self._countin_buf = None     # (frames, channels) float32 click track, or None
        self._countin_pos = 0        # frames of the count-in already written to the ring
        self._countin_active = False

        # Master output gain (linear): scales the MUSIC only. Applied to the rendered block before
        # the click overlays are mixed in, so the metronome / count-in are NOT affected by it
        # (they're practice scaffolding, not part of the mix). GUI writes it, producer reads it
        # (float write is GIL-atomic, no lock/queue).
        self._master_gain = 1.0
        # Click gain (linear): independent volume for the metronome overlay AND the count-in, mixed
        # in AFTER master. Lets the player balance click-vs-music (music down / click up to lock a
        # groove, or the reverse) which a single master couldn't do.
        self._metro_gain = 1.0

        # Metronome: overlay a click on the music at every beat while PLAYING. Beats are defined
        # on the SOURCE timeline (bpm + offset seconds); the producer detects beat crossings per
        # block using the source position, so it stays aligned even when the playback rate != 1.
        # GUI writes these scalars, producer reads them -- GIL-atomic, no queue.
        self._metronome_on = False
        self._metro_bpm = 120.0
        self._metro_offset = 0.0     # phase of beat 1, in SOURCE seconds
        # Cached click blips (built lazily; downbeat accented, others normal).
        self._metro_click_accent = None
        self._metro_click_normal = None
        # Overlay tail (output samples, channels): a click is ~45ms = several blocks long, so a
        # triggered click is spliced in here and drained block-by-block, letting it span block
        # boundaries. Reset on any playback discontinuity (see _resync).
        self._metro_tail = None
        # Output-sample position of the block currently being rendered (advances by block_frames
        # per PLAYING block; reset to the playhead on resync). Distinct from _render_pos, which
        # the rate!=1 path leaves untouched -- this counter is the metronome's output clock.
        self._render_out_pos = 0

    def _negotiate_rate(self) -> float:
        """Pick a sample rate the device will actually accept.

        Prefer the internal rate (no resampling). If the device rejects it (common for 48 kHz
        WASAPI endpoints -- PortAudio does NOT auto-convert and a hard 44100 open fails or, worse,
        plays pitched + glitchy), fall back to the device's reported default rate and let the
        callback resample. Returns the chosen rate.
        """
        try:
            sd.check_output_settings(
                device=self._device, samplerate=self.sample_rate,
                channels=self.channels, dtype="float32")
            return float(self.sample_rate)
        except Exception:
            pass
        try:
            info = sd.query_devices(self._device, "output") if self._device is not None \
                else sd.query_devices(sd.default.device[1], "output")
            dev_rate = float(info.get("default_samplerate") or self.sample_rate)
            if dev_rate != self.sample_rate:
                logger.info("Device rejects %d Hz; opening at its native %g Hz and resampling.",
                            self.sample_rate, dev_rate)
            return dev_rate
        except Exception:
            return float(self.sample_rate)

    def start(self):
        if self._stream is not None:
            return
        # Guard: a previous producer that refused to stop (see stop()) is still the sole writer of
        # the ring. Don't spawn a second one on top of it.
        if self._producer is not None and self._producer.is_alive():
            logger.error("Cannot start: a playback producer is still running.")
            return
        self._device_rate = self._negotiate_rate()
        # Reset resampler state for a clean start on the (possibly new) device rate.
        self._rs_phase = 0.0
        self._rs_prev = None
        self._stop.clear()
        self._producer = threading.Thread(target=self._producer_loop, daemon=True)
        self._producer.start()
        try:
            self._stream = sd.OutputStream(
                samplerate=self._device_rate,
                blocksize=0,   # let the device choose its block size; we resample to whatever it asks
                channels=self.channels,
                dtype="float32",
                device=self._device,
                callback=self._callback)
            self._stream.start()
        except Exception:
            self.stop()
            raise

    @property
    def current_device(self):
        """The output device index currently in use (None = system default). Read-only view for
        the UI so it never touches the private field."""
        return self._device

    @property
    def render_lock(self):
        """The lock serializing traversals of the (stateful) render chain. Any thread other than
        the producer that pulls the render chain -- the waveform scanner, an offline export, the
        heavy-bake input materializer -- must hold this around each render() call so it never
        traverses the shared effect-node DSP state at the same instant as live playback."""
        return self._render_lock

    def preset_device(self, device) -> None:
        """Set the output device BEFORE the stream is opened (startup only). Just records it; the
        first start() opens on it. Use set_device() to switch while running (it restarts the
        stream). No-op-safe if the stream is already open (falls through to set_device)."""
        if self._stream is None:
            self._device = device
        else:
            self.set_device(device)

    def set_device(self, device) -> bool:
        """Switch the output device, restarting the stream in place. Returns True on success.

        Safe to call while playing: the stream is torn down and reopened on the new device;
        the producer thread and transport state are untouched, so playback continues from the
        same playhead. On failure the previous device is restored and the stream reopened so
        the app is never left without audio.
        """
        prev = self._device
        was_running = self._stream is not None
        if was_running:
            self.stop()
        self._device = device
        try:
            self.start()
            return True
        except Exception:
            logger.exception("Failed to open audio device %r; reverting to previous", device)
            self._device = prev
            if was_running:
                try:
                    self.start()
                except Exception:
                    logger.exception("Failed to reopen previous audio device after a failed switch")
            return False

    def stop(self):
        # Called on app teardown (from main's finally) AND from set_device() to restart the
        # stream. Every step must run even if an earlier one raises, or the OutputStream leaks /
        # the process hangs on exit.
        #
        # Join with a generous timeout and only drop the producer reference once it has ACTUALLY
        # exited. The producer loop checks _stop every iteration and each iteration is bounded (one
        # block render, or a 1 ms sleep), so it exits promptly -- but if a join ever times out we
        # must NOT null the reference and then let start() revive a second producer via _stop.clear
        # (two writers to the single-producer ring). Keeping the reference makes start()'s
        # "already running" guard fire instead.
        self._stop.set()
        if self._producer is not None:
            try:
                self._producer.join(timeout=2.0)
            except Exception:
                pass
            if not self._producer.is_alive():
                self._producer = None
            else:
                logger.error("Playback producer did not stop; not restarting to avoid two producers.")
        if self._stream is not None:
            try:
                self._stream.stop()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    @property
    def is_running(self) -> bool:
        return self._stream is not None and self._stream.active

    def pop_underrun(self) -> bool:
        flag = self._underrun
        self._underrun = False
        return flag

    def set_source(self, source, length: int = 0):
        # Post a SOURCE command carrying the render callable AND its length together, so the
        # producer applies both atomically (no window where a new source has an old length).
        TRANSPORT.submit(SetSource(source, int(length)))

    def bind_control_mailbox(self, mailbox) -> None:
        if self._producer is not None and self._producer.is_alive():
            raise RuntimeError("bind transport mailbox before starting the producer")
        self._control_mailbox = mailbox

    def _drain_control_mailbox(self):
        if self._control_mailbox is None:
            return
        while True:
            try:
                message = self._control_mailbox.get_nowait()
            except queue.Empty:
                return
            if message.topic != TransportTopic.COMMAND:
                logger.warning("Transport ignored unsupported topic: %s", message.topic)
                continue
            yield message.payload

    def _make_click(self, freq: float, amp: float, dur: float = 0.045) -> np.ndarray:
        # A short exponentially-decaying sine "tick" as an (n, channels) float32 block. Purely
        # procedural (no sample assets); accented downbeat uses a higher freq + amplitude.
        n = max(1, int(dur * self.sample_rate))
        t = np.arange(n, dtype=np.float32) / self.sample_rate
        env = np.exp(-t * 32.0).astype(np.float32)
        wave = (np.sin(2.0 * np.pi * freq * t) * env * amp).astype(np.float32)
        return np.repeat(wave[:, None], self.channels, axis=1)

    def start_countin(self, interval_frames: int, beats: int = 4):
        """Play ``beats`` metronome clicks at ``interval_frames`` spacing, then resume playback.

        Builds the whole count-in track once and posts it to the producer, which streams it out
        before starting playback from the current playhead. Beat 1 is accented. A non-positive
        interval falls back to just starting playback (no usable tempo).
        """
        interval = int(interval_frames)
        if interval <= 0 or beats <= 0:
            TRANSPORT.play()
            return
        total = interval * beats
        buf = np.zeros((total, self.channels), dtype=np.float32)
        accent = self._make_click(self._CLICK_FREQ_ACCENT, 0.9)   # downbeat (beat 1)
        normal = self._make_click(self._CLICK_FREQ_NORMAL, 0.6)   # other beats
        for i in range(beats):
            click = accent if i == 0 else normal
            start = i * interval
            n = min(len(click), total - start)
            buf[start:start + n] += click[:n]
        TRANSPORT.submit(StartCountIn(buf))

    def set_master_gain(self, gain: float):
        """Set the master output gain (linear, clamped >= 0). Scales the MUSIC only (not the click).
        Cheap scalar write; read by the producer each block. GUI passes 10**(dB/20); 1.0 = 0 dB."""
        return TRANSPORT.submit(SetMasterGain(float(gain)))

    def set_metro_gain(self, gain: float):
        """Set the click gain (linear, clamped >= 0) for the metronome overlay AND the count-in,
        independent of master. Cheap scalar write; read by the producer each block."""
        return TRANSPORT.submit(SetMetronomeGain(float(gain)))

    def set_metronome(self, enabled: bool, bpm: float, offset: float):
        """Enable/disable the play-along metronome and set its beat grid (bpm + source offset).

        Written from the GUI; read by the producer. Building the click blips is deferred to the
        first overlay so this stays a cheap scalar write.
        """
        return TRANSPORT.submit(ConfigureMetronome(
            enabled=bool(enabled),
            bpm=float(bpm),
            offset=float(offset),
        ))

    def _callback(self, outdata, frames, time_info, status):
        # Real-time thread: do the absolute minimum. Drain the ring into the device buffer and
        # report how many INTERNAL frames were played; never touch TRANSPORT state directly.
        if status:
            self._underrun = True

        # Fast path: device runs at the internal rate -> straight copy, one internal frame per
        # output frame.
        if abs(self._device_rate - self.sample_rate) < 0.5:
            _, under = self.ring.read(outdata)
            if under:
                self._underrun = True
            TRANSPORT.note_consumed(frames)
            return

        # Resample path: the device wants `frames` samples at _device_rate. Produce them by
        # linearly interpolating internal-rate audio at positions phase + j*ratio, where
        # ratio = internal/device internal samples advance per output sample. _rs_prev is the
        # UNCONSUMED tail carried from the previous callback (index 0 is the sample `phase` is
        # measured from), so interpolation is continuous across callback boundaries and the ring
        # is only ever read for samples we actually account for.
        ratio = self.sample_rate / self._device_rate
        phase = self._rs_phase
        hold = self._rs_prev  # (h, channels) or None

        have = 0 if hold is None else len(hold)
        # Highest internal index the interpolation touches: floor(phase+(frames-1)*ratio)+1.
        need_total = int(np.floor(phase + (frames - 1) * ratio)) + 2
        need_new = max(0, need_total - have)

        if self._rs_scratch is None or len(self._rs_scratch) < need_new:
            self._rs_scratch = np.zeros((max(need_new, 1), self.channels), dtype=np.float32)
        new = self._rs_scratch[:need_new]
        if need_new > 0:
            _, under = self.ring.read(new)
            if under:
                self._underrun = True

        if have > 0:
            combined = np.concatenate((hold, new), axis=0)
        else:
            combined = new
        H = len(combined)

        pos = phase + np.arange(frames, dtype=np.float64) * ratio
        left = np.floor(pos).astype(np.int64)
        frac = (pos - left).astype(np.float32)[:, None]
        left = np.clip(left, 0, H - 1)
        right = np.clip(left + 1, 0, H - 1)
        outdata[:] = combined[left] * (1.0 - frac) + combined[right] * frac

        # Advance by frames output samples. The integer part is consumed; keep the tail (>=1
        # sample) as the carry so the next block interpolates from the right place.
        end = phase + frames * ratio
        consumed_int = int(np.floor(end))
        self._rs_phase = end - consumed_int
        keep = combined[consumed_int:] if consumed_int < H else combined[H - 1:]
        self._rs_prev = np.ascontiguousarray(keep)
        # Report only the NEW internal samples actually pulled from the ring (the carried tail
        # was already counted when first read), so the playhead advances at the internal rate.
        TRANSPORT.note_consumed(need_new)

    def _resync(self):
        # A playback discontinuity (seek/stop/play/source swap): realign the render head to
        # the playhead and drop the now-stale audio still queued in the ring.
        self._render_pos = TRANSPORT.current_index
        # Realign the metronome's output clock too, and drop any half-played click tail so a
        # seek doesn't leave a click ringing at the wrong spot.
        self._render_out_pos = TRANSPORT.current_index
        self._metro_tail = None
        if abs(self._rate - 1.0) > 1e-9:
            # The render head is in OUTPUT samples; the stretcher reads the SOURCE, so map
            # the playhead to a source position (source = output_index * rate) and re-align.
            self._stretcher.set_source(self._source)
            self._stretcher.set_rate(self._rate)
            self._stretcher.reset(TRANSPORT.current_index * self._rate)
        self.ring.request_flush()

    def _apply_cmd(self, cmd):
        # Producer thread ONLY. This is the single place _index/_state/_length/_source change.
        if isinstance(cmd, Play):
            cmd = ("PLAY",)
        elif isinstance(cmd, Pause):
            cmd = ("PAUSE",)
        elif isinstance(cmd, Stop):
            cmd = ("STOP",)
        elif isinstance(cmd, TogglePlayback):
            cmd = ("TOGGLE",)
        elif isinstance(cmd, Seek):
            cmd = ("SEEK", cmd.sample_index)
        elif isinstance(cmd, SetRate):
            cmd = ("RATE", cmd.rate)
        elif isinstance(cmd, SetLoopPoint):
            cmd = ("LOOP_A" if cmd.point == "A" else "LOOP_B", cmd.fraction)
        elif isinstance(cmd, SetLoopRange):
            cmd = ("LOOP_RANGE", cmd.start, cmd.end)
        elif isinstance(cmd, ClearLoop):
            cmd = ("LOOP_CLEAR",)
        elif isinstance(cmd, SetLoopEnabled):
            cmd = ("LOOP_ENABLE", cmd.enabled)
        elif isinstance(cmd, SetSnapEnabled):
            cmd = ("SNAP_ENABLE", cmd.enabled)
        elif isinstance(cmd, SetBeatTrim):
            cmd = ("BEAT_TRIM", cmd.bpm, cmd.offset)
        elif isinstance(cmd, SetMasterGain):
            cmd = ("MASTER_GAIN", cmd.gain)
        elif isinstance(cmd, SetMetronomeGain):
            cmd = ("METRONOME_GAIN", cmd.gain)
        elif isinstance(cmd, ConfigureMetronome):
            cmd = ("METRONOME", cmd.enabled, cmd.bpm, cmd.offset)
        elif isinstance(cmd, Resync):
            cmd = ("RESYNC",)
        elif isinstance(cmd, CancelCountIn):
            cmd = ("COUNTIN_CANCEL",)
        elif isinstance(cmd, SetLength):
            cmd = ("LENGTH", cmd.sample_count)
        elif isinstance(cmd, SetSource):
            cmd = ("SOURCE", cmd.render, cmd.sample_count)
        elif isinstance(cmd, StartCountIn):
            cmd = ("COUNTIN", cmd.audio)
        kind = cmd[0]
        if kind == "CONSUMED":
            # If the playhead wrapped back to the loop A point, the render head and queued ring
            # audio are now stale (they were rendering ahead past B): resync to realign.
            if TRANSPORT._apply_consumed(cmd[1]):
                self._resync()
        elif kind == "RESYNC":
            # Re-align the render/metronome clocks to the playhead without moving it (e.g. the
            # metronome was just enabled mid-playback and its output clock needs re-anchoring).
            self._resync()
        elif kind == "PLAY":
            TRANSPORT._apply_play()
            self._resync()
        elif kind == "PAUSE":
            self._cancel_countin()
            TRANSPORT._apply_pause()
            self.ring.request_flush()
        elif kind == "STOP":
            self._cancel_countin()
            TRANSPORT._apply_stop()
            self._resync()
        elif kind == "TOGGLE":
            TRANSPORT._apply_toggle()
            self._resync()
        elif kind == "SEEK":
            self._cancel_countin()
            TRANSPORT._apply_seek(cmd[1])
            self._resync()
        elif kind == "LENGTH":
            TRANSPORT._apply_length(cmd[1])
        elif kind == "RATE":
            TRANSPORT._apply_rate(cmd[1])
            self._rate = TRANSPORT.rate
            self._resync()
        elif kind == "SOURCE":
            self._source = cmd[1]
            TRANSPORT._apply_length(cmd[2])
            self._resync()
        elif kind == "LOOP_A":
            TRANSPORT._apply_loop_a(cmd[1])
        elif kind == "LOOP_B":
            TRANSPORT._apply_loop_b(cmd[1])
        elif kind == "LOOP_RANGE":
            TRANSPORT._apply_loop_range(cmd[1], cmd[2])
        elif kind == "LOOP_CLEAR":
            TRANSPORT._apply_loop_clear()
        elif kind == "LOOP_ENABLE":
            TRANSPORT._apply_loop_enable(cmd[1])
        elif kind == "SNAP_ENABLE":
            TRANSPORT._apply_snap_enabled(cmd[1])
        elif kind == "BEAT_TRIM":
            TRANSPORT._apply_beat_trim(cmd[1], cmd[2])
        elif kind == "MASTER_GAIN":
            self._master_gain = max(0.0, float(cmd[1]))
        elif kind == "METRONOME_GAIN":
            self._metro_gain = max(0.0, float(cmd[1]))
        elif kind == "METRONOME":
            self._metronome_on = bool(cmd[1])
            if cmd[2] > 0:
                self._metro_bpm = float(cmd[2])
            self._metro_offset = max(0.0, float(cmd[3]))
        elif kind == "COUNTIN":
            # Arm the count-in: the producer loop streams these clicks out, then starts playback
            # from the current playhead. Flush stale audio first so the clicks start immediately.
            self._countin_buf = cmd[1]
            self._countin_pos = 0
            self._countin_active = True
            self.ring.request_flush()
        elif kind == "COUNTIN_CANCEL":
            # Abort the lead-in without moving the playhead (unlike STOP, which resets to 0).
            self._cancel_countin()
            self.ring.request_flush()
            self._resync()
        TRANSPORT.publish_snapshot()

    def is_counting_in(self) -> bool:
        # Read by the GUI to reflect / gate the play button. Reading a bool is atomic under the
        # GIL, so no lock is needed against the producer thread that owns the flag.
        return self._countin_active

    def _cancel_countin(self):
        self._countin_buf = None
        self._countin_pos = 0
        self._countin_active = False

    def _producer_loop(self):
        block = np.empty((self.block_frames, self.channels), dtype=np.float32)
        while not self._stop.is_set():
            # The producer thread is the audio lifeline: if one iteration raises (e.g. a buggy
            # node render callable), swallow it and keep looping. Letting the exception escape
            # would silently kill the thread, freezing all playback with no recovery. Emit
            # silence for the offending block and carry on.
            try:
                # (A) Apply every pending command in order. Sole writer of the playback truth.
                for cmd in self._drain_control_mailbox():
                    self._apply_cmd(cmd)
                for cmd in TRANSPORT.drain_commands():
                    self._apply_cmd(cmd)

                # (B) Render ahead. Count-in clicks take priority; then real audio while PLAYING;
                # otherwise silence. The metronome overlays clicks on the music each PLAYING block.
                if self.ring.free_frames >= self.block_frames:
                    block[:] = 0.0
                    if self._countin_active:
                        # Count-in is pure click (no music): it's scaled by the click gain inside
                        # _render_countin_into, and master never touches it.
                        self._render_countin_into(block)
                    elif TRANSPORT.state == PlaybackState.PLAYING:
                        # Hold the render lock while pulling the chain so the waveform worker's
                        # scan can't traverse the same stateful effect nodes concurrently.
                        with self._render_lock:
                            self._render_block_into(block)
                        # Master scales the MUSIC only, applied BEFORE the click overlay so the
                        # metronome rides at its own gain, independent of master. Skip at unity.
                        g = self._master_gain
                        if g != 1.0:
                            block *= g
                        if self._metronome_on:
                            self._overlay_metronome_into(block)
                        self._render_out_pos += self.block_frames
                    self.ring.write(block)
                else:
                    time.sleep(0.001)
            except Exception:
                logger.exception("Playback producer iteration crashed; emitting silence and continuing")
                time.sleep(0.001)

    def _render_countin_into(self, block: np.ndarray):
        # Copy the next slice of the count-in click track into the output block. When the track
        # is exhausted, begin playback from the current playhead WITHOUT flushing the ring (a
        # flush would drop the clicks already queued ahead), so the beat lands seamlessly.
        buf = self._countin_buf
        if buf is None:
            self._countin_active = False
            return
        start = self._countin_pos
        remaining = len(buf) - start
        n = min(self.block_frames, remaining)
        if n > 0:
            # Count-in is click, so it rides the click gain (not master), matching the metronome.
            block[:n] = buf[start:start + n] * self._metro_gain
            self._countin_pos += n
        if self._countin_pos >= len(buf):
            # Count-in done: start playback. Align the render head to the playhead but do NOT
            # request_flush (unlike _resync), so the queued clicks and first audio blocks play
            # back-to-back without a gap.
            self._countin_active = False
            self._countin_buf = None
            self._render_pos = TRANSPORT.current_index
            # Realign the metronome's output clock too (it advances only on PLAYING blocks, so
            # without this it stays stuck at the last resync's value -- the beats would be
            # computed for the wrong time span and the clicks would seem to vanish after a
            # count-in). Drop any stale click tail for the same reason.
            self._render_out_pos = TRANSPORT.current_index
            self._metro_tail = None
            if abs(self._rate - 1.0) > 1e-9:
                self._stretcher.set_source(self._source)
                self._stretcher.set_rate(self._rate)
                self._stretcher.reset(TRANSPORT.current_index * self._rate)
            TRANSPORT._apply_play()

    def _render_block_into(self, block: np.ndarray):
        source = self._source
        if source is None:
            return
        if abs(self._rate - 1.0) > 1e-9:
            # Pitch-preserving slowdown: the stretcher owns its own source read position
            # (set in _resync) and pulls the source itself, so _render_pos is neither read
            # nor advanced on this path.
            out = self._stretcher.pull(self.block_frames)
            if out is not None and len(out) > 0:
                block[:out.shape[0]] = out
            return
        out = source(self._render_pos, self.block_frames)
        if out is not None and len(out) > 0:
            n = out.shape[0]
            block[:n] = out
            self._render_pos += n

    def _overlay_metronome_into(self, block: np.ndarray):
        """Add metronome clicks onto ``block`` at beats that fall within it. Additive, not
        replacing, so the click sits on top of the music.

        Beats live on the SOURCE timeline (offset + n*60/bpm seconds). This block covers OUTPUT
        samples [out_start, out_start+N); an output sample s maps to source time s*rate/sr, so a
        beat at source time t sits at output sample t*sr/rate. That rate factor is what keeps the
        clicks aligned to the music when the playback speed isn't 1.0. A click is longer than one
        block, so its overflow is carried in _metro_tail and drained on following blocks.
        """
        n = block.shape[0]
        if self._metro_click_accent is None:
            self._metro_click_accent = self._make_click(self._CLICK_FREQ_ACCENT, 0.7)
            self._metro_click_normal = self._make_click(self._CLICK_FREQ_NORMAL, 0.5)

        # Click gain (independent of master). Applied to both the tail drain and each beat click,
        # so the whole click follows the live fader; the tail is STORED raw and scaled on drain.
        g = self._metro_gain

        # (1) Drain any carry-over tail from a click that started in an earlier block.
        if self._metro_tail is not None and len(self._metro_tail) > 0:
            m = min(n, len(self._metro_tail))
            block[:m] += self._metro_tail[:m] * g
            self._metro_tail = self._metro_tail[m:]
            if len(self._metro_tail) == 0:
                self._metro_tail = None

        bpm = self._metro_bpm
        if bpm <= 0:
            return
        interval_src = 60.0 / bpm                     # seconds between beats (source time)
        rate = self._rate if abs(self._rate) > 1e-9 else 1.0
        sr = self.sample_rate
        out_start = self._render_out_pos

        # Source-time span covered by this block's output samples.
        t0 = (out_start * rate) / sr
        t1 = ((out_start + n) * rate) / sr

        # First beat index at or after t0 (only beats from the offset onward count).
        first = int(np.ceil((t0 - self._metro_offset) / interval_src))
        if first < 0:
            first = 0
        beat = first
        while True:
            t_beat = self._metro_offset + beat * interval_src
            if t_beat >= t1:
                break
            # Output sample of this beat, relative to the block start.
            s_out = int(round((t_beat * sr) / rate)) - out_start
            if 0 <= s_out < n:
                click = self._metro_click_accent if (beat % 4 == 0) else self._metro_click_normal
                end = min(n, s_out + len(click))
                block[s_out:end] += click[:end - s_out] * g
                # Stash the click's overflow past this block for the next block(s).
                if s_out + len(click) > n:
                    tail = click[end - s_out:]
                    if self._metro_tail is None:
                        self._metro_tail = tail.copy()
                    else:
                        # Overlap with an existing tail (very close beats): sum the shared span.
                        L = max(len(self._metro_tail), len(tail))
                        merged = np.zeros((L, self.channels), dtype=np.float32)
                        merged[:len(self._metro_tail)] += self._metro_tail
                        merged[:len(tail)] += tail
                        self._metro_tail = merged
            beat += 1


ENGINE = PlaybackEngine()


def list_output_devices() -> list:
    """Enumerate output-capable audio devices as ``(index, label)`` pairs.

    ``label`` is disambiguated with the host API name because the same physical output often
    appears once per host API (MME / WASAPI / DirectSound) with an identical device name.
    Returns an empty list if the audio backend cannot be queried (never raises).
    """
    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
    except Exception:
        logger.exception("Could not query audio devices")
        return []
    out = []
    for idx, dev in enumerate(devices):
        if dev.get("max_output_channels", 0) <= 0:
            continue
        api_name = ""
        api_idx = dev.get("hostapi")
        if isinstance(api_idx, int) and 0 <= api_idx < len(hostapis):
            api_name = hostapis[api_idx].get("name", "")
        name = dev.get("name", f"Device {idx}")
        label = f"{name} [{api_name}]" if api_name else name
        out.append((idx, label))
    return out


def default_output_device():
    """The system default output device index, or None if it cannot be resolved."""
    try:
        dev = sd.default.device
        # sd.default.device is (input, output); -1 means "no explicit default".
        out_idx = dev[1] if isinstance(dev, (list, tuple)) else dev
        return None if out_idx is None or out_idx < 0 else int(out_idx)
    except Exception:
        return None


_WAVEFORM_BINS = 2000          # envelope resolution (bins across the whole track)
# Frames pulled per render() call while scanning. Kept modest (not huge) because each chunk is
# rendered under the engine's render lock -- a smaller chunk bounds how long the playback
# producer can wait for the lock (one chunk's processing) so the ring never underruns.
_WAVEFORM_READ_BLOCK = 8192


def compute_waveform_envelope(render, length: int, bins: int = _WAVEFORM_BINS, lock=None):
    """Scan the whole render chain and return a downsampled (min_arr, max_arr) envelope.

    Runs on a worker thread (it pulls the entire track through ``render``). Mixes to mono, then
    reduces to ``bins`` min/max pairs across the source timeline -- the shape a waveform overview
    draws (filled band between per-bin min and max). Returns None if there's nothing to scan.

    ``lock`` (the engine's render lock) is acquired around EACH chunk's render() call so the scan
    never traverses the shared, stateful effect nodes at the same instant as the producer's
    playback render. It is released between chunks, so the producer waits at most one chunk. The
    scan does leave each effect node's DSP state at end-of-track; for the IIR/envelope followers
    used here that is a sub-block transient the producer's next blocks absorb, and the far more
    audible concurrent-corruption glitch is eliminated.
    """
    if render is None or length <= 0:
        return None
    bins = max(1, min(int(bins), int(length)))
    mn = np.zeros(bins, dtype=np.float32)
    mx = np.zeros(bins, dtype=np.float32)
    edges = np.linspace(0, length, bins + 1).astype(np.int64)

    # Pull the track in large chunks, accumulating each chunk's samples into the bins it spans.
    pos = 0
    bin_i = 0
    carry = None  # leftover mono samples not yet assigned to a completed bin
    carry_start = 0
    while pos < length:
        if lock is not None:
            with lock:
                block = render(pos, min(_WAVEFORM_READ_BLOCK, length - pos))
        else:
            block = render(pos, min(_WAVEFORM_READ_BLOCK, length - pos))
        if block is None or len(block) == 0:
            break
        mono = block.mean(axis=1) if block.ndim == 2 else np.asarray(block)
        mono = np.abs(np.asarray(mono, dtype=np.float32))
        seg = mono if carry is None else np.concatenate((carry, mono))
        seg_start = carry_start if carry is not None else pos
        # Assign to every bin fully covered by [seg_start, seg_start+len(seg)).
        while bin_i < bins and edges[bin_i + 1] <= seg_start + len(seg):
            lo = max(edges[bin_i], seg_start) - seg_start
            hi = edges[bin_i + 1] - seg_start
            chunk = seg[lo:hi]
            if len(chunk):
                peak = float(chunk.max())
                mx[bin_i] = peak
                mn[bin_i] = -peak   # symmetric envelope (we scanned |amplitude|)
            bin_i += 1
        # Keep the tail that belongs to the next (not-yet-complete) bin.
        if bin_i < bins:
            keep_from = max(0, edges[bin_i] - seg_start)
            carry = seg[keep_from:]
            carry_start = seg_start + keep_from
        pos += len(mono)
    return (mn, mx)


def resolve_output_device(label):
    """Map a saved device LABEL (from list_output_devices) back to a current device index.

    Device indices are NOT stable across unplug/replug/reboot, so we persist the label instead
    and re-resolve it here each run. Returns the matching index, or None (system default) when
    the label is empty or the device is no longer present -- so a remembered-but-now-absent
    device cleanly falls back instead of silently pointing at whatever now sits at that index.
    """
    if not label:
        return None
    for idx, lbl in list_output_devices():
        if lbl == label:
            return idx
    logger.info("Saved audio device '%s' not found; using system default.", label)
    return None
