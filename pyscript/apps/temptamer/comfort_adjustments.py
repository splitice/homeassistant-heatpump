from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import ceil, cos, exp, floor, isfinite, radians, sin
from typing import Mapping, Protocol


_UNSET = object()


class StateReaderLike(Protocol):
    def get_state(self, entity_id: str) -> object | None: ...

    def get_attr(self, entity_id: str, attr_name: str) -> object | None: ...


@dataclass(frozen=True)
class ConstructionProfile:
    key: str
    wall_u_value: float
    wall_filter_time_constant_seconds: int


@dataclass(frozen=True)
class WindowConfig:
    facade: str | None
    view_factor: float = 0.20
    u_value: float = 6.9
    shgc: float = 0.77
    cover_entity_id: str | None = None
    direct_shade_factor: float = 1.0
    diffuse_shade_factor: float = 1.0


@dataclass(frozen=True)
class RoomEnvelopeConfig:
    windows: tuple[WindowConfig, ...]
    opaque_wall_view_factor: float
    construction_profile: str
    comfort_weight: float = 1.0


@dataclass(frozen=True)
class FabricSolarConfig:
    """Per-zone calibration for delayed solar warmth in fabric and contents."""

    filter_time_constant_seconds: int = 30 * 60
    irradiance_threshold: float = 20.0
    score_coefficient: float = 0.0045
    score_limit: float = 0.40


@dataclass(frozen=True)
class ComfortAdjustmentRoomConfig:
    primary_temperature_entity_id: str
    fallback_temperature_entity_id: str | None = None
    # PyScript evaluates dataclass field annotations at runtime and cannot
    # evaluate a local class in a ``Type | None`` expression.
    envelope: object = None


@dataclass(frozen=True)
class ComfortAdjustmentZoneConfig:
    key: str
    output_entity_id: str
    fallback_temperature_entity_id: str | None
    rooms: tuple[ComfortAdjustmentRoomConfig, ...]
    upstairs: bool = False
    # Keep this as ``object`` for the PyScript evaluator; see the equivalent
    # envelope field above.
    fabric_solar: object = None


@dataclass(frozen=True)
class ComfortAdjustmentConfig:
    zones: tuple[ComfortAdjustmentZoneConfig, ...]
    house_temperature_entity_id: str
    heatpump_mode_user_entity_id: str
    climate_entity_id: str
    outdoor_temperature_entity_id: str
    weather_entity_id: str
    solar_radiation_entity_id: str
    sun_entity_id: str
    awning_min_sun_elevation_entity_id: str
    awning_exposure_half_band_entity_id: str
    facade_labels: Mapping[str, str]
    output_hysteresis: float
    construction_profiles: Mapping[str, ConstructionProfile]
    # Input validation and short-term availability guardrails.
    last_valid_hold_seconds: int = 5 * 60
    indoor_temperature_min_celsius: float = -10.0
    indoor_temperature_max_celsius: float = 50.0
    outdoor_temperature_min_celsius: float = -30.0
    outdoor_temperature_max_celsius: float = 60.0
    input_stale_after_seconds: int = 30 * 60
    last_valid_operating_mode_hold_seconds: int = 5 * 60
    # Set to ``legacy`` only as an immediate live rollback; production uses
    # the room-level operative model.
    calculation_model: str = "operative"
    indoor_surface_resistance: float = 0.12
    operative_air_weight: float = 0.5
    maximum_total_k: float = 0.8
    minimum_operative_denominator: float = 0.55
    window_outdoor_filter_time_constant_seconds: int = 15 * 60
    upstairs_wall_outdoor_filter_time_constant_seconds: int = 3 * 60 * 60
    downstairs_wall_outdoor_filter_time_constant_seconds: int = 8 * 60 * 60
    shutter_resistance: float = 0.05
    shutter_closed_direct_transmission: float = 0.02
    shutter_closed_diffuse_transmission: float = 0.05
    shutter_position_fallback: float = 0.5
    solar_direct_fraction_minimum: float = 0.20
    solar_direct_fraction_maximum: float = 0.80
    solar_maximum_direct_normal_irradiance: float = 1100.0
    solar_ground_reflection_fraction: float = 0.10
    solar_mrt_coefficient: float = 0.012
    solar_adjustment_limit: float = 0.4
    output_rate_limit_celsius: float = 0.2
    output_rate_limit_seconds: int = 15 * 60
    outdoor_filter_warmup_fraction: float = 0.5


@dataclass(frozen=True)
class ComfortAdjustmentResult:
    zone_temperatures: Mapping[str, float | None]
    outdoor_temperature: float | None
    effective_outdoor_temperatures: Mapping[str, float | None]
    effective_window_outdoor_temperatures: Mapping[str, float | None]
    effective_wall_outdoor_temperatures: Mapping[str, float | None]
    solar_index: float
    filtered_solar_irradiances: Mapping[str, float | None]
    operating_modes: Mapping[str, str | None]
    operating_mode: str | None
    operating_mode_source: str
    operating_indoor_temperature: float | None
    operating_indoor_temperature_source: str
    reference_temperatures: Mapping[str, float | None]
    envelope_adjustments: Mapping[str, float]
    solar_adjustments: Mapping[str, float]
    fabric_solar_scores: Mapping[str, float]
    raw_adjustments: Mapping[str, float]
    adjustments: Mapping[str, float]
    calculation_validity: Mapping[str, bool]
    zone_diagnostics: Mapping[str, Mapping[str, object]]
    global_input_issues: tuple[str, ...]
    filter_warming_up: Mapping[str, bool]
    room_adjustment_minimums: Mapping[str, float | None]
    room_adjustment_maximums: Mapping[str, float | None]
    room_adjustment_spreads: Mapping[str, float | None]


@dataclass(frozen=True)
class TemperatureReading:
    entity_id: str
    value: float | None
    status: str
    age_seconds: float | None = None


