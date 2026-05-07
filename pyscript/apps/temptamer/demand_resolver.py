from __future__ import annotations

from .constants import COMFORT_MODE_OFF
from .models import DemandSnapshot, EquipmentDemand


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


def resolve_equipment_demand(snapshot: DemandSnapshot, predicted_open_zones: tuple[str, ...]) -> EquipmentDemand:
    if snapshot.comfort_mode == COMFORT_MODE_OFF:
        return EquipmentDemand(reason="comfort mode is Off")

    if not predicted_open_zones:
        return EquipmentDemand(reason="no zones are predicted to be open")

    requested_by_zones, max_deficit = _ranked_requesting_zones(snapshot, snapshot.heat_calling_zones, "enable_below")
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
        snapshot.continue_heating_zones,
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

