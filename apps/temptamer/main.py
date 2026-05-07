from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .config import DEFAULT_SYSTEM_CONFIG
from .constants import CONTROL_INTERVAL_SECONDS, LOGGER_NAME
from .demand_resolver import resolve_equipment_demand
from .heatpump_dispatcher import apply_dispatch_plan, apply_zone_actions, build_dispatch_plan
from .state_reader import build_snapshot
from .zone_control import resolve_zone_actions

try:
    time_trigger
except NameError:  # pragma: no cover - used only outside PyScript runtime
    def time_trigger(*_args, **_kwargs):
        def decorator(func):
            return func

        return decorator


try:
    state_trigger
except NameError:  # pragma: no cover - used only outside PyScript runtime
    def state_trigger(*_args, **_kwargs):
        def decorator(func):
            return func

        return decorator


LOGGER = logging.getLogger(LOGGER_NAME)

RUNTIME_STATE: dict[str, Any] = {
    "last_successful_control_pass": None,
    "last_zone_change": {},
}


class PyscriptController:
    def get_state(self, entity_id: str) -> object | None:
        return state.get(entity_id)  # type: ignore[name-defined]

    def get_attr(self, entity_id: str, attr_name: str) -> object | None:
        attrs = state.getattr(entity_id)  # type: ignore[name-defined]
        if not attrs:
            return None
        return attrs.get(attr_name)

    def call_service(self, domain: str, service_name: str, **kwargs: object) -> None:
        service.call(domain, service_name, **kwargs)  # type: ignore[name-defined]


def run_control_pass(*, reason: str, comfort_mode_changed: bool = False) -> None:
    controller = PyscriptController()
    now = datetime.now(timezone.utc)

    snapshot = build_snapshot(
        controller,
        config=DEFAULT_SYSTEM_CONFIG,
        last_switch_changes=RUNTIME_STATE["last_zone_change"],
    )
    zone_actions, predicted_open_zones = resolve_zone_actions(
        snapshot,
        now,
        comfort_mode_changed=comfort_mode_changed,
    )
    demand = resolve_equipment_demand(snapshot, predicted_open_zones)

    climate_entity = DEFAULT_SYSTEM_CONFIG.climate_entity
    current_hvac_mode = controller.get_state(climate_entity)
    current_fan_mode = controller.get_attr(climate_entity, "fan_mode")
    current_setpoint = controller.get_attr(climate_entity, "temperature")

    if zone_actions:
        apply_zone_actions(controller, zone_actions, config=DEFAULT_SYSTEM_CONFIG)
        for action in zone_actions:
            RUNTIME_STATE["last_zone_change"][action.zone_key] = now
            LOGGER.info(
                "ZONES: %s %s because %s",
                "Opening" if action.turn_on else "Closing",
                DEFAULT_SYSTEM_CONFIG.zones[action.zone_key].label,
                action.reason,
            )

    plan = build_dispatch_plan(
        snapshot,
        demand,
        predicted_open_zones,
        current_hvac_mode=str(current_hvac_mode) if current_hvac_mode is not None else None,
        current_fan_mode=str(current_fan_mode) if current_fan_mode is not None else None,
    )

    apply_dispatch_plan(
        controller,
        plan,
        config=DEFAULT_SYSTEM_CONFIG,
        current_hvac_mode=str(current_hvac_mode) if current_hvac_mode is not None else None,
        current_fan_mode=str(current_fan_mode) if current_fan_mode is not None else None,
        current_setpoint=current_setpoint,
    )

    LOGGER.info(
        "DISPATCH: reason=%s requested_by_zone=%s hvac_mode=%s fan_mode=%s setpoint=%s open_zones=%s trigger=%s",
        plan.reason,
        plan.requested_by_zone,
        plan.hvac_mode or "off",
        plan.fan_mode,
        plan.setpoint,
        ", ".join(DEFAULT_SYSTEM_CONFIG.zones[zone_key].label for zone_key in plan.open_zones) or "none",
        reason,
    )
    RUNTIME_STATE["last_successful_control_pass"] = now


@time_trigger(f"period(now, {CONTROL_INTERVAL_SECONDS}s)")
def temptamer_control_loop() -> None:
    run_control_pass(reason="periodic loop")


@state_trigger(DEFAULT_SYSTEM_CONFIG.comfort_mode_entity)
def temptamer_comfort_mode_changed(*_args, **_kwargs) -> None:
    run_control_pass(reason="comfort mode changed", comfort_mode_changed=True)

