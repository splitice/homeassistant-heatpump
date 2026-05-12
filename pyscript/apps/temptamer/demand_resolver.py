from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from .constants import (
    COMFORT_MODE_OFF,
    CONTROL_HVAC_MODE_COOL,
    CONTROL_HVAC_MODE_HEAT,
    CONTROL_HVAC_MODE_HEATCOOL,
    CONTROL_HVAC_MODE_MANUAL,
    CONTROL_HVAC_MODE_OFF,
    HVAC_COOL,
    HVAC_HEAT,
    MIN_HEAT_COOL_TRANSITION_SECONDS,
)
from .models import DemandSnapshot, EquipmentDemand, ZoneRuntimeState


def _deficit(snapshot: DemandSnapshot, zone_key: str, threshold_name: str) -> float:
    zone = snapshot.zones[zone_key]
    threshold = getattr(zone.scheme, threshold_name)
    return max(0.0, threshold - zone.current_temp)


def _ranked_requesting_zones(
    snapshot: DemandSnapshot,
    zone_keys: tuple[str, ...],
    threshold_name: str,
) -> tuple[tuple[str, ...], float]:
    if not zone_keys:
        return (), 0.0

    ranked_zones: list[tuple[float, str]] = []
    for zone_key in zone_keys:
        deficit = _deficit(snapshot, zone_key, threshold_name)
        insert_at = len(ranked_zones)
        for index, existing in enumerate(ranked_zones):
            if deficit > existing[0]:
                insert_at = index
                break
        ranked_zones.insert(insert_at, (deficit, zone_key))

    ordered_zone_keys: list[str] = []
    for _deficit_value, zone_key in ranked_zones:
        ordered_zone_keys.append(zone_key)

    max_deficit = ranked_zones[0][0]
    return tuple(ordered_zone_keys), max_deficit


def _max_deficit(snapshot: DemandSnapshot, zone_keys: tuple[str, ...], threshold_name: str) -> tuple[str | None, float]:
    ranked_zone_keys, max_deficit = _ranked_requesting_zones(snapshot, zone_keys, threshold_name)
    if not ranked_zone_keys:
        return None, 0.0
    return ranked_zone_keys[0], max_deficit


def _max_excess(
    snapshot: DemandSnapshot,
    zone_keys: tuple[str, ...],
    threshold_resolver: Callable[[ZoneRuntimeState], float],
) -> tuple[str | None, float]:
    if not zone_keys:
        return None, 0.0

    selected_zone_key: str | None = None
    selected_excess = 0.0

    for zone_key in zone_keys:
        zone = snapshot.zones[zone_key]
        threshold = threshold_resolver(zone)
        excess = max(0.0, zone.current_temp - threshold)
        if selected_zone_key is None or excess > selected_excess:
            selected_zone_key = zone_key
            selected_excess = excess

    return selected_zone_key, selected_excess


def _intersect_zone_keys(zone_keys: tuple[str, ...], allowed_zone_keys: tuple[str, ...]) -> tuple[str, ...]:
    allowed = set(allowed_zone_keys)
    intersected_zone_keys: list[str] = []
    for zone_key in zone_keys:
        if zone_key in allowed:
            intersected_zone_keys.append(zone_key)
    return tuple(intersected_zone_keys)


def _normalize_timestamp(value: datetime | None) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def resolve_operating_mode(
    snapshot: DemandSnapshot,
    *,
    current_hvac_mode: str | None,
    last_active_hvac_mode: str | None,
    last_heatcool_transition: datetime | None,
    now: datetime,
) -> tuple[str | None, str]:
    selected_hvac_mode = snapshot.selected_hvac_mode

    if selected_hvac_mode == CONTROL_HVAC_MODE_OFF:
        return None, "hvac mode is Off"
    if selected_hvac_mode == CONTROL_HVAC_MODE_MANUAL:
        return None, "hvac mode is Manual"
    if selected_hvac_mode == CONTROL_HVAC_MODE_HEAT:
        return HVAC_HEAT, "hvac mode is Heat"
    if selected_hvac_mode == CONTROL_HVAC_MODE_COOL:
        return HVAC_COOL, "hvac mode is Cool"

    current_active_mode = (current_hvac_mode or "").lower()
    if current_active_mode not in {HVAC_HEAT, HVAC_COOL}:
        current_active_mode = None

    last_active_mode = (last_active_hvac_mode or "").lower()
    if last_active_mode not in {HVAC_HEAT, HVAC_COOL}:
        last_active_mode = None

    active_mode = current_active_mode or last_active_mode
    heat_zone, heat_deficit = _max_deficit(snapshot, snapshot.heat_calling_zones, "enable_outside")
    cool_zone, cool_excess = _max_excess(snapshot, snapshot.cool_calling_zones, lambda zone: zone.cool_scheme.enable_outside)

    preferred_mode: str | None = None
    if heat_zone and cool_zone:
        if active_mode == HVAC_HEAT and heat_deficit >= cool_excess:
            preferred_mode = HVAC_HEAT
        elif active_mode == HVAC_COOL and cool_excess >= heat_deficit:
            preferred_mode = HVAC_COOL
        else:
            preferred_mode = HVAC_HEAT if heat_deficit >= cool_excess else HVAC_COOL
    elif heat_zone:
        preferred_mode = HVAC_HEAT
    elif cool_zone:
        preferred_mode = HVAC_COOL
    elif active_mode == HVAC_HEAT and snapshot.continue_heating_zones:
        preferred_mode = HVAC_HEAT
    elif active_mode == HVAC_COOL and snapshot.continue_cooling_zones:
        preferred_mode = HVAC_COOL
    else:
        return None, "HeatCool mode has no active heating or cooling demand"

    normalized_last_transition = _normalize_timestamp(last_heatcool_transition)
    normalized_now = _normalize_timestamp(now)
    transition_window_active = (
        active_mode in {HVAC_HEAT, HVAC_COOL}
        and preferred_mode != active_mode
        and normalized_last_transition is not None
        and normalized_now is not None
        and normalized_now - normalized_last_transition < timedelta(seconds=MIN_HEAT_COOL_TRANSITION_SECONDS)
    )
    if transition_window_active:
        if current_active_mode in {HVAC_HEAT, HVAC_COOL}:
            return current_active_mode, f"holding {current_active_mode} during HeatCool anti-flap window"
        return None, f"waiting for HeatCool anti-flap window before switching to {preferred_mode}"

    return preferred_mode, f"HeatCool selected {preferred_mode}"


