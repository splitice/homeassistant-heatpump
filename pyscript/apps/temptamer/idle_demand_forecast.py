from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite

from .config import (
    IDLE_DEMAND_FORECAST_COOL_BASE_DRIFT_CELSIUS_PER_HOUR,
    IDLE_DEMAND_FORECAST_COOL_REFERENCE_INDOOR_CELSIUS,
    IDLE_DEMAND_FORECAST_COOL_REFERENCE_OUTDOOR_CELSIUS,
    IDLE_DEMAND_FORECAST_HEAT_BASE_DRIFT_CELSIUS_PER_HOUR,
    IDLE_DEMAND_FORECAST_HEAT_REFERENCE_INDOOR_CELSIUS,
    IDLE_DEMAND_FORECAST_HEAT_REFERENCE_OUTDOOR_CELSIUS,
)
from .constants import HVAC_COOL, HVAC_HEAT
from .models import DemandSnapshot


@dataclass(frozen=True)
class WeatherForecastPoint:
    at: datetime
    temperature: float | None
    condition: str | None = None
    humidity: float | None = None


@dataclass(frozen=True)
class IdleDemandForecast:
    generated_at: datetime | None
    horizon_seconds: int
    earliest_demand_at: datetime | None
    earliest_zone_key: str | None
    operation_mode: str | None
    safe_to_turn_off: bool
    source: str
    reason: str

    @property
    def earliest_demand_seconds(self) -> float | None:
        if self.generated_at is None or self.earliest_demand_at is None:
            return None
        return (self.earliest_demand_at - self.generated_at).total_seconds()


ForecastAdjustmentProvider = Callable[[datetime, float | None, str | None], Mapping[str, float] | None]


