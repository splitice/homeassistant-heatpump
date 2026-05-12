from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Protocol

from .config import DEFAULT_SYSTEM_CONFIG
from .constants import (
    COMFORT_MODE_AUTO,
    COMFORT_MODE_OFF,
    CONTROL_HVAC_MODE_COOL,
    CONTROL_HVAC_MODE_HEAT,
    CONTROL_HVAC_MODE_HEATCOOL,
    CONTROL_HVAC_MODE_MANUAL,
    CONTROL_HVAC_MODE_OFF,
    SCHEME_OFF,
    SWITCH_ON_STATES,
    SWITCH_STATE_SETTLE_SECONDS,
    UNKNOWN_STATES,
)
from .models import DemandSnapshot, SystemConfig, ZoneRuntimeState


CLIMATE_CURRENT_TEMPERATURE_ATTR = "current_temperature"


class StateReader(Protocol):
    def get_state(self, entity_id: str) -> object | None: ...

    def get_attr(self, entity_id: str, attr_name: str) -> object | None: ...


VALID_HVAC_MODES = frozenset(
    {
        CONTROL_HVAC_MODE_HEAT,
        CONTROL_HVAC_MODE_COOL,
        CONTROL_HVAC_MODE_HEATCOOL,
        CONTROL_HVAC_MODE_OFF,
        CONTROL_HVAC_MODE_MANUAL,
    }
)


def _normalize_timestamp(value: object | None) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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


def _resolve_switch_state(
    reader: StateReader,
    entity_id: str,
    *,
    pending_switch_state: object | None,
    last_switch_change: object | None,
    now: datetime | None,
) -> bool:
    actual_switch_state = is_switch_on(reader.get_state(entity_id))
    if not isinstance(pending_switch_state, bool) or pending_switch_state == actual_switch_state:
        return actual_switch_state

    normalized_last_change = _normalize_timestamp(last_switch_change)
    normalized_now = _normalize_timestamp(now)
    if normalized_last_change is None or normalized_now is None:
        return actual_switch_state

    if normalized_now - normalized_last_change <= timedelta(seconds=SWITCH_STATE_SETTLE_SECONDS):
        return pending_switch_state

    return actual_switch_state


def _resolve_temperature(reader: StateReader, entity_id: str | None, fallback: float) -> float:
    if entity_id:
        value = parse_float(reader.get_state(entity_id))
        if value is not None:
            return value
    return fallback


def _resolve_entity_attribute_temperature(
    reader: StateReader,
    entity_id: str,
    attr_name: str,
    fallback: float | None = None,
) -> float | None:
    value = parse_float(reader.get_attr(entity_id, attr_name))
    if value is not None:
        return value
    return fallback


def _resolve_house_temperature(reader: StateReader, config: SystemConfig) -> float:
    raw_house_temp = reader.get_state(config.house_temperature_sensor)
    house_temp = parse_float(raw_house_temp)
    if house_temp is not None:
        return house_temp

    raw_inlet_temp = _resolve_entity_attribute_temperature(
        reader,
        config.climate_entity,
        CLIMATE_CURRENT_TEMPERATURE_ATTR,
    )
    inlet_temp = raw_inlet_temp
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
        f"inlet={config.climate_entity}.{CLIMATE_CURRENT_TEMPERATURE_ATTR}:{raw_inlet_temp!r}, "
        f"zones={attempted_zone_values!r}"
    )


