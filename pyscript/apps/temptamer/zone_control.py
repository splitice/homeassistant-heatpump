from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .constants import (
    CONTROL_HVAC_MODE_MANUAL,
    CONTROL_HVAC_MODE_OFF,
    HVAC_COOL,
    HVAC_HEAT,
    MAX_DISCRETIONARY_ZONE_CHANGES_PER_PASS,
    MIN_OPEN_ZONES,
    MIN_ZONE_CHANGE_DELAY_SECONDS,
)
from .models import DemandSnapshot, ZoneAction, ZoneRuntimeState


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _last_change_key(zone: ZoneRuntimeState) -> datetime:
    if zone.last_switch_change is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return _normalize_datetime(zone.last_switch_change)


def _recent_change_rank(zone: ZoneRuntimeState) -> timedelta:
    if zone.last_switch_change is None:
        return timedelta.max
    normalized_last_change = _normalize_datetime(zone.last_switch_change)
    max_datetime = datetime.max.replace(tzinfo=timezone.utc)
    return max_datetime - normalized_last_change


def _temperature_deficit(zone: ZoneRuntimeState, threshold_name: str) -> float:
    return max(0.0, getattr(zone.scheme, threshold_name) - zone.current_temp)


def _temperature_excess(zone: ZoneRuntimeState, threshold: float) -> float:
    return max(0.0, zone.current_temp - threshold)


def _can_toggle(zone: ZoneRuntimeState, now: datetime, comfort_mode_changed: bool) -> bool:
    if comfort_mode_changed or zone.last_switch_change is None:
        return True
    normalized_now = _normalize_datetime(now)
    normalized_last_change = _normalize_datetime(zone.last_switch_change)
    return normalized_now - normalized_last_change >= timedelta(seconds=MIN_ZONE_CHANGE_DELAY_SECONDS)


def _sorted_by_rank(ranked_zones: list[tuple[object, ZoneRuntimeState]]) -> list[ZoneRuntimeState]:
    ordered: list[tuple[object, ZoneRuntimeState]] = []
    for ranked_zone in ranked_zones:
        insert_at = len(ordered)
        ranked_value = ranked_zone[0]
        for index, existing_ranked_zone in enumerate(ordered):
            if ranked_value < existing_ranked_zone[0]:
                insert_at = index
                break
        ordered.insert(insert_at, ranked_zone)

    result: list[ZoneRuntimeState] = []
    for _, zone in ordered:
        result.append(zone)
    return result


def _zone_should_open(zone: ZoneRuntimeState, operation_mode: str) -> bool:
    if operation_mode == HVAC_COOL:
        return zone.current_temp > zone.scheme.cool_continue_until()
    return zone.current_temp < zone.scheme.continue_until


def _zone_should_close(zone: ZoneRuntimeState, operation_mode: str) -> bool:
    if operation_mode == HVAC_COOL:
        return zone.current_temp <= zone.scheme.ideal_target
    return zone.current_temp >= zone.scheme.ideal_target


def _opening_rank(zone: ZoneRuntimeState, operation_mode: str) -> tuple[float, datetime]:
    if operation_mode == HVAC_COOL:
        # More overheated zones should sort first, so invert the distance from the cooling threshold.
        return (zone.scheme.cool_enable_above() - zone.current_temp, _last_change_key(zone))
    return (zone.current_temp - zone.scheme.enable_below, _last_change_key(zone))


def _closing_rank(zone: ZoneRuntimeState, operation_mode: str) -> tuple[float, datetime]:
    if operation_mode == HVAC_COOL:
        return (zone.current_temp - zone.scheme.ideal_target, _last_change_key(zone))
    return (-(zone.current_temp - zone.scheme.ideal_target), _last_change_key(zone))


def _safety_open_rank(zone: ZoneRuntimeState, operation_mode: str, *, continue_threshold: bool) -> tuple[float, timedelta]:
    if operation_mode == HVAC_COOL:
        threshold = zone.scheme.cool_continue_until() if continue_threshold else zone.scheme.ideal_target
        return (-_temperature_excess(zone, threshold), _recent_change_rank(zone))

    threshold_name = "continue_until" if continue_threshold else "ideal_target"
    return (-_temperature_deficit(zone, threshold_name), _recent_change_rank(zone))


