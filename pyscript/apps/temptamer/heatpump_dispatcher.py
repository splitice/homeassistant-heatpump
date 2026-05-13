from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Protocol

from .config import DEFAULT_SYSTEM_CONFIG
from .constants import (
    COMFORT_MODE_OFF,
    CONTROL_HVAC_MODE_OFF,
    HVAC_COOL,
    FAN_LOW,
    FAN_MEDIUM,
    HEAT_START_MEDIUM_FAN_DIFFERENTIAL,
    HVAC_FAN_ONLY,
    HVAC_HEAT,
    HVAC_OFF,
    MIN_IDLE_SECONDS,
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


def _normalize_timestamp(value: datetime | None) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def resolve_idle_started_at(
    idle_started_at: datetime | None,
    plan: DispatchPlan,
    *,
    current_hvac_mode: str | None,
    now: datetime | None,
) -> datetime | None:
    normalized_idle_started_at = _normalize_timestamp(idle_started_at)
    current_mode = (current_hvac_mode or "").lower()

    if plan.idle:
        return normalized_idle_started_at or _normalize_timestamp(now)
    if current_mode == HVAC_OFF:
        return None
    if plan.turn_off and normalized_idle_started_at is not None and current_mode in {HVAC_HEAT, HVAC_COOL}:
        return normalized_idle_started_at
    return None


def _maintain_trim_score(snapshot: DemandSnapshot, zone_keys: tuple[str, ...], *, cooling: bool) -> int:
    score = 0
    for zone_key in zone_keys:
        zone = snapshot.zones[zone_key]
        current_temp = zone.current_temp
        scheme = zone.cool_scheme if cooling else zone.scheme
        distance_to_ideal = abs(current_temp - scheme.ideal_target)
        distance_to_continue = abs(current_temp - scheme.continue_until)
        score += 1 if distance_to_ideal <= distance_to_continue else -1
        # Once a zone has already reached the continue threshold, bias the trim decision
        # further toward releasing heat/cooling instead of holding the current inlet target.
        reached_continue_threshold = current_temp <= scheme.continue_until if cooling else current_temp >= scheme.continue_until
        if reached_continue_threshold:
            score -= 1
    return score


def _requested_setpoint_raw(
    snapshot: DemandSnapshot,
    demand: EquipmentDemand,
    predicted_open_zones: tuple[str, ...],
) -> float:
    if demand.cool_requested:
        if demand.requested_by_zones:
            zone = snapshot.zones[demand.requested_by_zones[0]]
            return zone.cool_scheme.enable_outside
        return snapshot.inlet_temp

    if demand.maintain_cool_mode:
        trim_score = _maintain_trim_score(snapshot, predicted_open_zones, cooling=True)
        return snapshot.inlet_temp if trim_score > 0 else snapshot.inlet_temp + 1.0

    if demand.heat_requested and demand.requested_by_zones:
        zone = snapshot.zones[demand.requested_by_zones[0]]
        minimum_room_target = zone.scheme.enable_outside
        inlet_offset_target = snapshot.inlet_temp + SETPOINT_DELTA_FROM_INLET
        return max(minimum_room_target, inlet_offset_target)

    if demand.maintain_heat_mode:
        trim_score = _maintain_trim_score(snapshot, predicted_open_zones, cooling=False)
        return snapshot.inlet_temp if trim_score > 0 else snapshot.inlet_temp - 1.0

    return snapshot.inlet_temp


def _requested_setpoint(
    snapshot: DemandSnapshot,
    demand: EquipmentDemand,
    predicted_open_zones: tuple[str, ...],
) -> int:
    raw_requested_setpoint = _requested_setpoint_raw(snapshot, demand, predicted_open_zones)
    normalized_setpoint = normalize_setpoint(raw_requested_setpoint)

    if demand.cool_requested and demand.requested_by_zones:
        zone = snapshot.zones[demand.requested_by_zones[0]]
        LOGGER.info(
            "SETPOINT: inlet_temp=%.1f zone=%s enable_outside=%.1f raw=%.1f normalized=%s",
            snapshot.inlet_temp,
            zone.key,
            zone.cool_scheme.enable_outside,
            raw_requested_setpoint,
            normalized_setpoint,
        )
        return normalized_setpoint

    if demand.maintain_cool_mode:
        LOGGER.info(
            "SETPOINT: inlet_temp=%.1f mode=maintain_cool zones=%s score=%s raw=%.1f normalized=%s",
            snapshot.inlet_temp,
            ",".join(predicted_open_zones) if predicted_open_zones else "none",
            _maintain_trim_score(snapshot, predicted_open_zones, cooling=True),
            raw_requested_setpoint,
            normalized_setpoint,
        )
        return normalized_setpoint

    if demand.heat_requested and demand.requested_by_zones:
        zone = snapshot.zones[demand.requested_by_zones[0]]
        LOGGER.info(
            "SETPOINT: inlet_temp=%.1f zone=%s enable_outside=%.1f raw=%.1f normalized=%s",
            snapshot.inlet_temp,
            zone.key,
            zone.scheme.enable_outside,
            raw_requested_setpoint,
            normalized_setpoint,
        )
        return normalized_setpoint

    if demand.maintain_heat_mode:
        LOGGER.info(
            "SETPOINT: inlet_temp=%.1f mode=maintain_heat zones=%s score=%s raw=%.1f normalized=%s",
            snapshot.inlet_temp,
            ",".join(predicted_open_zones) if predicted_open_zones else "none",
            _maintain_trim_score(snapshot, predicted_open_zones, cooling=False),
            raw_requested_setpoint,
            normalized_setpoint,
        )
        return normalized_setpoint

    LOGGER.info(
        "SETPOINT: inlet_temp=%.1f no heat-requested zones raw=%.1f normalized=%s",
        snapshot.inlet_temp,
        raw_requested_setpoint,
        normalized_setpoint,
    )
    return normalized_setpoint


def resolve_fan_mode(current_fan_mode: str | None, current_hvac_mode: str | None, demand: EquipmentDemand) -> str | None:
    if demand.fan_only_requested:
        return FAN_LOW

    if not (demand.heat_requested or demand.maintain_heat_mode or demand.cool_requested or demand.maintain_cool_mode):
        return None

    current_fan = (current_fan_mode or "").lower()
    differential = demand.max_temperature_deficit
    currently_heating = (current_hvac_mode or "").lower() == HVAC_HEAT
    currently_cooling = (current_hvac_mode or "").lower() == HVAC_COOL

    if demand.cool_requested or demand.maintain_cool_mode:
        if not currently_cooling:
            return FAN_MEDIUM if differential > HEAT_START_MEDIUM_FAN_DIFFERENTIAL else FAN_LOW
        if current_fan == FAN_MEDIUM:
            return FAN_LOW if differential < MEDIUM_TO_LOW_FAN_DIFFERENTIAL else FAN_MEDIUM
        if current_fan == FAN_LOW:
            return FAN_MEDIUM if differential > LOW_TO_MEDIUM_FAN_DIFFERENTIAL else FAN_LOW
        return FAN_MEDIUM if differential > HEAT_START_MEDIUM_FAN_DIFFERENTIAL else FAN_LOW

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
    idle_started_at: datetime | None = None,
    now: datetime | None = None,
) -> DispatchPlan:
    if snapshot.comfort_mode == COMFORT_MODE_OFF or snapshot.selected_hvac_mode == CONTROL_HVAC_MODE_OFF:
        return DispatchPlan(turn_off=True, open_zones=predicted_open_zones, reason="comfort mode is Off")

    if not predicted_open_zones and (
        demand.heat_requested
        or demand.maintain_heat_mode
        or demand.fan_only_requested
        or demand.cool_requested
        or demand.maintain_cool_mode
    ):
        return DispatchPlan(turn_off=True, open_zones=predicted_open_zones, reason="no zones open for safe dispatch")

    if demand.fan_only_requested:
        return DispatchPlan(
            turn_off=False,
            hvac_mode=HVAC_FAN_ONLY,
            fan_mode=resolve_fan_mode(current_fan_mode, current_hvac_mode, demand),
            setpoint=_requested_setpoint(snapshot, demand, predicted_open_zones),
            open_zones=predicted_open_zones,
            reason=demand.reason,
        )

    if demand.maintain_heat_mode:
        raw_requested_setpoint = _requested_setpoint_raw(snapshot, demand, predicted_open_zones)
        if raw_requested_setpoint < MIN_HEAT_SETPOINT:
            return DispatchPlan(
                turn_off=False,
                hvac_mode=HVAC_FAN_ONLY,
                fan_mode=FAN_LOW,
                requested_by_zones=demand.requested_by_zones,
                open_zones=predicted_open_zones,
                reason=demand.reason,
            )
        return DispatchPlan(
            turn_off=False,
            hvac_mode=HVAC_HEAT,
            fan_mode=resolve_fan_mode(current_fan_mode, current_hvac_mode, demand),
            setpoint=_requested_setpoint(snapshot, demand, predicted_open_zones),
            requested_by_zones=demand.requested_by_zones,
            open_zones=predicted_open_zones,
            reason=demand.reason,
        )

    if demand.heat_requested:
        return DispatchPlan(
            turn_off=False,
            hvac_mode=HVAC_HEAT,
            fan_mode=resolve_fan_mode(current_fan_mode, current_hvac_mode, demand),
            setpoint=_requested_setpoint(snapshot, demand, predicted_open_zones),
            requested_by_zones=demand.requested_by_zones,
            open_zones=predicted_open_zones,
            reason=demand.reason,
        )

    if demand.cool_requested or demand.maintain_cool_mode:
        return DispatchPlan(
            turn_off=False,
            hvac_mode=HVAC_COOL,
            fan_mode=resolve_fan_mode(current_fan_mode, current_hvac_mode, demand),
            setpoint=_requested_setpoint(snapshot, demand, predicted_open_zones),
            requested_by_zones=demand.requested_by_zones,
            open_zones=predicted_open_zones,
            reason=demand.reason,
        )
      
    current_mode = (current_hvac_mode or "").lower()
    if current_mode in {HVAC_HEAT, HVAC_COOL}:
        normalized_now = _normalize_timestamp(now)
        normalized_idle_started_at = _normalize_timestamp(idle_started_at)
        if (
            normalized_now is not None
            and normalized_idle_started_at is not None
            and normalized_now - normalized_idle_started_at >= timedelta(seconds=MIN_IDLE_SECONDS)
        ):
            return DispatchPlan(turn_off=True, open_zones=predicted_open_zones, reason=demand.reason)
        return DispatchPlan(
            idle=True,
            hvac_mode=current_mode,
            setpoint=normalize_setpoint(snapshot.inlet_temp),
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