def resolve_equipment_demand(
    snapshot: DemandSnapshot,
    predicted_open_zones: tuple[str, ...],
    *,
    operation_mode: str | None,
) -> EquipmentDemand:
    if snapshot.comfort_mode == COMFORT_MODE_OFF:
        return EquipmentDemand(reason="comfort mode is Off")
    if snapshot.selected_hvac_mode == CONTROL_HVAC_MODE_OFF:
        return EquipmentDemand(reason="hvac mode is Off")
    if snapshot.selected_hvac_mode == CONTROL_HVAC_MODE_MANUAL:
        return EquipmentDemand(reason="hvac mode is Manual")
    if operation_mode is None:
        return EquipmentDemand(reason="no active heating or cooling mode selected")

    if not predicted_open_zones:
        return EquipmentDemand(reason=f"no zones are predicted to be open for {operation_mode}")

    if operation_mode == HVAC_COOL:
        requested_by_zone, max_excess = _max_excess(snapshot, snapshot.cool_calling_zones, lambda zone: zone.cool_scheme.enable_outside)
        if requested_by_zone is not None:
            return EquipmentDemand(
                cool_requested=True,
                requested_by_zones=(requested_by_zone,),
                max_temperature_deficit=max_excess,
                reason=f"{requested_by_zone} is above enable threshold",
            )

        continue_zone, continue_excess = _max_excess(
            snapshot,
            _intersect_zone_keys(snapshot.continue_cooling_zones, snapshot.above_ideal_zones),
            lambda zone: zone.cool_scheme.continue_until,
        )
        if continue_zone is not None:
            return EquipmentDemand(
                maintain_cool_mode=True,
                requested_by_zones=(continue_zone,),
                max_temperature_deficit=continue_excess,
                reason=f"{continue_zone} is above continue-until threshold",
            )

        return EquipmentDemand(reason="no active cooling demand")

    requested_by_zones, max_deficit = _ranked_requesting_zones(snapshot, snapshot.heat_calling_zones, "enable_outside")
    if requested_by_zones:
        primary_zone = requested_by_zones[0]
        return EquipmentDemand(
            heat_requested=True,
            requested_by_zones=requested_by_zones,
            max_temperature_deficit=max_deficit,
            reason=f"{primary_zone} is below enable threshold",
        )

    continue_zones, continue_deficit = _ranked_requesting_zones(
        snapshot,
        _intersect_zone_keys(snapshot.continue_heating_zones, snapshot.below_ideal_zones),
        "continue_until",
    )
    if continue_zones:
        primary_zone = continue_zones[0]
        return EquipmentDemand(
            maintain_heat_mode=True,
            requested_by_zones=continue_zones,
            max_temperature_deficit=continue_deficit,
            reason=f"{primary_zone} is below continue-until threshold",
        )

    open_zones = set(predicted_open_zones)
    open_at_ideal: list[str] = []
    all_open_zones_at_ideal = True
    for zone_key in predicted_open_zones:
        if zone_key in snapshot.at_ideal_zones:
            open_at_ideal.append(zone_key)
        else:
            all_open_zones_at_ideal = False

    if open_zones and open_at_ideal and snapshot.below_ideal_zones:
        if all_open_zones_at_ideal:
            return EquipmentDemand(
                fan_only_requested=True,
                reason="all open zones are at ideal while another enabled zone is still below ideal",
            )
        return EquipmentDemand(
            maintain_heat_mode=True,
            reason="balancing enabled zones with neutral heating setpoint",
        )


    return EquipmentDemand(reason="no active heating demand")
