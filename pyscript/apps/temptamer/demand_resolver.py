from __future__ import annotations

from .constants import COMFORT_MODE_OFF
from .models import DemandSnapshot, EquipmentDemand


def _max_deficit(snapshot: DemandSnapshot, zone_keys: tuple[str, ...], threshold_name: str) -> tuple[str | None, float]:
    if not zone_keys:
        return None, 0.0

    selected_zone_key: str | None = None
    selected_deficit = 0.0

    for zone_key in zone_keys:
        zone = snapshot.zones[zone_key]
        threshold = getattr(zone.scheme, threshold_name)
        deficit = max(0.0, threshold - zone.current_temp)
        if selected_zone_key is None or deficit > selected_deficit:
            selected_zone_key = zone_key
            selected_deficit = deficit

    return selected_zone_key, selected_deficit


def resolve_equipment_demand(snapshot: DemandSnapshot, predicted_open_zones: tuple[str, ...]) -> EquipmentDemand:
    if snapshot.comfort_mode == COMFORT_MODE_OFF:
        return EquipmentDemand(reason="comfort mode is Off")

    if not predicted_open_zones:
        return EquipmentDemand(reason="no zones are predicted to be open")

    requested_by_zone, max_deficit = _max_deficit(snapshot, snapshot.heat_calling_zones, "enable_below")
    if requested_by_zone is not None:
        return EquipmentDemand(
            heat_requested=True,
            requested_by_zone=requested_by_zone,
            max_temperature_deficit=max_deficit,
            reason=f"{requested_by_zone} is below enable threshold",
        )

    continue_zone, continue_deficit = _max_deficit(snapshot, snapshot.continue_heating_zones, "continue_until")
    if continue_zone is not None:
        return EquipmentDemand(
            maintain_heat_mode=True,
            requested_by_zone=continue_zone,
            max_temperature_deficit=continue_deficit,
            reason=f"{continue_zone} is below continue-until threshold",
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

