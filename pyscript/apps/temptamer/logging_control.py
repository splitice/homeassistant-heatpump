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


def temptamer_log_category(message: object) -> str:
    """Classify an app-owned log message without registering a logging callback."""
    text = str(message)
    for prefix, category in _PREFIX_CATEGORIES.items():
        if text.startswith(prefix):
            return category
    return "lifecycle"


def is_temptamer_log_enabled(message: object) -> bool:
    return bool(TEMPTAMER_LOGGING_CATEGORIES.get(temptamer_log_category(message), True))


class TempTamerLogger:
    """Gate app logs before they enter Python's synchronous logging stack.

    PyScript turns methods used by ``logging.Filter`` into awaitable callables,
    but ``logging`` cannot await them. This wrapper is only called by PyScript
    app code, so no PyScript callable is registered as a Python logging hook.
    """

    def __init__(self, logger) -> None:
        self._logger = logger

    def debug(self, message: object, *args: object, **kwargs: object) -> None:
        if is_temptamer_log_enabled(message):
            self._logger.debug(message, *args, **kwargs)

    def info(self, message: object, *args: object, **kwargs: object) -> None:
        if is_temptamer_log_enabled(message):
            self._logger.info(message, *args, **kwargs)

    def warning(self, message: object, *args: object, **kwargs: object) -> None:
        if is_temptamer_log_enabled(message):
            self._logger.warning(message, *args, **kwargs)

    def error(self, message: object, *args: object, **kwargs: object) -> None:
        if is_temptamer_log_enabled(message):
            self._logger.error(message, *args, **kwargs)

    def exception(self, message: object, *args: object, **kwargs: object) -> None:
        if is_temptamer_log_enabled(message):
            self._logger.exception(message, *args, **kwargs)


def get_temptamer_logger() -> TempTamerLogger:
    return TempTamerLogger(logging.getLogger(LOGGER_NAME))


def install_temptamer_log_filter() -> None:
    """Remove the old PyScript-incompatible logging filter after a reload."""
    logger = logging.getLogger(LOGGER_NAME)
    legacy_filter_was_installed = bool(
        getattr(logger, "_temptamer_category_filter_installed", False)
    )
    for current_filter in list(logger.filters):
        # The previous implementation only marked the logger, not its filter
        # instance. In a PyScript reload that instance is an opaque evaluator
        # object, so the marker is the only reliable way to remove it.
        if legacy_filter_was_installed or current_filter.__class__.__name__ == "_TempTamerCategoryFilter":
            logger.removeFilter(current_filter)
    if hasattr(logger, "_temptamer_category_filter_installed"):
        delattr(logger, "_temptamer_category_filter_installed")
