from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from .config import DEFAULT_SYSTEM_CONFIG
from .constants import COMFORT_MODE_OFF, SCHEME_OFF, SWITCH_ON_STATES, UNKNOWN_STATES
from .models import DemandSnapshot, SystemConfig, ZoneRuntimeState


class StateReader(Protocol):
    def get_state(self, entity_id: str) -> object | None: ...


def parse_float(value: object | None) -> float | None:
    if value is None:
        return None

    as_float = getattr(value, "as_float", None)
    if callable(as_float):
        try:
            return as_float(default=None)
        except TypeError:
            try:
                return as_float()
            except (TypeError, ValueError):
                return None

    if isinstance(value, str) and value.strip().lower() in UNKNOWN_STATES:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_switch_on(value: object | None) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in SWITCH_ON_STATES


def _resolve_temperature(reader: StateReader, entity_id: str | None, fallback: float) -> float:
    if entity_id:
        value = parse_float(reader.get_state(entity_id))
        if value is not None:
            return value
    return fallback


def _resolve_house_temperature(reader: StateReader, config: SystemConfig) -> float:
    raw_house_temp = reader.get_state(config.house_temperature_sensor)
    house_temp = parse_float(raw_house_temp)
    if house_temp is not None:
        return house_temp

    raw_inlet_temp = reader.get_state(config.inlet_temperature_sensor)
    inlet_temp = parse_float(raw_inlet_temp)
    if inlet_temp is not None:
        return inlet_temp

    attempted_zone_values: dict[str, object | None] = {}
    for zone in config.zones.values():
        raw_zone_temp = reader.get_state(zone.sensor_entity_id)
        attempted_zone_values[zone.key] = raw_zone_temp
        zone_temp = parse_float(raw_zone_temp)
        if zone_temp is not None:
            return zone_temp

    raise ValueError(
        "No usable temperature source is available; "
        f"house={config.house_temperature_sensor}:{raw_house_temp!r}, "
        f"inlet={config.inlet_temperature_sensor}:{raw_inlet_temp!r}, "
        f"zones={attempted_zone_values!r}"
    )


def build_snapshot(
    reader: StateReader,
    *,
    config: SystemConfig = DEFAULT_SYSTEM_CONFIG,
    last_switch_changes: Mapping[str, object] | None = None,
) -> DemandSnapshot:
    last_switch_changes = last_switch_changes or {}
    raw_mode = reader.get_state(config.comfort_mode_entity)
    comfort_mode = str(raw_mode) if raw_mode in config.comfort_modes else COMFORT_MODE_OFF

    house_temp = _resolve_house_temperature(reader, config)

    inlet_temp = _resolve_temperature(reader, config.inlet_temperature_sensor, house_temp)
    comfort_mapping = config.comfort_modes[comfort_mode]

    zones: dict[str, ZoneRuntimeState] = {}
    for zone_key, zone in config.zones.items():
        scheme_name = comfort_mapping.get(zone_key, SCHEME_OFF)
        scheme = config.control_schemes[scheme_name]
        current_temp = _resolve_temperature(reader, zone.sensor_entity_id, house_temp)
        zones[zone_key] = ZoneRuntimeState(
            key=zone_key,
            current_temp=current_temp,
            scheme=scheme,
            is_enabled_by_mode=scheme.name != SCHEME_OFF,
            switch_is_on=is_switch_on(reader.get_state(zone.switch_entity_id)),
            last_switch_change=last_switch_changes.get(zone_key),  # type: ignore[arg-type]
        )

    enabled_zones: dict[str, ZoneRuntimeState] = {}
    heat_calling_list: list[str] = []
    continue_heating_list: list[str] = []
    below_ideal_list: list[str] = []
    at_ideal_list: list[str] = []

    for key, zone in zones.items():
        if not zone.is_enabled_by_mode:
            continue

        enabled_zones[key] = zone
        if zone.current_temp < zone.scheme.enable_below:
            heat_calling_list.append(key)
        if zone.current_temp < zone.scheme.continue_until:
            continue_heating_list.append(key)
        if zone.current_temp < zone.scheme.ideal_target:
            below_ideal_list.append(key)
        else:
            at_ideal_list.append(key)

    heat_calling = tuple(heat_calling_list)
    continue_heating = tuple(continue_heating_list)
    below_ideal = tuple(below_ideal_list)
    at_ideal = tuple(at_ideal_list)

    return DemandSnapshot(
        comfort_mode=comfort_mode,
        inlet_temp=inlet_temp,
        zones=zones,
        heat_calling_zones=heat_calling,
        continue_heating_zones=continue_heating,
        below_ideal_zones=below_ideal,
        at_ideal_zones=at_ideal,
    )

