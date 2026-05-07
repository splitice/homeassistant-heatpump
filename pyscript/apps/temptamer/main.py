from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import DEFAULT_SYSTEM_CONFIG
from .constants import APP_NAME, CONTROL_INTERVAL_SECONDS, LOGGER_NAME, SWITCH_STATE_SETTLE_SECONDS
from .demand_resolver import resolve_equipment_demand
from .heatpump_dispatcher import apply_dispatch_plan, apply_zone_actions, build_dispatch_plan
from .state_reader import build_snapshot, is_switch_on
from .zone_control import describe_zone_predictions, resolve_zone_actions

USING_PYTHON_IMPORTS = __name__.startswith("pyscript.") or __name__ == "__main__"


if USING_PYTHON_IMPORTS and "service" not in globals():  # pragma: no cover - used only outside PyScript runtime
    class _ServiceRuntime:
        def __call__(self, *_args, **_kwargs):
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def call(_domain, _service_name, **_kwargs):
            return None

    service = _ServiceRuntime()


if USING_PYTHON_IMPORTS and "time_trigger" not in globals():  # pragma: no cover - used only outside PyScript runtime
    def time_trigger(*_args, **_kwargs):
        def decorator(func):
            return func

        return decorator


if USING_PYTHON_IMPORTS and "state_trigger" not in globals():  # pragma: no cover - used only outside PyScript runtime
    def state_trigger(*_args, **_kwargs):
        def decorator(func):
            return func

        return decorator


if USING_PYTHON_IMPORTS and "task" not in globals():  # pragma: no cover - used only outside PyScript runtime
    class _TaskRuntime:
        @staticmethod
        def create(func, *args, **kwargs):
            func(*args, **kwargs)
            return None

        @staticmethod
        def cancel(_task_id=None):
            return None

        @staticmethod
        def sleep(_seconds):
            return None

        @staticmethod
        def unique(_name, kill_me=False):
            return None

    task = _TaskRuntime()


if USING_PYTHON_IMPORTS and "state" not in globals():  # pragma: no cover - used only outside PyScript runtime
    class _StateRuntime:
        def __init__(self):
            self._values: dict[str, object | None] = {}
            self._attrs: dict[str, dict[str, object]] = {}

        def get(self, entity_id: str) -> object | None:
            return self._values.get(entity_id)

        def getattr(self, entity_id: str) -> dict[str, object] | None:
            return self._attrs.get(entity_id)

        def set(self, entity_id: str, value: object | None = None, new_attributes: dict[str, object] | None = None, **kwargs: object) -> None:
            self._values[entity_id] = value
            if new_attributes is not None:
                self._attrs[entity_id] = dict(new_attributes)
            elif kwargs:
                attrs = dict(self._attrs.get(entity_id, {}))
                attrs.update(kwargs)
                self._attrs[entity_id] = attrs

        def persist(
            self,
            entity_id: str,
            default_value: object | None = None,
            default_attributes: dict[str, object] | None = None,
        ) -> None:
            if entity_id not in self._values and default_value is not None:
                self._values[entity_id] = default_value
            if entity_id not in self._attrs and default_attributes is not None:
                self._attrs[entity_id] = dict(default_attributes)

    state = _StateRuntime()


LOGGER = logging.getLogger(LOGGER_NAME)

CONTROL_PASS_TASK_NAME = f"{APP_NAME}_control_pass"
STATUS_ENTITY_ID = f"pyscript.{APP_NAME}_status"
ENABLED_ENTITY_ID = f"pyscript.{APP_NAME}_enabled"

task.unique(CONTROL_PASS_TASK_NAME)

state.persist(  # type: ignore[name-defined]
    ENABLED_ENTITY_ID,
    default_value="on",
    default_attributes={"friendly_name": "TempTamer Enabled"},
)
state.persist(  # type: ignore[name-defined]
    STATUS_ENTITY_ID,
    default_value="stopped",
    default_attributes={"app_name": APP_NAME},
)

RUNTIME_STATE: dict[str, Any] = {
    "last_successful_control_pass": None,
    "last_zone_change": {},
    "pending_zone_state": {},
    "last_error": None,
    "last_trigger": None,
}


class PyscriptController:
    def get_state(self, entity_id: str) -> object | None:
        try:
            return state.get(entity_id)  # type: ignore[name-defined]
        except NameError:
            return None

    def get_attr(self, entity_id: str, attr_name: str) -> object | None:
        attrs = state.getattr(entity_id)  # type: ignore[name-defined]
        if not attrs:
            return None
        return attrs.get(attr_name)

    def call_service(self, domain: str, service_name: str, **kwargs: object) -> None:
        service.call(domain, service_name, blocking=True, **kwargs)  # type: ignore[name-defined]


def _describe_open_zones(open_zones: tuple[str, ...]) -> str:
    if not open_zones:
        return "none"

    zone_labels: list[str] = []
    for zone_key in open_zones:
        zone_labels.append(DEFAULT_SYSTEM_CONFIG.zones[zone_key].label)
    return ", ".join(zone_labels)


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _control_is_enabled() -> bool:
    enabled_state = state.get(ENABLED_ENTITY_ID)  # type: ignore[name-defined]
    if enabled_state is None:
        return True
    return str(enabled_state).strip().lower() in {"on", "true", "1", "enabled"}


def _set_control_enabled(enabled: bool, *, reason: str) -> None:
    state.set(  # type: ignore[name-defined]
        ENABLED_ENTITY_ID,
        "on" if enabled else "off",
        reason=reason,
    )


