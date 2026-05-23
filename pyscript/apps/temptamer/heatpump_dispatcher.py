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
    IDLE_HEAT_STAGE_1_SECONDS,
    IDLE_HEAT_STAGE_2_SECONDS,
    IDLE_HEAT_STAGE_3_SECONDS,
    IDLE_HEAT_STAGE_4_SECONDS,
    IDLE_HEAT_STAGE_5_SECONDS,
    IDLE_HEAT_RESTART_MEMORY_SECONDS,
    IDLE_HEAT_STEP_1_DELTA,
    IDLE_HEAT_STEP_2_DELTA,
    IDLE_HEAT_STEP_3_DELTA,
    IDLE_HEAT_STEP_4_DELTA,
    IDLE_HEAT_STEP_5_DELTA,
    IDLE_HEAT_STEP_6_DELTA,
    IDLE_HEAT_STEP_7_DELTA,
    IDLE_HEAT_UNWIND_SECONDS,
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
IDLE_HEAT_ALLOWED_STEPS = (0, -1, -2, -3, -4, -5, -6, -7)


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


def _current_active_heat_step(
    snapshot: DemandSnapshot,
    current_setpoint: object | None,
    *,
    current_hvac_mode: str | None,
    idle_heat_step: int | None,
) -> int | None:
    if (current_hvac_mode or "").lower() != HVAC_HEAT:
        return None

    candidate_steps: list[int] = []
    tracked_step = idle_heat_step if idle_heat_step in IDLE_HEAT_ALLOWED_STEPS and idle_heat_step < 0 else None
    if tracked_step is not None:
        candidate_steps.append(tracked_step)

    current_setpoint_value = parse_float(current_setpoint)
    inferred_step = _infer_idle_heat_step(snapshot, current_setpoint_value) if current_setpoint_value is not None else None
    if inferred_step is not None:
        candidate_steps.append(inferred_step)

    return min(candidate_steps) if candidate_steps else None


def _requested_maintain_heat_raw(
    snapshot: DemandSnapshot,
    current_setpoint: object | None,
    *,
    current_hvac_mode: str | None,
    idle_heat_step: int | None,
) -> tuple[float, int]:
    active_step = _current_active_heat_step(
        snapshot,
        current_setpoint,
        current_hvac_mode=current_hvac_mode,
        idle_heat_step=idle_heat_step,
    )
    selected_step = active_step if active_step is not None and active_step < -1 else -1
    return snapshot.inlet_temp - _idle_heat_delta_for_step(selected_step), selected_step


