from __future__ import annotations

from math import floor, isfinite


AIRFLOW_REFERENCE_VENT_EQUIVALENTS = 9.0
AIRFLOW_REFERENCE_FAN_SCALE = 2.0
AIRFLOW_MAX_SCALED_FAN_LEVEL = 6


def _finite_positive(value: object | None) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) and parsed > 0.0 else None


def round_nearest_fan_level(value: float) -> int:
    """Round a positive fan level to nearest, with half levels rounded up."""
    return max(1, floor(value + 0.5))


def airflow_vent_scale(open_vent_equivalents: object | None) -> float:
    """Return the physical fan multiplier for the reported-open vent area."""
    vent_equivalents = _finite_positive(open_vent_equivalents)
    if vent_equivalents is None:
        return 0.0
    return AIRFLOW_REFERENCE_FAN_SCALE * vent_equivalents / AIRFLOW_REFERENCE_VENT_EQUIVALENTS


def scale_logical_fan_level(
    logical_fan_level: object | None,
    open_vent_equivalents: object | None,
) -> int:
    """Scale a logical fan level to a physical level for the open vent area."""
    logical_level = _finite_positive(logical_fan_level)
    vent_scale = airflow_vent_scale(open_vent_equivalents)
    if logical_level is None or vent_scale <= 0.0:
        return 1
    return min(
        AIRFLOW_MAX_SCALED_FAN_LEVEL,
        round_nearest_fan_level(logical_level * vent_scale),
    )


def effective_airflow_fan_level(
    physical_fan_level: object | None,
    open_vent_equivalents: object | None,
) -> float | None:
    """Return fan intensity normalized for the reported-open vent area."""
    physical_level = _finite_positive(physical_fan_level)
    vent_scale = airflow_vent_scale(open_vent_equivalents)
    if physical_level is None or vent_scale <= 0.0:
        return None
    return physical_level / vent_scale


def infer_logical_fan_level(
    physical_fan_level: object | None,
    open_vent_equivalents: object | None,
) -> int | None:
    """Invert vent scaling for the low/high fan hysteresis state."""
    effective_level = effective_airflow_fan_level(
        physical_fan_level,
        open_vent_equivalents,
    )
    if effective_level is None:
        return None
    return round_nearest_fan_level(effective_level)
