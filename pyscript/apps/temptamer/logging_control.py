from __future__ import annotations

import logging

from .config import TEMPTAMER_LOGGING_CATEGORIES
from .constants import LOGGER_NAME


_PREFIX_CATEGORIES = {
    "COMFORT ADJUSTMENT:": "comfort_adjustment",
    "COMFORT DIAGNOSTICS:": "comfort_adjustment_diagnostics",
    "FAN BOOST:": "fan_boost",
    "POWERDAY:": "powerday",
    "POWEROFF:": "poweroff",
    "ZONES:": "zones",
    "DISPATCH:": "dispatch",
    "SETPOINT:": "setpoint",
}


class _TempTamerCategoryFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        category = "lifecycle"
        for prefix, candidate_category in _PREFIX_CATEGORIES.items():
            if message.startswith(prefix):
                category = candidate_category
                break
        return bool(TEMPTAMER_LOGGING_CATEGORIES.get(category, True))


def install_temptamer_log_filter() -> None:
    """Install one filter on TempTamer's shared logger across all modules."""
    logger = logging.getLogger(LOGGER_NAME)
    if getattr(logger, "_temptamer_category_filter_installed", False):
        return
    logger.addFilter(_TempTamerCategoryFilter())
    logger._temptamer_category_filter_installed = True