def _publish_runtime_state(status: str) -> None:
    state.set(  # type: ignore[name-defined]
        STATUS_ENTITY_ID,
        status,
        new_attributes={
            "app_name": APP_NAME,
            "control_interval_seconds": CONTROL_INTERVAL_SECONDS,
            "enabled": _control_is_enabled(),
            "last_trigger": RUNTIME_STATE["last_trigger"],
            "last_successful_control_pass": _isoformat(RUNTIME_STATE["last_successful_control_pass"]),
            "last_error": RUNTIME_STATE["last_error"],
        },
    )


def _reconcile_pending_zone_state(controller: PyscriptController, now: datetime) -> None:
    pending_zone_state = RUNTIME_STATE["pending_zone_state"]
    last_zone_change = RUNTIME_STATE["last_zone_change"]
    for zone_key in list(pending_zone_state.keys()):
        desired_state = pending_zone_state[zone_key]
        actual_state = is_switch_on(controller.get_state(DEFAULT_SYSTEM_CONFIG.zones[zone_key].switch_entity_id))
        if actual_state == desired_state:
            del pending_zone_state[zone_key]
            continue

        changed_at = last_zone_change.get(zone_key)
        if not isinstance(changed_at, datetime):
            del pending_zone_state[zone_key]
            continue

        normalized_changed_at = changed_at.astimezone(timezone.utc) if changed_at.tzinfo else changed_at.replace(tzinfo=timezone.utc)
        if now - normalized_changed_at > timedelta(seconds=SWITCH_STATE_SETTLE_SECONDS):
            del pending_zone_state[zone_key]


def run_control_pass(*, reason: str, comfort_mode_changed: bool = False) -> None:
    task.unique(CONTROL_PASS_TASK_NAME)

    controller = PyscriptController()
    now = datetime.now(timezone.utc)
    RUNTIME_STATE["last_trigger"] = reason
    _reconcile_pending_zone_state(controller, now)

    snapshot = build_snapshot(
        controller,
        config=DEFAULT_SYSTEM_CONFIG,
        last_switch_changes=RUNTIME_STATE["last_zone_change"],
        pending_switch_states=RUNTIME_STATE["pending_zone_state"],
        now=now,
    )
    zone_actions, predicted_open_zones = resolve_zone_actions(
        snapshot,
        now,
        comfort_mode_changed=comfort_mode_changed,
    )
    zone_diagnostics = describe_zone_predictions(
        snapshot,
        now,
        predicted_open_zones,
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
            RUNTIME_STATE["pending_zone_state"][action.zone_key] = action.turn_on
            LOGGER.info(
                "ZONES: %s %s because %s",
                "Opening" if action.turn_on else "Closing",
                DEFAULT_SYSTEM_CONFIG.zones[action.zone_key].label,
                action.reason,
            )

    if not predicted_open_zones:
        LOGGER.warning("ZONES: predicted_open=none details=%s", " | ".join(zone_diagnostics))

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
        _describe_open_zones(plan.open_zones),
        reason,
    )
    RUNTIME_STATE["last_error"] = None
    RUNTIME_STATE["last_successful_control_pass"] = now
    _publish_runtime_state("running")


def _run_enabled_control_pass(*, reason: str, comfort_mode_changed: bool = False) -> None:
    if not _control_is_enabled():
        LOGGER.info("TempTamer control is disabled; skipping trigger '%s'", reason)
        _publish_runtime_state("stopped")
        return

    try:
        run_control_pass(reason=reason, comfort_mode_changed=comfort_mode_changed)
    except Exception as exc:  # pragma: no cover - exercised in Home Assistant runtime
        RUNTIME_STATE["last_error"] = str(exc)
        LOGGER.exception("TempTamer control pass failed")
        _publish_runtime_state("error")


@service("temptamer.start")
def temptamer_start() -> None:
    """yaml
name: Start TempTamer
description: Enable TempTamer periodic pyscript control passes and run one immediately.
"""
    if _control_is_enabled():
        LOGGER.info("TempTamer control is already enabled")
        _publish_runtime_state("running")
        return

    RUNTIME_STATE["last_error"] = None
    RUNTIME_STATE["last_trigger"] = "service start"
    _set_control_enabled(True, reason="service start")
    _publish_runtime_state("starting")
    _run_enabled_control_pass(reason="service start")


@service("temptamer.stop")
def temptamer_stop() -> None:
    """yaml
name: Stop TempTamer
description: Disable TempTamer periodic pyscript control passes.
"""
    if not _control_is_enabled():
        LOGGER.info("TempTamer control is already disabled")
        _publish_runtime_state("stopped")
        return

    RUNTIME_STATE["last_trigger"] = "service stop"
    _set_control_enabled(False, reason="service stop")
    _publish_runtime_state("stopped")


@service("temptamer.run_once")
def temptamer_run_once(reason: str = "manual service call") -> None:
    """yaml
name: Run TempTamer once
description: Execute one TempTamer control pass immediately.
fields:
  reason:
    description: Optional reason string added to logs and status state.
    example: Manual reconciliation after config change
    required: false
    selector:
      text:
"""
    run_control_pass(reason=reason)


@time_trigger("startup")
def temptamer_initialize() -> None:
    RUNTIME_STATE["last_trigger"] = "startup"
    if _control_is_enabled():
        _publish_runtime_state("starting")
        _run_enabled_control_pass(reason="startup")
        return

    _publish_runtime_state("stopped")


@time_trigger(f"period(now, {CONTROL_INTERVAL_SECONDS}s)")
def temptamer_periodic_control_pass() -> None:
    _run_enabled_control_pass(reason="periodic trigger")


@state_trigger(DEFAULT_SYSTEM_CONFIG.comfort_mode_entity)
def temptamer_comfort_mode_changed(*_args, **_kwargs) -> None:
    _run_enabled_control_pass(reason="comfort mode changed", comfort_mode_changed=True)

