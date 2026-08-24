from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import ceil, exp, floor, isfinite, radians, sin
from typing import Mapping, Protocol


_UNSET = object()


class StateReaderLike(Protocol):
    def get_state(self, entity_id: str) -> object | None: ...

    def get_attr(self, entity_id: str, attr_name: str) -> object | None: ...


@dataclass(frozen=True)
class ComfortAdjustmentRoomConfig:
    primary_temperature_entity_id: str
    fallback_temperature_entity_id: str | None = None
    cover_entity_id: str | None = None
    facade: str | None = None
    solar_access_when_exposed: float | None = None
    solar_access_when_unexposed: float | None = None


@dataclass(frozen=True)
class ComfortAdjustmentZoneConfig:
    key: str
    output_entity_id: str
    fallback_temperature_entity_id: str | None
    rooms: tuple[ComfortAdjustmentRoomConfig, ...]
    upstairs: bool


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
    outdoor_filter_time_constant_seconds: int
    downstairs_outdoor_filter_time_constant_seconds: int
    output_hysteresis: float
    # Phase 1 guardrails and diagnostics.  The window U-value is diagnostic
    # only until the Phase 2 envelope calculation replaces the legacy model.
    last_valid_hold_seconds: int = 5 * 60
    indoor_temperature_min_celsius: float = -10.0
    indoor_temperature_max_celsius: float = 50.0
    outdoor_temperature_min_celsius: float = -30.0
    outdoor_temperature_max_celsius: float = 60.0
    input_stale_after_seconds: int = 30 * 60
    diagnostic_window_u_value: float = 6.9
    diagnostic_shutter_closed_u_multiplier: float = 0.75


