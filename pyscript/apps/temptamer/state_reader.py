from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Protocol

from .comfort_modes import ComfortModeSnapshotData
from .config import DEFAULT_SYSTEM_CONFIG
from .constants import (
    COMFORT_MODE_AUTO,
    COMFORT_MODE_OFF,
    COMFORT_MODE_POWER_OFF,
    COMFORT_SCORE_MAXIMUM,
    COMFORT_SCORE_MINIMUM,
    CONTROL_HVAC_MODE_COOL,
    CONTROL_HVAC_MODE_HEAT,
    CONTROL_HVAC_MODE_HEATCOOL,
    CONTROL_HVAC_MODE_MANUAL,
    CONTROL_HVAC_MODE_OFF,
    MIN_COOL_ROOM_TARGET,
    POWERDAY_HEATSOAK_FULL,
    POWERDAY_HEATSOAK_SUPPRESSED,
    SCHEME_OFF,
    SWITCH_ON_STATES,
    SWITCH_STATE_SETTLE_SECONDS,
    UNKNOWN_STATES,
)
from .models import ControlScheme, DemandSnapshot, SystemConfig, ZoneRuntimeState


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


def _resolve_optional_sensor(reader: StateReader, entity_id: str | None) -> float | None:
    if not entity_id:
        return None
    return parse_float(reader.get_state(entity_id))


def _resolve_manual_adjustment(reader: StateReader, entity_id: str | None) -> float:
    """Read a finite manual offset without limiting the user's adjustment."""
    if entity_id is None:
        return 0.0
    adjustment = parse_float(reader.get_state(entity_id))
    if adjustment is None or not isfinite(adjustment):
        return 0.0
    return adjustment


def _resolve_comfort_adjustment(reader: StateReader, entity_id: str | None) -> float:
    """Keep automatic comfort scores within the documented safe range."""
    adjustment = _resolve_manual_adjustment(reader, entity_id)
    return max(COMFORT_SCORE_MINIMUM, min(COMFORT_SCORE_MAXIMUM, adjustment))


def _shift_control_scheme(scheme: ControlScheme, setpoint_offset: float) -> ControlScheme:
    """Apply a conventional manual setpoint offset to every scheme threshold."""
    if setpoint_offset == 0.0:
        return scheme
    return replace(
        scheme,
        enable_outside=scheme.enable_outside + setpoint_offset,
        continue_until=scheme.continue_until + setpoint_offset,
        ideal_target=scheme.ideal_target + setpoint_offset,
    )


def _apply_comfort_score(scheme: ControlScheme, comfort_score: float) -> ControlScheme:
    """Lower targets for radiant warmth and raise them for a cold envelope."""
    return _shift_control_scheme(scheme, -comfort_score)


def _bound_adjusted_cool_scheme(scheme: ControlScheme) -> ControlScheme:
    """Keep adjusted cooling room thresholds ordered and above the safety floor.

    Translate the whole band when it crosses the floor so its hysteresis width
    is retained.  Then repair legacy configurations whose continue threshold
    is above their ideal target.
    """
    if scheme.name == SCHEME_OFF:
        return scheme

    coldest_threshold = min(scheme.enable_outside, scheme.ideal_target, scheme.continue_until)
    floor_shift = max(0.0, MIN_COOL_ROOM_TARGET - coldest_threshold)
    shifted_enable = scheme.enable_outside + floor_shift
    shifted_ideal = scheme.ideal_target + floor_shift
    shifted_continue = scheme.continue_until + floor_shift

    # Preserve all three values while assigning them to the cooling band's
    # required order.  This also repairs older configured bands whose ideal
    # and continuation thresholds were reversed.
    bounded_enable, bounded_ideal, bounded_continue = sorted(
        (shifted_enable, shifted_ideal, shifted_continue),
        reverse=True,
    )
    return replace(
        scheme,
        enable_outside=bounded_enable,
        ideal_target=bounded_ideal,
        continue_until=bounded_continue,
    )


def _can_use_min_sensor_for_heating(zone: ZoneRuntimeState, threshold: float) -> bool:
    if zone.min_temp is None or zone.min_temp >= threshold:
        return False
    if zone.max_temp is not None and zone.max_temp > zone.scheme.continue_until:
        return False
    return True


def _can_use_max_sensor_for_cooling(zone: ZoneRuntimeState, threshold: float) -> bool:
    """Mirror heating's minimum-sensor guard for a locally hot room."""
    if zone.max_temp is None or zone.max_temp <= threshold:
        return False
    if zone.min_temp is not None and zone.min_temp < zone.cool_scheme.continue_until:
        return False
    return True


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


def _is_supported_override_scheme(config: SystemConfig, scheme_name: str) -> bool:
    """Return whether a zone override can directly select this scheme for both heating and cooling."""
    return scheme_name in config.heat_control_schemes and scheme_name in config.cool_control_schemes


def _resolve_comfort_mode(raw_mode: object | None, config: SystemConfig) -> str:
    comfort_mode = str(raw_mode) if raw_mode is not None else ""
    return comfort_mode if comfort_mode in config.comfort_modes else COMFORT_MODE_OFF


