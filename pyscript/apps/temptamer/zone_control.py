from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .constants import (
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


def _select_safety_open_zone(snapshot: DemandSnapshot) -> str | None:
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
        if zone.current_temp < zone.scheme.continue_until:
            continue_zones.append(zone)

    if continue_zones:
        ranked_continue_zones: list[tuple[tuple[float, timedelta], ZoneRuntimeState]] = []
        for zone in continue_zones:
            ranked_continue_zones.append(((-_temperature_deficit(zone, "continue_until"), _recent_change_rank(zone)), zone))
        continue_zones = _sorted_by_rank(ranked_continue_zones)
        return continue_zones[0].key

    ranked_enabled_zones: list[tuple[tuple[float, timedelta], ZoneRuntimeState]] = []
    for zone in enabled_zones:
        ranked_enabled_zones.append(((-_temperature_deficit(zone, "ideal_target"), _recent_change_rank(zone)), zone))
    enabled_zones = _sorted_by_rank(ranked_enabled_zones)
    return enabled_zones[0].key


def _zone_temperature_reason(zone: ZoneRuntimeState) -> str:
    if not zone.is_enabled_by_mode:
        return f"mode disabled by scheme {zone.scheme.name}"
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
    comfort_mode_changed: bool = False,
) -> tuple[str, ...]:
    predicted_open = set(predicted_open_zones)
    descriptions: list[str] = []

    for zone_key, zone in snapshot.zones.items():
        status_parts: list[str] = []
        status_parts.append(_zone_temperature_reason(zone))
        if zone.switch_is_on:
            status_parts.append("switch currently on")
        else:
            status_parts.append("switch currently off")

        if zone.key in predicted_open:
            if zone.switch_is_on:
                status_parts.append("kept open")
            else:
                status_parts.append("predicted to open")
        else:
            if zone.is_enabled_by_mode and zone.current_temp < zone.scheme.continue_until:
                if _can_toggle(zone, now, comfort_mode_changed):
                    status_parts.append("eligible to open but another zone ranked ahead")
                else:
                    status_parts.append("held closed by anti-flap delay")
            elif zone.switch_is_on and zone.is_enabled_by_mode and zone.current_temp >= zone.scheme.ideal_target:
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
    comfort_mode_changed: bool = False,
) -> tuple[list[ZoneAction], tuple[str, ...]]:
    actions: list[ZoneAction] = []
    predicted_open: set[str] = set()
    for key, zone in snapshot.zones.items():
        if zone.switch_is_on:
            predicted_open.add(key)

    discretionary_used = 0

    opening_candidates: list[ZoneRuntimeState] = []
    for zone in snapshot.zones.values():
        if zone.is_enabled_by_mode and not zone.switch_is_on and zone.current_temp < zone.scheme.continue_until:
            opening_candidates.append(zone)
    ranked_opening_candidates: list[tuple[tuple[float, datetime], ZoneRuntimeState]] = []
    for zone in opening_candidates:
        ranked_opening_candidates.append(
            ((zone.current_temp - zone.scheme.enable_below, _last_change_key(zone)), zone)
        )
    opening_candidates = _sorted_by_rank(ranked_opening_candidates)

    closing_candidates: list[ZoneRuntimeState] = []
    for zone in snapshot.zones.values():
        if zone.switch_is_on and zone.is_enabled_by_mode and zone.current_temp >= zone.scheme.ideal_target:
            closing_candidates.append(zone)
    ranked_closing_candidates: list[tuple[tuple[float, datetime], ZoneRuntimeState]] = []
    for zone in closing_candidates:
        ranked_closing_candidates.append(
            ((-(zone.current_temp - zone.scheme.ideal_target), _last_change_key(zone)), zone)
        )
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
                    reason=f"{zone.current_temp:.1f} is below continue-until target {zone.scheme.continue_until:.1f}",
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
                reason=f"{zone.current_temp:.1f} is at or above ideal target {zone.scheme.ideal_target:.1f}",
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
                    reason=f"{zone.current_temp:.1f} is below continue-until target {zone.scheme.continue_until:.1f}",
                )
            )
            discretionary_used += 1

    if not predicted_open:
        safety_zone_key = _select_safety_open_zone(snapshot)
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
