from __future__ import annotations

from dataclasses import dataclass
from math import ceil, exp, floor, isfinite, radians, sin
from typing import Mapping, Protocol


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


@dataclass(frozen=True)
class ComfortAdjustmentResult:
    zone_temperatures: Mapping[str, float | None]
    outdoor_temperature: float | None
    effective_outdoor_temperatures: Mapping[str, float | None]
    solar_index: float
    operating_modes: Mapping[str, str | None]
    reference_temperatures: Mapping[str, float | None]
    raw_adjustments: Mapping[str, float]
    adjustments: Mapping[str, float]


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


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _read_first_temperature(reader: StateReaderLike, primary_entity_id: str, fallback_entity_id: str | None) -> float | None:
    primary = _parse_float(reader.get_state(primary_entity_id))
    if primary is not None:
        return primary
    if fallback_entity_id is None:
        return None
    return _parse_float(reader.get_state(fallback_entity_id))


def resolve_zone_temperature(
    reader: StateReaderLike,
    zone: ComfortAdjustmentZoneConfig,
    house_temperature: float | None,
) -> float | None:
    room_temperatures: list[float] = []
    for room in zone.rooms:
        room_temperature = _read_first_temperature(
            reader,
            room.primary_temperature_entity_id,
            room.fallback_temperature_entity_id,
        )
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


def resolve_operating_mode(
    *,
    user_mode: object | None,
    hvac_action: object | None,
    configured_hvac_mode: object | None,
    zone_temperature: float | None,
    outdoor_temperature: float | None,
) -> str | None:
    selected_mode = _normalized_mode(user_mode)
    if selected_mode is not None:
        return selected_mode

    active_mode = _normalized_mode(hvac_action)
    if active_mode is not None:
        return active_mode

    if zone_temperature is None or outdoor_temperature is None:
        return None

    if outdoor_temperature <= zone_temperature - 0.5:
        return "heat"
    if outdoor_temperature >= zone_temperature + 0.5:
        return "cool"

    configured_mode = _normalized_mode(configured_hvac_mode)
    if configured_mode is not None:
        return configured_mode
    return "heat" if outdoor_temperature <= zone_temperature else "cool"


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


def resolve_outdoor_temperature(reader: StateReaderLike, config: ComfortAdjustmentConfig) -> float | None:
    outdoor_temperature = _read_first_temperature(
        reader,
        config.outdoor_temperature_entity_id,
        None,
    )
    if outdoor_temperature is not None:
        return outdoor_temperature
    return _parse_float(reader.get_attr(config.weather_entity_id, "temperature"))


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


def _cover_openness(reader: StateReaderLike, cover_entity_id: str) -> float:
    position = _parse_float(reader.get_attr(cover_entity_id, "current_position"))
    if position is None:
        return 0.5
    return _clamp(position, 0.0, 100.0) / 100.0


def _room_envelope_and_solar_access(
    reader: StateReaderLike,
    config: ComfortAdjustmentConfig,
    room: ComfortAdjustmentRoomConfig,
    cover_facades: Mapping[str, str | None],
) -> tuple[float, float]:
    if room.cover_entity_id is not None:
        openness = _cover_openness(reader, room.cover_entity_id)
        envelope_transmission = 0.75 + 0.25 * openness
        facade = cover_facades.get(room.cover_entity_id)
        direct_gain_factor = facade_direct_gain_factor(reader, config, facade)
        solar_access = 0.25 + 0.75 * openness * direct_gain_factor
        return envelope_transmission, solar_access

    direct_gain_factor = facade_direct_gain_factor(reader, config, room.facade)
    if room.solar_access_when_exposed is not None and room.solar_access_when_unexposed is not None:
        solar_access = room.solar_access_when_unexposed + (
            room.solar_access_when_exposed - room.solar_access_when_unexposed
        ) * direct_gain_factor
        return 1.0, solar_access
    if room.solar_access_when_unexposed is not None:
        return 1.0, room.solar_access_when_unexposed
    return 1.0, 0.25