def _select_safety_open_zone(snapshot: DemandSnapshot, operation_mode: str) -> str | None:
    enabled_zones: list[ZoneRuntimeState] = []
    for zone in snapshot.zones.values():
        if zone.is_enabled_by_mode:
            enabled_zones.append(zone)

    if not enabled_zones:
        open_zones: list[ZoneRuntimeState] = []
        for zone in snapshot.zones.values():
            if zone.switch_is_on:
                open_zones.append(zone)
        if not open_zones:
            return None
        ranked_open_zones: list[tuple[timedelta, ZoneRuntimeState]] = []
        for zone in open_zones:
            ranked_open_zones.append((_recent_change_rank(zone), zone))
        open_zones = _sorted_by_rank(ranked_open_zones)
        return open_zones[0].key

    continue_zones: list[ZoneRuntimeState] = []
    for zone in enabled_zones:
        if _zone_should_open(zone, operation_mode):
            continue_zones.append(zone)

    if continue_zones:
        ranked_continue_zones: list[tuple[tuple[float, timedelta], ZoneRuntimeState]] = []
        for zone in continue_zones:
            ranked_continue_zones.append((_safety_open_rank(zone, operation_mode, continue_threshold=True), zone))
        continue_zones = _sorted_by_rank(ranked_continue_zones)
        return continue_zones[0].key

    ranked_enabled_zones: list[tuple[tuple[float, timedelta], ZoneRuntimeState]] = []
    for zone in enabled_zones:
        ranked_enabled_zones.append((_safety_open_rank(zone, operation_mode, continue_threshold=False), zone))
    enabled_zones = _sorted_by_rank(ranked_enabled_zones)
    return enabled_zones[0].key


def _zone_temperature_reason(zone: ZoneRuntimeState, operation_mode: str | None) -> str:
    if not zone.is_enabled_by_mode:
        return f"mode disabled by scheme {zone.scheme.name}"
    if operation_mode == HVAC_COOL:
        if zone.current_temp > zone.scheme.cool_enable_above():
            return f"above enable threshold {zone.current_temp:.1f}>{zone.scheme.cool_enable_above():.1f}"
        if zone.current_temp > zone.scheme.cool_continue_until():
            return f"above continue-until threshold {zone.current_temp:.1f}>{zone.scheme.cool_continue_until():.1f}"
        if zone.current_temp > zone.scheme.ideal_target:
            return f"above ideal target {zone.current_temp:.1f}>{zone.scheme.ideal_target:.1f}"
        return f"at or below ideal target {zone.current_temp:.1f}<={zone.scheme.ideal_target:.1f}"
    if zone.current_temp < zone.scheme.enable_below:
        return f"below enable threshold {zone.current_temp:.1f}<{zone.scheme.enable_below:.1f}"
    if zone.current_temp < zone.scheme.continue_until:
        return f"below continue-until threshold {zone.current_temp:.1f}<{zone.scheme.continue_until:.1f}"
    if zone.current_temp < zone.scheme.ideal_target:
        return f"below ideal target {zone.current_temp:.1f}<{zone.scheme.ideal_target:.1f}"
    return f"at or above ideal target {zone.current_temp:.1f}>={zone.scheme.ideal_target:.1f}"


def describe_zone_predictions(
    snapshot: DemandSnapshot,
    now: datetime,
    predicted_open_zones: tuple[str, ...],
    *,
    operation_mode: str | None = None,
    comfort_mode_changed: bool = False,
) -> tuple[str, ...]:
    predicted_open = set(predicted_open_zones)
    descriptions: list[str] = []

    for zone_key, zone in snapshot.zones.items():
        status_parts: list[str] = []
        status_parts.append(_zone_temperature_reason(zone, operation_mode))
        if zone.switch_is_on:
            status_parts.append("switch currently on")
        else:
            status_parts.append("switch currently off")

        if snapshot.selected_hvac_mode == CONTROL_HVAC_MODE_MANUAL:
            status_parts.append("manual mode leaves zone unchanged")
        elif snapshot.selected_hvac_mode == CONTROL_HVAC_MODE_OFF:
            status_parts.append("hvac mode Off closes all zones")
        elif operation_mode not in {HVAC_HEAT, HVAC_COOL}:
            status_parts.append("no active heating/cooling selection")

        if zone.key in predicted_open:
            if zone.switch_is_on:
                status_parts.append("kept open")
            else:
                status_parts.append("predicted to open")
        else:
            if zone.is_enabled_by_mode and operation_mode in {HVAC_HEAT, HVAC_COOL} and _zone_should_open(zone, operation_mode):
                if _can_toggle(zone, now, comfort_mode_changed):
                    status_parts.append("eligible to open but another zone ranked ahead")
                else:
                    status_parts.append("held closed by anti-flap delay")
            elif zone.switch_is_on and zone.is_enabled_by_mode and operation_mode in {HVAC_HEAT, HVAC_COOL} and _zone_should_close(zone, operation_mode):
                if _can_toggle(zone, now, comfort_mode_changed):
                    status_parts.append("eligible to close")
                else:
                    status_parts.append("held open by anti-flap delay")
            else:
                status_parts.append("not selected to open")

        descriptions.append(
            f"{zone_key}: scheme={zone.scheme.name} temp={zone.current_temp:.1f} switch={'on' if zone.switch_is_on else 'off'} predicted={'open' if zone_key in predicted_open else 'closed'} because "
            + "; ".join(status_parts)
        )

    return tuple(descriptions)


