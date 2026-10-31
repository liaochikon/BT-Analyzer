# Song Workflow And Node Guide

[繁體中文](song_workflow_and_nodes.zh-TW.md)

This guide explains how to learn a song with a preset and how to use every built-in node.

## Learn A Song With The Guitar Preset

The included `guitar.json` preset is the quickest way to analyse a guitar part. It already connects the nodes for stem separation, tempo and chord analysis, playback, mixing, transcription, and fretboard display.

### 1. Load the preset

Open the preset menu and load **Guitar**. Loading a preset creates the graph, but it does not choose a song or run the heavy analysis.

The important signal paths are:

```text
Audio Input -> Demucs -> Mixer -> Audio Output
            -> Beat / Tempo -> Beat Grid Output
            -> Beat / Tempo -> Chord Detection -> Chord Chart

Demucs Guitar -> Basic Pitch -> Guitar Fretboard
```

All six Demucs stems also feed separate Mixer channels, so you can mute, solo, or rebalance them during playback.

### 2. Choose a song

In **Audio Input**, choose an audio file. The app decodes the file and converts it to the project sample rate when the graph is baked.

For the first attempt, use a short section or a song with a clear arrangement. Source separation and transcription take longer on a full song.

### 3. Run Global Bake

Click **Global Bake**. Baked nodes run in graph order:

1. Audio Input loads the song.
2. Demucs creates drums, bass, other, vocals, guitar, and piano stems.
3. Beat / Tempo finds the beat grid and BPM.
4. Chord Detection uses the audio and detected beats to create a chord timeline.
5. Basic Pitch converts the isolated guitar stem to MIDI notes.
6. Display and playback nodes receive the completed results.

Heavy work runs in the background. The first Demucs run downloads the official model and can take longer. Watch the status area and Logger for progress or errors.

### 4. Practise the part

Press **Play**, then use the transport controls:

- Set **A** and **B** around the section you want to repeat.
- Enable loop playback.
- Lower the playback speed; pitch stays unchanged.
- Enable the beat grid or metronome when you need rhythmic guidance.
- Watch **Chord Chart** for harmony and **Guitar Fretboard** for note positions.
- Use Mixer **Solo** on the guitar stem to hear it clearly, or **Mute** it to practise over the remaining band.

Mixer and streaming-effect controls update during playback. Parameters on baked analysis nodes require another Bake or Global Bake.

### 5. Improve the result

- If the fretboard is too busy, raise Basic Pitch thresholds or narrow its frequency range.
- If chord changes are too coarse, increase Chord Detection granularity and bake again.
- If separation contains artifacts, increase Demucs shifts for higher-quality but slower inference.
- Use EQ or Gain after a stem when you need to make one instrument easier to hear.
- Use the fretboard's Suggested mode and Preferred Position to reduce impractical fingerings.

### 6. Save your workspace

Save a new preset after arranging the graph and panels. Save **with parameters** when you also want node settings, selected paths, playback speed, and loop state restored. Avoid distributing presets that contain personal absolute file paths.

## Processing Rules

BT Analyzer has two practical node types:

- **Baked nodes** process the complete song. After changing their inputs or parameters, run Bake again.
- **Streaming nodes** process audio while it plays. Their controls usually update immediately.

Display nodes show data but do not change it. Output nodes play or write data and normally end a signal chain.

## Built-In Node Reference

### Input, Output, And Conversion

| Node | Connect | How to use it | Result |
|---|---|---|---|
| **Audio Input** | No input | Choose an audio file, then Bake. | Channel-first audio for the rest of the graph. |
| **Audio Output** | Audio in | Connect the final mix. Only one Audio Output should be used. | Sends audio to the player and sound device. |
| **Audio Export** | Audio in | Choose a `.wav` destination before Bake. | Writes the complete input audio to a WAV file. |
| **MIDI Export** | MIDI in | Choose a `.mid` destination before Bake. | Writes notes or chord events to a MIDI file. |
| **MIDI to Audio** | MIDI in | Connect MIDI and Bake. | Produces simple synthesized audio that can be mixed or exported. |
| **Beat Grid Output** | Tempo and time offset in | Connect both outputs from Beat / Tempo. | Updates the transport beat grid and snapping reference. |