@dataclass(frozen=True)
class ComfortAdjustmentResult:
    zone_temperatures: Mapping[str, float | None]
    outdoor_temperature: float | None
    effective_outdoor_temperatures: Mapping[str, float | None]
    effective_window_outdoor_temperatures: Mapping[str, float | None]
    effective_wall_outdoor_temperatures: Mapping[str, float | None]
    solar_index: float
    operating_modes: Mapping[str, str | None]
    operating_mode: str | None
    operating_mode_source: str
    operating_indoor_temperature: float | None
    operating_indoor_temperature_source: str
    reference_temperatures: Mapping[str, float | None]
    envelope_adjustments: Mapping[str, float]
    solar_adjustments: Mapping[str, float]
    raw_adjustments: Mapping[str, float]
    adjustments: Mapping[str, float]
    calculation_validity: Mapping[str, bool]
    zone_diagnostics: Mapping[str, Mapping[str, object]]
    global_input_issues: tuple[str, ...]


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
                "aggregation_weight": 0.0,
            }
        )

    if room_values:
        aggregation_weight = 1.0 / len(room_values)
        for room_diagnostic in room_diagnostics:
            if room_diagnostic["temperature"] is not None:
                room_diagnostic["aggregation_weight"] = aggregation_weight
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
    zone_temperature: float | None,
    outdoor_temperature: float | None,
) -> str | None:
    """Return only the mode for existing callers; diagnostics use the source-aware form."""
    mode, _source = resolve_operating_mode_with_source(
        user_mode=user_mode,
        hvac_action=hvac_action,
        configured_hvac_mode=configured_hvac_mode,
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


def facade_direct_gain_factor(
    reader: StateReaderLike,
    config: ComfortAdjustmentConfig,
    facade: str | None,
) -> float:
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
        return 0.0
    if sun_elevation <= minimum_elevation:
        return 0.0

    azimuth_difference = _azimuth_difference(sun_azimuth, facade_azimuth)
    if exposure_half_band <= 0.0:
        return 1.0 if azimuth_difference == 0.0 else 0.0
    if azimuth_difference > exposure_half_band:
        return 0.0
    return 1.0 - azimuth_difference / exposure_half_band


def _cover_details(
    reader: StateReaderLike,
    config: ComfortAdjustmentConfig,
    cover_entity_id: str | None,
) -> dict[str, object]:
    if cover_entity_id is None:
        return {
            "cover_entity_id": None,
            "cover_state": "unshuttered",
            "cover_position": None,
            "cover_position_status": "not_applicable",
            "openness": 1.0,
            "effective_u_value": config.diagnostic_window_u_value,
            "effective_u_value_model": "phase_1_legacy_transmission_proxy",
        }

    position = _parse_float(reader.get_attr(cover_entity_id, "current_position"))
    if position is None:
        openness = 0.5
        position_status = "missing_or_rejected_defaulted"
    elif position < 0.0 or position > 100.0:
        openness = 0.5
        position_status = "rejected_implausible_defaulted"
    else:
        openness = position / 100.0
        position_status = "valid"

    if openness <= 0.0:
        cover_state = "closed"
    elif openness >= 1.0:
        cover_state = "open"
    else:
        cover_state = "partial"
    envelope_transmission = config.diagnostic_shutter_closed_u_multiplier + (
        1.0 - config.diagnostic_shutter_closed_u_multiplier
    ) * openness
    return {
        "cover_entity_id": cover_entity_id,
        "cover_state": cover_state,
        "cover_position": position if position_status == "valid" else None,
        "cover_position_status": position_status,
        "openness": openness,
        "effective_u_value": config.diagnostic_window_u_value * envelope_transmission,
        "effective_u_value_model": "phase_1_legacy_transmission_proxy",
    }


def _room_envelope_and_solar_access(
    reader: StateReaderLike,
    config: ComfortAdjustmentConfig,
    room: ComfortAdjustmentRoomConfig,
    cover_facades: Mapping[str, str | None],
) -> tuple[float, float, dict[str, object]]:
    if room.cover_entity_id is not None:
        cover_details = _cover_details(reader, config, room.cover_entity_id)
        openness = float(cover_details["openness"])
        envelope_transmission = config.diagnostic_shutter_closed_u_multiplier + (
            1.0 - config.diagnostic_shutter_closed_u_multiplier
        ) * openness
        facade = cover_facades.get(room.cover_entity_id)
        direct_gain_factor = facade_direct_gain_factor(reader, config, facade)
        solar_access = 0.25 + 0.75 * openness * direct_gain_factor
        cover_details["facade"] = facade
        cover_details["direct_gain_factor"] = direct_gain_factor
        cover_details["envelope_transmission"] = envelope_transmission
        cover_details["solar_access"] = solar_access
        return envelope_transmission, solar_access, cover_details

    direct_gain_factor = facade_direct_gain_factor(reader, config, room.facade)
    room_details = _cover_details(reader, config, None)
    room_details["facade"] = room.facade
    room_details["direct_gain_factor"] = direct_gain_factor
    if room.solar_access_when_exposed is not None and room.solar_access_when_unexposed is not None:
        solar_access = room.solar_access_when_unexposed + (
            room.solar_access_when_exposed - room.solar_access_when_unexposed
        ) * direct_gain_factor
        room_details["envelope_transmission"] = 1.0
        room_details["solar_access"] = solar_access
        return 1.0, solar_access, room_details
    if room.solar_access_when_unexposed is not None:
        room_details["envelope_transmission"] = 1.0
        room_details["solar_access"] = room.solar_access_when_unexposed
        return 1.0, room.solar_access_when_unexposed, room_details
    room_details["envelope_transmission"] = 1.0
    room_details["solar_access"] = 0.25
    return 1.0, 0.25, room_details


def _zone_envelope_and_solar_access(
    reader: StateReaderLike,
    config: ComfortAdjustmentConfig,
    zone: ComfortAdjustmentZoneConfig,
    cover_facades: Mapping[str, str | None],
) -> tuple[float, float, list[dict[str, object]]]:
    envelope_transmissions: list[float] = []
    solar_accesses: list[float] = []
    room_details: list[dict[str, object]] = []
    for room in zone.rooms:
        envelope_transmission, solar_access, details = _room_envelope_and_solar_access(
            reader,
            config,
            room,
            cover_facades,
        )
        envelope_transmissions.append(envelope_transmission)
        solar_accesses.append(solar_access)
        room_details.append(details)
    return (
        sum(envelope_transmissions) / len(envelope_transmissions),
        sum(solar_accesses) / len(solar_accesses),
        room_details,
    )


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
        zone_temperature=operating_indoor_temperature,
        outdoor_temperature=outdoor_temperature,
    )

    resolved_effective_outdoor_temperatures: dict[str, float | None] = {}
    operating_modes: dict[str, str | None] = {}
    reference_temperatures: dict[str, float | None] = {}
    envelope_adjustments: dict[str, float] = {}
    solar_adjustments: dict[str, float] = {}
    raw_adjustments: dict[str, float] = {}
    adjustments: dict[str, float] = {}
    calculation_validity: dict[str, bool] = {}
    zone_diagnostics: dict[str, Mapping[str, object]] = {}
    for zone in config.zones:
        zone_temperature = zone_temperatures[zone.key]
        effective_outdoor_temperature = outdoor_temperature
        if effective_outdoor_temperatures is not None and zone.key in effective_outdoor_temperatures:
            effective_outdoor_temperature = effective_outdoor_temperatures[zone.key]
        resolved_effective_outdoor_temperatures[zone.key] = effective_outdoor_temperature
        operating_modes[zone.key] = operating_mode
        input_issues = list(input_issues_by_zone[zone.key])
        _append_issue(input_issues, outdoor_sensor_reading)
        _append_issue(input_issues, weather_temperature_reading)
        if zone_temperature is None or effective_outdoor_temperature is None or operating_mode is None:
            reference_temperatures[zone.key] = None
            envelope_adjustments[zone.key] = 0.0
            solar_adjustments[zone.key] = 0.0
            raw_adjustments[zone.key] = 0.0
            adjustments[zone.key] = 0.0
            calculation_validity[zone.key] = False
            zone_diagnostics[zone.key] = {
                "calculation_status": "unavailable",
                "zone_temperature_source": zone_temperature_sources[zone.key],
                "room_values": room_diagnostics_by_zone[zone.key],
                "input_issues": tuple(input_issues),
                "outdoor_temperature_source": outdoor_temperature_source,
                "effective_outdoor_model": "phase_1_shared_filter",
            }
            continue

        reference_temperature = _reference_temperature(
            zone.key,
            operating_mode,
            reference_zone_targets,
        )
        reference_temperatures[zone.key] = reference_temperature

        envelope_transmission, solar_access, envelope_room_details = _zone_envelope_and_solar_access(
            reader,
            config,
            zone,
            cover_facades,
        )
        room_diagnostics = room_diagnostics_by_zone[zone.key]
        for room_index, envelope_room_detail in enumerate(envelope_room_details):
            if room_index < len(room_diagnostics):
                room_diagnostics[room_index].update(envelope_room_detail)
        temperature_gap = _clamp((reference_temperature - effective_outdoor_temperature) / 10.0, -1.0, 1.0)
        if zone.upstairs:
            solar_coefficient = 1.35 if operating_mode == "heat" else 1.50
            envelope_adjustment = 1.10 * envelope_transmission * temperature_gap
        else:
            solar_coefficient = 1.00 if operating_mode == "heat" else 1.20
            envelope_adjustment = 1.25 * temperature_gap
        solar_adjustment = -solar_coefficient * solar_access * solar_index
        raw_adjustment = envelope_adjustment + solar_adjustment
        envelope_adjustments[zone.key] = envelope_adjustment
        solar_adjustments[zone.key] = solar_adjustment
        raw_adjustments[zone.key] = raw_adjustment
        adjustments[zone.key] = round_comfort_adjustment(raw_adjustment)
        calculation_validity[zone.key] = True
        zone_diagnostics[zone.key] = {
            "calculation_status": "calculated",
            "zone_temperature_source": zone_temperature_sources[zone.key],
            "room_values": room_diagnostics,
            "room_aggregation": "equal_mean_of_available_rooms",
            "input_issues": tuple(input_issues),
            "outdoor_temperature_source": outdoor_temperature_source,
            "effective_outdoor_model": "phase_1_shared_filter",
            "envelope_transmission": envelope_transmission,
            "solar_access": solar_access,
        }

    global_input_issues: list[str] = []
    _append_issue(global_input_issues, house_temperature_reading)
    _append_issue(global_input_issues, outdoor_sensor_reading)
    _append_issue(global_input_issues, weather_temperature_reading)

    return ComfortAdjustmentResult(
        zone_temperatures=zone_temperatures,
        outdoor_temperature=outdoor_temperature,
        effective_outdoor_temperatures=resolved_effective_outdoor_temperatures,
        # Phase 1 records both elements explicitly while preserving the
        # existing shared filter.  Phase 3 gives windows and walls separate
        # thermal response filters.
        effective_window_outdoor_temperatures=resolved_effective_outdoor_temperatures,
        effective_wall_outdoor_temperatures=resolved_effective_outdoor_temperatures,
        solar_index=solar_index,
        operating_modes=operating_modes,
        operating_mode=operating_mode,
        operating_mode_source=operating_mode_source,
        operating_indoor_temperature=operating_indoor_temperature,
        operating_indoor_temperature_source=operating_indoor_temperature_source,
        reference_temperatures=reference_temperatures,
        envelope_adjustments=envelope_adjustments,
        solar_adjustments=solar_adjustments,
        raw_adjustments=raw_adjustments,
        adjustments=adjustments,
        calculation_validity=calculation_validity,
        zone_diagnostics=zone_diagnostics,
        global_input_issues=tuple(global_input_issues),
    )
