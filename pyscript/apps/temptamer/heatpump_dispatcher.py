from __future__ import annotations

import logging
import math
from typing import Protocol

from .config import DEFAULT_SYSTEM_CONFIG
from .constants import (
    COMFORT_MODE_OFF,
    FAN_LOW,
    FAN_MEDIUM,
    HEAT_START_MEDIUM_FAN_DIFFERENTIAL,
    HVAC_FAN_ONLY,
    HVAC_HEAT,
    LOW_TO_MEDIUM_FAN_DIFFERENTIAL,
    LOGGER_NAME,
    MAX_HEAT_SETPOINT,
    MEDIUM_TO_LOW_FAN_DIFFERENTIAL,
    MIN_HEAT_SETPOINT,
    SETPOINT_DELTA_FROM_INLET,
)
from .models import DemandSnapshot, DispatchPlan, EquipmentDemand, SystemConfig
from .state_reader import parse_float


LOGGER = logging.getLogger(LOGGER_NAME)


class ServiceController(Protocol):
    def call_service(self, domain: str, service: str, **kwargs: object) -> None: ...


def normalize_setpoint(value: float) -> int:
    return max(MIN_HEAT_SETPOINT, min(MAX_HEAT_SETPOINT, int(math.ceil(value))))


def _requested_setpoint(snapshot: DemandSnapshot, demand: EquipmentDemand) -> int:
    if demand.heat_requested and demand.requested_by_zones:
        zone = snapshot.zones[demand.requested_by_zones[0]]
        minimum_room_target = zone.scheme.enable_below
        temp_gap = minimum_room_target - snapshot.inlet_temp
        allowed_increase = min(temp_gap, SETPOINT_DELTA_FROM_INLET)
        inlet_cap_target = snapshot.inlet_temp + allowed_increase
        raw_requested_setpoint = max(minimum_room_target, inlet_cap_target)
        normalized_setpoint = normalize_setpoint(raw_requested_setpoint)
        LOGGER.info(
            "SETPOINT: inlet_temp=%.1f zone=%s enable_below=%.1f temp_gap=%.1f capped_delta=%.1f raw=%.1f normalized=%s",
            snapshot.inlet_temp,
            zone.key,
            minimum_room_target,
            temp_gap,
            allowed_increase,
            raw_requested_setpoint,
            normalized_setpoint,
        )
        return normalized_setpoint

    normalized_setpoint = normalize_setpoint(snapshot.inlet_temp)
    LOGGER.info(
        "SETPOINT: inlet_temp=%.1f no heat-requested zones raw=%.1f normalized=%s",
        snapshot.inlet_temp,
        snapshot.inlet_temp,
        normalized_setpoint,
    )
    return normalized_setpoint


def resolve_fan_mode(current_fan_mode: str | None, current_hvac_mode: str | None, demand: EquipmentDemand) -> str | None:
    if demand.fan_only_requested:
        return FAN_LOW

    if not (demand.heat_requested or demand.maintain_heat_mode):
        return None

    current_fan = (current_fan_mode or "").lower()
    differential = demand.max_temperature_deficit
    currently_heating = (current_hvac_mode or "").lower() == HVAC_HEAT

    if not currently_heating:
        return FAN_MEDIUM if differential > HEAT_START_MEDIUM_FAN_DIFFERENTIAL else FAN_LOW

    if current_fan == FAN_MEDIUM:
        return FAN_LOW if differential < MEDIUM_TO_LOW_FAN_DIFFERENTIAL else FAN_MEDIUM
    if current_fan == FAN_LOW:
        return FAN_MEDIUM if differential > LOW_TO_MEDIUM_FAN_DIFFERENTIAL else FAN_LOW
    return FAN_MEDIUM if differential > HEAT_START_MEDIUM_FAN_DIFFERENTIAL else FAN_LOW


def build_dispatch_plan(
    snapshot: DemandSnapshot,
    demand: EquipmentDemand,
    predicted_open_zones: tuple[str, ...],
    *,
    current_hvac_mode: str | None,
    current_fan_mode: str | None,
) -> DispatchPlan:
    if snapshot.comfort_mode == COMFORT_MODE_OFF:
        return DispatchPlan(turn_off=True, open_zones=predicted_open_zones, reason="comfort mode is Off")

    if not predicted_open_zones and (demand.heat_requested or demand.maintain_heat_mode or demand.fan_only_requested):
        return DispatchPlan(turn_off=True, open_zones=predicted_open_zones, reason="no zones open for safe dispatch")

    if demand.fan_only_requested:
        return DispatchPlan(
            turn_off=False,
            hvac_mode=HVAC_FAN_ONLY,
            fan_mode=resolve_fan_mode(current_fan_mode, current_hvac_mode, demand),
            setpoint=_requested_setpoint(snapshot, demand),
            open_zones=predicted_open_zones,
            reason=demand.reason,
        )

    if demand.heat_requested or demand.maintain_heat_mode:
        return DispatchPlan(
            turn_off=False,
            hvac_mode=HVAC_HEAT,
            fan_mode=resolve_fan_mode(current_fan_mode, current_hvac_mode, demand),
            setpoint=_requested_setpoint(snapshot, demand),
            requested_by_zones=demand.requested_by_zones,
            open_zones=predicted_open_zones,
            reason=demand.reason,
        )

    return DispatchPlan(turn_off=True, open_zones=predicted_open_zones, reason=demand.reason)


def apply_zone_actions(
    controller: ServiceController,
    zone_actions,
    *,
    config: SystemConfig = DEFAULT_SYSTEM_CONFIG,
) -> None:
    for action in zone_actions:
        entity_id = config.zones[action.zone_key].switch_entity_id
        controller.call_service("switch", "turn_on" if action.turn_on else "turn_off", entity_id=entity_id)


def apply_dispatch_plan(
    controller: ServiceController,
    plan: DispatchPlan,
    *,
    config: SystemConfig = DEFAULT_SYSTEM_CONFIG,
    current_hvac_mode: str | None,
    current_fan_mode: str | None,
    current_setpoint: object | None,
) -> None:
    entity_id = config.climate_entity
    normalized_setpoint = parse_float(current_setpoint)

    if plan.turn_off:
        if (current_hvac_mode or "").lower() != "off":
            controller.call_service("climate", "turn_off", entity_id=entity_id)
        return

    if plan.hvac_mode and (current_hvac_mode or "").lower() != plan.hvac_mode:
        controller.call_service("climate", "set_hvac_mode", entity_id=entity_id, hvac_mode=plan.hvac_mode)

    if plan.fan_mode and (current_fan_mode or "").lower() != plan.fan_mode:
        controller.call_service("climate", "set_fan_mode", entity_id=entity_id, fan_mode=plan.fan_mode)

    if plan.setpoint is not None and normalized_setpoint != float(plan.setpoint):
        controller.call_service("climate", "set_temperature", entity_id=entity_id, temperature=plan.setpoint)