def resolve_zone_actions(
    snapshot: DemandSnapshot,
    now: datetime,
    *,
    operation_mode: str | None = None,
    comfort_mode_changed: bool = False,
) -> tuple[list[ZoneAction], tuple[str, ...]]:
    actions: list[ZoneAction] = []
    predicted_open: set[str] = set()
    for key, zone in snapshot.zones.items():
        if zone.switch_is_on:
            predicted_open.add(key)

    if snapshot.selected_hvac_mode == CONTROL_HVAC_MODE_MANUAL:
        return actions, tuple(sorted(predicted_open))

    if snapshot.selected_hvac_mode == CONTROL_HVAC_MODE_OFF:
        for key in tuple(sorted(predicted_open)):
            actions.append(
                ZoneAction(
                    zone_key=key,
                    turn_on=False,
                    reason="hvac mode Off disables all zones",
                    discretionary=False,
                )
            )
        return actions, tuple()

    if operation_mode not in {HVAC_HEAT, HVAC_COOL}:
        return actions, tuple(sorted(predicted_open))

    discretionary_used = 0

    opening_candidates: list[ZoneRuntimeState] = []
    for zone in snapshot.zones.values():
        if zone.is_enabled_by_mode and not zone.switch_is_on and _zone_should_open(zone, operation_mode):
            opening_candidates.append(zone)
    ranked_opening_candidates: list[tuple[tuple[float, datetime], ZoneRuntimeState]] = []
    for zone in opening_candidates:
        ranked_opening_candidates.append((_opening_rank(zone, operation_mode), zone))
    opening_candidates = _sorted_by_rank(ranked_opening_candidates)

    closing_candidates: list[ZoneRuntimeState] = []
    for zone in snapshot.zones.values():
        if zone.switch_is_on and zone.is_enabled_by_mode and _zone_should_close(zone, operation_mode):
            closing_candidates.append(zone)
    ranked_closing_candidates: list[tuple[tuple[float, datetime], ZoneRuntimeState]] = []
    for zone in closing_candidates:
        ranked_closing_candidates.append((_closing_rank(zone, operation_mode), zone))
    closing_candidates = _sorted_by_rank(ranked_closing_candidates)

    if comfort_mode_changed:
        for zone in opening_candidates:
            if zone.key in predicted_open or not _can_toggle(zone, now, comfort_mode_changed):
                continue
            predicted_open.add(zone.key)
            actions.append(
                ZoneAction(
                    zone_key=zone.key,
                    turn_on=True,
                    reason=(
                        f"{zone.current_temp:.1f} is above continue-until target {zone.scheme.cool_continue_until():.1f}"
                        if operation_mode == HVAC_COOL
                        else f"{zone.current_temp:.1f} is below continue-until target {zone.scheme.continue_until:.1f}"
                    ),
                )
            )

    for zone in closing_candidates:
        if zone.key not in predicted_open or len(predicted_open) <= MIN_OPEN_ZONES:
            continue
        if not _can_toggle(zone, now, comfort_mode_changed):
            continue
        if not comfort_mode_changed and discretionary_used >= MAX_DISCRETIONARY_ZONE_CHANGES_PER_PASS:
            break
        predicted_open.remove(zone.key)
        actions.append(
            ZoneAction(
                zone_key=zone.key,
                turn_on=False,
                reason=(
                    f"{zone.current_temp:.1f} is at or below ideal target {zone.scheme.ideal_target:.1f}"
                    if operation_mode == HVAC_COOL
                    else f"{zone.current_temp:.1f} is at or above ideal target {zone.scheme.ideal_target:.1f}"
                ),
            )
        )
        if not comfort_mode_changed:
            discretionary_used += 1

    if not comfort_mode_changed:
        for zone in opening_candidates:
            if zone.key in predicted_open or not _can_toggle(zone, now, comfort_mode_changed):
                continue
            if discretionary_used >= MAX_DISCRETIONARY_ZONE_CHANGES_PER_PASS:
                break
            predicted_open.add(zone.key)
            actions.append(
                ZoneAction(
                    zone_key=zone.key,
                    turn_on=True,
                    reason=(
                        f"{zone.current_temp:.1f} is above continue-until target {zone.scheme.cool_continue_until():.1f}"
                        if operation_mode == HVAC_COOL
                        else f"{zone.current_temp:.1f} is below continue-until target {zone.scheme.continue_until:.1f}"
                    ),
                )
            )
            discretionary_used += 1

    if not predicted_open:
        safety_zone_key = _select_safety_open_zone(snapshot, operation_mode)
        if safety_zone_key:
            zone = snapshot.zones[safety_zone_key]
            safety_override_required = not _can_toggle(zone, now, comfort_mode_changed)
            if not zone.switch_is_on:
                predicted_open.add(zone.key)
                reason = "keeping at least one zone open for safety"
                if safety_override_required:
                    reason += "; overriding anti-flap delay because every zone would otherwise be closed"
                actions.append(
                    ZoneAction(
                        zone_key=zone.key,
                        turn_on=True,
                        reason=reason,
                        safety_required=True,
                        discretionary=False,
                    )
                )
            elif zone.switch_is_on:
                predicted_open.add(zone.key)

    return actions, tuple(sorted(predicted_open))
