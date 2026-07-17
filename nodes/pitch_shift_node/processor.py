"""Headless whole-track pitch shift processor."""

from nodes._shared.librosa_effect import (
    AudioEffectResult,
    AudioEffectStatus,
    process_per_channel,
)


class PitchShiftProcessor:
    def process(self, audio, *, semitones: float, sample_rate: int,
                librosa_module=None, progress=None) -> AudioEffectResult:
        is_noop = abs(semitones) < 1e-6
        if audio is None or is_noop:
            return process_per_channel(
                audio, transform=None, is_noop=is_noop, progress=progress)
        try:
            if librosa_module is None:
                import librosa as librosa_module
        except ImportError as exc:
            return AudioEffectResult(
                AudioEffectStatus.DEPENDENCY_UNAVAILABLE, error=str(exc))
        return process_per_channel(
            audio,
            transform=lambda channel: librosa_module.effects.pitch_shift(
                y=channel, sr=sample_rate, n_steps=semitones),
            is_noop=False,
            progress=progress,
        )