def _parse_float(value: object | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _normalize_datetime(value: object | None) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_hourly_weather_forecast(response: object, weather_entity_id: str) -> tuple[WeatherForecastPoint, ...]:
    """Extract usable Home Assistant hourly forecast entries from a service response."""
    if not isinstance(response, Mapping):
        return ()
    entity_response = response.get(weather_entity_id)
    if not isinstance(entity_response, Mapping):
        return ()
    raw_forecast = entity_response.get("forecast")
    if not isinstance(raw_forecast, Sequence) or isinstance(raw_forecast, (str, bytes)):
        return ()

    points: list[WeatherForecastPoint] = []
    for raw_point in raw_forecast:
        if not isinstance(raw_point, Mapping):
            continue
        at = _normalize_datetime(raw_point.get("datetime"))
        if at is None:
            continue
        raw_condition = raw_point.get("condition")
        condition = str(raw_condition) if raw_condition is not None else None
        points.append(
            WeatherForecastPoint(
                at=at,
                temperature=_parse_float(raw_point.get("temperature")),
                condition=condition,
                humidity=_parse_float(raw_point.get("humidity")),
            )
        )
    points.sort(key=lambda point: point.at)
    return tuple(points)


def forecast_temperature_at(
    points: Sequence[WeatherForecastPoint],
    *,
    now: datetime,
    at: datetime,
    current_outdoor_temperature: float | None,
) -> float | None:
    """Linearly interpolate forecast temperature, anchored by the current reading."""
    normalized_now = _normalize_datetime(now)
    normalized_at = _normalize_datetime(at)
    if normalized_now is None or normalized_at is None:
        return current_outdoor_temperature

    anchors: list[WeatherForecastPoint] = []
    for point in points:
        normalized_point_at = _normalize_datetime(point.at)
        if normalized_point_at is not None and point.temperature is not None:
            anchors.append(
                WeatherForecastPoint(
                    normalized_point_at,
                    point.temperature,
                    point.condition,
                    point.humidity,
                )
            )
    if current_outdoor_temperature is not None:
        anchors.append(WeatherForecastPoint(normalized_now, current_outdoor_temperature))
    anchors.sort(key=lambda point: point.at)
    if not anchors:
        return None

    previous: WeatherForecastPoint | None = None
    following: WeatherForecastPoint | None = None
    for point in anchors:
        if point.at <= normalized_at:
            previous = point
            continue
        following = point
        break

    if previous is None:
        return anchors[0].temperature
    if following is None or following.temperature is None or previous.temperature is None:
        return previous.temperature
    interval_seconds = (following.at - previous.at).total_seconds()
    if interval_seconds <= 0.0:
        return previous.temperature
    elapsed_seconds = (normalized_at - previous.at).total_seconds()
    fraction = max(0.0, min(1.0, elapsed_seconds / interval_seconds))
    return previous.temperature + (following.temperature - previous.temperature) * fraction


def forecast_condition_at(
    points: Sequence[WeatherForecastPoint],
    *,
    at: datetime,
    current_condition: str | None,
) -> str | None:
    """Use the first future hourly condition, falling back to the current condition."""
    normalized_at = _normalize_datetime(at)
    if normalized_at is None:
        return current_condition
    last_condition = current_condition
    for point in points:
        normalized_point_at = _normalize_datetime(point.at)
        if normalized_point_at is None or point.condition is None:
            continue
        if normalized_point_at >= normalized_at:
            return point.condition
        last_condition = point.condition
    return last_condition


def _drift_celsius(
    temperature: float,
    outdoor_temperature: float | None,
    operation_mode: str,
    step_seconds: int,
) -> float:
    hours = max(0, step_seconds) / 3600.0
    if operation_mode == HVAC_HEAT:
        if outdoor_temperature is None:
            rate = IDLE_DEMAND_FORECAST_HEAT_BASE_DRIFT_CELSIUS_PER_HOUR
        else:
            reference_gap = (
                IDLE_DEMAND_FORECAST_HEAT_REFERENCE_INDOOR_CELSIUS
                - IDLE_DEMAND_FORECAST_HEAT_REFERENCE_OUTDOOR_CELSIUS
            )
            rate = (
                IDLE_DEMAND_FORECAST_HEAT_BASE_DRIFT_CELSIUS_PER_HOUR
                * max(0.0, temperature - outdoor_temperature)
                / reference_gap
            )
        return -rate * hours

    if outdoor_temperature is None:
        rate = IDLE_DEMAND_FORECAST_COOL_BASE_DRIFT_CELSIUS_PER_HOUR
    else:
        reference_gap = (
            IDLE_DEMAND_FORECAST_COOL_REFERENCE_OUTDOOR_CELSIUS
            - IDLE_DEMAND_FORECAST_COOL_REFERENCE_INDOOR_CELSIUS
        )
        rate = (
            IDLE_DEMAND_FORECAST_COOL_BASE_DRIFT_CELSIUS_PER_HOUR
            * max(0.0, outdoor_temperature - temperature)
            / reference_gap
        )
    return rate * hours


def _forecast_adjustment(
    zone_key: str,
    current_adjustment: float,
    adjustments: Mapping[str, float] | None,
) -> float:
    if adjustments is None:
        return current_adjustment
    candidate = _parse_float(adjustments.get(zone_key))
    return current_adjustment if candidate is None else candidate


def _heat_calling(
    *,
    primary_temperature: float,
    minimum_temperature: float | None,
    enable_threshold: float,
    ideal_threshold: float,
) -> bool:
    if primary_temperature < enable_threshold:
        return True
    return (
        minimum_temperature is not None
        and minimum_temperature < enable_threshold
        and primary_temperature < ideal_threshold
    )


def _cool_calling(
    *,
    primary_temperature: float,
    maximum_temperature: float | None,
    enable_threshold: float,
    ideal_threshold: float,
) -> bool:
    if primary_temperature > enable_threshold:
        return True
    return (
        maximum_temperature is not None
        and maximum_temperature > enable_threshold
        and primary_temperature > ideal_threshold
    )


def forecast_idle_demand(
    snapshot: DemandSnapshot,
    *,
    operation_mode: str | None,
    now: datetime | None,
    weather_points: Sequence[WeatherForecastPoint] = (),
    current_outdoor_temperature: float | None = None,
    current_condition: str | None = None,
    horizon_seconds: int = 30 * 60,
    step_seconds: int = 60,
    adjustment_provider: ForecastAdjustmentProvider | None = None,
) -> IdleDemandForecast:
    """Project the first future heat/cool call using the current control configuration."""
    normalized_now = _normalize_datetime(now)
    if operation_mode not in {HVAC_HEAT, HVAC_COOL} or normalized_now is None:
        return IdleDemandForecast(
            generated_at=normalized_now,
            horizon_seconds=horizon_seconds,
            earliest_demand_at=None,
            earliest_zone_key=None,
            operation_mode=operation_mode,
            safe_to_turn_off=False,
            source="unavailable",
            reason="idle demand forecast requires an active mode and timestamp",
        )

    normalized_horizon = max(0, int(horizon_seconds))
    normalized_step = max(1, int(step_seconds))
    primary_temperatures: dict[str, float] = {}
    minimum_temperatures: dict[str, float | None] = {}
    maximum_temperatures: dict[str, float | None] = {}
    enabled_zone_keys: list[str] = []
    for zone_key, zone in snapshot.zones.items():
        if not zone.is_enabled_by_mode:
            continue
        enabled_zone_keys.append(zone_key)
        primary_temperatures[zone_key] = zone.current_temp
        minimum_temperatures[zone_key] = zone.min_temp
        maximum_temperatures[zone_key] = zone.max_temp

    source = "base_rate"
    for point in weather_points:
        if point.temperature is not None:
            source = "forecast"
            break
    elapsed_seconds = 0
    while elapsed_seconds < normalized_horizon:
        next_elapsed_seconds = min(normalized_horizon, elapsed_seconds + normalized_step)
        simulated_at = normalized_now + timedelta(seconds=next_elapsed_seconds)
        outdoor_temperature = forecast_temperature_at(
            weather_points,
            now=normalized_now,
            at=simulated_at,
            current_outdoor_temperature=current_outdoor_temperature,
        )
        condition = forecast_condition_at(
            weather_points,
            at=simulated_at,
            current_condition=current_condition,
        )
        adjustments: Mapping[str, float] | None = None
        if adjustment_provider is not None and outdoor_temperature is not None:
            try:
                adjustments = adjustment_provider(simulated_at, outdoor_temperature, condition)
            except Exception:
                adjustments = None

        step_elapsed_seconds = next_elapsed_seconds - elapsed_seconds
        for zone_key in enabled_zone_keys:
            primary_delta = _drift_celsius(
                primary_temperatures[zone_key],
                outdoor_temperature,
                operation_mode,
                step_elapsed_seconds,
            )
            primary_temperatures[zone_key] += primary_delta
            if minimum_temperatures[zone_key] is not None:
                minimum_temperatures[zone_key] = float(minimum_temperatures[zone_key]) + primary_delta
            if maximum_temperatures[zone_key] is not None:
                maximum_temperatures[zone_key] = float(maximum_temperatures[zone_key]) + primary_delta

        calling_zones: list[str] = []
        for zone_key in enabled_zone_keys:
            zone = snapshot.zones[zone_key]
            forecast_score = _forecast_adjustment(zone_key, zone.comfort_adjustment, adjustments)
            score_delta = forecast_score - zone.comfort_adjustment
            if operation_mode == HVAC_HEAT:
                calling = _heat_calling(
                    primary_temperature=primary_temperatures[zone_key],
                    minimum_temperature=minimum_temperatures[zone_key],
                    enable_threshold=zone.scheme.enable_outside - score_delta,
                    ideal_threshold=zone.scheme.ideal_target - score_delta,
                )
            else:
                calling = _cool_calling(
                    primary_temperature=primary_temperatures[zone_key],
                    maximum_temperature=maximum_temperatures[zone_key],
                    enable_threshold=zone.cool_scheme.enable_outside - score_delta,
                    ideal_threshold=zone.cool_scheme.ideal_target - score_delta,
                )
            if calling:
                calling_zones.append(zone_key)

        if calling_zones:
            calling_zones.sort()
            earliest_zone_key = calling_zones[0]
            return IdleDemandForecast(
                generated_at=normalized_now,
                horizon_seconds=normalized_horizon,
                earliest_demand_at=simulated_at,
                earliest_zone_key=earliest_zone_key,
                operation_mode=operation_mode,
                safe_to_turn_off=next_elapsed_seconds >= normalized_horizon,
                source=source,
                reason=(
                    f"{earliest_zone_key} {operation_mode} demand predicted in "
                    f"{next_elapsed_seconds // 60} minutes"
                ),
            )
        elapsed_seconds = next_elapsed_seconds

    return IdleDemandForecast(
        generated_at=normalized_now,
        horizon_seconds=normalized_horizon,
        earliest_demand_at=None,
        earliest_zone_key=None,
        operation_mode=operation_mode,
        safe_to_turn_off=True,
        source=source,
        reason=f"no {operation_mode} demand predicted within {normalized_horizon // 60} minutes",
    )
