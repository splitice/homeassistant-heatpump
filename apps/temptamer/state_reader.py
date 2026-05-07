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


def build_snapshot(
    reader: StateReader,
    *,
    config: SystemConfig = DEFAULT_SYSTEM_CONFIG,
    last_switch_changes: Mapping[str, object] | None = None,
) -> DemandSnapshot:
    last_switch_changes = last_switch_changes or {}
    raw_mode = reader.get_state(config.comfort_mode_entity)
    comfort_mode = str(raw_mode) if raw_mode in config.comfort_modes else COMFORT_MODE_OFF

    house_temp = parse_float(reader.get_state(config.house_temperature_sensor))
    if house_temp is None:
        raise ValueError(f"House temperature sensor {config.house_temperature_sensor} is unavailable")

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

    enabled_zones = {key: zone for key, zone in zones.items() if zone.is_enabled_by_mode}
    heat_calling = tuple(
        key for key, zone in enabled_zones.items() if zone.current_temp < zone.scheme.enable_below
    )
    continue_heating = tuple(
        key for key, zone in enabled_zones.items() if zone.current_temp < zone.scheme.continue_until
    )
    below_ideal = tuple(key for key, zone in enabled_zones.items() if zone.current_temp < zone.scheme.ideal_target)
    at_ideal = tuple(key for key, zone in enabled_zones.items() if zone.current_temp >= zone.scheme.ideal_target)

    return DemandSnapshot(
        comfort_mode=comfort_mode,
        inlet_temp=inlet_temp,
        zones=zones,
        heat_calling_zones=heat_calling,
        continue_heating_zones=continue_heating,
        below_ideal_zones=below_ideal,
        at_ideal_zones=at_ideal,
    )

