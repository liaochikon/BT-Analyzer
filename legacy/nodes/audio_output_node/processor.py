"""Headless audio output sink processor."""


class AudioOutputProcessor:
    """Bind a compiled render source to playback engine and transport owners."""

    def bind_source(self, input_pin, *, build_source, engine, transport) -> bool:
        render, length = build_source(input_pin)
        if render is None or length <= 0:
            engine.set_source(None, 0)
            transport.set_waveform(None)
            transport.set_waveform_source(None, 0)
            transport.stop()
            return False

        engine.set_source(render, length)
        transport.set_waveform_source(render, length)
        return True
