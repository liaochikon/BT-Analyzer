"""Headless reference-audio MIDI velocity mapping processor."""

from dataclasses import dataclass
from enum import Enum, auto

import numpy as np

from core.datatypes import NOTE_DTYPE


@dataclass(frozen=True, slots=True)
class AudioVelocityMapParameters:
    band_semitones: float
    num_harmonics: float
    dynamic_range_db: float
    gain_db: float


class AudioVelocityMapStatus(Enum):
    NO_MIDI = auto()
    NO_AUDIO = auto()
    MAPPING_FAILED = auto()
    MAPPED = auto()


@dataclass(frozen=True, slots=True)
class AudioVelocityMapResult:
    status: AudioVelocityMapStatus
    notes: np.ndarray | None = None
    error: str = ""


class AudioVelocityMapProcessor:
    N_FFT = 2048
    HOP = 512

    @staticmethod
    def pitch_to_freq(pitch: int) -> float:
        return 440.0 * (2.0 ** ((pitch - 69) / 12.0))

    @staticmethod
    def amps_to_velocities(amps, dynamic_range_db: float, gain_db: float):
        amps = np.asarray(amps, dtype=np.float64)
        dynamic_range = max(1.0, dynamic_range_db)
        reference = float(amps.max()) if amps.size else 0.0
        if reference <= 0.0:
            return np.ones(amps.shape, dtype=np.uint8)
        db = 20.0 * np.log10(np.maximum(amps, 1e-12) / reference) + gain_db
        db = np.clip(db, -dynamic_range, 0.0)
        normalized = (db + dynamic_range) / dynamic_range
        return np.clip(
            np.round(1.0 + normalized * 126.0), 1, 127).astype(np.uint8)

    def power_spectrogram(self, mono: np.ndarray, sample_rate: int):
        if len(mono) < self.N_FFT:
            mono = np.pad(mono, (0, self.N_FFT - len(mono)))
        window = np.hanning(self.N_FFT).astype(np.float32)
        frames = np.lib.stride_tricks.sliding_window_view(
            mono, self.N_FFT)[::self.HOP]
        spectrum = np.fft.rfft(frames * window, axis=1)
        power = (spectrum.real ** 2 + spectrum.imag ** 2).astype(np.float64)
        frequencies = np.fft.rfftfreq(self.N_FFT, d=1.0 / sample_rate)
        frame_times = (
            np.arange(frames.shape[0]) * self.HOP + self.N_FFT / 2.0
        ) / sample_rate
        return power, frequencies, frame_times

    def note_power(self, power, frequencies, frame_times, pitch, start, end,
                   harmonics, half_semitones) -> float:
        time_mask = (frame_times >= start) & (frame_times < end)
        if not time_mask.any():
            index = int(np.argmin(np.abs(frame_times - 0.5 * (start + end))))
            rows = power[index:index + 1]
        else:
            rows = power[time_mask]

        fundamental = self.pitch_to_freq(pitch)
        low_ratio = 2.0 ** (-half_semitones / 12.0)
        high_ratio = 2.0 ** (half_semitones / 12.0)
        per_frame = np.zeros(rows.shape[0], dtype=np.float64)
        for harmonic in range(1, harmonics + 1):
            center = fundamental * harmonic
            if center >= frequencies[-1]:
                break
            bin_mask = (
                (frequencies >= center * low_ratio)
                & (frequencies <= center * high_ratio)
            )
            if bin_mask.any():
                per_frame += rows[:, bin_mask].sum(axis=1)
        return float(per_frame.mean()) if per_frame.size else 0.0

    def map_velocities(self, notes, audio, *, parameters: AudioVelocityMapParameters,
                       sample_rate: int, progress=None) -> AudioVelocityMapResult:
        if notes is None:
            return AudioVelocityMapResult(AudioVelocityMapStatus.NO_MIDI)
        if audio is None:
            return AudioVelocityMapResult(AudioVelocityMapStatus.NO_AUDIO)

        output = np.array(notes, dtype=NOTE_DTYPE, copy=True)
        if len(output) == 0:
            return AudioVelocityMapResult(AudioVelocityMapStatus.MAPPED, output)

        try:
            normalized = np.asarray(audio, dtype=np.float32)
            mono = normalized.mean(axis=0) if normalized.ndim == 2 else normalized
            if progress is not None:
                progress(0.1)
            power, frequencies, frame_times = self.power_spectrogram(
                mono, sample_rate)
            if progress is not None:
                progress(0.5)

            harmonics = max(1, int(round(parameters.num_harmonics)))
            half_semitones = max(0.05, parameters.band_semitones / 2.0)
            powers = np.empty(len(output), dtype=np.float64)
            for index in range(len(output)):
                powers[index] = self.note_power(
                    power, frequencies, frame_times,
                    int(output["pitch"][index]),
                    float(output["start"][index]),
                    float(output["end"][index]),
                    harmonics, half_semitones,
                )
            if progress is not None:
                progress(0.9)
            output["velocity"] = self.amps_to_velocities(
                np.sqrt(powers), parameters.dynamic_range_db, parameters.gain_db)
        except Exception as exc:
            return AudioVelocityMapResult(
                AudioVelocityMapStatus.MAPPING_FAILED, error=str(exc))

        return AudioVelocityMapResult(AudioVelocityMapStatus.MAPPED, output)