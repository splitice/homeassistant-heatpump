from __future__ import annotations

import logging
import math
import re
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Protocol

from .config import DEFAULT_SYSTEM_CONFIG
from .constants import (
    COMFORT_MODE_OFF,
    CONTROL_HVAC_MODE_OFF,
    FAN_LOW,
    FAN_MEDIUM,
    HEAT_START_MEDIUM_FAN_DIFFERENTIAL,
    HVAC_COOL,
    HVAC_FAN_ONLY,
    HVAC_HEAT,
    HVAC_OFF,
    IDLE_HEAT_STAGE_1_SECONDS,
    IDLE_HEAT_STAGE_2_SECONDS,
    IDLE_HEAT_STAGE_3_SECONDS,
    IDLE_HEAT_STAGE_4_SECONDS,
    IDLE_HEAT_STAGE_5_SECONDS,
    IDLE_HEAT_STAGE_6_SECONDS,
    IDLE_HEAT_RESTART_MEMORY_SECONDS,
    IDLE_HEAT_STEP_11_DELTA,
    IDLE_HEAT_STEP_1_DELTA,
    IDLE_HEAT_STEP_2_DELTA,
    IDLE_HEAT_STEP_3_DELTA,
    IDLE_HEAT_STEP_4_DELTA,
    IDLE_HEAT_STEP_5_DELTA,
    IDLE_HEAT_STEP_6_DELTA,
    IDLE_HEAT_STEP_7_DELTA,
    IDLE_HEAT_UNWIND_SECONDS,
    INITIAL_IDLE_HEAT_BLEND_FACTOR,
    INITIAL_IDLE_HEAT_GAP_THRESHOLD,
    MIN_IDLE_SECONDS,
    LOW_TO_MEDIUM_FAN_DIFFERENTIAL,
    LOGGER_NAME,
    MAX_HEAT_SETPOINT,
    MEDIUM_TO_LOW_FAN_DIFFERENTIAL,
    MIN_HEAT_SETPOINT,
)
from .models import DemandSnapshot, DispatchPlan, EquipmentDemand, SystemConfig, ZoneRuntimeState
from .state_reader import parse_float


LOGGER = logging.getLogger(LOGGER_NAME)
IDLE_HEAT_ALLOWED_STEPS = (0, -1, -2, -3, -4, -5, -6, -7, -11)
IDLE_HEAT_UNWIND_LADDER = (-11, -7, -6, -5, -4, -3, -2, -1)
LEVEL_FAN_MODE_PATTERN = re.compile(r"^level\s+([1-9]\d*)$", re.IGNORECASE)


class ServiceController(Protocol):
    def call_service(self, domain: str, service: str, **kwargs: object) -> None: ...


def _target_temp_step_value(target_temp_step: object | None) -> float:
    parsed_step = parse_float(target_temp_step)
    if parsed_step is None or parsed_step <= 0.0:
        return 1.0
    return parsed_step


def _setpoint_value(value: float) -> int | float:
    rounded_value = round(value, 6)
    if math.isclose(rounded_value, round(rounded_value), abs_tol=1e-9):
        return int(round(rounded_value))
    return rounded_value


def normalize_setpoint(value: float, target_temp_step: object | None = 1.0) -> int | float:
    return normalize_cool_setpoint(value, target_temp_step)


def normalize_heat_setpoint(value: float, target_temp_step: object | None = 1.0) -> int | float:
    step = _target_temp_step_value(target_temp_step)
    snapped_value = math.floor((value / step) + 1e-9) * step
    clamped_value = max(MIN_HEAT_SETPOINT, min(MAX_HEAT_SETPOINT, snapped_value))
    return _setpoint_value(clamped_value)


def normalize_cool_setpoint(value: float, target_temp_step: object | None = 1.0) -> int | float:
    step = _target_temp_step_value(target_temp_step)
    snapped_value = math.ceil((value / step) - 1e-9) * step
    clamped_value = max(MIN_HEAT_SETPOINT, min(MAX_HEAT_SETPOINT, snapped_value))
    return _setpoint_value(clamped_value)