def _resolve_comfort_mode_scheme_name(
    reader: StateReader,
    config: SystemConfig,
    comfort_mode: str,
    zone_key: str,
    snapshot_data: ComfortModeSnapshotData,
) -> str:
    comfort_mode_behavior = config.comfort_modes[comfort_mode]
    effective_mode = getattr(comfort_mode_behavior, "effective_mode", None)
    if callable(effective_mode):
        comfort_mode_behavior = effective_mode(snapshot_data)
    scheme_for_zone = getattr(comfort_mode_behavior, "scheme_for_zone", None)
    if callable(scheme_for_zone):
        return scheme_for_zone(zone_key, reader, snapshot_data)
    if isinstance(comfort_mode_behavior, Mapping):
        return comfort_mode_behavior.get(zone_key, SCHEME_OFF)
    return SCHEME_OFF


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
    heat_sink_available: bool = False,
    battery_free_power_boost_available: bool = False,
    free_power_later_available: bool = False,
    free_power_heat_soak_level: str = POWERDAY_HEATSOAK_FULL,
    poweroff_active: bool = False,
    now: datetime | None = None,
) -> DemandSnapshot:
    last_switch_changes = last_switch_changes or {}
    pending_switch_states = pending_switch_states or {}
    raw_mode = reader.get_state(config.comfort_mode_entity)
    comfort_mode = _resolve_comfort_mode(raw_mode, config)
    raw_hvac_mode = reader.get_state(config.hvac_mode_entity)
    selected_hvac_mode = str(raw_hvac_mode) if raw_hvac_mode in VALID_HVAC_MODES else CONTROL_HVAC_MODE_HEAT

    house_temp = _resolve_house_temperature(reader, config)

    inlet_temp = _resolve_entity_attribute_temperature(
        reader,
        config.climate_entity,
        CLIMATE_CURRENT_TEMPERATURE_ATTR,
        house_temp,
    )
    initial_snapshot_data = ComfortModeSnapshotData(
        comfort_mode=comfort_mode,
        selected_hvac_mode=selected_hvac_mode,
        inlet_temp=inlet_temp,
        free_power_available=False,
        heat_sink_available=False,
        battery_free_power_boost_available=False,
        poweroff_active=poweroff_active,
        now=now,
        free_power_heat_soak_level=POWERDAY_HEATSOAK_FULL,
        surplus_heat_sink_available=False,
    )
    comfort_mode_behavior = config.comfort_modes[comfort_mode]
    effective_mode = getattr(comfort_mode_behavior, "effective_mode", None)
    if callable(effective_mode):
        comfort_mode_behavior = effective_mode(initial_snapshot_data)
    free_power_is_available = getattr(comfort_mode_behavior, "free_power_is_available", None)
    free_power_available = free_power_is_available(reader) if callable(free_power_is_available) else False
    free_power_heat_sink_available = (
        free_power_available and free_power_heat_soak_level != POWERDAY_HEATSOAK_SUPPRESSED
    )
    resolved_heat_sink_available = free_power_heat_sink_available or bool(heat_sink_available)
    resolved_free_power_later_available = (
        free_power_available
        and free_power_heat_soak_level == POWERDAY_HEATSOAK_FULL
        and bool(free_power_later_available)
    )
    snapshot_data = ComfortModeSnapshotData(
        comfort_mode=comfort_mode,
        selected_hvac_mode=selected_hvac_mode,
        inlet_temp=inlet_temp,
        free_power_available=free_power_available,
        heat_sink_available=resolved_heat_sink_available,
        battery_free_power_boost_available=bool(battery_free_power_boost_available),
        free_power_later_available=resolved_free_power_later_available,
        poweroff_active=poweroff_active,
        now=now,
        free_power_heat_soak_level=free_power_heat_soak_level,
        surplus_heat_sink_available=bool(heat_sink_available),
    )

    zones: dict[str, ZoneRuntimeState] = {}
    for zone_key, zone in config.zones.items():
        override_entity_id = config.zone_comfort_mode_entities.get(zone_key)
        raw_override_mode = reader.get_state(override_entity_id) if override_entity_id else None
        override_mode = str(raw_override_mode)
        if raw_override_mode is not None and override_mode != COMFORT_MODE_AUTO and _is_supported_override_scheme(config, override_mode):
            applied_comfort_mode = override_mode
            scheme_name = override_mode
        elif raw_override_mode is not None and override_mode != COMFORT_MODE_AUTO and override_mode in config.comfort_modes:
            applied_comfort_mode = override_mode
            scheme_name = _resolve_comfort_mode_scheme_name(
                reader,
                config,
                applied_comfort_mode,
                zone_key,
                snapshot_data,
            )
        else:
            applied_comfort_mode = comfort_mode
            scheme_name = _resolve_comfort_mode_scheme_name(
                reader,
                config,
                applied_comfort_mode,
                zone_key,
                snapshot_data,
            )
        scheme = config.heat_control_schemes[scheme_name]
        cool_scheme = config.cool_control_schemes[scheme_name]
        temperature_sensor_entity_id = zone.scheme_sensor_entity_ids.get(scheme_name, zone.sensor_entity_id)
        current_temp = _resolve_temperature(reader, temperature_sensor_entity_id, house_temp)
        min_temp = _resolve_optional_sensor(reader, zone.min_sensor_entity_id)
        max_temp = _resolve_optional_sensor(reader, zone.max_sensor_entity_id)
        zone_state = ZoneRuntimeState(
            key=zone_key,
            current_temp=current_temp,
            min_temp=min_temp,
            max_temp=max_temp,
            setpoint_delta_from_inlet=zone.setpoint_delta_from_inlet,
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
        zones[zone_key] = zone_state

    downstairs_zone = zones.get("downstairs")
    adjustment_snapshot_data = replace(
        snapshot_data,
        downstairs_temp=downstairs_zone.current_temp if downstairs_zone is not None else None,
    )
    for zone_key, zone_state in zones.items():
        if zone_state.applied_comfort_mode == comfort_mode:
            zones[zone_key] = comfort_mode_behavior.adjust_zone(zone_state, adjustment_snapshot_data)

    # Capture the dynamic schemes after comfort-/power-mode supplements but
    # before applying the published comfort value or global manual offset.
    # These base targets are consumed by the independent publisher so an
    # adjustment never becomes its own future reference.
    base_zone_targets: dict[str, tuple[float, float]] = {}
    global_setpoint_adjustment = _resolve_manual_adjustment(
        reader,
        config.global_setpoint_adjustment_entity,
    )
    for zone_key, zone_state in zones.items():
        base_zone_targets[zone_key] = (zone_state.scheme.ideal_target, zone_state.cool_scheme.ideal_target)
        comfort_adjustment = _resolve_comfort_adjustment(
            reader,
            config.zone_comfort_adjustment_entities.get(zone_key),
        )
        zones[zone_key] = replace(
            zone_state,
            scheme=_apply_comfort_score(
                _shift_control_scheme(zone_state.scheme, global_setpoint_adjustment),
                comfort_adjustment,
            ),
            cool_scheme=_bound_adjusted_cool_scheme(
                _apply_comfort_score(
                    _shift_control_scheme(zone_state.cool_scheme, global_setpoint_adjustment),
                    comfort_adjustment,
                )
            ),
            comfort_adjustment=comfort_adjustment,
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
        # Primary activation: average/primary sensor below enable threshold
        if zone.current_temp < zone.scheme.enable_outside:
            heat_calling_list.append(key)
        else:
            # Secondary activation for heating: if a min sensor exists and it's below the enable threshold
            # AND the average/current temp is still below the ideal target
            if _can_use_min_sensor_for_heating(zone, zone.scheme.enable_outside) and zone.current_temp < zone.scheme.ideal_target:
                heat_calling_list.append(key)
        if zone.current_temp < zone.scheme.continue_until or _can_use_min_sensor_for_heating(zone, zone.scheme.continue_until):
            continue_heating_list.append(key)
        if zone.current_temp < zone.scheme.ideal_target:
            below_ideal_list.append(key)
        else:
            at_ideal_list.append(key)
        # Primary activation: average/primary sensor above enable threshold
        if zone.current_temp > zone.cool_scheme.enable_outside:
            cool_calling_list.append(key)
        else:
            # Secondary activation for cooling: if a max sensor exists and it's above the enable threshold
            # AND the average/current temp is still above the ideal target
            if _can_use_max_sensor_for_cooling(zone, zone.cool_scheme.enable_outside) and zone.current_temp > zone.cool_scheme.ideal_target:
                cool_calling_list.append(key)
        if zone.current_temp > zone.cool_scheme.continue_until or _can_use_max_sensor_for_cooling(
            zone,
            zone.cool_scheme.continue_until,
        ):
            continue_cooling_list.append(key)
        if zone.current_temp > zone.cool_scheme.ideal_target or _can_use_max_sensor_for_cooling(
            zone,
            zone.cool_scheme.ideal_target,
        ):
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
        comfort_mode_behavior=comfort_mode_behavior,
        selected_hvac_mode=selected_hvac_mode,
        inlet_temp=inlet_temp,
        free_power_available=free_power_available,
        heat_sink_available=resolved_heat_sink_available,
        battery_free_power_boost_available=bool(battery_free_power_boost_available),
        free_power_later_available=resolved_free_power_later_available,
        poweroff_forced_off=comfort_mode == COMFORT_MODE_POWER_OFF and not poweroff_active,
        zones=zones,
        heat_calling_zones=heat_calling,
        continue_heating_zones=continue_heating,
        below_ideal_zones=below_ideal,
        at_ideal_zones=at_ideal,
        cool_calling_zones=cool_calling,
        continue_cooling_zones=continue_cooling,
        above_ideal_zones=above_ideal,
        at_or_below_ideal_zones=at_or_below_ideal,
        base_zone_targets=base_zone_targets,
        global_setpoint_adjustment=global_setpoint_adjustment,
        free_power_heat_soak_level=free_power_heat_soak_level,
        surplus_heat_sink_available=bool(heat_sink_available),
    )
