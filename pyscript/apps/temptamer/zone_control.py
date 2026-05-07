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
        ranked_open_zones: list[tuple[datetime, ZoneRuntimeState]] = []
        for zone in open_zones:
            ranked_open_zones.append((_last_change_key(zone), zone))
        open_zones = _sorted_by_rank(ranked_open_zones)
        return open_zones[0].key

    continue_zones: list[ZoneRuntimeState] = []
    for zone in enabled_zones:
        if zone.current_temp < zone.scheme.continue_until:
            continue_zones.append(zone)

    if continue_zones:
        ranked_continue_zones: list[tuple[tuple[float, datetime], ZoneRuntimeState]] = []
        for zone in continue_zones:
            ranked_continue_zones.append(((zone.current_temp, _last_change_key(zone)), zone))
        continue_zones = _sorted_by_rank(ranked_continue_zones)
        return continue_zones[0].key

    ranked_enabled_zones: list[tuple[tuple[float, datetime], ZoneRuntimeState]] = []
    for zone in enabled_zones:
        ranked_enabled_zones.append(((zone.current_temp, _last_change_key(zone)), zone))
    enabled_zones = _sorted_by_rank(ranked_enabled_zones)
    return enabled_zones[0].key


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
            if (not zone.switch_is_on) and _can_toggle(zone, now, comfort_mode_changed):
                predicted_open.add(zone.key)
                actions.append(
                    ZoneAction(
                        zone_key=zone.key,
                        turn_on=True,
                        reason="keeping at least one zone open for safety",
                        safety_required=True,
                        discretionary=False,
                    )
                )
            elif zone.switch_is_on:
                predicted_open.add(zone.key)

    return actions, tuple(sorted(predicted_open))