def _normalize_timestamp(value: datetime | None) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _select_initial_idle_heat_setpoint(
    snapshot: DemandSnapshot,
    predicted_open_zones: tuple[str, ...],
    current_setpoint_value: float,
    target_temp_step: object | None,
) -> tuple[ZoneRuntimeState, int | float, str, float, float | None]:
    candidate_zones: list[ZoneRuntimeState] = []
    for zone_key in predicted_open_zones:
        candidate_zones.append(snapshot.zones[zone_key])

    anchor_zone = min(candidate_zones, key=lambda zone: (zone.current_temp, zone.key))
    target_gap = current_setpoint_value - anchor_zone.current_temp
    normalized_current_setpoint = normalize_heat_setpoint(current_setpoint_value, target_temp_step)
    if target_gap <= INITIAL_IDLE_HEAT_GAP_THRESHOLD:
        return anchor_zone, normalized_current_setpoint, "entry_hold", target_gap, None

    midpoint_raw = anchor_zone.current_temp + (target_gap * INITIAL_IDLE_HEAT_BLEND_FACTOR)
    selected_setpoint = min(normalized_current_setpoint, normalize_heat_setpoint(midpoint_raw, target_temp_step))
    return anchor_zone, selected_setpoint, "entry_midpoint", target_gap, midpoint_raw


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
    target_temp_step: object | None,
) -> int | None:
    if (current_hvac_mode or "").lower() != HVAC_HEAT:
        return None

    candidate_steps: list[int] = []
    tracked_step = idle_heat_step if idle_heat_step in IDLE_HEAT_ALLOWED_STEPS and idle_heat_step < 0 else None
    if tracked_step is not None:
        candidate_steps.append(tracked_step)

    current_setpoint_value = parse_float(current_setpoint)
    inferred_step = (
        _infer_idle_heat_step(snapshot, current_setpoint_value, target_temp_step)
        if current_setpoint_value is not None
        else None
    )
    if inferred_step is not None:
        candidate_steps.append(inferred_step)

    return min(candidate_steps) if candidate_steps else None


def _requested_maintain_heat_raw(
    snapshot: DemandSnapshot,
    current_setpoint: object | None,
    *,
    primary_zone: ZoneRuntimeState | None,
    current_hvac_mode: str | None,
    idle_heat_step: int | None,
    target_temp_step: object | None,
) -> tuple[float, int]:
    active_step = _current_active_heat_step(
        snapshot,
        current_setpoint,
        current_hvac_mode=current_hvac_mode,
        idle_heat_step=idle_heat_step,
        target_temp_step=target_temp_step,
    )
    minimum_step = _minimum_idle_heat_step(primary_zone) if primary_zone is not None else -1
    if active_step is not None and active_step < 0:
        selected_step = min(active_step, minimum_step)
    else:
        selected_step = minimum_step
    return snapshot.inlet_temp - _idle_heat_delta_for_step(selected_step), selected_step


