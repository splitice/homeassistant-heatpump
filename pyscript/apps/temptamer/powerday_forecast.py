from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from math import isfinite

from .config import (
    POWERDAY_DRY_ABORT_MIN_ZONE_CELSIUS,
    POWERDAY_DRY_CONDITIONAL_INDOOR_HUMIDITY_THRESHOLD,
    POWERDAY_DRY_FORECAST_HUMIDITY_END_TIME,
    POWERDAY_DRY_FORECAST_HUMIDITY_START_TIME,
    POWERDAY_DRY_FORECAST_HUMIDITY_THRESHOLD,
    POWERDAY_DRY_START_MIN_ZONE_CELSIUS,
    POWERDAY_DRY_UNCONDITIONAL_INDOOR_HUMIDITY_THRESHOLD,
    POWERDAY_FORECAST_DAY_END_TIME,
    POWERDAY_FORECAST_DAY_START_TIME,
    POWERDAY_FORECAST_EVENING_END_TIME,
    POWERDAY_FORECAST_EVENING_START_TIME,
    POWERDAY_FORECAST_FULL_DAY_MAX_CELSIUS,
    POWERDAY_FORECAST_FULL_EVENING_MIN_CELSIUS,
    POWERDAY_FORECAST_SUPPRESSED_DAY_MIN_CELSIUS,
    POWERDAY_FORECAST_SUPPRESSED_EVENING_MIN_CELSIUS,
)
from .constants import (
    COMFORT_MODE_POWER_DAY,
    CONTROL_HVAC_MODE_COOL,
    CONTROL_HVAC_MODE_HEAT,
    CONTROL_HVAC_MODE_HEATCOOL,
    HVAC_DRY,
    POWERDAY_HEATSOAK_FULL,
    POWERDAY_HEATSOAK_REDUCED,
    POWERDAY_HEATSOAK_SUPPRESSED,
)
from .idle_demand_forecast import WeatherForecastPoint
from .models import DemandSnapshot


@dataclass(frozen=True)
class PowerDayForecastAssessment:
    generated_at: datetime
    level: str
    daytime_peak_celsius: float | None
    evening_minimum_celsius: float | None
    evening_maximum_humidity: float | None
    source: str
    reason: str


@dataclass(frozen=True)
class DehumidificationDecision:
    eligible: bool
    humidity_eligible: bool
    coldest_enabled_zone_celsius: float | None
    reason: str


def _finite_float(value: object | None) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _local_point_time(point: WeatherForecastPoint, now: datetime) -> datetime | None:
    point_at = point.at
    if not isinstance(point_at, datetime):
        return None
    if point_at.tzinfo is None or point_at.tzinfo.utcoffset(point_at) is None:
        if now.tzinfo is None:
            return point_at
        return point_at.replace(tzinfo=now.tzinfo)
    if now.tzinfo is None:
        return point_at.replace(tzinfo=None)
    return point_at.astimezone(now.tzinfo)