def _requested_setpoint_raw(
    snapshot: DemandSnapshot,
    demand: EquipmentDemand,
    predicted_open_zones: tuple[str, ...],
    *,
    current_setpoint: object | None = None,
    current_hvac_mode: str | None = None,
    idle_heat_step: int | None = None,
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
        return min(minimum_room_target, inlet_offset_target)

    if demand.maintain_heat_mode:
        raw_requested_setpoint, _ = _requested_maintain_heat_raw(
            snapshot,
            current_setpoint,
            current_hvac_mode=current_hvac_mode,
            idle_heat_step=idle_heat_step,
        )
        return raw_requested_setpoint

    return snapshot.inlet_temp


def _requested_setpoint(
    snapshot: DemandSnapshot,
    demand: EquipmentDemand,
    predicted_open_zones: tuple[str, ...],
    *,
    current_setpoint: object | None = None,
    current_hvac_mode: str | None = None,
    idle_heat_step: int | None = None,
) -> int:
    raw_requested_setpoint = _requested_setpoint_raw(
        snapshot,
        demand,
        predicted_open_zones,
        current_setpoint=current_setpoint,
        current_hvac_mode=current_hvac_mode,
        idle_heat_step=idle_heat_step,
    )
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
        _, selected_step = _requested_maintain_heat_raw(
            snapshot,
            current_setpoint,
            current_hvac_mode=current_hvac_mode,
            idle_heat_step=idle_heat_step,
        )
        LOGGER.info(
            "SETPOINT: inlet_temp=%.1f mode=maintain_heat zones=%s step=%s raw=%.1f normalized=%s",
            snapshot.inlet_temp,
            ",".join(predicted_open_zones) if predicted_open_zones else "none",
            selected_step,
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


def _idle_heat_delta_for_step(step: int) -> float:
    if step == -7:
        return IDLE_HEAT_STEP_7_DELTA
    if step == -1:
        return IDLE_HEAT_STEP_1_DELTA
    if step == -2:
        return IDLE_HEAT_STEP_2_DELTA
    if step == -3:
        return IDLE_HEAT_STEP_3_DELTA
    if step == -4:
        return IDLE_HEAT_STEP_4_DELTA
    if step == -5:
        return IDLE_HEAT_STEP_5_DELTA
    return IDLE_HEAT_STEP_6_DELTA


def _idle_heat_raw_setpoint_for_step(snapshot: DemandSnapshot, current_setpoint_value: float, step: int) -> float:
    if step == 0:
        return current_setpoint_value
    return snapshot.inlet_temp - _idle_heat_delta_for_step(step)


def _infer_idle_heat_step(snapshot: DemandSnapshot, current_setpoint_value: float) -> int | None:
    normalized_current_setpoint = normalize_setpoint(current_setpoint_value)
    for step in (-7, -6, -5, -4, -3, -2, -1):
        raw_setpoint = _idle_heat_raw_setpoint_for_step(snapshot, current_setpoint_value, step)
        if raw_setpoint <= MIN_HEAT_SETPOINT:
            continue
        if normalize_setpoint(raw_setpoint) == normalized_current_setpoint:
            return step
    return None


def _idle_heat_setpoint_for_step(snapshot: DemandSnapshot, current_setpoint_value: float, step: int) -> int:
    return normalize_setpoint(_idle_heat_raw_setpoint_for_step(snapshot, current_setpoint_value, step))


def _resolve_idle_heat_step(
    snapshot: DemandSnapshot,
    predicted_open_zones: tuple[str, ...],
    current_setpoint: object | None,
    idle_started_at: datetime | None,
    idle_heat_step: int | None,
    idle_heat_step_changed_at: datetime | None,
    idle_heat_zone_key: str | None,
    now: datetime | None,
) -> tuple[int | None, int | None, bool, bool]:
    current_setpoint_value = parse_float(current_setpoint)
    if current_setpoint_value is None:
        LOGGER.info("SETPOINT: idle_heat stage=hold zones=none reason=missing_current_setpoint")
        return None, None, False, False

    normalized_setpoint = normalize_setpoint(current_setpoint_value)
    normalized_idle_started_at = _normalize_timestamp(idle_started_at)
    normalized_now = _normalize_timestamp(now)
    if normalized_idle_started_at is None or normalized_now is None or not predicted_open_zones:
        LOGGER.info(
            "SETPOINT: idle_heat stage=hold zones=%s raw=%.1f normalized=%s",
            ",".join(predicted_open_zones) if predicted_open_zones else "none",
            current_setpoint_value,
            normalized_setpoint,
        )
        return normalized_setpoint, None, False, False

    zone = snapshot.zones[predicted_open_zones[0]]
    idle_seconds = (normalized_now - normalized_idle_started_at).total_seconds()
    tracked_step = idle_heat_step if idle_heat_step in IDLE_HEAT_ALLOWED_STEPS else None
    inferred_step = _infer_idle_heat_step(snapshot, current_setpoint_value)
    if idle_heat_zone_key != zone.key:
        tracked_step = inferred_step if inferred_step in IDLE_HEAT_ALLOWED_STEPS else 0
        tracked_changed_at = None
    else:
        tracked_changed_at = _normalize_timestamp(idle_heat_step_changed_at)
        if inferred_step is not None:
            tracked_step = inferred_step
        elif tracked_step is None:
            tracked_step = 0

    selected_step = tracked_step
    timer_reset = False
    stage = "hold"

    if zone.current_temp > zone.scheme.continue_until:
        if idle_seconds >= IDLE_HEAT_STAGE_5_SECONDS:
            required_step = -7
            stage = "idle_heat_step_7"
        elif idle_seconds >= IDLE_HEAT_STAGE_4_SECONDS:
            required_step = -4
            stage = "idle_heat_step_4"
        elif idle_seconds >= IDLE_HEAT_STAGE_3_SECONDS:
            required_step = -3
            stage = "idle_heat_step_3"
        elif idle_seconds >= IDLE_HEAT_STAGE_2_SECONDS:
            required_step = -2
            stage = "idle_heat_step_2"
        elif idle_seconds >= IDLE_HEAT_STAGE_1_SECONDS:
            required_step = -1
            stage = "idle_heat_step_1"
        else:
            required_step = 0
        selected_step = min(tracked_step, required_step)
    else:
        if tracked_step is not None and tracked_step <= -2:
            stage = "idle_heat_unwind"
            if tracked_changed_at is None:
                timer_reset = True
            else:
                unwind_seconds = (normalized_now - tracked_changed_at).total_seconds()
                if unwind_seconds >= IDLE_HEAT_UNWIND_SECONDS:
                    selected_step = tracked_step + 1
                else:
                    selected_step = tracked_step
        elif tracked_step == -1:
            stage = "idle_heat_step_1"
        else:
            stage = "hold"

    step_changed = timer_reset or selected_step != tracked_step
    selected_setpoint = _idle_heat_setpoint_for_step(snapshot, current_setpoint_value, selected_step)
    minimum_setpoint_reached = (
        selected_step is not None
        and selected_step < 0
        and _idle_heat_raw_setpoint_for_step(snapshot, current_setpoint_value, selected_step) <= MIN_HEAT_SETPOINT
    )
    tracked_changed_display = "none" if tracked_changed_at is None else tracked_changed_at.isoformat()
    LOGGER.info(
        "SETPOINT: inlet_temp=%.1f stage=%s zone=%s zone_temp=%.1f continue_until=%.1f idle_seconds=%.0f tracked_step=%s tracked_changed_at=%s selected_step=%s timer_reset=%s min_reached=%s normalized=%s",
        snapshot.inlet_temp,
        stage,
        zone.key,
        zone.current_temp,
        zone.scheme.continue_until,
        idle_seconds,
        tracked_step,
        tracked_changed_display,
        selected_step,
        timer_reset,
        minimum_setpoint_reached,
        selected_setpoint,
    )
    return selected_setpoint, selected_step, step_changed, minimum_setpoint_reached


def _requested_idle_heat_setpoint(
    snapshot: DemandSnapshot,
    predicted_open_zones: tuple[str, ...],
    current_setpoint: object | None,
    idle_started_at: datetime | None,
    idle_heat_step: int | None,
    idle_heat_step_changed_at: datetime | None,
    idle_heat_zone_key: str | None,
    now: datetime | None,
) -> tuple[int | None, int | None, bool, bool]:
    selected_setpoint, selected_step, step_changed, minimum_setpoint_reached = _resolve_idle_heat_step(
        snapshot,
        predicted_open_zones,
        current_setpoint,
        idle_started_at,
        idle_heat_step,
        idle_heat_step_changed_at,
        idle_heat_zone_key,
        now,
    )
    if selected_setpoint is None:
        LOGGER.info(
            "SETPOINT: idle_heat stage=hold zones=%s reason=missing_current_setpoint",
            ",".join(predicted_open_zones) if predicted_open_zones else "none",
        )
    return selected_setpoint, selected_step, step_changed, minimum_setpoint_reached


def _resolve_idle_heat_restart_step(
    demand: EquipmentDemand,
    predicted_open_zones: tuple[str, ...],
    *,
    current_hvac_mode: str | None,
    idle_shutdown_at: datetime | None,
    idle_shutdown_heat_step: int | None,
    idle_shutdown_zone_key: str | None,
    now: datetime | None,
) -> int | None:
    if (current_hvac_mode or "").lower() != HVAC_OFF:
        return None
    if not demand.maintain_heat_mode or demand.heat_requested:
        return None
    if not predicted_open_zones:
        return None

    remembered_step = (
        idle_shutdown_heat_step
        if idle_shutdown_heat_step in {-7, -6, -5, -4, -3, -2, -1}
        else None
    )
    if remembered_step is None:
        return None

    primary_zone_key = demand.requested_by_zones[0] if demand.requested_by_zones else predicted_open_zones[0]
    if idle_shutdown_zone_key != primary_zone_key:
        return None

    normalized_shutdown_at = _normalize_timestamp(idle_shutdown_at)
    normalized_now = _normalize_timestamp(now)
    if normalized_shutdown_at is None or normalized_now is None:
        return None

    off_seconds = (normalized_now - normalized_shutdown_at).total_seconds()
    if off_seconds < 0 or off_seconds > IDLE_HEAT_RESTART_MEMORY_SECONDS:
        return None

    resumed_step = remembered_step + int(off_seconds // IDLE_HEAT_UNWIND_SECONDS)
    if resumed_step >= 0:
        return None

    return resumed_step


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
    current_setpoint: object | None = None,
    idle_started_at: datetime | None = None,
    idle_heat_step: int | None = None,
    idle_heat_step_changed_at: datetime | None = None,
    idle_heat_zone_key: str | None = None,
    idle_shutdown_at: datetime | None = None,
    idle_shutdown_heat_step: int | None = None,
    idle_shutdown_zone_key: str | None = None,
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
            setpoint=_requested_setpoint(
                snapshot,
                demand,
                predicted_open_zones,
                current_setpoint=current_setpoint,
                current_hvac_mode=current_hvac_mode,
                idle_heat_step=idle_heat_step,
            ),
            open_zones=predicted_open_zones,
            reason=demand.reason,
        )

    if demand.maintain_heat_mode:
        raw_requested_setpoint = _requested_setpoint_raw(
            snapshot,
            demand,
            predicted_open_zones,
            current_setpoint=current_setpoint,
            current_hvac_mode=current_hvac_mode,
            idle_heat_step=idle_heat_step,
        )
        if raw_requested_setpoint < MIN_HEAT_SETPOINT:
            return DispatchPlan(
                turn_off=False,
                hvac_mode=HVAC_FAN_ONLY,
                fan_mode=FAN_LOW,
                requested_by_zones=demand.requested_by_zones,
                open_zones=predicted_open_zones,
                reason="maintain_heat: " + demand.reason,
            )
        requested_setpoint = _requested_setpoint(
            snapshot,
            demand,
            predicted_open_zones,
            current_setpoint=current_setpoint,
            current_hvac_mode=current_hvac_mode,
            idle_heat_step=idle_heat_step,
        )
        restart_step = _resolve_idle_heat_restart_step(
            demand,
            predicted_open_zones,
            current_hvac_mode=current_hvac_mode,
            idle_shutdown_at=idle_shutdown_at,
            idle_shutdown_heat_step=idle_shutdown_heat_step,
            idle_shutdown_zone_key=idle_shutdown_zone_key,
            now=now,
        )
        selected_setpoint = requested_setpoint
        if restart_step is not None:
            restart_setpoint = normalize_setpoint(snapshot.inlet_temp - _idle_heat_delta_for_step(restart_step))
            selected_setpoint = min(requested_setpoint, restart_setpoint)
            LOGGER.info(
                "SETPOINT: idle_heat_restart zone=%s remembered_step=%s off_seconds=%.0f restart_step=%s requested=%s selected=%s",
                demand.requested_by_zones[0] if demand.requested_by_zones else predicted_open_zones[0],
                idle_shutdown_heat_step,
                (_normalize_timestamp(now) - _normalize_timestamp(idle_shutdown_at)).total_seconds() if _normalize_timestamp(now) and _normalize_timestamp(idle_shutdown_at) else -1,
                restart_step,
                requested_setpoint,
                selected_setpoint,
            )
        return DispatchPlan(
            turn_off=False,
            hvac_mode=HVAC_HEAT,
            fan_mode=resolve_fan_mode(current_fan_mode, current_hvac_mode, demand),
            setpoint=selected_setpoint,
            requested_by_zones=demand.requested_by_zones,
            open_zones=predicted_open_zones,
            reason=demand.reason,
        )

    if demand.heat_requested:
        return DispatchPlan(
            turn_off=False,
            hvac_mode=HVAC_HEAT,
            fan_mode=resolve_fan_mode(current_fan_mode, current_hvac_mode, demand),
            setpoint=_requested_setpoint(
                snapshot,
                demand,
                predicted_open_zones,
                current_setpoint=current_setpoint,
                current_hvac_mode=current_hvac_mode,
                idle_heat_step=idle_heat_step,
            ),
            requested_by_zones=demand.requested_by_zones,
            open_zones=predicted_open_zones,
            reason="heat_requested: " + demand.reason,
        )

    if demand.cool_requested or demand.maintain_cool_mode:
        return DispatchPlan(
            turn_off=False,
            hvac_mode=HVAC_COOL,
            fan_mode=resolve_fan_mode(current_fan_mode, current_hvac_mode, demand),
            setpoint=_requested_setpoint(
                snapshot,
                demand,
                predicted_open_zones,
                current_setpoint=current_setpoint,
                current_hvac_mode=current_hvac_mode,
                idle_heat_step=idle_heat_step,
            ),
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
            return DispatchPlan(
                turn_off=True,
                idle_shutdown=current_mode == HVAC_HEAT,
                open_zones=predicted_open_zones,
                reason=demand.reason,
            )
        if current_mode == HVAC_HEAT:
            idle_setpoint, resolved_idle_heat_step, idle_heat_step_changed, minimum_setpoint_reached = _requested_idle_heat_setpoint(
                snapshot,
                predicted_open_zones,
                current_setpoint,
                idle_started_at,
                idle_heat_step,
                idle_heat_step_changed_at,
                idle_heat_zone_key,
                now,
            )
            if minimum_setpoint_reached:
                return DispatchPlan(
                    turn_off=True,
                    idle_shutdown=True,
                    idle_heat_step=resolved_idle_heat_step,
                    open_zones=predicted_open_zones,
                    reason="idle minimum heat setpoint reached: " + demand.reason,
                )
        else:
            normalized_current_setpoint = parse_float(current_setpoint)
            idle_setpoint = normalize_setpoint(normalized_current_setpoint) if normalized_current_setpoint is not None else None
            resolved_idle_heat_step = None
            idle_heat_step_changed = False
        return DispatchPlan(
            idle=True,
            hvac_mode=current_mode,
            setpoint=idle_setpoint,
            idle_heat_step=resolved_idle_heat_step if current_mode == HVAC_HEAT else None,
            idle_heat_step_changed=idle_heat_step_changed if current_mode == HVAC_HEAT else False,
            open_zones=predicted_open_zones,
            reason="idle: " + demand.reason,
        )

    return DispatchPlan(turn_off=True, open_zones=predicted_open_zones, reason="else: " + demand.reason)


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