def _parse_float(value: object | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in {"", "none", "unknown", "unavailable"}:
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


def _read_temperature(
    reader: StateReaderLike,
    entity_id: str,
    *,
    raw_value: object = _UNSET,
    now: datetime | None = None,
    minimum: float,
    maximum: float,
    stale_after_seconds: int,
) -> TemperatureReading:
    value_to_parse = reader.get_state(entity_id) if raw_value is _UNSET else raw_value
    if value_to_parse is None or (
        isinstance(value_to_parse, str) and value_to_parse.strip().lower() in {"", "none", "unknown", "unavailable"}
    ):
        return TemperatureReading(entity_id=entity_id, value=None, status="missing")

    parsed = _parse_float(value_to_parse)
    if parsed is None:
        return TemperatureReading(entity_id=entity_id, value=None, status="rejected_non_finite_or_not_numeric")
    if parsed < minimum or parsed > maximum:
        return TemperatureReading(entity_id=entity_id, value=None, status="rejected_implausible")

    timestamp = _normalize_datetime(reader.get_attr(entity_id, "last_updated"))
    if timestamp is None:
        timestamp = _normalize_datetime(reader.get_attr(entity_id, "last_changed"))
    if timestamp is None:
        timestamp = _normalize_datetime(getattr(value_to_parse, "last_updated", None))
    if timestamp is None:
        timestamp = _normalize_datetime(getattr(value_to_parse, "last_changed", None))
    normalized_now = _normalize_datetime(now)
    age_seconds = None
    if timestamp is not None and normalized_now is not None:
        age_seconds = max(0.0, (normalized_now - timestamp).total_seconds())
        if stale_after_seconds > 0 and age_seconds > stale_after_seconds:
            return TemperatureReading(entity_id=entity_id, value=None, status="stale", age_seconds=age_seconds)
    return TemperatureReading(entity_id=entity_id, value=parsed, status="valid", age_seconds=age_seconds)


def _append_issue(issues: list[str], reading: TemperatureReading) -> None:
    if reading.status in {"valid", "not_used"}:
        return
    issue = f"{reading.entity_id}:{reading.status}"
    if issue not in issues:
        issues.append(issue)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _read_first_temperature(reader: StateReaderLike, primary_entity_id: str, fallback_entity_id: str | None) -> float | None:
    primary = _parse_float(reader.get_state(primary_entity_id))
    if primary is not None:
        return primary
    if fallback_entity_id is None:
        return None
    return _parse_float(reader.get_state(fallback_entity_id))


def _resolve_zone_temperature_details(
    reader: StateReaderLike,
    zone: ComfortAdjustmentZoneConfig,
    house_temperature: TemperatureReading,
    config: ComfortAdjustmentConfig,
    now: datetime | None,
) -> tuple[float | None, str, list[dict[str, object]], list[str]]:
    room_values: list[float] = []
    room_diagnostics: list[dict[str, object]] = []
    issues: list[str] = []
    for room in zone.rooms:
        primary = _read_temperature(
            reader,
            room.primary_temperature_entity_id,
            now=now,
            minimum=config.indoor_temperature_min_celsius,
            maximum=config.indoor_temperature_max_celsius,
            stale_after_seconds=config.input_stale_after_seconds,
        )
        _append_issue(issues, primary)
        fallback = None
        source = "primary"
        room_temperature = primary.value
        if room_temperature is None and room.fallback_temperature_entity_id is not None:
            fallback = _read_temperature(
                reader,
                room.fallback_temperature_entity_id,
                now=now,
                minimum=config.indoor_temperature_min_celsius,
                maximum=config.indoor_temperature_max_celsius,
                stale_after_seconds=config.input_stale_after_seconds,
            )
            _append_issue(issues, fallback)
            room_temperature = fallback.value
            source = "fallback"
        if room_temperature is None:
            source = "unavailable"
        else:
            room_values.append(room_temperature)
        room_diagnostics.append(
            {
                "temperature_entity_id": room.primary_temperature_entity_id,
                "fallback_temperature_entity_id": room.fallback_temperature_entity_id,
                "temperature": room_temperature,
                "temperature_source": source,
                "primary_status": primary.status,
                "primary_age_seconds": primary.age_seconds,
                "fallback_status": fallback.status if fallback is not None else "not_used",
                "fallback_age_seconds": fallback.age_seconds if fallback is not None else None,
                "temperature_weight": 0.0,
            }
        )

    if room_values:
        aggregation_weight = 1.0 / len(room_values)
        for room_diagnostic in room_diagnostics:
            if room_diagnostic["temperature"] is not None:
                room_diagnostic["temperature_weight"] = aggregation_weight
        return sum(room_values) / len(room_values), "room_mean", room_diagnostics, issues

    if zone.fallback_temperature_entity_id is not None:
        zone_fallback = _read_temperature(
            reader,
            zone.fallback_temperature_entity_id,
            now=now,
            minimum=config.indoor_temperature_min_celsius,
            maximum=config.indoor_temperature_max_celsius,
            stale_after_seconds=config.input_stale_after_seconds,
        )
        _append_issue(issues, zone_fallback)
        if zone_fallback.value is not None:
            return zone_fallback.value, "zone_fallback", room_diagnostics, issues
    if house_temperature.value is not None:
        return house_temperature.value, "house_fallback", room_diagnostics, issues
    _append_issue(issues, house_temperature)
    return None, "unavailable", room_diagnostics, issues


def resolve_zone_temperature(
    reader: StateReaderLike,
    zone: ComfortAdjustmentZoneConfig,
    house_temperature: float | None,
) -> float | None:
    # Retained as a small public helper; the calculation path below uses the
    # detailed resolver so diagnostics and validation share the same values.
    room_temperatures: list[float] = []
    for room in zone.rooms:
        room_temperature = _read_first_temperature(reader, room.primary_temperature_entity_id, room.fallback_temperature_entity_id)
        if room_temperature is not None:
            room_temperatures.append(room_temperature)
    if room_temperatures:
        return sum(room_temperatures) / len(room_temperatures)
    if zone.fallback_temperature_entity_id is not None:
        zone_temperature = _parse_float(reader.get_state(zone.fallback_temperature_entity_id))
        if zone_temperature is not None:
            return zone_temperature
    return house_temperature


def _normalized_mode(value: object | None) -> str | None:
    normalized = str(value).strip().lower() if value is not None else ""
    if normalized in {"heat", "heating"}:
        return "heat"
    if normalized in {"cool", "cooling"}:
        return "cool"
    return None


def resolve_operating_mode_with_source(
    *,
    user_mode: object | None,
    hvac_action: object | None,
    configured_hvac_mode: object | None,
    last_valid_mode: object | None = None,
    last_valid_mode_at: object | None = None,
    now: object | None = None,
    last_valid_mode_hold_seconds: int = 0,
    zone_temperature: float | None,
    outdoor_temperature: float | None,
) -> tuple[str | None, str]:
    selected_mode = _normalized_mode(user_mode)
    if selected_mode is not None:
        return selected_mode, "user_mode"

    active_mode = _normalized_mode(hvac_action)
    if active_mode is not None:
        return active_mode, "hvac_action"

    if zone_temperature is None or outdoor_temperature is None:
        return None, "unavailable"

    previous_mode = _normalized_mode(last_valid_mode)
    last_valid_at = _normalize_datetime(last_valid_mode_at)
    resolved_now = _normalize_datetime(now)
    last_valid_age_seconds = (
        (resolved_now - last_valid_at).total_seconds()
        if resolved_now is not None and last_valid_at is not None
        else None
    )
    if (
        previous_mode is not None
        and last_valid_age_seconds is not None
        and 0.0 <= last_valid_age_seconds <= max(0, last_valid_mode_hold_seconds)
    ):
        return previous_mode, "last_valid_mode"

    if outdoor_temperature <= zone_temperature - 0.5:
        return "heat", "temperature_inference"
    if outdoor_temperature >= zone_temperature + 0.5:
        return "cool", "temperature_inference"

    configured_mode = _normalized_mode(configured_hvac_mode)
    if configured_mode is not None:
        return configured_mode, "configured_hvac_mode"
    return ("heat" if outdoor_temperature <= zone_temperature else "cool"), "temperature_fallback"


def resolve_operating_mode(
    *,
    user_mode: object | None,
    hvac_action: object | None,
    configured_hvac_mode: object | None,
    last_valid_mode: object | None = None,
    last_valid_mode_at: object | None = None,
    now: object | None = None,
    last_valid_mode_hold_seconds: int = 0,
    zone_temperature: float | None,
    outdoor_temperature: float | None,
) -> str | None:
    """Return only the mode for existing callers; diagnostics use the source-aware form."""
    mode, _source = resolve_operating_mode_with_source(
        user_mode=user_mode,
        hvac_action=hvac_action,
        configured_hvac_mode=configured_hvac_mode,
        last_valid_mode=last_valid_mode,
        last_valid_mode_at=last_valid_mode_at,
        now=now,
        last_valid_mode_hold_seconds=last_valid_mode_hold_seconds,
        zone_temperature=zone_temperature,
        outdoor_temperature=outdoor_temperature,
    )
    return mode


def _weather_solar_factor(weather_condition: object | None) -> float:
    normalized = str(weather_condition).strip().lower().replace("_", "").replace("-", "").replace(" ", "")
    if normalized == "sunny":
        return 1.0
    if normalized in {"partlycloudy", "partlysunny"}:
        return 0.55
    if normalized in {"cloudy", "fog", "foggy", "windy"}:
        return 0.20
    return 0.10


def resolve_solar_index(reader: StateReaderLike, config: ComfortAdjustmentConfig) -> float:
    elevation = _parse_float(reader.get_attr(config.sun_entity_id, "elevation"))
    if elevation is not None and elevation <= 0.0:
        return 0.0

    irradiance = _parse_float(reader.get_state(config.solar_radiation_entity_id))
    if irradiance is not None:
        return _clamp((irradiance - 75.0) / 525.0, 0.0, 1.0)

    if elevation is None:
        return 0.0
    return _weather_solar_factor(reader.get_state(config.weather_entity_id)) * sin(radians(elevation))


def resolve_outdoor_temperature(
    reader: StateReaderLike,
    config: ComfortAdjustmentConfig,
    now: datetime | None = None,
) -> float | None:
    outdoor_reading, weather_reading, _source = _resolve_outdoor_temperature_details(reader, config, now=now)
    if outdoor_reading.value is not None:
        return outdoor_reading.value
    return weather_reading.value


def _resolve_outdoor_temperature_details(
    reader: StateReaderLike,
    config: ComfortAdjustmentConfig,
    now: datetime | None,
) -> tuple[TemperatureReading, TemperatureReading, str]:
    outdoor_reading = _read_temperature(
        reader,
        config.outdoor_temperature_entity_id,
        now=now,
        minimum=config.outdoor_temperature_min_celsius,
        maximum=config.outdoor_temperature_max_celsius,
        stale_after_seconds=config.input_stale_after_seconds,
    )
    if outdoor_reading.value is not None:
        return outdoor_reading, TemperatureReading(config.weather_entity_id, None, "not_used"), "outdoor_sensor"

    weather_reading = _read_temperature(
        reader,
        config.weather_entity_id,
        raw_value=reader.get_attr(config.weather_entity_id, "temperature"),
        now=now,
        minimum=config.outdoor_temperature_min_celsius,
        maximum=config.outdoor_temperature_max_celsius,
        stale_after_seconds=config.input_stale_after_seconds,
    )
    if weather_reading.value is not None:
        return outdoor_reading, weather_reading, "weather_temperature"
    return outdoor_reading, weather_reading, "unavailable"


def _azimuth_difference(first: float, second: float) -> float:
    difference = abs((first - second) % 360.0)
    return min(difference, 360.0 - difference)


def _facade_azimuth(facade: str | None) -> float | None:
    if facade == "n":
        return 0.0
    if facade == "e":
        return 90.0
    if facade == "s":
        return 180.0
    if facade == "w":
        return 270.0
    return None


def facade_direct_gain_components(
    reader: StateReaderLike,
    config: ComfortAdjustmentConfig,
    facade: str | None,
) -> tuple[float, float, float]:
    """Return direct incidence, awning mask, and their combined gain factor."""
    facade_azimuth = _facade_azimuth(facade)
    sun_elevation = _parse_float(reader.get_attr(config.sun_entity_id, "elevation"))
    sun_azimuth = _parse_float(reader.get_attr(config.sun_entity_id, "azimuth"))
    minimum_elevation = _parse_float(reader.get_state(config.awning_min_sun_elevation_entity_id))
    exposure_half_band = _parse_float(reader.get_state(config.awning_exposure_half_band_entity_id))
    if (
        facade_azimuth is None
        or sun_elevation is None
        or sun_azimuth is None
        or minimum_elevation is None
        or exposure_half_band is None
    ):
        return 0.0, 0.0, 0.0
    if sun_elevation <= 0.0 or sun_elevation <= minimum_elevation:
        return 0.0, 0.0, 0.0

    azimuth_difference = _azimuth_difference(sun_azimuth, facade_azimuth)
    if exposure_half_band <= 0.0:
        awning_mask = 1.0 if azimuth_difference == 0.0 else 0.0
    elif azimuth_difference > exposure_half_band:
        return 0.0, 0.0, 0.0
    else:
        awning_mask = 1.0 - azimuth_difference / exposure_half_band

    direct_incidence = max(
        0.0,
        cos(radians(sun_elevation)) * cos(radians(azimuth_difference)),
    )
    return direct_incidence, awning_mask, direct_incidence * awning_mask


def facade_direct_gain_factor(
    reader: StateReaderLike,
    config: ComfortAdjustmentConfig,
    facade: str | None,
) -> float:
    """Return incidence geometry after the configured awning shading mask."""
    _incidence, _awning_mask, direct_gain = facade_direct_gain_components(reader, config, facade)
    return direct_gain


def _room_envelope_config(room: ComfortAdjustmentRoomConfig) -> RoomEnvelopeConfig | None:
    return room.envelope


def _has_valid_room_envelope(
    config: ComfortAdjustmentConfig,
    envelope: RoomEnvelopeConfig,
    profile: ConstructionProfile,
) -> bool:
    if (
        not envelope.windows
        or not isfinite(envelope.opaque_wall_view_factor)
        or envelope.opaque_wall_view_factor < 0.0
        or not isfinite(envelope.comfort_weight)
        or envelope.comfort_weight <= 0.0
        or not isfinite(profile.wall_u_value)
        or profile.wall_u_value < 0.0
        or not isfinite(config.indoor_surface_resistance)
        or config.indoor_surface_resistance <= 0.0
        or not 0.0 <= config.operative_air_weight <= 1.0
        or not isfinite(config.shutter_resistance)
        or config.shutter_resistance < 0.0
        or not isfinite(config.maximum_total_k)
        or config.maximum_total_k <= 0.0
        or not isfinite(config.minimum_operative_denominator)
        or config.minimum_operative_denominator <= 0.0
    ):
        return False
    for window in envelope.windows:
        if (
            not isfinite(window.view_factor)
            or window.view_factor < 0.0
            or not isfinite(window.u_value)
            or window.u_value <= 0.0
            or not isfinite(window.shgc)
            or window.shgc < 0.0
            or not isfinite(window.direct_shade_factor)
            or window.direct_shade_factor < 0.0
            or not isfinite(window.diffuse_shade_factor)
            or window.diffuse_shade_factor < 0.0
        ):
            return False
    return True


def _window_facade(
    window: WindowConfig,
    cover_facades: Mapping[str, str | None],
) -> tuple[str | None, str]:
    if window.cover_entity_id is not None:
        # Cover-device labels are authoritative for shuttered windows.  A
        # present-but-empty entry represents a missing or ambiguous label and
        # deliberately leaves direct solar unexposed.
        if window.cover_entity_id in cover_facades:
            return cover_facades[window.cover_entity_id], "cover_device_label"
    if window.facade is not None:
        return window.facade, "configured_fallback"
    return None, "unconfigured"


def _window_shutter_details(
    reader: StateReaderLike,
    config: ComfortAdjustmentConfig,
    window: WindowConfig,
    cover_position_overrides: Mapping[str, tuple[float, str]] | None,
) -> dict[str, object]:
    if window.cover_entity_id is None:
        openness = 1.0
        position_status = "unshuttered"
        cover_state = "unshuttered"
    else:
        override = cover_position_overrides.get(window.cover_entity_id) if cover_position_overrides is not None else None
        position = _parse_float(reader.get_attr(window.cover_entity_id, "current_position"))
        if position is not None and 0.0 <= position <= 100.0:
            openness = position / 100.0
            position_status = "live"
        elif override is not None:
            openness = _clamp(override[0], 0.0, 1.0)
            position_status = override[1]
        else:
            openness = _clamp(config.shutter_position_fallback, 0.0, 1.0)
            position_status = "configured_fallback"
        if openness <= 0.0:
            cover_state = "closed"
        elif openness >= 1.0:
            cover_state = "open"
        else:
            cover_state = "partial"

    closed_u_value = 1.0 / (1.0 / window.u_value + config.shutter_resistance)
    effective_u_value = openness * window.u_value + (1.0 - openness) * closed_u_value
    direct_shgc = window.shgc * (
        openness + (1.0 - openness) * config.shutter_closed_direct_transmission
    )
    diffuse_shgc = window.shgc * (
        openness + (1.0 - openness) * config.shutter_closed_diffuse_transmission
    )
    return {
        "cover_entity_id": window.cover_entity_id,
        "cover_state": cover_state,
        "cover_position": openness * 100.0 if window.cover_entity_id is not None else None,
        "cover_position_status": position_status,
        "openness": openness,
        "open_u_value": window.u_value,
        "closed_u_value": closed_u_value,
        "effective_u_value": effective_u_value,
        "effective_u_value_model": "physical_shutter_resistance",
        "effective_direct_shgc": direct_shgc,
        "effective_diffuse_shgc": diffuse_shgc,
        "direct_shade_factor": window.direct_shade_factor,
        "diffuse_shade_factor": window.diffuse_shade_factor,
    }


def _solar_irradiance_components(
    reader: StateReaderLike,
    config: ComfortAdjustmentConfig,
    solar_index: float,
) -> tuple[float, float, float, str]:
    """Return direct-normal, diffuse-horizontal, ground-reflected irradiance."""
    elevation = _parse_float(reader.get_attr(config.sun_entity_id, "elevation"))
    measured_irradiance = _parse_float(reader.get_state(config.solar_radiation_entity_id))
    if measured_irradiance is None or measured_irradiance < 0.0:
        measured_irradiance = _clamp(solar_index, 0.0, 1.0) * 600.0
        source = "weather_or_elevation_fallback"
    else:
        source = "global_horizontal_irradiance_estimate"

    if elevation is None:
        diffuse_horizontal = measured_irradiance
        return (
            0.0,
            diffuse_horizontal,
            diffuse_horizontal * config.solar_ground_reflection_fraction,
            "elevation_unavailable_diffuse_fallback",
        )
    if elevation <= 0.0:
        return 0.0, 0.0, 0.0, "below_horizon"

    elevation_sine = max(sin(radians(elevation)), 1e-6)
    direct_fraction = _clamp(
        config.solar_direct_fraction_minimum
        + (config.solar_direct_fraction_maximum - config.solar_direct_fraction_minimum) * solar_index,
        config.solar_direct_fraction_minimum,
        config.solar_direct_fraction_maximum,
    )
    direct_horizontal = measured_irradiance * direct_fraction
    diffuse_horizontal = max(0.0, measured_irradiance - direct_horizontal)
    direct_normal = min(
        direct_horizontal / elevation_sine,
        config.solar_maximum_direct_normal_irradiance,
    )
    ground_reflected = measured_irradiance * config.solar_ground_reflection_fraction
    return direct_normal, diffuse_horizontal, ground_reflected, source


def _calculate_room_operative_adjustment(
    reader: StateReaderLike,
    config: ComfortAdjustmentConfig,
    room: ComfortAdjustmentRoomConfig,
    *,
    target_temperature: float,
    effective_window_outdoor_temperature: float,
    effective_wall_outdoor_temperature: float,
    solar_index: float,
    cover_facades: Mapping[str, str | None],
    cover_position_overrides: Mapping[str, tuple[float, str]] | None,
) -> tuple[float, float, float, dict[str, object]] | None:
    envelope = _room_envelope_config(room)
    if envelope is None:
        return None
    profile = config.construction_profiles.get(envelope.construction_profile)
    if profile is None or not _has_valid_room_envelope(config, envelope, profile):
        return None

    direct_normal, diffuse_horizontal, ground_reflected, irradiance_source = _solar_irradiance_components(
        reader,
        config,
        solar_index,
    )
    window_k = 0.0
    conductive_drive = 0.0
    solar_drive = 0.0
    window_details: list[dict[str, object]] = []
    for window in envelope.windows:
        shutter_details = _window_shutter_details(reader, config, window, cover_position_overrides)
        facade, facade_source = _window_facade(window, cover_facades)
        direct_incidence_factor, awning_shading_factor, direct_factor = facade_direct_gain_components(
            reader,
            config,
            facade,
        )
        current_window_k = window.view_factor * float(shutter_details["effective_u_value"]) * config.indoor_surface_resistance
        window_k += current_window_k
        conductive_drive += current_window_k * (target_temperature - effective_window_outdoor_temperature)
        direct_irradiance = direct_normal * direct_factor * window.direct_shade_factor
        diffuse_irradiance = (
            0.5 * diffuse_horizontal + ground_reflected
        ) * window.diffuse_shade_factor
        transmitted_direct = direct_irradiance * float(shutter_details["effective_direct_shgc"])
        transmitted_diffuse = diffuse_irradiance * float(shutter_details["effective_diffuse_shgc"])
        current_solar_drive = (transmitted_direct + transmitted_diffuse) * window.view_factor
        solar_drive += current_solar_drive
        shutter_details.update(
            {
                "facade": facade,
                "facade_source": facade_source,
                "window_view_factor": window.view_factor,
                "window_k": current_window_k,
                "direct_incidence_factor": direct_incidence_factor,
                "awning_shading_factor": awning_shading_factor,
                "direct_gain_factor": direct_factor,
                "direct_irradiance": direct_irradiance,
                "diffuse_irradiance": diffuse_irradiance,
                "solar_drive": current_solar_drive,
            }
        )
        window_details.append(shutter_details)

    wall_k = envelope.opaque_wall_view_factor * profile.wall_u_value * config.indoor_surface_resistance
    conductive_drive += wall_k * (target_temperature - effective_wall_outdoor_temperature)
    unclamped_total_k = window_k + wall_k
    total_k = min(unclamped_total_k, config.maximum_total_k)
    if unclamped_total_k > 0.0 and total_k < unclamped_total_k:
        conductive_drive *= total_k / unclamped_total_k
    operative_denominator = 1.0 - (1.0 - config.operative_air_weight) * total_k
    if operative_denominator < config.minimum_operative_denominator:
        return None

    envelope_target_compensation = (
        (1.0 - config.operative_air_weight) * conductive_drive / operative_denominator
    )
    solar_mrt_increase = config.solar_mrt_coefficient * solar_drive
    current_solar_adjustment = -(
        (1.0 - config.operative_air_weight) * solar_mrt_increase / operative_denominator
    )
    current_solar_adjustment = _clamp(
        current_solar_adjustment,
        -config.solar_adjustment_limit,
        config.solar_adjustment_limit,
    )
    # A comfort score uses the inverse polarity of a target compensation:
    # cold fabric is negative (request heating sooner), solar warmth positive
    # (delay heating).  The consumer therefore applies target - score.
    envelope_score = -envelope_target_compensation
    window_solar_score = -current_solar_adjustment
    room_comfort_score = _clamp(envelope_score + window_solar_score, -1.5, 1.5)
    return envelope_score, window_solar_score, room_comfort_score, {
        "comfort_weight": envelope.comfort_weight,
        "construction_profile": profile.key,
        "wall_u_value": profile.wall_u_value,
        "window_k": window_k,
        "wall_k": wall_k,
        "total_k": total_k,
        "operative_denominator": operative_denominator,
        "window_envelope_adjustment": (
            (1.0 - config.operative_air_weight)
            * window_k
            * (target_temperature - effective_window_outdoor_temperature)
            / operative_denominator
        ),
        "wall_envelope_adjustment": (
            (1.0 - config.operative_air_weight)
            * wall_k
            * (target_temperature - effective_wall_outdoor_temperature)
            / operative_denominator
        ),
        "solar_mrt_increase": solar_mrt_increase,
        "solar_drive": solar_drive,
        "solar_irradiance_source": irradiance_source,
        "windows": window_details,
        "envelope_target_compensation": envelope_target_compensation,
        "current_solar_adjustment": current_solar_adjustment,
        "envelope_score": envelope_score,
        "window_solar_score": window_solar_score,
        "room_envelope_adjustment": envelope_score,
        "room_solar_adjustment": window_solar_score,
        "room_raw_adjustment": room_comfort_score,
    }


def _calculate_room_legacy_adjustment(
    reader: StateReaderLike,
    config: ComfortAdjustmentConfig,
    room: ComfortAdjustmentRoomConfig,
    *,
    target_temperature: float,
    effective_outdoor_temperature: float,
    upstairs: bool,
    operating_mode: str,
    solar_index: float,
    cover_facades: Mapping[str, str | None],
    cover_position_overrides: Mapping[str, tuple[float, str]] | None,
) -> tuple[float, float, float, dict[str, object]] | None:
    envelope = _room_envelope_config(room)
    if envelope is None or not envelope.windows:
        return None
    profile = config.construction_profiles.get(envelope.construction_profile)
    if profile is None or not _has_valid_room_envelope(config, envelope, profile):
        return None
    envelope_transmission = 0.0
    solar_access = 0.0
    window_details: list[dict[str, object]] = []
    for window in envelope.windows:
        shutter_details = _window_shutter_details(reader, config, window, cover_position_overrides)
        facade, facade_source = _window_facade(window, cover_facades)
        direct_incidence_factor, awning_shading_factor, direct_factor = facade_direct_gain_components(
            reader,
            config,
            facade,
        )
        openness = float(shutter_details["openness"])
        envelope_transmission += float(shutter_details["effective_u_value"]) / window.u_value
        if window.cover_entity_id is None:
            solar_access += 0.25 + 0.75 * direct_factor * window.direct_shade_factor
        else:
            solar_access += 0.25 + 0.75 * openness * direct_factor * window.direct_shade_factor
        shutter_details.update(
            {
                "facade": facade,
                "facade_source": facade_source,
                "direct_incidence_factor": direct_incidence_factor,
                "awning_shading_factor": awning_shading_factor,
                "direct_gain_factor": direct_factor,
            }
        )
        window_details.append(shutter_details)
    envelope_transmission /= len(envelope.windows)
    solar_access /= len(envelope.windows)
    temperature_gap = _clamp((target_temperature - effective_outdoor_temperature) / 10.0, -1.0, 1.0)
    if upstairs:
        solar_coefficient = 1.35 if operating_mode == "heat" else 1.50
        envelope_target_compensation = 1.10 * envelope_transmission * temperature_gap
    else:
        solar_coefficient = 1.00 if operating_mode == "heat" else 1.20
        envelope_target_compensation = 1.25 * temperature_gap
    current_solar_adjustment = -solar_coefficient * solar_access * solar_index
    envelope_score = -envelope_target_compensation
    window_solar_score = -current_solar_adjustment
    room_comfort_score = _clamp(envelope_score + window_solar_score, -1.5, 1.5)
    return envelope_score, window_solar_score, room_comfort_score, {
        "comfort_weight": envelope.comfort_weight,
        "legacy_envelope_transmission": envelope_transmission,
        "legacy_solar_access": solar_access,
        "windows": window_details,
        "envelope_target_compensation": envelope_target_compensation,
        "current_solar_adjustment": current_solar_adjustment,
        "envelope_score": envelope_score,
        "window_solar_score": window_solar_score,
        "room_envelope_adjustment": envelope_score,
        "room_solar_adjustment": window_solar_score,
        "room_raw_adjustment": room_comfort_score,
    }


def round_comfort_adjustment(value: float) -> float:
    clamped = _clamp(value, -1.5, 1.5)
    rounded = floor(clamped * 10.0 + 0.5) / 10.0 if clamped >= 0.0 else ceil(clamped * 10.0 - 0.5) / 10.0
    return 0.0 if rounded == 0.0 else rounded


def filter_outdoor_temperature(
    raw_temperature: float | None,
    previous_temperature: float | None,
    elapsed_seconds: float | None,
    time_constant_seconds: int,
) -> float | None:
    if raw_temperature is None:
        return None
    if previous_temperature is None or elapsed_seconds is None or elapsed_seconds <= 0.0 or time_constant_seconds <= 0:
        return raw_temperature
    alpha = 1.0 - exp(-elapsed_seconds / time_constant_seconds)
    return previous_temperature + (raw_temperature - previous_temperature) * alpha


def calculate_fabric_solar_score(
    fabric_solar: object,
    filtered_irradiance: float | None,
) -> float:
    """Return a configured zone's slow solar-warmth score."""
    if not isinstance(fabric_solar, FabricSolarConfig) or filtered_irradiance is None:
        return 0.0
    return _clamp(
        fabric_solar.score_coefficient
        * max(filtered_irradiance - fabric_solar.irradiance_threshold, 0.0),
        0.0,
        fabric_solar.score_limit,
    )


def apply_adjustment_hysteresis(
    raw_adjustment: float,
    previous_adjustment: float | None,
    hysteresis: float,
) -> float:
    rounded_adjustment = round_comfort_adjustment(raw_adjustment)
    if previous_adjustment is None:
        return rounded_adjustment

    clamped_previous = round_comfort_adjustment(previous_adjustment)
    boundary_margin = 0.05 + max(0.0, hysteresis)
    if raw_adjustment > clamped_previous and raw_adjustment < clamped_previous + boundary_margin:
        return clamped_previous
    if raw_adjustment < clamped_previous and raw_adjustment > clamped_previous - boundary_margin:
        return clamped_previous
    return rounded_adjustment


def _reference_temperature(
    zone_key: str,
    operating_mode: str,
    reference_zone_targets: Mapping[str, tuple[float, float]] | None,
) -> float:
    """Return the active scheme's unadjusted target, or the nominal fallback."""
    if reference_zone_targets is None:
        return 20.0

    targets = reference_zone_targets.get(zone_key)
    if targets is None:
        return 20.0
    target_index = 0 if operating_mode == "heat" else 1
    try:
        target = _parse_float(targets[target_index])
    except (IndexError, TypeError):
        target = None
    # The Off scheme has a 0.0 target, which is a sentinel rather than a
    # usable comfort reference for the independent publisher.
    return target if target is not None and target > 0.0 else 20.0


def calculate_comfort_adjustments(
    reader: StateReaderLike,
    *,
    config: ComfortAdjustmentConfig,
    cover_facades: Mapping[str, str | None],
    effective_outdoor_temperatures: Mapping[str, float | None] | None = None,
    reference_zone_targets: Mapping[str, tuple[float, float]] | None = None,
    now: datetime | None = None,
    effective_window_outdoor_temperatures: Mapping[str, float | None] | None = None,
    effective_wall_outdoor_temperatures: Mapping[str, float | None] | None = None,
    filter_warming_up: Mapping[str, bool] | None = None,
    cover_position_overrides: Mapping[str, tuple[float, str]] | None = None,
    filtered_solar_irradiances: Mapping[str, float | None] | None = None,
    last_valid_operating_mode: object | None = None,
    last_valid_operating_mode_at: object | None = None,
) -> ComfortAdjustmentResult:
    house_temperature_reading = _read_temperature(
        reader,
        config.house_temperature_entity_id,
        now=now,
        minimum=config.indoor_temperature_min_celsius,
        maximum=config.indoor_temperature_max_celsius,
        stale_after_seconds=config.input_stale_after_seconds,
    )
    outdoor_sensor_reading, weather_temperature_reading, outdoor_temperature_source = _resolve_outdoor_temperature_details(
        reader,
        config,
        now,
    )
    outdoor_temperature = outdoor_sensor_reading.value
    if outdoor_temperature is None:
        outdoor_temperature = weather_temperature_reading.value
    solar_index = resolve_solar_index(reader, config)
    sun_elevation = _parse_float(reader.get_attr(config.sun_entity_id, "elevation"))
    resolved_filtered_solar_irradiances: dict[str, float | None] = {}
    for zone in config.zones:
        filtered_irradiance = (
            filtered_solar_irradiances.get(zone.key)
            if filtered_solar_irradiances is not None
            else None
        )
        resolved_filtered_irradiance = _parse_float(filtered_irradiance)
        resolved_filtered_solar_irradiances[zone.key] = (
            max(0.0, resolved_filtered_irradiance)
            if resolved_filtered_irradiance is not None and (sun_elevation is None or sun_elevation > 0.0)
            else None
        )
    user_mode = reader.get_state(config.heatpump_mode_user_entity_id)
    configured_hvac_mode = reader.get_state(config.climate_entity_id)
    hvac_action = reader.get_attr(config.climate_entity_id, "hvac_action")

    zone_temperatures: dict[str, float | None] = {}
    zone_temperature_sources: dict[str, str] = {}
    room_diagnostics_by_zone: dict[str, list[dict[str, object]]] = {}
    input_issues_by_zone: dict[str, list[str]] = {}
    for zone in config.zones:
        zone_temperature, temperature_source, room_diagnostics, input_issues = _resolve_zone_temperature_details(
            reader,
            zone,
            house_temperature_reading,
            config,
            now,
        )
        zone_temperatures[zone.key] = zone_temperature
        zone_temperature_sources[zone.key] = temperature_source
        room_diagnostics_by_zone[zone.key] = room_diagnostics
        input_issues_by_zone[zone.key] = input_issues

    operating_indoor_temperature = house_temperature_reading.value
    operating_indoor_temperature_source = "house_temperature"
    if operating_indoor_temperature is None:
        available_zone_temperatures: list[float] = []
        for zone_temperature in zone_temperatures.values():
            if zone_temperature is not None:
                available_zone_temperatures.append(zone_temperature)
        if available_zone_temperatures:
            operating_indoor_temperature = sum(available_zone_temperatures) / len(available_zone_temperatures)
            operating_indoor_temperature_source = "zone_temperature_mean"
        else:
            operating_indoor_temperature_source = "unavailable"
    operating_mode, operating_mode_source = resolve_operating_mode_with_source(
        user_mode=user_mode,
        hvac_action=hvac_action,
        configured_hvac_mode=configured_hvac_mode,
        last_valid_mode=last_valid_operating_mode,
        last_valid_mode_at=last_valid_operating_mode_at,
        now=now,
        last_valid_mode_hold_seconds=config.last_valid_operating_mode_hold_seconds,
        zone_temperature=operating_indoor_temperature,
        outdoor_temperature=outdoor_temperature,
    )

    resolved_effective_outdoor_temperatures: dict[str, float | None] = {}
    operating_modes: dict[str, str | None] = {}
    reference_temperatures: dict[str, float | None] = {}
    envelope_adjustments: dict[str, float] = {}
    solar_adjustments: dict[str, float] = {}
    fabric_solar_scores: dict[str, float] = {}
    raw_adjustments: dict[str, float] = {}
    adjustments: dict[str, float] = {}
    calculation_validity: dict[str, bool] = {}
    zone_diagnostics: dict[str, Mapping[str, object]] = {}
    resolved_window_outdoor_temperatures: dict[str, float | None] = {}
    resolved_wall_outdoor_temperatures: dict[str, float | None] = {}
    resolved_filter_warming_up: dict[str, bool] = {}
    room_adjustment_minimums: dict[str, float | None] = {}
    room_adjustment_maximums: dict[str, float | None] = {}
    room_adjustment_spreads: dict[str, float | None] = {}
    for zone in config.zones:
        zone_temperature = zone_temperatures[zone.key]
        effective_outdoor_temperature = outdoor_temperature
        if effective_outdoor_temperatures is not None and zone.key in effective_outdoor_temperatures:
            effective_outdoor_temperature = effective_outdoor_temperatures[zone.key]
        effective_window_outdoor_temperature = effective_outdoor_temperature
        if effective_window_outdoor_temperatures is not None and zone.key in effective_window_outdoor_temperatures:
            effective_window_outdoor_temperature = effective_window_outdoor_temperatures[zone.key]
        effective_wall_outdoor_temperature = effective_outdoor_temperature
        if effective_wall_outdoor_temperatures is not None and zone.key in effective_wall_outdoor_temperatures:
            effective_wall_outdoor_temperature = effective_wall_outdoor_temperatures[zone.key]
        resolved_effective_outdoor_temperatures[zone.key] = effective_outdoor_temperature
        resolved_window_outdoor_temperatures[zone.key] = effective_window_outdoor_temperature
        resolved_wall_outdoor_temperatures[zone.key] = effective_wall_outdoor_temperature
        resolved_filter_warming_up[zone.key] = bool(filter_warming_up.get(zone.key)) if filter_warming_up is not None else False
        operating_modes[zone.key] = operating_mode
        input_issues = list(input_issues_by_zone[zone.key])
        _append_issue(input_issues, outdoor_sensor_reading)
        _append_issue(input_issues, weather_temperature_reading)
        if (
            effective_window_outdoor_temperature is None
            or effective_wall_outdoor_temperature is None
            or operating_mode is None
        ):
            reference_temperatures[zone.key] = None
            envelope_adjustments[zone.key] = 0.0
            solar_adjustments[zone.key] = 0.0
            fabric_solar_scores[zone.key] = 0.0
            raw_adjustments[zone.key] = 0.0
            adjustments[zone.key] = 0.0
            calculation_validity[zone.key] = False
            room_adjustment_minimums[zone.key] = None
            room_adjustment_maximums[zone.key] = None
            room_adjustment_spreads[zone.key] = None
            zone_diagnostics[zone.key] = {
                "calculation_status": "unavailable",
                "zone_temperature_source": zone_temperature_sources[zone.key],
                "room_values": room_diagnostics_by_zone[zone.key],
                "input_issues": tuple(input_issues),
                "outdoor_temperature_source": outdoor_temperature_source,
                "effective_outdoor_model": "separate_window_and_wall_filters",
                "filtered_solar_irradiance": resolved_filtered_solar_irradiances[zone.key],
                "fabric_solar_score": 0.0,
            }
            continue

        reference_temperature = _reference_temperature(
            zone.key,
            operating_mode,
            reference_zone_targets,
        )
        reference_temperatures[zone.key] = reference_temperature

        room_diagnostics = room_diagnostics_by_zone[zone.key]
        weighted_envelope_adjustment = 0.0
        weighted_solar_adjustment = 0.0
        comfort_weight_total = 0.0
        weighted_window_k = 0.0
        weighted_wall_k = 0.0
        weighted_total_k = 0.0
        weighted_denominator = 0.0
        room_adjustments: list[float] = []
        room_calculation_failed = False
        for room_index, room in enumerate(zone.rooms):
            if config.calculation_model == "legacy":
                room_result = _calculate_room_legacy_adjustment(
                    reader,
                    config,
                    room,
                    target_temperature=reference_temperature,
                    effective_outdoor_temperature=effective_outdoor_temperature,
                    upstairs=zone.upstairs,
                    operating_mode=operating_mode,
                    solar_index=solar_index,
                    cover_facades=cover_facades,
                    cover_position_overrides=cover_position_overrides,
                )
            else:
                room_result = _calculate_room_operative_adjustment(
                    reader,
                    config,
                    room,
                    target_temperature=reference_temperature,
                    effective_window_outdoor_temperature=effective_window_outdoor_temperature,
                    effective_wall_outdoor_temperature=effective_wall_outdoor_temperature,
                    solar_index=solar_index,
                    cover_facades=cover_facades,
                    cover_position_overrides=cover_position_overrides,
                )
            if room_result is None:
                room_calculation_failed = True
                continue
            room_envelope_adjustment, room_solar_adjustment, room_adjustment, room_details = room_result
            comfort_weight = float(room_details["comfort_weight"])
            if room_index < len(room_diagnostics):
                room_diagnostics[room_index].update(room_details)
            weighted_envelope_adjustment += comfort_weight * room_envelope_adjustment
            weighted_solar_adjustment += comfort_weight * room_solar_adjustment
            comfort_weight_total += comfort_weight
            weighted_window_k += comfort_weight * float(room_details.get("window_k", 0.0))
            weighted_wall_k += comfort_weight * float(room_details.get("wall_k", 0.0))
            weighted_total_k += comfort_weight * float(room_details.get("total_k", 0.0))
            weighted_denominator += comfort_weight * float(room_details.get("operative_denominator", 0.0))
            room_adjustments.append(room_adjustment)
        if comfort_weight_total <= 0.0 or room_calculation_failed:
            envelope_adjustments[zone.key] = 0.0
            solar_adjustments[zone.key] = 0.0
            fabric_solar_scores[zone.key] = 0.0
            raw_adjustments[zone.key] = 0.0
            adjustments[zone.key] = 0.0
            calculation_validity[zone.key] = False
            room_adjustment_minimums[zone.key] = min(room_adjustments) if room_adjustments else None
            room_adjustment_maximums[zone.key] = max(room_adjustments) if room_adjustments else None
            room_adjustment_spreads[zone.key] = (
                max(room_adjustments) - min(room_adjustments) if room_adjustments else None
            )
            zone_diagnostics[zone.key] = {
                "calculation_status": "invalid_operative_calculation",
                "zone_temperature_source": zone_temperature_sources[zone.key],
                "room_values": room_diagnostics,
                "input_issues": tuple(input_issues),
                "outdoor_temperature_source": outdoor_temperature_source,
                "effective_outdoor_model": "separate_window_and_wall_filters",
                "filtered_solar_irradiance": resolved_filtered_solar_irradiances[zone.key],
                "fabric_solar_score": 0.0,
            }
            continue
        envelope_adjustment = weighted_envelope_adjustment / comfort_weight_total
        solar_adjustment = weighted_solar_adjustment / comfort_weight_total
        fabric_solar_score = calculate_fabric_solar_score(
            zone.fabric_solar,
            resolved_filtered_solar_irradiances[zone.key],
        )
        raw_adjustment = _clamp(envelope_adjustment + solar_adjustment + fabric_solar_score, -1.5, 1.5)
        envelope_adjustments[zone.key] = envelope_adjustment
        solar_adjustments[zone.key] = solar_adjustment
        fabric_solar_scores[zone.key] = fabric_solar_score
        raw_adjustments[zone.key] = raw_adjustment
        adjustments[zone.key] = round_comfort_adjustment(raw_adjustment)
        calculation_validity[zone.key] = True
        room_adjustment_minimums[zone.key] = min(room_adjustments)
        room_adjustment_maximums[zone.key] = max(room_adjustments)
        room_adjustment_spreads[zone.key] = max(room_adjustments) - min(room_adjustments)
        zone_diagnostics[zone.key] = {
            "calculation_status": "calculated",
            "zone_temperature_source": zone_temperature_sources[zone.key],
            "room_values": room_diagnostics,
            "room_aggregation": "comfort_weighted_mean",
            "input_issues": tuple(input_issues),
            "outdoor_temperature_source": outdoor_temperature_source,
            "effective_outdoor_model": "separate_window_and_wall_filters",
            "filtered_solar_irradiance": resolved_filtered_solar_irradiances[zone.key],
            "fabric_solar_score": fabric_solar_score,
            "window_k": weighted_window_k / comfort_weight_total,
            "wall_k": weighted_wall_k / comfort_weight_total,
            "total_k": weighted_total_k / comfort_weight_total,
            "operative_denominator": weighted_denominator / comfort_weight_total,
            "window_envelope_adjustment": sum(
                [
                    float(room_diagnostic.get("window_envelope_adjustment", 0.0))
                    * float(room_diagnostic.get("comfort_weight", 0.0))
                    for room_diagnostic in room_diagnostics
                ]
            )
            / comfort_weight_total,
            "wall_envelope_adjustment": sum(
                [
                    float(room_diagnostic.get("wall_envelope_adjustment", 0.0))
                    * float(room_diagnostic.get("comfort_weight", 0.0))
                    for room_diagnostic in room_diagnostics
                ]
            )
            / comfort_weight_total,
            "room_adjustment_minimum": room_adjustment_minimums[zone.key],
            "room_adjustment_maximum": room_adjustment_maximums[zone.key],
            "room_adjustment_spread": room_adjustment_spreads[zone.key],
            "filter_warming_up": resolved_filter_warming_up[zone.key],
        }

    global_input_issues: list[str] = []
    _append_issue(global_input_issues, house_temperature_reading)
    _append_issue(global_input_issues, outdoor_sensor_reading)
    _append_issue(global_input_issues, weather_temperature_reading)

    return ComfortAdjustmentResult(
        zone_temperatures=zone_temperatures,
        outdoor_temperature=outdoor_temperature,
        effective_outdoor_temperatures=resolved_effective_outdoor_temperatures,
        effective_window_outdoor_temperatures=resolved_window_outdoor_temperatures,
        effective_wall_outdoor_temperatures=resolved_wall_outdoor_temperatures,
        solar_index=solar_index,
        filtered_solar_irradiances=resolved_filtered_solar_irradiances,
        operating_modes=operating_modes,
        operating_mode=operating_mode,
        operating_mode_source=operating_mode_source,
        operating_indoor_temperature=operating_indoor_temperature,
        operating_indoor_temperature_source=operating_indoor_temperature_source,
        reference_temperatures=reference_temperatures,
        envelope_adjustments=envelope_adjustments,
        solar_adjustments=solar_adjustments,
        fabric_solar_scores=fabric_solar_scores,
        raw_adjustments=raw_adjustments,
        adjustments=adjustments,
        calculation_validity=calculation_validity,
        zone_diagnostics=zone_diagnostics,
        global_input_issues=tuple(global_input_issues),
        filter_warming_up=resolved_filter_warming_up,
        room_adjustment_minimums=room_adjustment_minimums,
        room_adjustment_maximums=room_adjustment_maximums,
        room_adjustment_spreads=room_adjustment_spreads,
    )
