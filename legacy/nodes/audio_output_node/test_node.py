"""Audio Output node: the MM:SS time formatter shown in the status line.

The formatter itself was hoisted to the shared ``core.constants.format_mmss`` (it was copy-pasted in
three places); this test now pins the node's chosen form -- padded minutes, no hour rollover.
"""

from core.constants import format_mmss


def test_audio_output_processor_binds_and_clears_source():
    from nodes.audio_output_node.processor import AudioOutputProcessor

    class FakeEngine:
        source = None

        def set_source(self, render, length):
            self.source = (render, length)

    class FakeTransport:
        waveform = "unchanged"
        waveform_source = "unchanged"
        stopped = False

        def set_waveform(self, waveform):
            self.waveform = waveform

        def set_waveform_source(self, render, length):
            self.waveform_source = (render, length)

        def stop(self):
            self.stopped = True

    processor = AudioOutputProcessor()
    engine = FakeEngine()
    transport = FakeTransport()
    render = lambda start, frames: None

    assert processor.bind_source(
        "input", build_source=lambda pin: (render, 100),
        engine=engine, transport=transport)
    assert engine.source == (render, 100)
    assert transport.waveform_source == (render, 100)

    assert not processor.bind_source(
        "input", build_source=lambda pin: (None, 0),
        engine=engine, transport=transport)
    assert engine.source == (None, 0)
    assert transport.waveform is None
    assert transport.waveform_source == (None, 0)
    assert transport.stopped


def test_fmt_time():
    assert format_mmss(0) == "00:00"
    assert format_mmss(65) == "01:05"
    assert format_mmss(3599) == "59:59"
    # No hour rollover: minutes just keep counting past 60 (pins the current, intended behaviour).
    assert format_mmss(3661) == "61:01"
