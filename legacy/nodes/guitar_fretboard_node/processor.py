"""Headless guitar fretboard position and fingering processor."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FretboardParameters:
    num_frets: int
    prefer_position: float
    allow_open: bool


@dataclass(frozen=True, slots=True)
class FretboardLayers:
    all_positions: tuple[tuple[int, int, int], ...]
    emphasized: tuple[tuple[int, int, int], ...]
    hand_shape: tuple[tuple[int, int, int], ...]
    anchor: int


class GuitarFretboardProcessor:
    OPEN_STRINGS = (64, 59, 55, 50, 45, 40)
    HARMONIC_INTERVAL_TO_FRET = {12: 12, 19: 7, 24: 5}
    HAND_SPAN = 4
    HAND_SMOOTH = 0.30

    def __init__(self):
        self.hand_center = 5.0

    def reset_hand(self) -> None:
        self.hand_center = 5.0

    def advance_hand(self, anchor: int) -> None:
        self.hand_center += self.HAND_SMOOTH * (anchor - self.hand_center)

    @staticmethod
    def active_notes(notes, time_seconds: float) -> dict[int, int]:
        active = {}
        if notes is None:
            return active
        for note in notes:
            if float(note["start"]) <= time_seconds < float(note["end"]):
                pitch = int(note["pitch"])
                velocity = (
                    int(note["velocity"])
                    if "velocity" in note.dtype.names else 127
                )
                active[pitch] = max(active.get(pitch, 0), velocity)
        return active

    def press_positions(self, pitch: int, parameters: FretboardParameters,
                        *, allow_open: bool | None = None):
        include_open = parameters.allow_open if allow_open is None else allow_open
        minimum_fret = 0 if include_open else 1
        return [
            (index, pitch - open_pitch)
            for index, open_pitch in enumerate(self.OPEN_STRINGS)
            if minimum_fret <= pitch - open_pitch <= parameters.num_frets
        ]

    def harmonic_positions(self, pitch: int, parameters: FretboardParameters):
        positions = []
        for index, open_pitch in enumerate(self.OPEN_STRINGS):
            touch_fret = self.HARMONIC_INTERVAL_TO_FRET.get(pitch - open_pitch)
            if touch_fret is not None and touch_fret <= parameters.num_frets:
                positions.append((index, touch_fret))
        return positions

    def pick_window(self, unique_pitches, parameters: FretboardParameters) -> int:
        minimum_anchor = 0 if parameters.allow_open else 1
        maximum_anchor = max(
            minimum_anchor, parameters.num_frets - self.HAND_SPAN)
        if parameters.prefer_position > 0:
            return int(max(
                minimum_anchor,
                min(maximum_anchor, round(parameters.prefer_position) - 1),
            ))
        if self.hand_center > 0:
            return int(max(
                minimum_anchor, min(maximum_anchor, round(self.hand_center))))

        def coverage(anchor):
            return sum(
                any(
                    anchor <= fret <= anchor + self.HAND_SPAN
                    for _, fret in self.press_positions(pitch, parameters)
                )
                for pitch in unique_pitches
            )

        return max(
            range(minimum_anchor, maximum_anchor + 1),
            key=lambda anchor: (coverage(anchor), -anchor),
        )

    def assign_layers(self, active: dict,
                      parameters: FretboardParameters) -> FretboardLayers:
        unique_pitches = sorted(active)
        anchor = self.pick_window(unique_pitches, parameters)
        low, high = anchor, anchor + self.HAND_SPAN

        all_positions = []
        emphasized = []
        for pitch in unique_pitches:
            every_position = self.press_positions(
                pitch, parameters, allow_open=True)
            all_positions.extend(
                (pitch, string, fret) for string, fret in every_position)
            candidates = self.press_positions(pitch, parameters) or every_position
            if not candidates:
                continue
            in_window = [
                (string, fret)
                for string, fret in candidates
                if low <= fret <= high
            ]
            if in_window:
                string, fret = min(
                    in_window, key=lambda candidate: (
                        abs(candidate[1] - anchor), candidate[0]))
            else:
                string, fret = min(
                    candidates, key=lambda candidate: (
                        self.window_distance(candidate[1], low, high), candidate[0]))
            emphasized.append((pitch, string, fret))

        hand_shape = self.hand_shape(emphasized, low, high)
        return FretboardLayers(
            tuple(all_positions), tuple(emphasized), tuple(hand_shape), anchor)

    @staticmethod
    def window_distance(fret, low, high) -> int:
        return 0 if low <= fret <= high else min(
            abs(fret - low), abs(fret - high))

    @staticmethod
    def hand_shape(emphasized, low, high):
        in_window = [
            (pitch, string, fret)
            for pitch, string, fret in emphasized
            if low <= fret <= high
        ]
        by_string = {}
        for pitch, string, fret in in_window:
            if string not in by_string or fret < by_string[string][2]:
                by_string[string] = (pitch, string, fret)
        return sorted(by_string.values(), key=lambda item: item[1])