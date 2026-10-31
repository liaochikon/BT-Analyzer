"""Two logging channels with one clear split:

  * The TERMINAL is the full debug log. Every record goes here, formatted uniformly and
    tagged with the file + line it came from. Modules emit to it with a plain
    ``logging.getLogger(__name__)`` (``logger.debug/info/warning/error``).
  * The on-screen LOG PANEL is the user prompt. Only messages the user actually needs to see
    go here, via :func:`append_log`. Those are also mirrored into the terminal so the debug
    log stays complete.

Rule of thumb: internal/diagnostic chatter -> ``logger.*`` (terminal only); something the
user should notice (a result, a warning, an error to act on) -> ``append_log`` (panel + terminal).
"""

import logging
from datetime import datetime
from pathlib import Path
import os

import dearpygui.dearpygui as dpg

import palette
from app_logging.dispatch import run_on_gui


logger = logging.getLogger(__name__)

# Unified terminal format: time | LEVEL | file:line | message
_CONSOLE_FORMAT = "%(asctime)s | %(levelname)-7s | %(filename)s:%(lineno)-4d | %(message)s"
_CONSOLE_DATEFMT = "%H:%M:%S"
_FILE_FORMAT = (
    "%(asctime)s | %(levelname)-7s | %(threadName)s | "
    "%(name)s | %(filename)s:%(lineno)d | %(message)s"
)
_FILE_DATEFMT = "%Y-%m-%d %H:%M:%S"
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SESSION_LOG_PATH: Path | None = None

# Third-party loggers that would otherwise flood the terminal.
_NOISY_LIBRARIES = ("numba", "matplotlib", "librosa", "PIL", "h5py", "asyncio")

# String level -> logging level, for the append_log() convenience API.
_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}

# On-screen panel colours per level (RGBA). Colour carries the severity so the line text itself
# can stay short. These are the log-panel views of the shared semantic palette (see palette.py);
# the panel uses full alpha (255).
_UI_COLORS = {
    "INFO": palette.rgba(palette.INFO_TEXT, 255),
    "WARNING": palette.rgba(palette.WARN_TEXT, 255),
    "ERROR": palette.rgba(palette.ERROR_TEXT, 255),
}
_UI_DEFAULT_COLOR = palette.rgba(palette.NEUTRAL, 255)


def setup_logging(level: int = logging.INFO, *, logs_dir=None) -> Path:
    """Install the single terminal handler with the unified, file-tagged format.

    Call once at startup (replaces ``logging.basicConfig``). The terminal is the debug log,
    so this owns the root handler; modules just grab a named logger and emit to it.
    """
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):  # idempotent: drop any pre-existing handler
        root.removeHandler(handler)
        handler.close()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(_CONSOLE_FORMAT, _CONSOLE_DATEFMT))
    root.addHandler(console_handler)

    directory = Path(logs_dir) if logs_dir is not None else _PROJECT_ROOT / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    global _SESSION_LOG_PATH
    _SESSION_LOG_PATH = directory / f"bt_analyzer_{timestamp}_{os.getpid()}.txt"
    file_handler = logging.FileHandler(_SESSION_LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(_FILE_FORMAT, _FILE_DATEFMT))
    root.addHandler(file_handler)

    for name in _NOISY_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)

    logger.info("Session log started: %s", _SESSION_LOG_PATH)
    return _SESSION_LOG_PATH


def session_log_path() -> Path | None:
    return _SESSION_LOG_PATH


def shutdown_logging() -> None:
    """Flush and close handlers without disabling future library logging configuration."""
    root = logging.getLogger()
    if _SESSION_LOG_PATH is not None:
        logger.info("Session log closed: %s", _SESSION_LOG_PATH)
    for handler in list(root.handlers):
        handler.flush()
        handler.close()
        root.removeHandler(handler)


def set_log_level(level: int) -> None:
    """Change terminal log verbosity at runtime by adjusting the root logger level."""
    logging.getLogger().setLevel(level)


def append_log(message: str, level: str = "INFO") -> None:
    """User-facing prompt: show it in the on-screen panel AND mirror it to the terminal.

    For terminal-only diagnostics the user does not need, use a module
    ``logging.getLogger(__name__)`` directly instead of this function.
    """
    level = level.upper()

    # Terminal: route through standard logging. stacklevel=2 makes file:line in the formatted
    # line point at the CALLER of append_log, not this module.
    logger.log(_LEVELS.get(level, logging.INFO), message, stacklevel=2)

    # Panel: one concise, colour-coded line. Routed through the UI dispatch queue so worker
    # threads (bake/compile/export) never touch DearPyGui directly.
    full_text = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
    color = _UI_COLORS.get(level, _UI_DEFAULT_COLOR)

    def _emit():
        if dpg.does_item_exist("log_scroll_region"):
            dpg.add_text(full_text, color=color, parent="log_scroll_region")
            dpg.set_y_scroll("log_scroll_region", -1.0)
    run_on_gui(_emit)


def clear_log_canvas():
    if dpg.does_item_exist("log_scroll_region"):
        children = dpg.get_item_children("log_scroll_region", slot=1)
        for child in children:
            dpg.delete_item(child)
        append_log("Log cleared.", "INFO")