def build_snapshot(
    reader: StateReader,
    *,
    config: SystemConfig = DEFAULT_SYSTEM_CONFIG,
    last_switch_changes: Mapping[str, object] | None = None,
    pending_switch_states: Mapping[str, object] | None = None,
    now: datetime | None = None,
) -> DemandSnapshot:
    last_switch_changes = last_switch_changes or {}
    pending_switch_states = pending_switch_states or {}
    raw_mode = reader.get_state(config.comfort_mode_entity)
    comfort_mode = str(raw_mode) if raw_mode in config.comfort_modes else COMFORT_MODE_OFF
    raw_hvac_mode = reader.get_state(config.hvac_mode_entity)
    selected_hvac_mode = str(raw_hvac_mode) if raw_hvac_mode in VALID_HVAC_MODES else CONTROL_HVAC_MODE_HEAT

    house_temp = _resolve_house_temperature(reader, config)

    inlet_temp = _resolve_entity_attribute_temperature(
        reader,
        config.climate_entity,
        CLIMATE_CURRENT_TEMPERATURE_ATTR,
        house_temp,
    )

    zones: dict[str, ZoneRuntimeState] = {}
    for zone_key, zone in config.zones.items():
        override_entity_id = config.zone_comfort_mode_entities.get(zone_key)
        raw_override_mode = reader.get_state(override_entity_id) if override_entity_id else None
        override_mode = str(raw_override_mode)
        zone_override_schemes = config.zone_override_schemes.get(zone_key, {})
        if override_mode != COMFORT_MODE_AUTO and raw_override_mode in config.comfort_modes:
            applied_comfort_mode = override_mode
        else:
            applied_comfort_mode = comfort_mode
        comfort_mapping = config.comfort_modes[applied_comfort_mode]
        scheme_name = zone_override_schemes.get(override_mode, comfort_mapping.get(zone_key, SCHEME_OFF))
        scheme = config.heat_control_schemes[scheme_name]
        cool_scheme = config.cool_control_schemes[scheme_name]
        current_temp = _resolve_temperature(reader, zone.sensor_entity_id, house_temp)
        zones[zone_key] = ZoneRuntimeState(
            key=zone_key,
            current_temp=current_temp,
            scheme=scheme,
            cool_scheme=cool_scheme,
            applied_comfort_mode=applied_comfort_mode,
            is_enabled_by_mode=scheme.name != SCHEME_OFF,
            switch_is_on=_resolve_switch_state(
                reader,
                zone.switch_entity_id,
                pending_switch_state=pending_switch_states.get(zone_key),
                last_switch_change=last_switch_changes.get(zone_key),
                now=now,
            ),
            last_switch_change=_normalize_timestamp(last_switch_changes.get(zone_key)),
        )

    enabled_zones: dict[str, ZoneRuntimeState] = {}
    heat_calling_list: list[str] = []
    continue_heating_list: list[str] = []
    below_ideal_list: list[str] = []
    at_ideal_list: list[str] = []
    cool_calling_list: list[str] = []
    continue_cooling_list: list[str] = []
    above_ideal_list: list[str] = []
    at_or_below_ideal_list: list[str] = []

    for key, zone in zones.items():
        if not zone.is_enabled_by_mode:
            continue

        enabled_zones[key] = zone
        if zone.current_temp < zone.scheme.enable_outside:
            heat_calling_list.append(key)
        if zone.current_temp < zone.scheme.continue_until:
            continue_heating_list.append(key)
        if zone.current_temp < zone.scheme.ideal_target:
            below_ideal_list.append(key)
        else:
            at_ideal_list.append(key)
        if zone.current_temp > zone.cool_scheme.enable_outside:
            cool_calling_list.append(key)
        if zone.current_temp > zone.cool_scheme.continue_until:
            continue_cooling_list.append(key)
        if zone.current_temp > zone.cool_scheme.ideal_target:
            above_ideal_list.append(key)
        else:
            at_or_below_ideal_list.append(key)

    heat_calling = tuple(heat_calling_list)
    continue_heating = tuple(continue_heating_list)
    below_ideal = tuple(below_ideal_list)
    at_ideal = tuple(at_ideal_list)
    cool_calling = tuple(cool_calling_list)
    continue_cooling = tuple(continue_cooling_list)
    above_ideal = tuple(above_ideal_list)
    at_or_below_ideal = tuple(at_or_below_ideal_list)

    return DemandSnapshot(
        comfort_mode=comfort_mode,
        selected_hvac_mode=selected_hvac_mode,
        inlet_temp=inlet_temp,
        zones=zones,
        heat_calling_zones=heat_calling,
        continue_heating_zones=continue_heating,
        below_ideal_zones=below_ideal,
        at_ideal_zones=at_ideal,
        cool_calling_zones=cool_calling,
        continue_cooling_zones=continue_cooling,
        above_ideal_zones=above_ideal,
        at_or_below_ideal_zones=at_or_below_ideal,
    )
