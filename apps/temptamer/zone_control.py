from __future__ import annotations

from datetime import datetime, timedelta

from .constants import MIN_OPEN_ZONES, MIN_ZONE_CHANGE_DELAY_SECONDS
from .models import DemandSnapshot, ZoneAction, ZoneRuntimeState


def _last_change_key(zone: ZoneRuntimeState) -> datetime:
    return zone.last_switch_change or datetime.min


def _can_toggle(zone: ZoneRuntimeState, now: datetime, comfort_mode_changed: bool) -> bool:
    if comfort_mode_changed or zone.last_switch_change is None:
        return True
    return now - zone.last_switch_change >= timedelta(seconds=MIN_ZONE_CHANGE_DELAY_SECONDS)


def _select_safety_open_zone(snapshot: DemandSnapshot) -> str | None:
    enabled_zones = [zone for zone in snapshot.zones.values() if zone.is_enabled_by_mode]
    if not enabled_zones:
        open_zones = [zone for zone in snapshot.zones.values() if zone.switch_is_on]
        if not open_zones:
            return None
        open_zones.sort(key=lambda zone: _last_change_key(zone))
        return open_zones[0].key

    continue_zones = [zone for zone in enabled_zones if zone.current_temp < zone.scheme.continue_until]
    if continue_zones:
        continue_zones.sort(key=lambda zone: (zone.current_temp, _last_change_key(zone)))
        return continue_zones[0].key

    enabled_zones.sort(key=lambda zone: (zone.current_temp, _last_change_key(zone)))
    return enabled_zones[0].key


def resolve_zone_actions(
    snapshot: DemandSnapshot,
    now: datetime,
    *,
    comfort_mode_changed: bool = False,
) -> tuple[list[ZoneAction], tuple[str, ...]]:
    actions: list[ZoneAction] = []
    predicted_open = {key for key, zone in snapshot.zones.items() if zone.switch_is_on}
    discretionary_used = 0

    opening_candidates = sorted(
        (
            zone
            for zone in snapshot.zones.values()
            if zone.is_enabled_by_mode and not zone.switch_is_on and zone.current_temp < zone.scheme.continue_until
        ),
        key=lambda zone: (
            zone.current_temp - zone.scheme.enable_below,
            _last_change_key(zone),
        ),
    )

    closing_candidates = sorted(
        (
            zone
            for zone in snapshot.zones.values()
            if zone.switch_is_on and zone.is_enabled_by_mode and zone.current_temp >= zone.scheme.ideal_target
        ),
        key=lambda zone: (
            -(zone.current_temp - zone.scheme.ideal_target),
            _last_change_key(zone),
        ),
    )

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
        if not comfort_mode_changed and discretionary_used >= 1:
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

    for zone in opening_candidates:
        if comfort_mode_changed:
            continue
        if zone.key in predicted_open or not _can_toggle(zone, now, comfort_mode_changed):
            continue
        if not comfort_mode_changed and discretionary_used >= 1:
            break
        predicted_open.add(zone.key)
        actions.append(
            ZoneAction(
                zone_key=zone.key,
                turn_on=True,
                reason=f"{zone.current_temp:.1f} is below continue-until target {zone.scheme.continue_until:.1f}",
            )
        )
        if not comfort_mode_changed:
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
