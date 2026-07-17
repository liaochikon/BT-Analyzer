"""Headless whole-track time stretch processor."""

from nodes._shared.librosa_effect import (
    AudioEffectResult,
    AudioEffectStatus,
    process_per_channel,
)


class TimeStretchProcessor:
    def process(self, audio, *, rate: float, librosa_module=None,
                progress=None) -> AudioEffectResult:
        is_noop = abs(rate - 1.0) < 1e-6
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
            transform=lambda channel: librosa_module.effects.time_stretch(
                y=channel, rate=rate),
            is_noop=False,
            progress=progress,
        )