def assess_powerday_forecast(
    points: Sequence[WeatherForecastPoint],
    *,
    now: datetime,
    current_outdoor_temperature: float | None = None,
) -> PowerDayForecastAssessment:
    """Classify today's zero-price heatsoak from daytime and evening forecasts."""
    daytime_temperatures: list[float] = []
    evening_temperatures: list[float] = []
    evening_humidities: list[float] = []
    for point in points:
        point_at = _local_point_time(point, now)
        if point_at is None or point_at.date() != now.date():
            continue
        point_temperature = _finite_float(point.temperature)
        if (
            point_temperature is not None
            and POWERDAY_FORECAST_DAY_START_TIME <= point_at.time() <= POWERDAY_FORECAST_DAY_END_TIME
        ):
            daytime_temperatures.append(point_temperature)
        if POWERDAY_FORECAST_EVENING_START_TIME <= point_at.time() <= POWERDAY_FORECAST_EVENING_END_TIME:
            if point_temperature is not None:
                evening_temperatures.append(point_temperature)
        if (
            POWERDAY_DRY_FORECAST_HUMIDITY_START_TIME
            <= point_at.time()
            <= POWERDAY_DRY_FORECAST_HUMIDITY_END_TIME
        ):
            point_humidity = _finite_float(point.humidity)
            if point_humidity is not None and 0.0 <= point_humidity <= 100.0:
                evening_humidities.append(point_humidity)

    current_temperature = _finite_float(current_outdoor_temperature)
    if (
        current_temperature is not None
        and POWERDAY_FORECAST_DAY_START_TIME <= now.time() <= POWERDAY_FORECAST_DAY_END_TIME
    ):
        daytime_temperatures.append(current_temperature)

    daytime_peak = max(daytime_temperatures) if daytime_temperatures else None
    evening_minimum = min(evening_temperatures) if evening_temperatures else None
    evening_maximum_humidity = max(evening_humidities) if evening_humidities else None
    if daytime_peak is None or evening_minimum is None:
        return PowerDayForecastAssessment(
            generated_at=now,
            level=POWERDAY_HEATSOAK_FULL,
            daytime_peak_celsius=daytime_peak,
            evening_minimum_celsius=evening_minimum,
            evening_maximum_humidity=evening_maximum_humidity,
            source="unavailable",
            reason="forecast lacks daytime peak or evening minimum coverage; retaining full heatsoak",
        )

    if (
        daytime_peak < POWERDAY_FORECAST_FULL_DAY_MAX_CELSIUS
        or evening_minimum < POWERDAY_FORECAST_FULL_EVENING_MIN_CELSIUS
    ):
        level = POWERDAY_HEATSOAK_FULL
    elif (
        daytime_peak >= POWERDAY_FORECAST_SUPPRESSED_DAY_MIN_CELSIUS
        and evening_minimum >= POWERDAY_FORECAST_SUPPRESSED_EVENING_MIN_CELSIUS
    ):
        level = POWERDAY_HEATSOAK_SUPPRESSED
    else:
        level = POWERDAY_HEATSOAK_REDUCED

    return PowerDayForecastAssessment(
        generated_at=now,
        level=level,
        daytime_peak_celsius=daytime_peak,
        evening_minimum_celsius=evening_minimum,
        evening_maximum_humidity=evening_maximum_humidity,
        source="forecast",
        reason=(
            f"{level} heatsoak: daytime peak {daytime_peak:.1f}C; "
            f"evening minimum {evening_minimum:.1f}C"
        ),
    )


def _supports_dry_mode(supported_hvac_modes: Iterable[object] | None) -> bool:
    if supported_hvac_modes is None:
        return False
    for mode in supported_hvac_modes:
        if str(mode).strip().lower() == HVAC_DRY:
            return True
    return False


def _coldest_enabled_zone(snapshot: DemandSnapshot) -> float | None:
    coldest: float | None = None
    for zone in snapshot.zones.values():
        if not zone.is_enabled_by_mode:
            continue
        candidates = [zone.current_temp]
        if zone.min_temp is not None:
            candidates.append(zone.min_temp)
        for candidate in candidates:
            parsed = _finite_float(candidate)
            if parsed is not None and (coldest is None or parsed < coldest):
                coldest = parsed
    return coldest


