import os
import json
import logging
from enum import Enum, auto


logger = logging.getLogger(__name__)

# Project root (one level above this ui/ package) so locale files resolve no matter where
# the app is launched from.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class LocaleCode(Enum):
    EN_US = auto()
    ZH_TW = auto()


def load_locale(locale_code: LocaleCode) -> dict | None:
    file_path = os.path.join(_PROJECT_ROOT, "locales", f"{locale_code.name}.json")
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            logger.exception("Failed to load locale '%s'", file_path)
            return None
    else:
        logger.warning("Locale file not found: '%s'", file_path)
        return None