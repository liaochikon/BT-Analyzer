"""Headless chord-chart segment preparation."""

from dataclasses import dataclass

from nodes._shared.midi_draw import chord_name_from_pitches


@dataclass(frozen=True, slots=True)
class ChordChartSegment:
    start: float
    end: float
    name: str


@dataclass(frozen=True, slots=True)
class ChordChartSnapshot:
    segments: tuple[ChordChartSegment, ...] = ()
    song_length: float = 0.0


class ChordChartProcessor:
    GAP_TOLERANCE = 0.12

    def build_snapshot(self, notes) -> ChordChartSnapshot:
        if notes is None or len(notes) == 0:
            return ChordChartSnapshot()

        buckets = {}
        for note in notes:
            key = (round(float(note["start"]), 4), round(float(note["end"]), 4))
            buckets.setdefault(key, []).append(int(note["pitch"]))

        segments = []
        song_length = 0.0
        for start, end in sorted(buckets):
            name = chord_name_from_pitches(buckets[(start, end)])
            if (segments and segments[-1].name == name
                    and abs(start - segments[-1].end) <= self.GAP_TOLERANCE):
                previous = segments[-1]
                segments[-1] = ChordChartSegment(previous.start, end, name)
            else:
                segments.append(ChordChartSegment(start, end, name))
            song_length = max(song_length, end)
        return ChordChartSnapshot(tuple(segments), song_length)