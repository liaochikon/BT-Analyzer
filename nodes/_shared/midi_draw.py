"""Shared MIDI note-naming helpers.

``note_name`` (pitch -> "C4") and the ``PITCH_CLASSES`` tuple were duplicated verbatim across the
Guitar Fretboard, MIDI Piano Roll, and Chord Detection nodes. Centralised here so the naming
convention (and the octave numbering) lives in one place.

Octave numbering follows the common convention where MIDI 60 = C4 (``pitch // 12 - 1``).
"""

# Pitch-class names, C .. B (index = semitone offset from C).
PITCH_CLASSES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

# NOTE: per-pitch-class COLOURS are intentionally NOT shared here -- each view owns its own palette
# so it can tune saturation/brightness for its context (the fretboard's small dots vs the chord
# chart's large filled blocks want different intensities). Only naming lives in this module.


def pitch_class(pitch: int) -> str:
    """Name of the pitch class only (no octave), e.g. 60 -> 'C', 61 -> 'C#'."""
    return PITCH_CLASSES[pitch % 12]


def note_name(pitch: int) -> str:
    """Full note name with octave, e.g. 60 -> 'C4', 45 -> 'A2'."""
    return f"{PITCH_CLASSES[pitch % 12]}{pitch // 12 - 1}"


def chord_name_from_pitches(pitches) -> str:
    """Name a chord from the MIDI pitches sounding together (a triad -> 'C' / 'Am' / ...).

    Works on the pitch-class SET so voicing/octave don't matter. Recognises major (root, +4, +7)
    and minor (root, +3, +7) triads; the root is the pitch class that, taken as the root, makes
    the other classes line up as one of those interval patterns. Falls back to naming the lowest
    pitch class when no triad matches (e.g. a partial or extended chord), so the label is never
    blank. Matches how ChordDetection encodes chords, but works for any triad MIDI source.
    """
    classes = sorted({int(p) % 12 for p in pitches})
    if not classes:
        return "--"
    MAJOR = frozenset((0, 4, 7))
    MINOR = frozenset((0, 3, 7))
    for root in classes:
        rel = frozenset((c - root) % 12 for c in classes)
        if rel == MAJOR:
            return PITCH_CLASSES[root]
        if rel == MINOR:
            return PITCH_CLASSES[root] + "m"
    # No clean triad: label by the lowest sounding pitch class.
    return PITCH_CLASSES[min(int(p) % 12 for p in pitches)]


def chord_root_pitch_class(name: str):
    """Pitch class (0..11) of a chord name's ROOT, e.g. 'C' -> 0, 'F#m' -> 6, 'Am' -> 9.

    Reads the leading note letter + optional accidental (the quality suffix like 'm' is ignored).
    Returns None for a blank/unnamed segment ('--'), so callers can fall back to a neutral colour.
    """
    if not name or name[0] not in "ABCDEFG":
        return None
    root = name[0]
    if len(name) > 1 and name[1] in "#b":
        root += name[1]
        # Normalise a flat to its enharmonic sharp so it indexes into PITCH_CLASSES.
        if name[1] == "b":
            flat_to_sharp = {"Ab": "G#", "Bb": "A#", "Cb": "B", "Db": "C#", "Eb": "D#",
                             "Fb": "E", "Gb": "F#"}
            root = flat_to_sharp.get(root, root)
    return PITCH_CLASSES.index(root) if root in PITCH_CLASSES else None