### Separation And Analysis

| Node | Connect | Main controls | How to use it |
|---|---|---|---|
| **Demucs v6** | Mixed audio in | Split, Shifts, Overlap, Segment | Bake to create six stems. More shifts can improve consistency but increases processing time. The model downloads on first use. |
| **BS-RoFormer** | Mixed audio in | Model | Bake to create vocal and instrumental stems. The selected checkpoint downloads on first use. |
| **Beat / Tempo** | Audio in | No required controls | Bake to produce BPM, beat offset, and beat MIDI. Feed its MIDI into Chord Detection and tempo/offset into Beat Grid Output. |
| **Chord Detection** | Audio and beat MIDI in | Granularity | Bake after Beat / Tempo. It produces chord MIDI and a key label. It needs at least two detected beats. |
| **Basic Pitch** | Audio in | Melodia Trick, onset/frame thresholds, minimum note length, frequency range | Prefer an isolated instrument stem. Bake to produce MIDI notes. Raise thresholds to remove false notes. |
| **Audio Velocity Map** | MIDI and matching audio in | Band, Harmonics, Dynamic Range, Gain | Bake to rewrite MIDI velocities from the energy of corresponding pitches in the audio. |

### Mixing And Effects

| Node | Processing | Main controls | How to use it |
|---|---|---|---|
| **Mixer** | Streaming | Eight volume faders, Mute, Solo, labels | Connect up to eight audio signals. Use one channel per stem and route Master Out to Audio Output. |
| **Gain** | Streaming | Gain, Bypass | Place it in an audio chain for simple level adjustment. |
| **EQ** | Streaming | Low shelf, mid peak, high shelf | Shape one stem or the full mix. The panel shows the response and live spectrum. |
| **Compressor** | Streaming | Threshold, Ratio, Makeup, Attack, Release | Reduce dynamic range. Start with a moderate ratio and lower the threshold until gain reduction appears. |
| **Limiter** | Streaming | Pre Gain, Output Gain, Ceiling, Attack, Release | Place near the end of a chain to prevent peaks above the ceiling. |
| **Noise Gate** | Streaming | Threshold, Attack, Hold, Release, Floor | Reduce low-level noise between phrases. Set the threshold just above the unwanted noise. |
| **Pitch Shift** | Baked | Semitones | Bake to transpose audio while keeping approximately the same duration. |
| **Time Stretch** | Baked | Speed | Bake to change duration while preserving pitch. Transport speed is usually more convenient for normal practice. |

### Displays

| Node | Connect | Main controls | What it shows |
|---|---|---|---|
| **Chord Chart** | Chord MIDI in | Visible time window | A scrolling chord timeline synchronized to the playhead. |
| **Guitar Fretboard** | MIDI in | Frets, velocity and pitch filters, fingering mode, alternatives, open strings, harmonics, preferred position | Current MIDI notes and possible guitar positions. Suggested mode gives a less crowded fingering. |
| **MIDI Piano Roll** | MIDI in | Plot pan and zoom | MIDI notes over time and pitch. |
| **Spectrum** | Audio in and passthrough out | No required controls | A live frequency spectrum. Insert it anywhere in an audio chain without changing the signal. |
| **Oscilloscope** | Audio in and passthrough out | No required controls | The latest waveform samples. It also passes audio through unchanged. |

## Common Problems

- **A baked node says it has no input:** check the pin connections, then run Global Bake again.
- **Chord Detection fails:** connect Beat / Tempo's MIDI output to its beat input and make sure beats were detected.
- **No sound:** connect the final audio chain to Audio Output, confirm Mixer channels are not muted, and check the selected audio device.
- **An export node fails:** choose an output path before baking it.
- **AI reports a missing dependency:** install either `requirements-ai-cpu.txt` or `requirements-ai-gpu.txt` in `bt_env`.
- **The first AI run appears slow:** Demucs and BS-RoFormer may be downloading model weights.
- **A parameter changed but the result did not:** baked nodes need Bake; streaming nodes update during playback.
