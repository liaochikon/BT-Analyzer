"""Central Nerd Font icon glyphs for the whole UI.

Every icon is defined by its Unicode CODEPOINT via ``chr(...)`` -- never as a raw private-use
character pasted into the source. Raw PUA glyphs look invisible in most editors/terminals and get
silently eaten by some copy / tar / encoding round-trips (which is exactly how the transport
icons went missing once). Defining them as ``chr(0xF04B)`` keeps the source plain-ASCII and
100% copy-safe while producing the identical glyph at import time.

All codepoints below were verified present in the bundled merged font
(``fonts/sarasa-gothic-tc-nerd-merged.ttf``). Names in comments are the Nerd Font glyph names
(``fa-`` = Font Awesome, ``md-`` = Material Design Icons). To add an icon: look it up at
nerdfonts.com, confirm it's in the merged font (``tools/check_font_icons.py``), add a line here.

Usage:  ``from ui import icons``  then  ``dpg.add_button(label=icons.PLAY)``.
"""

# --- Transport / playback ---
PLAY        = chr(0xF04B)   # fa-play
PAUSE       = chr(0xF04C)   # fa-pause
STOP        = chr(0xF04D)   # fa-stop
RECORD      = chr(0xF111)   # fa-circle
SKIP_NEXT   = chr(0xF051)   # fa-step-forward
SKIP_PREV   = chr(0xF048)   # fa-step-backward
FFORWARD    = chr(0xF04E)   # fa-forward
REWIND      = chr(0xF04A)   # fa-backward
LOOP        = chr(0xF01C9)  # md-ab_testing (A/B loop)
REPEAT_ONE  = chr(0xF0458)  # md-repeat_once
SHUFFLE     = chr(0xF074)   # fa-random

# --- Beat grid / metronome / snap ---
COUNTIN     = chr(0xF1ADC)  # md-timer_music
METRONOME   = chr(0xF07DA)  # md-metronome
SNAP        = chr(0xF0347)  # md-magnet
CLEAR       = chr(0xF0F09)  # md-map_marker_remove (clear A/B)

# --- Audio ---
MUSIC       = chr(0xF001)   # fa-music
SLIDERS     = chr(0xF1DE)   # fa-sliders
WAVEFORM    = SLIDERS        # alias of SLIDERS (used for waveform/mixer)
EQUALIZER   = chr(0xF0EA2)  # md-equalizer
MICROPHONE  = chr(0xF130)   # fa-microphone
HEADPHONES  = chr(0xF025)   # fa-headphones
GUITAR      = chr(0xF0F5B)  # md-guitar_electric
PIANO       = chr(0xF0678)  # md-piano
VOLUME      = chr(0xF028)   # fa-volume-up
MUTE        = chr(0xF026)   # fa-volume-off
VOLUME_OFF  = chr(0xF466)   # fa-volume-mute

# --- Edit ---
UNDO        = chr(0xF0E2)   # fa-undo (also the reset-to-default buttons)
REDO        = chr(0xF01E)   # fa-repeat
ADD         = chr(0xF067)   # fa-plus
REMOVE      = chr(0xF068)   # fa-minus
PLUS_CIRCLE = chr(0xF055)   # fa-plus-circle
MINUS_CIRCLE = chr(0xF056)  # fa-minus-circle
TRASH       = chr(0xF014)   # fa-trash-o (the bin/delete icon)
EDIT        = chr(0xF044)   # fa-pencil
COPY        = chr(0xF0C5)   # fa-copy
CUT         = chr(0xF0C4)   # fa-scissors
PASTE       = chr(0xF0EA)   # fa-paste
SAVE        = chr(0xF0C7)   # fa-floppy
CLOSE       = chr(0xF00D)   # fa-times
CHECK       = chr(0xF00C)   # fa-check

# --- Files / IO ---
FOLDER      = chr(0xF07B)   # fa-folder
FILE        = chr(0xF15B)   # fa-file
FILE_AUDIO  = chr(0xF1C7)   # fa-file-audio
IMPORT      = chr(0xF019)   # fa-download
EXPORT      = chr(0xF093)   # fa-upload
REFRESH     = chr(0xF021)   # fa-refresh
SEARCH      = chr(0xF002)   # fa-search

# --- Settings / status ---
SETTINGS    = chr(0xF013)   # fa-cog (alias GEAR)
GEAR        = chr(0xF013)   # fa-cog
POWER       = chr(0xF011)   # fa-power-off
WARNING     = chr(0xF071)   # fa-warning
ERROR       = chr(0xF06A)   # fa-exclamation-circle
INFO        = chr(0xF05A)   # fa-info-circle
CHECK_CIRCLE = chr(0xF058)  # fa-check-circle
EYE         = chr(0xF06E)   # fa-eye
EYE_OFF     = chr(0xF070)   # fa-eye-slash
LOCK        = chr(0xF023)   # fa-lock
UNLOCK      = chr(0xF09C)   # fa-unlock

# --- Navigation / arrows ---
ARROW_LEFT   = chr(0xF060)  # fa-arrow-left
ARROW_RIGHT  = chr(0xF061)  # fa-arrow-right
ARROW_UP     = chr(0xF062)  # fa-arrow-up
ARROW_DOWN   = chr(0xF063)  # fa-arrow-down
CHEVRON_LEFT = chr(0xF053)  # fa-chevron-left
CHEVRON_RIGHT = chr(0xF054) # fa-chevron-right