def resolve_powerday_dehumidification(
    snapshot: DemandSnapshot,
    assessment: PowerDayForecastAssessment,
    *,
    indoor_humidity: float | None,
    supported_hvac_modes: Iterable[object] | None,
    has_thermal_demand: bool,
    heat_forecast_safe: bool,
    cool_forecast_safe: bool,
    cycle_completed: bool,
    currently_active: bool,
) -> DehumidificationDecision:
    """Resolve whether forecast-aware PowerDay may request dry mode."""
    parsed_humidity = _finite_float(indoor_humidity)
    if parsed_humidity is not None and not 0.0 <= parsed_humidity <= 100.0:
        parsed_humidity = None
    forecast_humidity = _finite_float(assessment.evening_maximum_humidity)
    if forecast_humidity is not None and not 0.0 <= forecast_humidity <= 100.0:
        forecast_humidity = None
    forecast_humidity_high = (
        forecast_humidity is not None
        and forecast_humidity > POWERDAY_DRY_FORECAST_HUMIDITY_THRESHOLD
    )
    humidity_eligible = parsed_humidity is not None and (
        parsed_humidity > POWERDAY_DRY_UNCONDITIONAL_INDOOR_HUMIDITY_THRESHOLD
        or (
            parsed_humidity > POWERDAY_DRY_CONDITIONAL_INDOOR_HUMIDITY_THRESHOLD
            and forecast_humidity_high
        )
    )
    coldest_zone = _coldest_enabled_zone(snapshot)

    if snapshot.comfort_mode != COMFORT_MODE_POWER_DAY:
        reason = "comfort mode is not PowerDay"
    elif not snapshot.free_power_available:
        reason = "free power is not available"
    elif snapshot.free_power_heat_soak_level != POWERDAY_HEATSOAK_SUPPRESSED:
        reason = f"heatsoak level is {snapshot.free_power_heat_soak_level}"
    elif not _supports_dry_mode(supported_hvac_modes):
        reason = "climate entity does not advertise dry mode"
    elif parsed_humidity is None:
        reason = "indoor humidity is unavailable"
    elif not humidity_eligible:
        reason = (
            f"humidity gate is false: indoor {parsed_humidity:.1f}%; "
            f"evening forecast {forecast_humidity if forecast_humidity is not None else 'unknown'}%"
        )
    elif has_thermal_demand:
        reason = "normal heating or cooling demand takes priority"
    elif cycle_completed:
        reason = "dry cycle is already complete for this free-power period"
    elif coldest_zone is None:
        reason = "no enabled zone temperature is available"
    elif currently_active and coldest_zone <= POWERDAY_DRY_ABORT_MIN_ZONE_CELSIUS:
        reason = (
            f"coldest enabled zone {coldest_zone:.1f}C <= "
            f"abort floor {POWERDAY_DRY_ABORT_MIN_ZONE_CELSIUS:.1f}C"
        )
    elif not currently_active and coldest_zone < POWERDAY_DRY_START_MIN_ZONE_CELSIUS:
        reason = (
            f"coldest enabled zone {coldest_zone:.1f}C < "
            f"start floor {POWERDAY_DRY_START_MIN_ZONE_CELSIUS:.1f}C"
        )
    elif snapshot.selected_hvac_mode == CONTROL_HVAC_MODE_HEAT and not heat_forecast_safe:
        reason = "heating demand is predicted within 30 minutes"
    elif snapshot.selected_hvac_mode == CONTROL_HVAC_MODE_COOL and not cool_forecast_safe:
        reason = "cooling demand is predicted within 30 minutes"
    elif snapshot.selected_hvac_mode == CONTROL_HVAC_MODE_HEATCOOL and not (
        heat_forecast_safe and cool_forecast_safe
    ):
        reason = "heating or cooling demand is predicted within 30 minutes"
    elif snapshot.selected_hvac_mode not in {
        CONTROL_HVAC_MODE_HEAT,
        CONTROL_HVAC_MODE_COOL,
        CONTROL_HVAC_MODE_HEATCOOL,
    }:
        reason = f"hvac selector {snapshot.selected_hvac_mode} does not allow automatic dry mode"
    else:
        return DehumidificationDecision(
            eligible=True,
            humidity_eligible=True,
            coldest_enabled_zone_celsius=coldest_zone,
            reason=(
                f"dry eligible: indoor {parsed_humidity:.1f}%; "
                f"evening forecast {forecast_humidity if forecast_humidity is not None else 'unknown'}%"
            ),
        )

    return DehumidificationDecision(
        eligible=False,
        humidity_eligible=humidity_eligible,
        coldest_enabled_zone_celsius=coldest_zone,
        reason=reason,
    )
