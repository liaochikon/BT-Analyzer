"""Shared MIDI-naming helpers (nodes/_shared/midi_draw.py): chord naming, note naming, chord root
pitch class. Pure functions used by several nodes -- no fixture needed."""

from nodes._shared.midi_draw import chord_name_from_pitches, chord_root_pitch_class, note_name


def test_chord_name_major_minor():
    assert chord_name_from_pitches([48, 52, 55]) == "C"      # C major triad
    assert chord_name_from_pitches([57, 60, 64]) == "Am"     # A minor triad
    assert chord_name_from_pitches([55, 59, 62]) == "G"
    assert chord_name_from_pitches([50, 53, 57]) == "Dm"


def test_chord_name_voicing_independent():
    # Octave/order of the same pitch classes must not change the name.
    assert chord_name_from_pitches([64, 55, 72]) == "C"      # E, G, C(up an octave) = C major
    assert chord_name_from_pitches([]) == "--"


def test_note_name_octaves():
    assert note_name(60) == "C4"
    assert note_name(45) == "A2"
    assert note_name(69) == "A4"


def test_chord_root_pitch_class():
    assert chord_root_pitch_class("C") == 0
    assert chord_root_pitch_class("Am") == 9          # quality suffix ignored -> root A
    assert chord_root_pitch_class("F#") == 6
    assert chord_root_pitch_class("F#m") == 6
    assert chord_root_pitch_class("Bb") == 10         # flat normalised to A# enharmonic
    assert chord_root_pitch_class("--") is None       # unnamed -> no colour
    assert chord_root_pitch_class("") is None
