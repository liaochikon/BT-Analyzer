"""Headless stateful three-band EQ processor."""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class EqParameters:
    low_gain: float
    low_freq: float
    mid_gain: float
    mid_freq: float
    mid_q: float
    high_gain: float
    high_freq: float


class EqProcessor:
    """Apply three RBJ biquads while preserving per-channel filter state."""

    def __init__(self):
        self._states = {}

    def process_block(self, block, *, parameters: EqParameters, sample_rate: int):
        audio = np.asarray(block, dtype=np.float64)
        if audio.ndim == 1:
            audio = audio[:, None]
        output = self._apply_band(
            audio, self._shelf_coeffs(
                parameters.low_gain, parameters.low_freq,
                sample_rate, low=True), "low")
        output = self._apply_band(
            output, self._peaking_coeffs(parameters, sample_rate), "mid")
        output = self._apply_band(
            output, self._shelf_coeffs(
                parameters.high_gain, parameters.high_freq,
                sample_rate, low=False), "high")
        return output.astype(np.float32)

    def response(self, *, parameters: EqParameters, sample_rate: int):
        maximum = min(20000.0, sample_rate * 0.5 * 0.999)
        frequencies = np.logspace(np.log10(20.0), np.log10(maximum), 200)
        angular = 2.0 * np.pi * frequencies / sample_rate
        total = np.zeros_like(frequencies)
        coefficients = (
            self._shelf_coeffs(
                parameters.low_gain, parameters.low_freq,
                sample_rate, low=True),
            self._peaking_coeffs(parameters, sample_rate),
            self._shelf_coeffs(
                parameters.high_gain, parameters.high_freq,
                sample_rate, low=False),
        )
        for numerator, denominator in coefficients:
            response = self._frequency_response(
                numerator, denominator, angular)
            total += 20.0 * np.log10(np.abs(response) + 1e-12)
        return frequencies, total

    def _apply_band(self, audio, coefficients, key):
        numerator, denominator = coefficients
        state = self._states.get(key)
        if state is None or state.shape[-1] != audio.shape[1]:
            state = np.zeros((2, audio.shape[1]), dtype=np.float64)
        output = np.empty_like(audio)
        z1 = state[0].copy()
        z2 = state[1].copy()
        b0, b1, b2 = numerator
        _, a1, a2 = denominator
        for index in range(audio.shape[0]):
            sample = audio[index]
            filtered = b0 * sample + z1
            z1 = b1 * sample - a1 * filtered + z2
            z2 = b2 * sample - a2 * filtered
            output[index] = filtered
        state[0] = z1
        state[1] = z2
        self._states[key] = state
        return output

    @staticmethod
    def _frequency_response(numerator, denominator, angular):
        z1 = np.exp(-1j * angular)
        z2 = z1 * z1
        return (
            numerator[0] + numerator[1] * z1 + numerator[2] * z2
        ) / (
            denominator[0] + denominator[1] * z1 + denominator[2] * z2
        )

    @staticmethod
    def _shelf_coeffs(gain_db, frequency, sample_rate, *, low):
        amplitude = 10.0 ** (gain_db / 40.0)
        omega = (
            2.0 * np.pi * np.clip(frequency, 10.0, sample_rate * 0.49)
            / sample_rate)
        cosine, sine = np.cos(omega), np.sin(omega)
        alpha = sine / 2.0 * np.sqrt(2.0)
        beta = 2.0 * np.sqrt(amplitude) * alpha
        if low:
            b0 = amplitude * ((amplitude + 1) - (amplitude - 1) * cosine + beta)
            b1 = 2 * amplitude * ((amplitude - 1) - (amplitude + 1) * cosine)
            b2 = amplitude * ((amplitude + 1) - (amplitude - 1) * cosine - beta)
            a0 = (amplitude + 1) + (amplitude - 1) * cosine + beta
            a1 = -2 * ((amplitude - 1) + (amplitude + 1) * cosine)
            a2 = (amplitude + 1) + (amplitude - 1) * cosine - beta
        else:
            b0 = amplitude * ((amplitude + 1) + (amplitude - 1) * cosine + beta)
            b1 = -2 * amplitude * ((amplitude - 1) + (amplitude + 1) * cosine)
            b2 = amplitude * ((amplitude + 1) + (amplitude - 1) * cosine - beta)
            a0 = (amplitude + 1) - (amplitude - 1) * cosine + beta
            a1 = 2 * ((amplitude - 1) - (amplitude + 1) * cosine)
            a2 = (amplitude + 1) - (amplitude - 1) * cosine - beta
        numerator = np.array([b0, b1, b2], dtype=np.float64) / a0
        denominator = np.array([a0, a1, a2], dtype=np.float64) / a0
        return numerator, denominator

    @staticmethod
    def _peaking_coeffs(parameters, sample_rate):
        amplitude = 10.0 ** (parameters.mid_gain / 40.0)
        omega = (
            2.0 * np.pi
            * np.clip(parameters.mid_freq, 10.0, sample_rate * 0.49)
            / sample_rate)
        cosine, sine = np.cos(omega), np.sin(omega)
        alpha = sine / (2.0 * parameters.mid_q)
        b0 = 1 + alpha * amplitude
        b1 = -2 * cosine
        b2 = 1 - alpha * amplitude
        a0 = 1 + alpha / amplitude
        a1 = -2 * cosine
        a2 = 1 - alpha / amplitude
        numerator = np.array([b0, b1, b2], dtype=np.float64) / a0
        denominator = np.array([a0, a1, a2], dtype=np.float64) / a0
        return numerator, denominator