def _zone_envelope_and_solar_access(
    reader: StateReaderLike,
    config: ComfortAdjustmentConfig,
    zone: ComfortAdjustmentZoneConfig,
    cover_facades: Mapping[str, str | None],
) -> tuple[float, float]:
    envelope_transmissions: list[float] = []
    solar_accesses: list[float] = []
    for room in zone.rooms:
        envelope_transmission, solar_access = _room_envelope_and_solar_access(
            reader,
            config,
            room,
            cover_facades,
        )
        envelope_transmissions.append(envelope_transmission)
        solar_accesses.append(solar_access)
    return sum(envelope_transmissions) / len(envelope_transmissions), sum(solar_accesses) / len(solar_accesses)


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
) -> ComfortAdjustmentResult:
    house_temperature = _parse_float(reader.get_state(config.house_temperature_entity_id))
    outdoor_temperature = resolve_outdoor_temperature(reader, config)
    solar_index = resolve_solar_index(reader, config)
    user_mode = reader.get_state(config.heatpump_mode_user_entity_id)
    configured_hvac_mode = reader.get_state(config.climate_entity_id)
    hvac_action = reader.get_attr(config.climate_entity_id, "hvac_action")

    zone_temperatures: dict[str, float | None] = {}
    resolved_effective_outdoor_temperatures: dict[str, float | None] = {}
    operating_modes: dict[str, str | None] = {}
    reference_temperatures: dict[str, float | None] = {}
    raw_adjustments: dict[str, float] = {}
    adjustments: dict[str, float] = {}
    for zone in config.zones:
        zone_temperature = resolve_zone_temperature(reader, zone, house_temperature)
        zone_temperatures[zone.key] = zone_temperature
        effective_outdoor_temperature = outdoor_temperature
        if effective_outdoor_temperatures is not None and zone.key in effective_outdoor_temperatures:
            effective_outdoor_temperature = effective_outdoor_temperatures[zone.key]
        resolved_effective_outdoor_temperatures[zone.key] = effective_outdoor_temperature
        operating_mode = resolve_operating_mode(
            user_mode=user_mode,
            hvac_action=hvac_action,
            configured_hvac_mode=configured_hvac_mode,
            zone_temperature=zone_temperature,
            outdoor_temperature=effective_outdoor_temperature,
        )
        operating_modes[zone.key] = operating_mode
        if zone_temperature is None or effective_outdoor_temperature is None or operating_mode is None:
            reference_temperatures[zone.key] = None
            raw_adjustments[zone.key] = 0.0
            adjustments[zone.key] = 0.0
            continue

        reference_temperature = _reference_temperature(
            zone.key,
            operating_mode,
            reference_zone_targets,
        )
        reference_temperatures[zone.key] = reference_temperature

        envelope_transmission, solar_access = _zone_envelope_and_solar_access(
            reader,
            config,
            zone,
            cover_facades,
        )
        temperature_gap = _clamp((reference_temperature - effective_outdoor_temperature) / 10.0, -1.0, 1.0)
        if zone.upstairs:
            solar_coefficient = 1.35 if operating_mode == "heat" else 1.50
            raw_adjustment = 1.10 * envelope_transmission * temperature_gap - solar_coefficient * solar_access * solar_index
        else:
            solar_coefficient = 1.00 if operating_mode == "heat" else 1.20
            raw_adjustment = 1.25 * temperature_gap - solar_coefficient * solar_access * solar_index
        raw_adjustments[zone.key] = raw_adjustment
        adjustments[zone.key] = round_comfort_adjustment(raw_adjustment)

    return ComfortAdjustmentResult(
        zone_temperatures=zone_temperatures,
        outdoor_temperature=outdoor_temperature,
        effective_outdoor_temperatures=resolved_effective_outdoor_temperatures,
        solar_index=solar_index,
        operating_modes=operating_modes,
        reference_temperatures=reference_temperatures,
        raw_adjustments=raw_adjustments,
        adjustments=adjustments,
    )
