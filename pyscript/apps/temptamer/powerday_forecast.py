from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from math import isfinite

from .config import (
    POWERDAY_DRY_ABORT_MIN_ZONE_CELSIUS,
    POWERDAY_DRY_CONDITIONAL_INDOOR_HUMIDITY_THRESHOLD,
    POWERDAY_DRY_COOLING_ENTRY_EXCESS_CELSIUS,
    POWERDAY_DRY_COOLING_EXIT_EXCESS_CELSIUS,
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
    entry_kind: str | None = None
    energy_eligible: bool = False
    energy_source: str | None = None
    cooling_excess_celsius: float | None = None
    cooling_threshold_celsius: float | None = None
    coldest_target_zone_celsius: float | None = None
    cooldown_active: bool = False


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


def _coldest_enabled_zone(
    snapshot: DemandSnapshot,
    zone_keys: Iterable[str] | None = None,
) -> float | None:
    coldest: float | None = None
    selected_zone_keys = set(zone_keys) if zone_keys is not None else None
    for zone_key, zone in snapshot.zones.items():
        if selected_zone_keys is not None:
            if zone_key not in selected_zone_keys:
                continue
        elif not zone.is_enabled_by_mode:
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
    cooling_demand: bool = False,
    cooling_excess_celsius: float = 0.0,
    cooling_zone_keys: Iterable[str] | None = None,
    active_entry_kind: str | None = None,
    cooldown_active: bool = False,
    cooling_exit_confirmed: bool = True,
) -> DehumidificationDecision:
    """Resolve idle dehumidification or low-demand cooling substitution."""
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
    substitution_selected = bool(
        cooling_demand
        and snapshot.selected_hvac_mode in {CONTROL_HVAC_MODE_COOL, CONTROL_HVAC_MODE_HEATCOOL}
    )
    entry_kind = "cooling_substitution" if substitution_selected else "idle"
    target_zone_keys = cooling_zone_keys if substitution_selected else None
    coldest_zone = _coldest_enabled_zone(snapshot, target_zone_keys)
    energy_sources: list[str] = []
    if snapshot.free_power_available:
        energy_sources.append("free_power")
    if snapshot.battery_free_power_boost_available:
        energy_sources.append("battery_latch")
    substitution_energy_eligible = bool(energy_sources)
    energy_source = "+".join(energy_sources) if energy_sources else None
    parsed_cooling_excess = _finite_float(cooling_excess_celsius)
    if parsed_cooling_excess is None:
        parsed_cooling_excess = 0.0
    cooling_threshold = (
        POWERDAY_DRY_COOLING_EXIT_EXCESS_CELSIUS
        if currently_active and active_entry_kind == "cooling_substitution"
        else POWERDAY_DRY_COOLING_ENTRY_EXCESS_CELSIUS
    )

    if snapshot.comfort_mode != COMFORT_MODE_POWER_DAY:
        reason = "comfort mode is not PowerDay"
    elif not _supports_dry_mode(supported_hvac_modes):
        reason = "climate entity does not advertise dry mode"
    elif parsed_humidity is None:
        reason = "indoor humidity is unavailable"
    elif not humidity_eligible:
        reason = (
            f"humidity gate is false: indoor {parsed_humidity:.1f}%; "
            f"evening forecast {forecast_humidity if forecast_humidity is not None else 'unknown'}%"
        )
    elif cycle_completed or cooldown_active:
        reason = "dry cycle cooldown is active"
    elif substitution_selected:
        if not substitution_energy_eligible:
            reason = "neither free power nor the battery boost latch is available"
        elif (
            parsed_cooling_excess >= cooling_threshold
            and not (
                currently_active
                and active_entry_kind == "cooling_substitution"
                and not cooling_exit_confirmed
            )
        ):
            reason = (
                f"cooling excess {parsed_cooling_excess:.2f}C >= "
                f"dry threshold {cooling_threshold:.2f}C"
            )
        elif snapshot.selected_hvac_mode == CONTROL_HVAC_MODE_HEATCOOL and not heat_forecast_safe:
            reason = "heating demand is predicted within 30 minutes"
        else:
            confirmation_status = (
                "; high-demand exit confirmation pending"
                if parsed_cooling_excess >= cooling_threshold
                else ""
            )
            return DehumidificationDecision(
                eligible=True,
                humidity_eligible=True,
                coldest_enabled_zone_celsius=coldest_zone,
                reason=(
                    f"cooling-substitution dry eligible: indoor {parsed_humidity:.1f}%; "
                    f"cooling excess {parsed_cooling_excess:.2f}C; energy {energy_source}"
                    f"{confirmation_status}"
                ),
                entry_kind=entry_kind,
                energy_eligible=True,
                energy_source=energy_source,
                cooling_excess_celsius=parsed_cooling_excess,
                cooling_threshold_celsius=cooling_threshold,
                coldest_target_zone_celsius=coldest_zone,
                cooldown_active=False,
            )
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
    elif not snapshot.free_power_available:
        reason = "free power is not available"
    elif snapshot.free_power_heat_soak_level != POWERDAY_HEATSOAK_SUPPRESSED:
        reason = f"heatsoak level is {snapshot.free_power_heat_soak_level}"
    elif has_thermal_demand:
        reason = "normal heating or cooling demand takes priority"
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
            entry_kind=entry_kind,
            energy_eligible=True,
            energy_source="free_power",
            coldest_target_zone_celsius=coldest_zone,
        )

    return DehumidificationDecision(
        eligible=False,
        humidity_eligible=humidity_eligible,
        coldest_enabled_zone_celsius=coldest_zone,
        reason=reason,
        entry_kind=entry_kind,
        energy_eligible=(
            substitution_energy_eligible if substitution_selected else snapshot.free_power_available
        ),
        energy_source=energy_source if substitution_selected else (
            "free_power" if snapshot.free_power_available else None
        ),
        cooling_excess_celsius=parsed_cooling_excess if substitution_selected else None,
        cooling_threshold_celsius=cooling_threshold if substitution_selected else None,
        coldest_target_zone_celsius=coldest_zone,
        cooldown_active=cycle_completed or cooldown_active,
    )