def _requested_setpoint_raw(
    snapshot: DemandSnapshot,
    demand: EquipmentDemand,
    predicted_open_zones: tuple[str, ...],
    *,
    current_setpoint: object | None = None,
    current_hvac_mode: str | None = None,
    idle_heat_step: int | None = None,
    target_temp_step: object | None = 1.0,
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
        inlet_offset_target = snapshot.inlet_temp + zone.setpoint_delta_from_inlet
        return min(minimum_room_target, inlet_offset_target)

    if demand.maintain_heat_mode:
        primary_zone = snapshot.zones[demand.requested_by_zones[0]] if demand.requested_by_zones else None
        raw_requested_setpoint, _ = _requested_maintain_heat_raw(
            snapshot,
            current_setpoint,
            primary_zone=primary_zone,
            current_hvac_mode=current_hvac_mode,
            idle_heat_step=idle_heat_step,
            target_temp_step=target_temp_step,
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
    target_temp_step: object | None = 1.0,
) -> int | float:
    raw_requested_setpoint = _requested_setpoint_raw(
        snapshot,
        demand,
        predicted_open_zones,
        current_setpoint=current_setpoint,
        current_hvac_mode=current_hvac_mode,
        idle_heat_step=idle_heat_step,
        target_temp_step=target_temp_step,
    )
    normalize_requested_setpoint = (
        normalize_cool_setpoint if demand.cool_requested or demand.maintain_cool_mode else normalize_heat_setpoint
    )
    normalized_setpoint = normalize_requested_setpoint(raw_requested_setpoint, target_temp_step)

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
        primary_zone = snapshot.zones[demand.requested_by_zones[0]] if demand.requested_by_zones else None
        _, selected_step = _requested_maintain_heat_raw(
            snapshot,
            current_setpoint,
            primary_zone=primary_zone,
            current_hvac_mode=current_hvac_mode,
            idle_heat_step=idle_heat_step,
            target_temp_step=target_temp_step,
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


def _minimum_idle_heat_step(zone: ZoneRuntimeState) -> int:
    required_delta = max(0.0, -zone.setpoint_delta_from_inlet)
    for step in (-1, -2, -3, -4, -5, -6, -7, -11):
        if _idle_heat_delta_for_step(step) >= required_delta:
            return step
    return -11


def _relax_idle_heat_step(step: int, intervals: int = 1, *, minimum_step: int | None = None) -> int | None:
    if step not in IDLE_HEAT_UNWIND_LADDER or intervals < 0:
        return None

    relaxed_index = IDLE_HEAT_UNWIND_LADDER.index(step) + intervals
    if minimum_step in IDLE_HEAT_UNWIND_LADDER:
        relaxed_index = min(relaxed_index, IDLE_HEAT_UNWIND_LADDER.index(minimum_step))
    if relaxed_index >= len(IDLE_HEAT_UNWIND_LADDER):
        return None
    return IDLE_HEAT_UNWIND_LADDER[relaxed_index]


def _idle_heat_delta_for_step(step: int) -> float:
    if step == -11:
        return IDLE_HEAT_STEP_11_DELTA
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


def _infer_idle_heat_step(
    snapshot: DemandSnapshot,
    current_setpoint_value: float,
    target_temp_step: object | None,
) -> int | None:
    normalized_current_setpoint = normalize_heat_setpoint(current_setpoint_value, target_temp_step)
    for step in (-11, -7, -6, -5, -4, -3, -2, -1):
        raw_setpoint = _idle_heat_raw_setpoint_for_step(snapshot, current_setpoint_value, step)
        if raw_setpoint <= MIN_HEAT_SETPOINT:
            continue
        if normalize_heat_setpoint(raw_setpoint, target_temp_step) == normalized_current_setpoint:
            return step
    return None


def _idle_heat_setpoint_for_step(
    snapshot: DemandSnapshot,
    current_setpoint_value: float,
    step: int,
    target_temp_step: object | None,
) -> int | float:
    return normalize_heat_setpoint(
        _idle_heat_raw_setpoint_for_step(snapshot, current_setpoint_value, step),
        target_temp_step,
    )


def _resolve_idle_heat_step(
    snapshot: DemandSnapshot,
    predicted_open_zones: tuple[str, ...],
    current_setpoint: object | None,
    idle_started_at: datetime | None,
    idle_heat_step: int | None,
    idle_heat_step_changed_at: datetime | None,
    idle_heat_zone_key: str | None,
    now: datetime | None,
    target_temp_step: object | None,
) -> tuple[int | float | None, int | None, bool, bool]:
    current_setpoint_value = parse_float(current_setpoint)
    if current_setpoint_value is None:
        LOGGER.info("SETPOINT: idle_heat stage=hold zones=none reason=missing_current_setpoint")
        return None, None, False, False

    normalized_setpoint = normalize_heat_setpoint(current_setpoint_value, target_temp_step)
    normalized_idle_started_at = _normalize_timestamp(idle_started_at)
    normalized_now = _normalize_timestamp(now)
    if not predicted_open_zones:
        LOGGER.info(
            "SETPOINT: idle_heat stage=hold zones=%s raw=%.1f normalized=%s",
            ",".join(predicted_open_zones) if predicted_open_zones else "none",
            current_setpoint_value,
            normalized_setpoint,
        )
        return normalized_setpoint, None, False, False

    if normalized_idle_started_at is None:
        zone, selected_setpoint, stage, target_gap, midpoint_raw = _select_initial_idle_heat_setpoint(
            snapshot,
            predicted_open_zones,
            current_setpoint_value,
            target_temp_step,
        )
        LOGGER.info(
            "SETPOINT: inlet_temp=%.1f stage=%s zone=%s room_temp=%.1f current_setpoint=%.1f gap=%.1f midpoint_raw=%s normalized=%s threshold=%.1f",
            snapshot.inlet_temp,
            stage,
            zone.key,
            zone.current_temp,
            current_setpoint_value,
            target_gap,
            f"{midpoint_raw:.1f}" if midpoint_raw is not None else "none",
            selected_setpoint,
            INITIAL_IDLE_HEAT_GAP_THRESHOLD,
        )
        return selected_setpoint, 0, False, False

    if normalized_now is None:
        LOGGER.info(
            "SETPOINT: idle_heat stage=hold zones=%s raw=%.1f normalized=%s",
            ",".join(predicted_open_zones) if predicted_open_zones else "none",
            current_setpoint_value,
            normalized_setpoint,
        )
        return normalized_setpoint, None, False, False

    zone = snapshot.zones[predicted_open_zones[0]]
    minimum_step = _minimum_idle_heat_step(zone)
    idle_seconds = (normalized_now - normalized_idle_started_at).total_seconds()
    tracked_step = idle_heat_step if idle_heat_step in IDLE_HEAT_ALLOWED_STEPS else None
    inferred_step = _infer_idle_heat_step(snapshot, current_setpoint_value, target_temp_step)
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
        if idle_seconds >= IDLE_HEAT_STAGE_6_SECONDS:
            required_step = -11
            stage = "idle_heat_step_11"
        elif idle_seconds >= IDLE_HEAT_STAGE_5_SECONDS:
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
        if required_step < 0:
            required_step = min(required_step, minimum_step)
        selected_step = min(tracked_step, required_step)
    else:
        if tracked_step is not None and tracked_step < minimum_step:
            stage = "idle_heat_unwind"
            if tracked_changed_at is None:
                timer_reset = True
            else:
                unwind_seconds = (normalized_now - tracked_changed_at).total_seconds()
                if unwind_seconds >= IDLE_HEAT_UNWIND_SECONDS:
                    selected_step = _relax_idle_heat_step(
                        tracked_step,
                        minimum_step=minimum_step,
                    ) or tracked_step
                else:
                    selected_step = tracked_step
        elif tracked_step is not None and tracked_step == minimum_step and tracked_step < 0:
            stage = f"idle_heat_step_{abs(tracked_step)}"
        else:
            stage = "hold"

    step_changed = timer_reset or selected_step != tracked_step
    selected_setpoint = _idle_heat_setpoint_for_step(
        snapshot,
        current_setpoint_value,
        selected_step,
        target_temp_step,
    )
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
    target_temp_step: object | None,
) -> tuple[int | float | None, int | None, bool, bool]:
    selected_setpoint, selected_step, step_changed, minimum_setpoint_reached = _resolve_idle_heat_step(
        snapshot,
        predicted_open_zones,
        current_setpoint,
        idle_started_at,
        idle_heat_step,
        idle_heat_step_changed_at,
        idle_heat_zone_key,
        now,
        target_temp_step,
    )
    if selected_setpoint is None:
        LOGGER.info(
            "SETPOINT: idle_heat stage=hold zones=%s reason=missing_current_setpoint",
            ",".join(predicted_open_zones) if predicted_open_zones else "none",
        )
    return selected_setpoint, selected_step, step_changed, minimum_setpoint_reached


def _resolve_idle_heat_restart_step(
    snapshot: DemandSnapshot,
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
        if idle_shutdown_heat_step in {-11, -7, -6, -5, -4, -3, -2, -1}
        else None
    )
    if remembered_step is None:
        return None

    primary_zone_key = demand.requested_by_zones[0] if demand.requested_by_zones else predicted_open_zones[0]
    if idle_shutdown_zone_key != primary_zone_key:
        return None
    minimum_step = _minimum_idle_heat_step(snapshot.zones[primary_zone_key])

    normalized_shutdown_at = _normalize_timestamp(idle_shutdown_at)
    normalized_now = _normalize_timestamp(now)
    if normalized_shutdown_at is None or normalized_now is None:
        return None

    off_seconds = (normalized_now - normalized_shutdown_at).total_seconds()
    if off_seconds < 0 or off_seconds > IDLE_HEAT_RESTART_MEMORY_SECONDS:
        return None

    return _relax_idle_heat_step(
        remembered_step,
        int(off_seconds // IDLE_HEAT_UNWIND_SECONDS),
        minimum_step=minimum_step,
    )


def _fan_modes_tuple(supported_fan_modes: Iterable[object] | None) -> tuple[str, ...]:
    if supported_fan_modes is None:
        return ()
    fan_modes: list[str] = []
    for mode in supported_fan_modes:
        if mode is not None:
            fan_modes.append(str(mode))
    return tuple(fan_modes)


def _supported_level_fan_modes(supported_fan_modes: Iterable[object] | None) -> tuple[str, ...]:
    levels: list[tuple[int, str]] = []
    for fan_mode in _fan_modes_tuple(supported_fan_modes):
        match = LEVEL_FAN_MODE_PATTERN.match(fan_mode.strip())
        if match is not None:
            levels.append((int(match.group(1)), fan_mode))

    if not levels:
        return ()

    levels.sort(key=lambda item: item[0])
    consecutive_levels: list[str] = []
    expected_level = 1
    for level_number, fan_mode in levels:
        if level_number < expected_level:
            continue
        if level_number > expected_level:
            break
        consecutive_levels.append(fan_mode)
        expected_level += 1
    return tuple(consecutive_levels)


def _supported_named_fan_mode(logical_fan_mode: str, supported_fan_modes: Iterable[object] | None) -> str | None:
    for fan_mode in _fan_modes_tuple(supported_fan_modes):
        if fan_mode.strip().lower() == logical_fan_mode:
            return fan_mode
    return None


def _actual_fan_mode(logical_fan_mode: str, supported_fan_modes: Iterable[object] | None) -> str:
    level_fan_modes = _supported_level_fan_modes(supported_fan_modes)
    if level_fan_modes:
        if logical_fan_mode == FAN_LOW:
            return level_fan_modes[0]
        if logical_fan_mode == FAN_MEDIUM:
            return level_fan_modes[min(1, len(level_fan_modes) - 1)]

    supported_named_mode = _supported_named_fan_mode(logical_fan_mode, supported_fan_modes)
    return supported_named_mode or logical_fan_mode


def _canonical_fan_mode(fan_mode: str | None) -> str:
    normalized_fan_mode = (fan_mode or "").strip().lower()
    if normalized_fan_mode in {FAN_LOW, FAN_MEDIUM}:
        return normalized_fan_mode

    match = LEVEL_FAN_MODE_PATTERN.match((fan_mode or "").strip())
    if match is None:
        return normalized_fan_mode
    level_number = int(match.group(1))
    return FAN_LOW if level_number <= 1 else FAN_MEDIUM


def resolve_fan_mode(
    current_fan_mode: str | None,
    current_hvac_mode: str | None,
    demand: EquipmentDemand,
    *,
    supported_fan_modes: Iterable[object] | None = None,
) -> str | None:
    if demand.fan_only_requested:
        return _actual_fan_mode(FAN_LOW, supported_fan_modes)

    if not (demand.heat_requested or demand.maintain_heat_mode or demand.cool_requested or demand.maintain_cool_mode):
        return None

    current_fan = _canonical_fan_mode(current_fan_mode)
    differential = demand.max_temperature_deficit
    currently_heating = (current_hvac_mode or "").lower() == HVAC_HEAT
    currently_cooling = (current_hvac_mode or "").lower() == HVAC_COOL

    if demand.cool_requested or demand.maintain_cool_mode:
        if not currently_cooling:
            logical_fan_mode = FAN_MEDIUM if differential > HEAT_START_MEDIUM_FAN_DIFFERENTIAL else FAN_LOW
            return _actual_fan_mode(logical_fan_mode, supported_fan_modes)
        if current_fan == FAN_MEDIUM:
            logical_fan_mode = FAN_LOW if differential < MEDIUM_TO_LOW_FAN_DIFFERENTIAL else FAN_MEDIUM
            return _actual_fan_mode(logical_fan_mode, supported_fan_modes)
        if current_fan == FAN_LOW:
            logical_fan_mode = FAN_MEDIUM if differential > LOW_TO_MEDIUM_FAN_DIFFERENTIAL else FAN_LOW
            return _actual_fan_mode(logical_fan_mode, supported_fan_modes)
        logical_fan_mode = FAN_MEDIUM if differential > HEAT_START_MEDIUM_FAN_DIFFERENTIAL else FAN_LOW
        return _actual_fan_mode(logical_fan_mode, supported_fan_modes)

    if not currently_heating:
        logical_fan_mode = FAN_MEDIUM if differential > HEAT_START_MEDIUM_FAN_DIFFERENTIAL else FAN_LOW
        return _actual_fan_mode(logical_fan_mode, supported_fan_modes)

    if current_fan == FAN_MEDIUM:
        logical_fan_mode = FAN_LOW if differential < MEDIUM_TO_LOW_FAN_DIFFERENTIAL else FAN_MEDIUM
        return _actual_fan_mode(logical_fan_mode, supported_fan_modes)
    if current_fan == FAN_LOW:
        logical_fan_mode = FAN_MEDIUM if differential > LOW_TO_MEDIUM_FAN_DIFFERENTIAL else FAN_LOW
        return _actual_fan_mode(logical_fan_mode, supported_fan_modes)
    logical_fan_mode = FAN_MEDIUM if differential > HEAT_START_MEDIUM_FAN_DIFFERENTIAL else FAN_LOW
    return _actual_fan_mode(logical_fan_mode, supported_fan_modes)


def build_dispatch_plan(
    snapshot: DemandSnapshot,
    demand: EquipmentDemand,
    predicted_open_zones: tuple[str, ...],
    *,
    current_hvac_mode: str | None,
    current_fan_mode: str | None,
    current_setpoint: object | None = None,
    target_temp_step: object | None = 1.0,
    idle_started_at: datetime | None = None,
    idle_heat_step: int | None = None,
    idle_heat_step_changed_at: datetime | None = None,
    idle_heat_zone_key: str | None = None,
    idle_shutdown_at: datetime | None = None,
    idle_shutdown_heat_step: int | None = None,
    idle_shutdown_zone_key: str | None = None,
    supported_fan_modes: Iterable[object] | None = None,
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
            fan_mode=resolve_fan_mode(
                current_fan_mode,
                current_hvac_mode,
                demand,
                supported_fan_modes=supported_fan_modes,
            ),
            setpoint=_requested_setpoint(
                snapshot,
                demand,
                predicted_open_zones,
                current_setpoint=current_setpoint,
                current_hvac_mode=current_hvac_mode,
                idle_heat_step=idle_heat_step,
                target_temp_step=target_temp_step,
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
            target_temp_step=target_temp_step,
        )
        if raw_requested_setpoint < MIN_HEAT_SETPOINT:
            return DispatchPlan(
                turn_off=False,
                hvac_mode=HVAC_FAN_ONLY,
                fan_mode=_actual_fan_mode(FAN_LOW, supported_fan_modes),
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
            target_temp_step=target_temp_step,
        )
        restart_step = _resolve_idle_heat_restart_step(
            snapshot,
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
            restart_setpoint = normalize_heat_setpoint(
                snapshot.inlet_temp - _idle_heat_delta_for_step(restart_step),
                target_temp_step,
            )
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
            fan_mode=resolve_fan_mode(
                current_fan_mode,
                current_hvac_mode,
                demand,
                supported_fan_modes=supported_fan_modes,
            ),
            setpoint=selected_setpoint,
            requested_by_zones=demand.requested_by_zones,
            open_zones=predicted_open_zones,
            reason=demand.reason,
        )

    if demand.heat_requested:
        return DispatchPlan(
            turn_off=False,
            hvac_mode=HVAC_HEAT,
            fan_mode=resolve_fan_mode(
                current_fan_mode,
                current_hvac_mode,
                demand,
                supported_fan_modes=supported_fan_modes,
            ),
            setpoint=_requested_setpoint(
                snapshot,
                demand,
                predicted_open_zones,
                current_setpoint=current_setpoint,
                current_hvac_mode=current_hvac_mode,
                idle_heat_step=idle_heat_step,
                target_temp_step=target_temp_step,
            ),
            requested_by_zones=demand.requested_by_zones,
            open_zones=predicted_open_zones,
            reason="heat_requested: " + demand.reason,
        )

    if demand.cool_requested or demand.maintain_cool_mode:
        return DispatchPlan(
            turn_off=False,
            hvac_mode=HVAC_COOL,
            fan_mode=resolve_fan_mode(
                current_fan_mode,
                current_hvac_mode,
                demand,
                supported_fan_modes=supported_fan_modes,
            ),
            setpoint=_requested_setpoint(
                snapshot,
                demand,
                predicted_open_zones,
                current_setpoint=current_setpoint,
                current_hvac_mode=current_hvac_mode,
                idle_heat_step=idle_heat_step,
                target_temp_step=target_temp_step,
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
                target_temp_step,
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
            idle_setpoint = (
                normalize_cool_setpoint(normalized_current_setpoint, target_temp_step)
                if normalized_current_setpoint is not None
                else None
            )
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
        LOGGER.info(
            "ZONES: requesting %s for %s via %s entity=%s because %s",
            "open" if action.turn_on else "close",
            config.zones[action.zone_key].label,
            "switch.turn_on" if action.turn_on else "switch.turn_off",
            entity_id,
            action.reason,
        )
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

    planned_setpoint = parse_float(plan.setpoint)
    if plan.setpoint is not None and (
        normalized_setpoint is None
        or planned_setpoint is None
        or not math.isclose(normalized_setpoint, planned_setpoint, abs_tol=1e-6)
    ):
        controller.call_service("climate", "set_temperature", entity_id=entity_id, temperature=plan.setpoint)
