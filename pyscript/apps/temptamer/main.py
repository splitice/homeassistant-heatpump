from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import (
    COMFORT_ADJUSTMENT_TRIGGER_ENTITIES,
    COMFORT_FABRIC_SOLAR_FILTER_STATE_FILE,
    COMFORT_SCORE_SEMANTICS_STATE_FILE,
    COMFORT_SCORE_SEMANTICS_VERSION,
    DEFAULT_COMFORT_ADJUSTMENT_CONFIG,
    DEFAULT_SYSTEM_CONFIG,
    DOWNSTAIRS_STARTUP_PRIORITY_INACTIVE_SECONDS,
    DOWNSTAIRS_STARTUP_PRIORITY_MIN_SECONDS,
    DOWNSTAIRS_STARTUP_PRIORITY_UPSTAIRS_ZONE_KEYS,
    DOWNSTAIRS_STARTUP_PRIORITY_ZONE_KEY,
    EAGLE_200_MAX_POWER_DEMAND_5M_SENSOR,
    EAGLE_200_POWER_DEMAND_SENSOR,
    POWERDAY_BATTERY_FREE_POWER_BOOST_RELEASE_THRESHOLD,
    GOODWE_BATTERY_REMAINING_SENSOR,
    GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR,
    GOODWE_PV_POWER_SENSOR,
    HEAT_DEMAND_FAN_BOOST_STATE_FILE,
    IDLE_DEMAND_FORECAST_HORIZON_SECONDS,
    IDLE_DEMAND_FORECAST_REFRESH_SECONDS,
    IDLE_DEMAND_FORECAST_STEP_SECONDS,
    IDLE_DEMAND_FORECAST_WEATHER_ENTITY,
    IMMEDIATE_RECONCILIATION_TRIGGER_ENTITIES,
    IMMEDIATE_SHUTDOWN_ZONE_CLOSE_DELAY_SECONDS,
    NORMAL_RECALCULATION_TRIGGER_ENTITIES,
    POWERDAY_BATTERY_THRESHOLD,
    POWERDAY_DOWNSTAIRS_PRIORITY_ENTER_GAP,
    POWERDAY_DOWNSTAIRS_PRIORITY_EXIT_GAP,
    POWERDAY_DOWNSTAIRS_PRIORITY_FAN_BOOST_LEVELS,
    POWERDAY_DOWNSTAIRS_FREE_POWER_FAN_BOOST_LEVELS,
    POWERDAY_DOWNSTAIRS_PRIORITY_MIN_SECONDS,
    POWERDAY_DOWNSTAIRS_PRIORITY_UPSTAIRS_ZONE_KEYS,
    POWERDAY_DOWNSTAIRS_PRIORITY_ZONE_KEY,
    POWERDAY_EXPORT_AVERAGE_WINDOW_SECONDS,
    POWERDAY_EXPORT_POWER_THRESHOLD,
    POWERDAY_FREE_POWER_PV_AVERAGE_WINDOW_SECONDS,
    POWERDAY_FREE_POWER_PV_POWER_THRESHOLD,
    POWERDAY_FREE_POWER_START_TIME,
    POWERDAY_HEAT_SINK_MIN_SECONDS,
    POWERDAY_INDOOR_HUMIDITY_SENSOR,
    POWERDAY_DRY_CYCLE_STATE_FILE,
    POWERDAY_DRY_HEAT_TRANSITION_SECONDS,
    POWERDAY_DRY_MAX_SECONDS,
    POWEROFF_ACTIVATION_BATTERY_THRESHOLD,
    POWEROFF_DEACTIVATION_BATTERY_THRESHOLD,
    POWEROFF_MIN_ACTIVATION_SECONDS,
    POWEROFF_PV_POWER_THRESHOLD,
    WEATHER_FORECAST_MAX_AGE_SECONDS,
)
from .comfort_adjustments import (
    apply_adjustment_hysteresis,
    calculate_comfort_adjustments,
    filter_outdoor_temperature,
    round_comfort_adjustment,
    resolve_outdoor_temperature,
)
from .constants import (
    APP_NAME,
    COMFORT_MODE_POWER_DAY,
    COMFORT_MODE_POWER_OFF,
    CONTROL_HVAC_MODE_COOL,
    CONTROL_HVAC_MODE_HEAT,
    CONTROL_HVAC_MODE_HEATCOOL,
    CONTROL_HVAC_MODE_MANUAL,
    CONTROL_INTERVAL_SECONDS,
    HEAT_DEMAND_FAN_BOOST_INTERVAL_SECONDS,
    HEAT_DEMAND_FAN_BOOST_MAX_LEVEL,
    HVAC_START_FAN_RAMP_DURATION_SECONDS,
    HVAC_START_FAN_RAMP_MIN_OFF_SECONDS,
    HVAC_COOL,
    HVAC_DRY,
    HVAC_HEAT,
    HVAC_OFF,
    NORMAL_RECALCULATION_DEBOUNCE_SECONDS,
    POWERDAY_HEATSOAK_FULL,
    SWITCH_STATE_SETTLE_SECONDS,
)
from .demand_resolver import resolve_equipment_demand, resolve_operating_mode
from .heatpump_dispatcher import (
    apply_dispatch_plan,
    apply_zone_actions,
    build_dispatch_plan,
    fan_speed_level,
    is_fan_speed_decrease,
    resolve_heat_demand_fan_boost,
    resolve_idle_started_at,
)
from .logging_control import get_temptamer_logger, install_temptamer_log_filter
from .idle_demand_forecast import (
    IdleDemandForecast,
    WeatherForecastPoint,
    forecast_idle_demand,
    parse_hourly_weather_forecast,
)
from .models import DispatchPlan, EquipmentDemand
from .powerday_forecast import (
    DehumidificationDecision,
    PowerDayForecastAssessment,
    assess_powerday_forecast,
    resolve_powerday_dehumidification,
)
from .state_reader import build_snapshot, is_switch_on, parse_float
from .zone_control import describe_zone_predictions, resolve_dry_zone_actions, resolve_zone_actions

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


if USING_PYTHON_IMPORTS and "pyscript_executor" not in globals():  # pragma: no cover - used only outside PyScript runtime
    def pyscript_executor(func):
        return func


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


install_temptamer_log_filter()
LOGGER = get_temptamer_logger()

CONTROL_PASS_TASK_NAME = f"{APP_NAME}_control_pass"
COMFORT_ADJUSTMENT_PASS_TASK_NAME = f"{APP_NAME}_comfort_adjustment_pass"
STATUS_ENTITY_ID = f"pyscript.{APP_NAME}_status"
ENABLED_ENTITY_ID = f"pyscript.{APP_NAME}_enabled"

task.unique(CONTROL_PASS_TASK_NAME)
task.unique(COMFORT_ADJUSTMENT_PASS_TASK_NAME)

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
    "last_heatcool_transition": None,
    "last_active_hvac_mode": None,
    "immediate_shutdown_zone_close_not_before": None,
    "last_heatcool_request_at": None,
    "downstairs_startup_priority_started_at": None,
    "downstairs_startup_priority_active": False,
    "downstairs_startup_priority_reason": None,
    "idle_started_at": None,
    "idle_heat_step": None,
    "idle_heat_step_changed_at": None,
    "idle_heat_zone_key": None,
    "idle_shutdown_at": None,
    "idle_shutdown_heat_step": None,
    "idle_shutdown_zone_key": None,
    "last_fan_speed_decrease_at": None,
    "hvac_off_started_at": None,
    "hvac_start_fan_ramp_started_at": None,
    "heat_demand_fan_boost_level": 0,
    "last_heat_demand_fan_boost_at": None,
    "heat_demand_fan_boost_restore_checked": False,
    "max_power_demand_5m_kw": None,
    "heat_demand_fan_boost_reason": None,
    "last_trigger": None,
    "normal_recalculation_generation": 0,
    "powerday_export_power_samples": [],
    "powerday_export_average": None,
    "powerday_battery_remaining": None,
    "powerday_battery_free_power_boost_active": False,
    "powerday_battery_free_power_boost_reason": None,
    "powerday_heat_sink_started_at": None,
    "powerday_heat_sink_hold_until": None,
    "powerday_heat_sink_active": False,
    "powerday_heat_sink_reason": None,
    "powerday_pv_power_samples": [],
    "powerday_pv_power_average": None,
    "powerday_free_power_later_started_at": None,
    "powerday_free_power_later_active": False,
    "powerday_free_power_later_reason": None,
    "powerday_downstairs_priority_started_at": None,
    "powerday_downstairs_priority_active": False,
    "powerday_downstairs_priority_gap": None,
    "powerday_downstairs_priority_upstairs_zones": (),
    "powerday_downstairs_priority_reason": None,
    "poweroff_battery_remaining": None,
    "poweroff_pv_power": None,
    "poweroff_activation_started_at": None,
    "poweroff_activation_hold_until": None,
    "poweroff_active": False,
    "poweroff_reason": None,
    "comfort_adjustment_label_warnings": set(),
    "comfort_adjustment_last_error": None,
    "comfort_adjustment_outdoor_filter_values": {},
    "comfort_adjustment_outdoor_filter_updated_at": {},
    "comfort_adjustment_outdoor_filter_seeded_at": {},
    "comfort_adjustment_fabric_solar_filter_values": {},
    "comfort_adjustment_fabric_solar_filter_updated_at": {},
    "comfort_adjustment_fabric_solar_filter_seeded_at": {},
    "comfort_adjustment_fabric_solar_filter_restore_checked": False,
    "comfort_adjustment_last_valid_cover_positions": {},
    "comfort_adjustment_last_published_adjustments": {},
    "comfort_adjustment_last_valid": {},
    "comfort_adjustment_last_valid_mode": None,
    "comfort_adjustment_calibration_parameters": None,
    "comfort_score_semantics_restore_checked": False,
    "comfort_score_semantics_version": None,
    "comfort_score_semantics_migration_in_progress": False,
    "idle_demand_forecast_weather_points": (),
    "idle_demand_forecast_weather_fetched_at": None,
    "weather_forecast_last_success_at": None,
    "idle_demand_forecast_weather_error": None,
    "idle_demand_forecast": None,
    "powerday_forecast_assessment": None,
    "powerday_indoor_humidity": None,
    "powerday_dry_eligible": False,
    "powerday_dry_humidity_eligible": False,
    "powerday_dry_coldest_enabled_zone_celsius": None,
    "powerday_dry_heat_forecast_safe": None,
    "powerday_dry_cool_forecast_safe": None,
    "powerday_dry_active": False,
    "powerday_dry_started_at": None,
    "powerday_dry_completed": False,
    "powerday_dry_completed_at": None,
    "powerday_dry_elapsed_seconds": 0.0,
    "powerday_dry_reason": None,
    "powerday_dry_restore_checked": False,
    "powerday_free_power_period_active": False,
    "powerday_dry_heat_transition_started_at": None,
    "powerday_dry_heat_transition_pending_off": False,
}


def _home_assistant_config_timezone() -> ZoneInfo | None:
    try:
        configured_time_zone = getattr(getattr(hass, "config", None), "time_zone", None)  # type: ignore[name-defined]
    except NameError:
        return None

    if not configured_time_zone:
        return None

    try:
        return ZoneInfo(str(configured_time_zone))
    except ZoneInfoNotFoundError:
        return None


def _system_now() -> datetime:
    try:
        import homeassistant.util.dt as dt_util
        hass_now = dt_util.now()
        if isinstance(hass_now, datetime):
            return hass_now
    except (ImportError, AttributeError):
        pass

    configured_time_zone = _home_assistant_config_timezone()
    if configured_time_zone is not None:
        return datetime.now(configured_time_zone)
    return datetime.now().astimezone()


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

    def call_service(self, domain: str, service_name: str, **kwargs: object) -> object | None:
        return service.call(domain, service_name, blocking=True, **kwargs)  # type: ignore[name-defined]


def _cached_idle_demand_weather_points() -> tuple[WeatherForecastPoint, ...]:
    raw_points = RUNTIME_STATE.get("idle_demand_forecast_weather_points")
    if not isinstance(raw_points, (list, tuple)):
        return ()
    points: list[WeatherForecastPoint] = []
    for point in raw_points:
        if isinstance(point, WeatherForecastPoint):
            points.append(point)
    return tuple(points)


def _fresh_cached_weather_points(now: datetime) -> tuple[WeatherForecastPoint, ...]:
    last_success_at = _normalize_runtime_datetime(RUNTIME_STATE.get("weather_forecast_last_success_at"))
    if last_success_at is None and RUNTIME_STATE.get("idle_demand_forecast_weather_error") is None:
        last_success_at = _normalize_runtime_datetime(
            RUNTIME_STATE.get("idle_demand_forecast_weather_fetched_at")
        )
    if last_success_at is None:
        return ()
    age_seconds = (now - last_success_at).total_seconds()
    if age_seconds < 0.0 or age_seconds > WEATHER_FORECAST_MAX_AGE_SECONDS:
        return ()
    return _cached_idle_demand_weather_points()


def _refresh_idle_demand_weather_forecast(
    controller: PyscriptController,
    now: datetime,
) -> tuple[WeatherForecastPoint, ...]:
    fetched_at = _normalize_runtime_datetime(RUNTIME_STATE.get("idle_demand_forecast_weather_fetched_at"))
    if fetched_at is not None:
        age_seconds = (now - fetched_at).total_seconds()
        if 0.0 <= age_seconds < IDLE_DEMAND_FORECAST_REFRESH_SECONDS:
            return _fresh_cached_weather_points(now)

    RUNTIME_STATE["idle_demand_forecast_weather_fetched_at"] = now
    try:
        response = controller.call_service(
            "weather",
            "get_forecasts",
            entity_id=IDLE_DEMAND_FORECAST_WEATHER_ENTITY,
            type="hourly",
            return_response=True,
        )
        points = parse_hourly_weather_forecast(response, IDLE_DEMAND_FORECAST_WEATHER_ENTITY)
    except Exception as exc:  # pragma: no cover - Home Assistant service failures are runtime-dependent
        RUNTIME_STATE["idle_demand_forecast_weather_error"] = str(exc)
        LOGGER.warning("DISPATCH: idle demand forecast weather refresh failed: %s", exc)
        return _fresh_cached_weather_points(now)

    if points:
        RUNTIME_STATE["idle_demand_forecast_weather_points"] = points
        RUNTIME_STATE["weather_forecast_last_success_at"] = now
        RUNTIME_STATE["idle_demand_forecast_weather_error"] = None
    else:
        RUNTIME_STATE["idle_demand_forecast_weather_error"] = "hourly forecast response contained no valid points"
        LOGGER.warning("DISPATCH: idle demand forecast weather refresh returned no valid hourly points")
    return _fresh_cached_weather_points(now)


def _resolve_powerday_forecast_assessment(
    controller: PyscriptController,
    now: datetime,
) -> PowerDayForecastAssessment:
    points = _refresh_idle_demand_weather_forecast(controller, now)
    outdoor_temperature = resolve_outdoor_temperature(
        controller,
        DEFAULT_COMFORT_ADJUSTMENT_CONFIG,
        now=now,
    )
    assessment = assess_powerday_forecast(
        points,
        now=now,
        current_outdoor_temperature=outdoor_temperature,
    )
    previous = _current_powerday_forecast_assessment()
    RUNTIME_STATE["powerday_forecast_assessment"] = assessment
    if previous is None or previous.level != assessment.level or previous.source != assessment.source:
        LOGGER.info("POWERDAY: forecast assessment changed: %s", assessment.reason)
    return assessment


def _has_active_equipment_demand(demand) -> bool:
    return bool(
        demand.heat_requested
        or demand.cool_requested
        or demand.fan_only_requested
        or demand.dry_requested
        or demand.maintain_heat_mode
        or demand.maintain_cool_mode
    )


def _resolve_idle_demand_forecast(
    controller: PyscriptController,
    snapshot,
    demand,
    *,
    current_hvac_mode: str | None,
    operation_mode: str | None,
    now: datetime,
) -> IdleDemandForecast | None:
    current_mode = (current_hvac_mode or "").lower()
    if current_mode not in {HVAC_HEAT, HVAC_COOL} or operation_mode not in {HVAC_HEAT, HVAC_COOL}:
        return None
    if _has_active_equipment_demand(demand):
        return None
    full_heat_soak_active = snapshot.surplus_heat_sink_available or (
        snapshot.free_power_available
        and snapshot.free_power_heat_soak_level == POWERDAY_HEATSOAK_FULL
    )
    if snapshot.comfort_mode == COMFORT_MODE_POWER_DAY and full_heat_soak_active and operation_mode == HVAC_HEAT:
        return IdleDemandForecast(
            generated_at=now,
            horizon_seconds=IDLE_DEMAND_FORECAST_HORIZON_SECONDS,
            earliest_demand_at=None,
            earliest_zone_key=None,
            operation_mode=operation_mode,
            safe_to_turn_off=False,
            source="powerday_heat_soak",
            reason="PowerDay boosted heat soak retains the existing idle behavior",
        )

    weather_points = _refresh_idle_demand_weather_forecast(controller, now)
    outdoor_temperature = resolve_outdoor_temperature(controller, DEFAULT_COMFORT_ADJUSTMENT_CONFIG, now=now)
    has_forecast_temperature = False
    for point in weather_points:
        if point.temperature is not None:
            has_forecast_temperature = True
    forecast_anchor_temperature = outdoor_temperature if has_forecast_temperature else None
    # ``forecast_idle_demand`` lives in a separate PyScript module.  Passing
    # it a PyScript method as a callback yields a coroutine when that module
    # invokes the callback, which a normal mapping lookup cannot consume.
    # Forecast from the current snapshot's already comfort-adjusted thresholds
    # instead; the temperature projection remains weather-aware.
    return forecast_idle_demand(
        snapshot,
        operation_mode=operation_mode,
        now=now,
        weather_points=weather_points,
        current_outdoor_temperature=forecast_anchor_temperature,
        horizon_seconds=IDLE_DEMAND_FORECAST_HORIZON_SECONDS,
        step_seconds=IDLE_DEMAND_FORECAST_STEP_SECONDS,
    )


def _comfort_adjustment_warn_once(warning_key: str, message: str) -> None:
    warnings = RUNTIME_STATE.setdefault("comfort_adjustment_label_warnings", set())
    if not isinstance(warnings, set):
        warnings = set()
        RUNTIME_STATE["comfort_adjustment_label_warnings"] = warnings
    if warning_key in warnings:
        return
    warnings.add(warning_key)
    LOGGER.warning("COMFORT ADJUSTMENT: %s", message)


def _resolve_cover_facades() -> dict[str, str | None]:
    """Return façade directions from labels assigned to each cover's device."""
    cover_entity_ids: list[str] = []
    for zone in DEFAULT_COMFORT_ADJUSTMENT_CONFIG.zones:
        for room in zone.rooms:
            if room.envelope is None:
                continue
            for window in room.envelope.windows:
                if window.cover_entity_id and window.cover_entity_id not in cover_entity_ids:
                    cover_entity_ids.append(window.cover_entity_id)

    resolved_facades: dict[str, str | None] = {}
    try:
        active_hass = hass  # type: ignore[name-defined]
        from homeassistant.helpers import device_registry as device_registry
        from homeassistant.helpers import entity_registry as entity_registry

        devices = device_registry.async_get(active_hass)
        entities = entity_registry.async_get(active_hass)
    except (AttributeError, ImportError, NameError) as exc:
        _comfort_adjustment_warn_once(
            "device-label-registry-unavailable",
            "unable to read cover-device labels; set pyscript hass_is_global and allow_all_imports: %s" % exc,
        )
        for cover_entity_id in cover_entity_ids:
            resolved_facades[cover_entity_id] = None
        return resolved_facades

    for cover_entity_id in cover_entity_ids:
        try:
            entity_entry = entities.async_get(cover_entity_id)
            device_id = getattr(entity_entry, "device_id", None) if entity_entry is not None else None
            device_entry = devices.async_get(device_id) if device_id else None
            device_labels = getattr(device_entry, "labels", ()) if device_entry is not None else ()
            matching_facades: set[str] = set()
            for label_id in device_labels:
                facade = DEFAULT_COMFORT_ADJUSTMENT_CONFIG.facade_labels.get(str(label_id))
                if facade is not None:
                    matching_facades.add(facade)
        except Exception as exc:  # pragma: no cover - depends on the Home Assistant registry runtime
            _comfort_adjustment_warn_once(
                f"cover-label-read-{cover_entity_id}",
                f"unable to read labels for {cover_entity_id}; treating it as unexposed: {exc}",
            )
            resolved_facades[cover_entity_id] = None
            continue

        if len(matching_facades) == 1:
            resolved_facades[cover_entity_id] = next(iter(matching_facades))
            continue

        resolved_facades[cover_entity_id] = None
        if not matching_facades:
            _comfort_adjustment_warn_once(
                f"cover-label-missing-{cover_entity_id}",
                f"{cover_entity_id} device has no awning_n/e/s/w label; treating it as unexposed",
            )
        else:
            _comfort_adjustment_warn_once(
                f"cover-label-ambiguous-{cover_entity_id}",
                f"{cover_entity_id} device has multiple façade labels; treating it as unexposed",
            )

    return resolved_facades


def _comfort_adjustment_runtime_mapping(key: str) -> dict[str, object]:
    values = RUNTIME_STATE.setdefault(key, {})
    if isinstance(values, dict):
        return values
    values = {}
    RUNTIME_STATE[key] = values
    return values


def _normalize_comfort_adjustment_timestamp(value: object | None) -> datetime | None:
    """Accept Home Assistant and persisted ISO timestamps."""
    normalized = _normalize_runtime_datetime(value)
    if normalized is not None or not isinstance(value, str):
        return normalized
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _normalize_runtime_datetime(parsed)


@pyscript_executor
def _read_comfort_fabric_solar_filter_state(file_path):
    """Read persisted filter memory without importing JSON in PyScript context."""
    import json

    try:
        with open(file_path, encoding="utf-8") as state_file:
            persisted_state = json.load(state_file)
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, str(exc)
    except (TypeError, ValueError) as exc:
        return None, f"invalid JSON: {exc}"
    if not isinstance(persisted_state, dict):
        return None, "state must be an object"
    return persisted_state, None


@pyscript_executor
def _write_comfort_fabric_solar_filter_state(file_path, persisted_state):
    """Atomically persist the delayed fabric-solar filter memory."""
    import json
    import os

    temporary_file_path = f"{file_path}.tmp"
    try:
        with open(temporary_file_path, "w", encoding="utf-8") as state_file:
            json.dump(persisted_state, state_file, sort_keys=True, separators=(",", ":"))
            state_file.write("\n")
        os.replace(temporary_file_path, file_path)
    except (OSError, TypeError, ValueError) as exc:
        try:
            os.unlink(temporary_file_path)
        except OSError:
            pass
        return str(exc)
    return None


def _restore_comfort_fabric_solar_filter_state() -> None:
    """Restore valid fabric-filter samples once per PyScript module lifetime."""
    if RUNTIME_STATE.get("comfort_adjustment_fabric_solar_filter_restore_checked"):
        return
    RUNTIME_STATE["comfort_adjustment_fabric_solar_filter_restore_checked"] = True

    persisted_state, read_error = _read_comfort_fabric_solar_filter_state(
        COMFORT_FABRIC_SOLAR_FILTER_STATE_FILE
    )
    if read_error is not None:
        LOGGER.warning("COMFORT ADJUSTMENT: unable to restore fabric-solar filters: %s", read_error)
        return
    if persisted_state is None:
        return
    if persisted_state.get("version") != 1:
        LOGGER.warning("COMFORT ADJUSTMENT: ignoring fabric-solar filters with an unsupported state version")
        return

    raw_values = persisted_state.get("values")
    raw_updated_at = persisted_state.get("updated_at")
    raw_seeded_at = persisted_state.get("seeded_at")
    if not isinstance(raw_values, dict) or not isinstance(raw_updated_at, dict) or not isinstance(raw_seeded_at, dict):
        LOGGER.warning("COMFORT ADJUSTMENT: ignoring malformed fabric-solar filter state")
        return

    values = _comfort_adjustment_runtime_mapping("comfort_adjustment_fabric_solar_filter_values")
    updated_at = _comfort_adjustment_runtime_mapping("comfort_adjustment_fabric_solar_filter_updated_at")
    seeded_at = _comfort_adjustment_runtime_mapping("comfort_adjustment_fabric_solar_filter_seeded_at")
    restored_count = 0
    for filter_key, raw_value in raw_values.items():
        if not isinstance(filter_key, str):
            continue
        value = parse_float(raw_value)
        updated_timestamp = _normalize_comfort_adjustment_timestamp(raw_updated_at.get(filter_key))
        seeded_timestamp = _normalize_comfort_adjustment_timestamp(raw_seeded_at.get(filter_key))
        if value is None or value < 0.0 or updated_timestamp is None or seeded_timestamp is None:
            continue
        values[filter_key] = value
        updated_at[filter_key] = updated_timestamp
        seeded_at[filter_key] = seeded_timestamp
        restored_count += 1
    if restored_count:
        LOGGER.info("COMFORT ADJUSTMENT: restored %s fabric-solar filter sample(s)", restored_count)


def _persist_comfort_fabric_solar_filter_state() -> None:
    """Save serializable fabric-filter memory after a live sample or reset."""
    values = _comfort_adjustment_runtime_mapping("comfort_adjustment_fabric_solar_filter_values")
    updated_at = _comfort_adjustment_runtime_mapping("comfort_adjustment_fabric_solar_filter_updated_at")
    seeded_at = _comfort_adjustment_runtime_mapping("comfort_adjustment_fabric_solar_filter_seeded_at")
    persisted_values: dict[str, float] = {}
    persisted_updated_at: dict[str, str] = {}
    persisted_seeded_at: dict[str, str] = {}
    for filter_key, raw_value in values.items():
        if not isinstance(filter_key, str):
            continue
        value = parse_float(raw_value)
        updated_timestamp = _normalize_comfort_adjustment_timestamp(updated_at.get(filter_key))
        seeded_timestamp = _normalize_comfort_adjustment_timestamp(seeded_at.get(filter_key))
        if value is None or value < 0.0 or updated_timestamp is None or seeded_timestamp is None:
            continue
        persisted_values[filter_key] = value
        persisted_updated_at[filter_key] = updated_timestamp.isoformat()
        persisted_seeded_at[filter_key] = seeded_timestamp.isoformat()

    write_error = _write_comfort_fabric_solar_filter_state(
        COMFORT_FABRIC_SOLAR_FILTER_STATE_FILE,
        {
            "version": 1,
            "values": persisted_values,
            "updated_at": persisted_updated_at,
            "seeded_at": persisted_seeded_at,
        },
    )
    if write_error is not None:
        _comfort_adjustment_warn_once(
            "fabric-solar-filter-persist-failed",
            f"unable to persist fabric-solar filters: {write_error}",
        )


def _clear_comfort_fabric_solar_filter_memory() -> bool:
    """Forget delayed solar heat when it cannot be physically present."""
    values = _comfort_adjustment_runtime_mapping("comfort_adjustment_fabric_solar_filter_values")
    updated_at = _comfort_adjustment_runtime_mapping("comfort_adjustment_fabric_solar_filter_updated_at")
    seeded_at = _comfort_adjustment_runtime_mapping("comfort_adjustment_fabric_solar_filter_seeded_at")
    changed = False
    for zone in DEFAULT_COMFORT_ADJUSTMENT_CONFIG.zones:
        filter_key = f"fabric_solar_{zone.key}"
        for mapping in (values, updated_at, seeded_at):
            if filter_key in mapping:
                mapping.pop(filter_key, None)
                changed = True
    return changed


def _fabric_solar_source_is_stale(controller: PyscriptController, now: datetime) -> bool:
    entity_id = DEFAULT_COMFORT_ADJUSTMENT_CONFIG.solar_radiation_entity_id
    timestamp = _normalize_comfort_adjustment_timestamp(controller.get_attr(entity_id, "last_updated"))
    if timestamp is None:
        timestamp = _normalize_comfort_adjustment_timestamp(controller.get_attr(entity_id, "last_changed"))
    if timestamp is None:
        return False
    age_seconds = (now - timestamp).total_seconds()
    return age_seconds > DEFAULT_COMFORT_ADJUSTMENT_CONFIG.input_stale_after_seconds


def _fabric_solar_is_below_horizon(controller: PyscriptController) -> bool:
    elevation = parse_float(
        controller.get_attr(DEFAULT_COMFORT_ADJUSTMENT_CONFIG.sun_entity_id, "elevation")
    )
    return elevation is not None and elevation <= 0.0


def _update_outdoor_temperature_filter(
    *,
    filter_key: str,
    outdoor_temperature: float,
    now: datetime,
    time_constant_seconds: int,
    warmup_fraction: float,
    previous_temperatures: dict[str, object],
    previous_updated_at: dict[str, object],
    filter_seeded_at: dict[str, object],
) -> tuple[float, bool]:
    """Update one independent outdoor-temperature filter.

    This is deliberately a module-level function: PyScript does not resolve
    local variables captured by a nested helper function reliably.
    """
    previous_temperature = parse_float(previous_temperatures.get(filter_key))
    updated_at = _normalize_runtime_datetime(previous_updated_at.get(filter_key))
    elapsed_seconds = (now - updated_at).total_seconds() if updated_at is not None else None
    effective_temperature = filter_outdoor_temperature(
        outdoor_temperature,
        previous_temperature,
        elapsed_seconds,
        time_constant_seconds,
    )
    previous_temperatures[filter_key] = effective_temperature
    previous_updated_at[filter_key] = now
    seeded_at = _normalize_runtime_datetime(filter_seeded_at.get(filter_key))
    if seeded_at is None:
        seeded_at = now
        filter_seeded_at[filter_key] = now
    warmup_seconds = max(0.0, time_constant_seconds * max(0.0, warmup_fraction))
    seed_age_seconds = max(0.0, (now - seeded_at).total_seconds())
    return effective_temperature, seed_age_seconds < warmup_seconds


def _resolve_effective_outdoor_temperatures(
    outdoor_temperature: float | None,
    now: datetime,
) -> tuple[dict[str, float | None], dict[str, float | None], dict[str, bool]]:
    window_temperatures: dict[str, float | None] = {}
    wall_temperatures: dict[str, float | None] = {}
    filter_warming_up: dict[str, bool] = {}
    if outdoor_temperature is None:
        for zone in DEFAULT_COMFORT_ADJUSTMENT_CONFIG.zones:
            window_temperatures[zone.key] = None
            wall_temperatures[zone.key] = None
            filter_warming_up[zone.key] = False
        return window_temperatures, wall_temperatures, filter_warming_up

    previous_temperatures = _comfort_adjustment_runtime_mapping("comfort_adjustment_outdoor_filter_values")
    previous_updated_at = _comfort_adjustment_runtime_mapping("comfort_adjustment_outdoor_filter_updated_at")
    filter_seeded_at = _comfort_adjustment_runtime_mapping("comfort_adjustment_outdoor_filter_seeded_at")
    window_temperature, window_warming_up = _update_outdoor_temperature_filter(
        filter_key="windows",
        outdoor_temperature=outdoor_temperature,
        now=now,
        time_constant_seconds=DEFAULT_COMFORT_ADJUSTMENT_CONFIG.window_outdoor_filter_time_constant_seconds,
        warmup_fraction=DEFAULT_COMFORT_ADJUSTMENT_CONFIG.outdoor_filter_warmup_fraction,
        previous_temperatures=previous_temperatures,
        previous_updated_at=previous_updated_at,
        filter_seeded_at=filter_seeded_at,
    )
    resolved_wall_filters: dict[str, tuple[float, bool]] = {}
    for zone in DEFAULT_COMFORT_ADJUSTMENT_CONFIG.zones:
        wall_profile_key = None
        for room in zone.rooms:
            if room.envelope is not None:
                wall_profile_key = room.envelope.construction_profile
                break
        profile = DEFAULT_COMFORT_ADJUSTMENT_CONFIG.construction_profiles.get(str(wall_profile_key))
        if profile is not None:
            wall_time_constant_seconds = profile.wall_filter_time_constant_seconds
            wall_filter_key = f"walls_{profile.key}"
        elif zone.upstairs:
            wall_time_constant_seconds = DEFAULT_COMFORT_ADJUSTMENT_CONFIG.upstairs_wall_outdoor_filter_time_constant_seconds
            wall_filter_key = "walls_upstairs"
        else:
            wall_time_constant_seconds = DEFAULT_COMFORT_ADJUSTMENT_CONFIG.downstairs_wall_outdoor_filter_time_constant_seconds
            wall_filter_key = "walls_downstairs"
        wall_filter_result = resolved_wall_filters.get(wall_filter_key)
        if wall_filter_result is None:
            wall_filter_result = _update_outdoor_temperature_filter(
                filter_key=wall_filter_key,
                outdoor_temperature=outdoor_temperature,
                now=now,
                time_constant_seconds=wall_time_constant_seconds,
                warmup_fraction=DEFAULT_COMFORT_ADJUSTMENT_CONFIG.outdoor_filter_warmup_fraction,
                previous_temperatures=previous_temperatures,
                previous_updated_at=previous_updated_at,
                filter_seeded_at=filter_seeded_at,
            )
            resolved_wall_filters[wall_filter_key] = wall_filter_result
        wall_temperature, wall_warming_up = wall_filter_result
        window_temperatures[zone.key] = window_temperature
        wall_temperatures[zone.key] = wall_temperature
        filter_warming_up[zone.key] = window_warming_up or wall_warming_up
    return window_temperatures, wall_temperatures, filter_warming_up


def _resolve_filtered_solar_irradiances(
    controller: PyscriptController,
    now: datetime,
) -> dict[str, float | None]:
    """Low-pass measured irradiance for each zone with a fabric calibration."""
    _restore_comfort_fabric_solar_filter_state()
    filtered_irradiances: dict[str, float | None] = {
        zone.key: None for zone in DEFAULT_COMFORT_ADJUSTMENT_CONFIG.zones
    }
    # A positive irradiance sample cannot produce retained solar warmth after
    # sunset.  The source timestamp also protects against a weather station
    # that froze while reporting a daytime value.
    if _fabric_solar_is_below_horizon(controller) or _fabric_solar_source_is_stale(controller, now):
        if _clear_comfort_fabric_solar_filter_memory():
            _persist_comfort_fabric_solar_filter_state()
        return filtered_irradiances

    irradiance = parse_float(
        controller.get_state(DEFAULT_COMFORT_ADJUSTMENT_CONFIG.solar_radiation_entity_id)
    )
    previous_values = _comfort_adjustment_runtime_mapping(
        "comfort_adjustment_fabric_solar_filter_values"
    )
    previous_updated_at = _comfort_adjustment_runtime_mapping(
        "comfort_adjustment_fabric_solar_filter_updated_at"
    )
    seeded_at = _comfort_adjustment_runtime_mapping(
        "comfort_adjustment_fabric_solar_filter_seeded_at"
    )
    has_live_filter_update = False
    for zone in DEFAULT_COMFORT_ADJUSTMENT_CONFIG.zones:
        fabric_solar = zone.fabric_solar
        if fabric_solar is None or irradiance is None or irradiance < 0.0:
            continue
        time_constant_seconds = int(
            getattr(fabric_solar, "filter_time_constant_seconds", 0)
        )
        filtered_irradiance, _warming_up = _update_outdoor_temperature_filter(
            filter_key=f"fabric_solar_{zone.key}",
            outdoor_temperature=irradiance,
            now=now,
            time_constant_seconds=time_constant_seconds,
            warmup_fraction=0.0,
            previous_temperatures=previous_values,
            previous_updated_at=previous_updated_at,
            filter_seeded_at=seeded_at,
        )
        filtered_irradiances[zone.key] = filtered_irradiance
        has_live_filter_update = True
    if has_live_filter_update:
        _persist_comfort_fabric_solar_filter_state()
    return filtered_irradiances


def _current_filtered_solar_irradiances(
    controller: PyscriptController,
    now: datetime,
) -> dict[str, float | None]:
    """Return usable current fabric warmth without mutating filter state.

    Idle-demand forecasting has no future irradiance series.  It therefore
    carries this live, already-filtered state through its short horizon rather
    than treating every forecast minute as zero solar warmth.
    """
    _restore_comfort_fabric_solar_filter_state()
    current: dict[str, float | None] = {
        zone.key: None for zone in DEFAULT_COMFORT_ADJUSTMENT_CONFIG.zones
    }
    irradiance = parse_float(
        controller.get_state(DEFAULT_COMFORT_ADJUSTMENT_CONFIG.solar_radiation_entity_id)
    )
    if (
        irradiance is None
        or irradiance < 0.0
        or _fabric_solar_is_below_horizon(controller)
        or _fabric_solar_source_is_stale(controller, now)
    ):
        return current

    values = _comfort_adjustment_runtime_mapping("comfort_adjustment_fabric_solar_filter_values")
    updated_at = _comfort_adjustment_runtime_mapping("comfort_adjustment_fabric_solar_filter_updated_at")
    for zone in DEFAULT_COMFORT_ADJUSTMENT_CONFIG.zones:
        if zone.fabric_solar is None:
            continue
        filter_key = f"fabric_solar_{zone.key}"
        filtered_irradiance = parse_float(values.get(filter_key))
        timestamp = _normalize_comfort_adjustment_timestamp(updated_at.get(filter_key))
        if filtered_irradiance is None or filtered_irradiance < 0.0 or timestamp is None:
            continue
        age_seconds = (now - timestamp).total_seconds()
        if age_seconds > DEFAULT_COMFORT_ADJUSTMENT_CONFIG.input_stale_after_seconds:
            continue
        current[zone.key] = filtered_irradiance
    return current


def _resolve_cover_position_overrides(controller: PyscriptController, now: datetime) -> dict[str, tuple[float, str]]:
    overrides: dict[str, tuple[float, str]] = {}
    cached_positions = _comfort_adjustment_runtime_mapping("comfort_adjustment_last_valid_cover_positions")
    cover_entity_ids: list[str] = []
    for zone in DEFAULT_COMFORT_ADJUSTMENT_CONFIG.zones:
        for room in zone.rooms:
            if room.envelope is None:
                continue
            for window in room.envelope.windows:
                if window.cover_entity_id and window.cover_entity_id not in cover_entity_ids:
                    cover_entity_ids.append(window.cover_entity_id)
    for cover_entity_id in cover_entity_ids:
        position = parse_float(controller.get_attr(cover_entity_id, "current_position"))
        if position is not None and 0.0 <= position <= 100.0:
            openness = position / 100.0
            cached_positions[cover_entity_id] = {"at": now, "openness": openness}
            overrides[cover_entity_id] = (openness, "live")
            continue
        cached_position = cached_positions.get(cover_entity_id)
        cached_at = _normalize_runtime_datetime(cached_position.get("at") if isinstance(cached_position, dict) else None)
        cached_openness = parse_float(cached_position.get("openness") if isinstance(cached_position, dict) else None)
        if (
            cached_at is not None
            and cached_openness is not None
            and 0.0 <= (now - cached_at).total_seconds() <= DEFAULT_COMFORT_ADJUSTMENT_CONFIG.last_valid_hold_seconds
        ):
            overrides[cover_entity_id] = (cached_openness, "held_last_valid")
    return overrides


def _comfort_adjustment_calibration_parameters() -> dict[str, object]:
    """Return the live-tunable model settings in a log-friendly form."""
    config = DEFAULT_COMFORT_ADJUSTMENT_CONFIG
    rooms: dict[str, tuple[dict[str, object], ...]] = {}
    fabric_solar: dict[str, dict[str, object]] = {}
    for zone in config.zones:
        if zone.fabric_solar is not None:
            fabric_solar[zone.key] = {
                "filter_seconds": zone.fabric_solar.filter_time_constant_seconds,
                "irradiance_threshold": zone.fabric_solar.irradiance_threshold,
                "score_coefficient": zone.fabric_solar.score_coefficient,
                "score_limit": zone.fabric_solar.score_limit,
            }
        room_parameters: list[dict[str, object]] = []
        for room in zone.rooms:
            envelope = room.envelope
            if envelope is None:
                continue
            room_parameters.append(
                {
                    "temperature_entity": room.primary_temperature_entity_id,
                    "comfort_weight": envelope.comfort_weight,
                    "wall_view_factor": envelope.opaque_wall_view_factor,
                    "construction_profile": envelope.construction_profile,
                    "windows": tuple(
                        [
                            {
                                "facade": window.facade,
                                "view_factor": window.view_factor,
                                "u_value": window.u_value,
                                "shgc": window.shgc,
                                "cover": window.cover_entity_id,
                                "direct_shade_factor": window.direct_shade_factor,
                                "diffuse_shade_factor": window.diffuse_shade_factor,
                            }
                            for window in envelope.windows
                        ]
                    ),
                }
            )
        rooms[zone.key] = tuple(room_parameters)
    return {
        "model": config.calculation_model,
        "operative_air_weight": config.operative_air_weight,
        "indoor_surface_resistance": config.indoor_surface_resistance,
        "maximum_total_k": config.maximum_total_k,
        "minimum_operative_denominator": config.minimum_operative_denominator,
        "last_valid_operating_mode_hold_seconds": config.last_valid_operating_mode_hold_seconds,
        "window_filter_seconds": config.window_outdoor_filter_time_constant_seconds,
        "outdoor_filter_warmup_fraction": config.outdoor_filter_warmup_fraction,
        "profiles": {
            key: {
                "wall_u_value": profile.wall_u_value,
                "wall_filter_seconds": profile.wall_filter_time_constant_seconds,
            }
            for key, profile in config.construction_profiles.items()
        },
        "shutter_resistance": config.shutter_resistance,
        "closed_direct_transmission": config.shutter_closed_direct_transmission,
        "closed_diffuse_transmission": config.shutter_closed_diffuse_transmission,
        "solar_direct_fraction": (
            config.solar_direct_fraction_minimum,
            config.solar_direct_fraction_maximum,
        ),
        "solar_mrt_coefficient": config.solar_mrt_coefficient,
        "solar_maximum_direct_normal_irradiance": config.solar_maximum_direct_normal_irradiance,
        "solar_adjustment_limit": config.solar_adjustment_limit,
        "fabric_solar": fabric_solar,
        "output_rate_limit": (
            config.output_rate_limit_celsius,
            config.output_rate_limit_seconds,
        ),
        "rooms": rooms,
    }


def _log_comfort_adjustment_calibration_parameters() -> None:
    parameters = _comfort_adjustment_calibration_parameters()
    previous_parameters = RUNTIME_STATE.get("comfort_adjustment_calibration_parameters")
    if previous_parameters == parameters:
        return
    RUNTIME_STATE["comfort_adjustment_calibration_parameters"] = parameters
    LOGGER.info("COMFORT ADJUSTMENT: calibration_parameters=%s", parameters)


def _reference_zone_targets_for_comfort_adjustments(
    controller: PyscriptController,
    now: datetime,
) -> dict[str, tuple[float, float]] | None:
    """Read dynamic, unadjusted zone targets without running control actions."""
    if not _control_is_enabled():
        return None

    assessment = _current_powerday_forecast_assessment()
    free_power_heat_soak_level = (
        assessment.level if assessment is not None else POWERDAY_HEATSOAK_FULL
    )
    try:
        snapshot = build_snapshot(
            controller,
            config=DEFAULT_SYSTEM_CONFIG,
            last_switch_changes=RUNTIME_STATE.get("last_zone_change"),
            pending_switch_states=RUNTIME_STATE.get("pending_zone_state"),
            heat_sink_available=bool(RUNTIME_STATE.get("powerday_heat_sink_active")),
            free_power_later_available=bool(RUNTIME_STATE.get("powerday_free_power_later_active")),
            free_power_heat_soak_level=free_power_heat_soak_level,
            poweroff_active=bool(RUNTIME_STATE.get("poweroff_active")),
            now=now,
        )
    except ValueError:
        # A control snapshot with no usable target is equivalent to disabled
        # control for this calculation, so retain the safe nominal fallback.
        return None
    return snapshot.base_zone_targets


def _resolve_published_comfort_adjustments(
    result,
    now: datetime,
    controller: PyscriptController,
    *,
    force_reseed: bool = False,
) -> dict[str, dict[str, object]]:
    """Apply output hysteresis and retain a short, explicit last-valid hold."""
    last_valid = _comfort_adjustment_runtime_mapping("comfort_adjustment_last_valid")
    last_published = _comfort_adjustment_runtime_mapping("comfort_adjustment_last_published_adjustments")
    publications: dict[str, dict[str, object]] = {}
    for zone in DEFAULT_COMFORT_ADJUSTMENT_CONFIG.zones:
        zone_key = zone.key
        previous_adjustment = parse_float(controller.get_state(zone.output_entity_id))
        if result.calculation_validity[zone_key]:
            raw_adjustment = result.raw_adjustments[zone_key]
            rounded_adjustment = result.adjustments[zone_key]
            filtered_adjustment = rounded_adjustment if force_reseed else apply_adjustment_hysteresis(
                raw_adjustment,
                previous_adjustment,
                DEFAULT_COMFORT_ADJUSTMENT_CONFIG.output_hysteresis,
            )
            previous_published = last_published.get(zone_key)
            previous_published_at = _normalize_runtime_datetime(
                previous_published.get("at") if isinstance(previous_published, dict) else None
            )
            previous_rate_limited_value = parse_float(
                previous_published.get("unrounded_value") if isinstance(previous_published, dict) else previous_adjustment
            )
            if previous_rate_limited_value is None:
                previous_rate_limited_value = parse_float(
                    previous_published.get("value") if isinstance(previous_published, dict) else previous_adjustment
                )
            if previous_rate_limited_value is None and result.filter_warming_up.get(zone_key, False):
                previous_rate_limited_value = 0.0
            elapsed_seconds = (
                (now - previous_published_at).total_seconds() if previous_published_at is not None else None
            )
            if previous_rate_limited_value is not None and not force_reseed:
                if elapsed_seconds is None:
                    allowed_change = (
                        DEFAULT_COMFORT_ADJUSTMENT_CONFIG.output_rate_limit_celsius
                        if result.filter_warming_up.get(zone_key, False)
                        else None
                    )
                else:
                    allowed_change = (
                        DEFAULT_COMFORT_ADJUSTMENT_CONFIG.output_rate_limit_celsius
                        * max(0.0, elapsed_seconds)
                        / DEFAULT_COMFORT_ADJUSTMENT_CONFIG.output_rate_limit_seconds
                    )
                if allowed_change is not None:
                    filtered_adjustment = _clamp_comfort_adjustment_change(
                        filtered_adjustment,
                        previous_rate_limited_value,
                        allowed_change,
                    )
            rate_limited_adjustment = raw_adjustment if force_reseed else filtered_adjustment
            filtered_adjustment = rounded_adjustment if force_reseed else round_comfort_adjustment(rate_limited_adjustment)
            last_published[zone_key] = {
                "at": now,
                "unrounded_value": rate_limited_adjustment,
                "value": filtered_adjustment,
            }
            last_valid[zone_key] = {
                "at": now,
                "raw_adjustment": raw_adjustment,
                "rounded_adjustment": rounded_adjustment,
                "filtered_adjustment": filtered_adjustment,
                "unrounded_rate_limited_adjustment": rate_limited_adjustment,
            }
            publications[zone_key] = {
                "raw_adjustment": raw_adjustment,
                "rounded_adjustment": rounded_adjustment,
                "filtered_adjustment": filtered_adjustment,
                "unrounded_rate_limited_adjustment": rate_limited_adjustment,
                "calculation_status": "reseeded" if force_reseed else "calculated",
                "last_valid_age_seconds": 0.0,
            }
            continue

        if force_reseed:
            # Never preserve a legacy-polarity helper value when source data
            # is unavailable during the one-time score migration.
            last_valid.pop(zone_key, None)
            last_published[zone_key] = {
                "at": now,
                "unrounded_value": 0.0,
                "value": 0.0,
            }
            publications[zone_key] = {
                "raw_adjustment": 0.0,
                "rounded_adjustment": 0.0,
                "filtered_adjustment": 0.0,
                "unrounded_rate_limited_adjustment": 0.0,
                "calculation_status": "reseeded_unavailable",
                "last_valid_age_seconds": None,
            }
            continue

        previous_calculation = last_valid.get(zone_key)
        held_at = _normalize_runtime_datetime(
            previous_calculation.get("at") if isinstance(previous_calculation, dict) else None
        )
        hold_age_seconds = (now - held_at).total_seconds() if held_at is not None else None
        if (
            isinstance(previous_calculation, dict)
            and hold_age_seconds is not None
            and 0.0 <= hold_age_seconds <= DEFAULT_COMFORT_ADJUSTMENT_CONFIG.last_valid_hold_seconds
        ):
            raw_adjustment = parse_float(previous_calculation.get("raw_adjustment"))
            rounded_adjustment = parse_float(previous_calculation.get("rounded_adjustment"))
            filtered_adjustment = parse_float(previous_calculation.get("filtered_adjustment"))
            unrounded_rate_limited_adjustment = parse_float(
                previous_calculation.get("unrounded_rate_limited_adjustment")
            )
            if raw_adjustment is not None and rounded_adjustment is not None and filtered_adjustment is not None:
                publications[zone_key] = {
                    "raw_adjustment": raw_adjustment,
                    "rounded_adjustment": rounded_adjustment,
                    "filtered_adjustment": filtered_adjustment,
                    "unrounded_rate_limited_adjustment": (
                        unrounded_rate_limited_adjustment
                        if unrounded_rate_limited_adjustment is not None
                        else filtered_adjustment
                    ),
                    "calculation_status": "held_last_valid",
                    "last_valid_age_seconds": hold_age_seconds,
                }
                continue

        publications[zone_key] = {
            "raw_adjustment": 0.0,
            "rounded_adjustment": 0.0,
            "filtered_adjustment": 0.0,
            "unrounded_rate_limited_adjustment": 0.0,
            "calculation_status": "unavailable",
            "last_valid_age_seconds": hold_age_seconds,
        }
    return publications


def _clamp_comfort_adjustment_change(target: float, previous: float, maximum_change: float) -> float:
    if maximum_change <= 0.0:
        return previous
    return max(previous - maximum_change, min(previous + maximum_change, target))


def _log_comfort_adjustment_diagnostics(result, publications: dict[str, dict[str, object]], now: datetime) -> None:
    """Emit physical comfort-model components when the diagnostic category is enabled."""
    for zone in DEFAULT_COMFORT_ADJUSTMENT_CONFIG.zones:
        zone_key = zone.key
        zone_diagnostics = dict(result.zone_diagnostics[zone_key])
        publication = publications[zone_key]
        calculation_status = str(publication["calculation_status"])
        source_status = str(zone_diagnostics.get("calculation_status", "unavailable"))
        LOGGER.info(
            "COMFORT DIAGNOSTICS: at=%s zone=%s model=%s status=%s source_status=%s envelope_score=%s window_solar_score=%s fabric_solar_score=%s comfort_score=%s rounded=%s rate_limited_unrounded=%s filtered=%s target=%s filtered_irradiance=%s window_outdoor=%s wall_outdoor=%s filter_warming_up=%s mode=%s mode_source=%s mode_indoor=%s mode_indoor_source=%s zone_temperature=%s zone_temperature_source=%s aggregation=%s window_k=%s wall_k=%s total_k=%s operative_denominator=%s window_envelope=%s wall_envelope=%s room_minimum=%s room_maximum=%s room_spread=%s room_values=%s input_issues=%s global_input_issues=%s last_valid_age_seconds=%s",
            now.isoformat(),
            zone_key,
            DEFAULT_COMFORT_ADJUSTMENT_CONFIG.calculation_model,
            calculation_status,
            source_status,
            result.envelope_adjustments[zone_key],
            result.solar_adjustments[zone_key],
            result.fabric_solar_scores[zone_key],
            publication["raw_adjustment"],
            publication["rounded_adjustment"],
            publication["unrounded_rate_limited_adjustment"],
            publication["filtered_adjustment"],
            result.reference_temperatures[zone_key],
            result.filtered_solar_irradiances[zone_key],
            result.effective_window_outdoor_temperatures[zone_key],
            result.effective_wall_outdoor_temperatures[zone_key],
            zone_diagnostics.get("filter_warming_up", result.filter_warming_up.get(zone_key, False)),
            result.operating_mode,
            result.operating_mode_source,
            result.operating_indoor_temperature,
            result.operating_indoor_temperature_source,
            result.zone_temperatures[zone_key],
            zone_diagnostics.get("zone_temperature_source"),
            zone_diagnostics.get("room_aggregation", "not_available"),
            zone_diagnostics.get("window_k"),
            zone_diagnostics.get("wall_k"),
            zone_diagnostics.get("total_k"),
            zone_diagnostics.get("operative_denominator"),
            zone_diagnostics.get("window_envelope_adjustment"),
            zone_diagnostics.get("wall_envelope_adjustment"),
            result.room_adjustment_minimums[zone_key],
            result.room_adjustment_maximums[zone_key],
            result.room_adjustment_spreads[zone_key],
            zone_diagnostics.get("room_values", ()),
            zone_diagnostics.get("input_issues", ()),
            result.global_input_issues,
            publication["last_valid_age_seconds"],
        )


def _publish_input_number_if_changed(
    controller: PyscriptController,
    entity_id: str,
    value: float,
) -> bool:
    current_value = parse_float(controller.get_state(entity_id))
    if current_value is not None and abs(current_value - value) <= 1e-9:
        return False
    controller.call_service("input_number", "set_value", entity_id=entity_id, value=value)
    return True


def run_comfort_adjustment_pass(*, reason: str, force_reseed: bool = False) -> None:
    """Calculate and publish independent per-zone comfort adjustments."""
    task.unique(COMFORT_ADJUSTMENT_PASS_TASK_NAME)

    controller = PyscriptController()
    now = _system_now()
    _log_comfort_adjustment_calibration_parameters()
    outdoor_temperature = resolve_outdoor_temperature(controller, DEFAULT_COMFORT_ADJUSTMENT_CONFIG, now=now)
    window_outdoor_temperatures, wall_outdoor_temperatures, filter_warming_up = _resolve_effective_outdoor_temperatures(
        outdoor_temperature,
        now,
    )
    filtered_solar_irradiances = _resolve_filtered_solar_irradiances(controller, now)
    reference_zone_targets = _reference_zone_targets_for_comfort_adjustments(controller, now)
    cover_position_overrides = _resolve_cover_position_overrides(controller, now)
    result = calculate_comfort_adjustments(
        controller,
        config=DEFAULT_COMFORT_ADJUSTMENT_CONFIG,
        cover_facades=_resolve_cover_facades(),
        effective_outdoor_temperatures=window_outdoor_temperatures,
        effective_window_outdoor_temperatures=window_outdoor_temperatures,
        effective_wall_outdoor_temperatures=wall_outdoor_temperatures,
        filter_warming_up=filter_warming_up,
        cover_position_overrides=cover_position_overrides,
        filtered_solar_irradiances=filtered_solar_irradiances,
        reference_zone_targets=reference_zone_targets,
        last_valid_operating_mode=(
            RUNTIME_STATE["comfort_adjustment_last_valid_mode"].get("mode")
            if isinstance(RUNTIME_STATE.get("comfort_adjustment_last_valid_mode"), dict)
            else None
        ),
        last_valid_operating_mode_at=(
            RUNTIME_STATE["comfort_adjustment_last_valid_mode"].get("at")
            if isinstance(RUNTIME_STATE.get("comfort_adjustment_last_valid_mode"), dict)
            else None
        ),
        now=now,
    )
    if result.operating_mode is not None and result.operating_mode_source != "last_valid_mode":
        RUNTIME_STATE["comfort_adjustment_last_valid_mode"] = {"mode": result.operating_mode, "at": now}
    publications = _resolve_published_comfort_adjustments(
        result,
        now,
        controller,
        force_reseed=force_reseed,
    )
    published_adjustments: list[str] = []
    for zone in DEFAULT_COMFORT_ADJUSTMENT_CONFIG.zones:
        adjustment = float(publications[zone.key]["filtered_adjustment"])
        _publish_input_number_if_changed(controller, zone.output_entity_id, adjustment)
        published_adjustments.append(f"{zone.key}={float(adjustment):.1f}")

    _log_comfort_adjustment_diagnostics(result, publications, now)

    RUNTIME_STATE["comfort_adjustment_last_error"] = None
    LOGGER.info(
        "COMFORT ADJUSTMENT: trigger=%s model=%s mode=%s mode_source=%s outdoor=%s downstairs_window_outdoor=%s downstairs_wall_outdoor=%s filter_warming_up=%s solar_index=%.2f reference_targets=%s values=%s",
        reason,
        DEFAULT_COMFORT_ADJUSTMENT_CONFIG.calculation_model,
        result.operating_mode or "unavailable",
        result.operating_mode_source,
        f"{result.outdoor_temperature:.1f}" if result.outdoor_temperature is not None else "unavailable",
        (
            f"{result.effective_window_outdoor_temperatures['downstairs']:.1f}"
            if result.effective_window_outdoor_temperatures.get("downstairs") is not None
            else "unavailable"
        ),
        (
            f"{result.effective_wall_outdoor_temperatures['downstairs']:.1f}"
            if result.effective_wall_outdoor_temperatures.get("downstairs") is not None
            else "unavailable"
        ),
        result.filter_warming_up.get("downstairs", False),
        result.solar_index,
        ",".join(
            [
                f"{zone_key}={reference_temperature:.1f}"
                for zone_key, reference_temperature in result.reference_temperatures.items()
                if reference_temperature is not None
            ]
        )
        or "unavailable",
        ",".join(published_adjustments),
    )


def _run_comfort_adjustment_pass(*, reason: str) -> bool:
    force_reseed = _comfort_score_semantics_requires_migration()
    if force_reseed and RUNTIME_STATE.get("comfort_score_semantics_migration_in_progress"):
        return False
    try:
        if force_reseed:
            RUNTIME_STATE["comfort_score_semantics_migration_in_progress"] = True
        run_comfort_adjustment_pass(reason=reason, force_reseed=force_reseed)
        if force_reseed:
            _persist_comfort_score_semantics_version()
            LOGGER.info(
                "COMFORT ADJUSTMENT: reseeded helpers for comfort-score semantics version %s",
                COMFORT_SCORE_SEMANTICS_VERSION,
            )
        return True
    except Exception as exc:  # pragma: no cover - exercised in Home Assistant runtime
        RUNTIME_STATE["comfort_adjustment_last_error"] = str(exc)
        LOGGER.exception("COMFORT ADJUSTMENT: pass failed")
        return False
    finally:
        if force_reseed:
            RUNTIME_STATE["comfort_score_semantics_migration_in_progress"] = False


def _describe_open_zones(open_zones: tuple[str, ...]) -> str:
    if not open_zones:
        return "none"

    zone_labels: list[str] = []
    for zone_key in open_zones:
        zone_labels.append(DEFAULT_SYSTEM_CONFIG.zones[zone_key].label)
    return ", ".join(zone_labels)


def _reported_open_zones(snapshot) -> tuple[str, ...]:
    reported_open: list[str] = []
    for zone_key, zone in snapshot.zones.items():
        if zone.switch_is_on:
            reported_open.append(zone_key)
    return tuple(sorted(reported_open))


def _format_zone_temps(snapshot, plan) -> str:
    """Return a string avg/min/max for the primary zone relevant to the plan.

    Order: average/current, min, max. Use '-' where a value is not available.
    Primary zone preference: first requested_by_zones, then first open zone.
    """
    zone_key = None
    if plan.requested_by_zones:
        zone_key = plan.requested_by_zones[0]
    elif plan.open_zones:
        zone_key = plan.open_zones[0]

    if not zone_key:
        return "-/-/-"

    zone = snapshot.zones.get(zone_key)
    if zone is None:
        return "-/-/-"

    def fmt(value):
        return f"{value:.1f}" if isinstance(value, (int, float)) else "-"

    avg = fmt(zone.current_temp)
    mn = fmt(zone.min_temp) if getattr(zone, "min_temp", None) is not None else "-"
    mx = fmt(zone.max_temp) if getattr(zone, "max_temp", None) is not None else "-"
    return f"{avg}/{mn}/{mx}"


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _current_idle_demand_forecast() -> IdleDemandForecast | None:
    forecast = RUNTIME_STATE.get("idle_demand_forecast")
    return forecast if isinstance(forecast, IdleDemandForecast) else None


def _current_powerday_forecast_assessment() -> PowerDayForecastAssessment | None:
    assessment = RUNTIME_STATE.get("powerday_forecast_assessment")
    return assessment if isinstance(assessment, PowerDayForecastAssessment) else None


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
    idle_demand_forecast = _current_idle_demand_forecast()
    powerday_forecast = _current_powerday_forecast_assessment()
    state.set(  # type: ignore[name-defined]
        STATUS_ENTITY_ID,
        status,
        new_attributes={
            "app_name": APP_NAME,
            "control_interval_seconds": CONTROL_INTERVAL_SECONDS,
            "enabled": _control_is_enabled(),
            "last_trigger": RUNTIME_STATE["last_trigger"],
            "last_successful_control_pass": _isoformat(RUNTIME_STATE["last_successful_control_pass"]),
            "last_heatcool_transition": _isoformat(RUNTIME_STATE["last_heatcool_transition"]),
            "last_active_hvac_mode": RUNTIME_STATE["last_active_hvac_mode"],
            "immediate_shutdown_zone_close_not_before": _isoformat(
                RUNTIME_STATE.get("immediate_shutdown_zone_close_not_before")
            ),
            "last_heatcool_request_at": _isoformat(RUNTIME_STATE.get("last_heatcool_request_at")),
            "downstairs_startup_priority_started_at": _isoformat(
                RUNTIME_STATE.get("downstairs_startup_priority_started_at")
            ),
            "downstairs_startup_priority_active": RUNTIME_STATE.get("downstairs_startup_priority_active"),
            "downstairs_startup_priority_reason": RUNTIME_STATE.get("downstairs_startup_priority_reason"),
            "idle_started_at": _isoformat(RUNTIME_STATE["idle_started_at"]),
            "idle_heat_step": RUNTIME_STATE.get("idle_heat_step"),
            "idle_heat_step_changed_at": _isoformat(RUNTIME_STATE.get("idle_heat_step_changed_at")),
            "idle_heat_zone_key": RUNTIME_STATE.get("idle_heat_zone_key"),
            "idle_shutdown_at": _isoformat(RUNTIME_STATE.get("idle_shutdown_at")),
            "idle_shutdown_heat_step": RUNTIME_STATE.get("idle_shutdown_heat_step"),
            "idle_shutdown_zone_key": RUNTIME_STATE.get("idle_shutdown_zone_key"),
            "idle_demand_forecast_generated_at": _isoformat(
                idle_demand_forecast.generated_at if idle_demand_forecast is not None else None
            ),
            "idle_demand_forecast_horizon_seconds": (
                idle_demand_forecast.horizon_seconds if idle_demand_forecast is not None else None
            ),
            "idle_demand_forecast_earliest_demand_at": _isoformat(
                idle_demand_forecast.earliest_demand_at if idle_demand_forecast is not None else None
            ),
            "idle_demand_forecast_earliest_zone": (
                idle_demand_forecast.earliest_zone_key if idle_demand_forecast is not None else None
            ),
            "idle_demand_forecast_safe_to_turn_off": (
                idle_demand_forecast.safe_to_turn_off if idle_demand_forecast is not None else None
            ),
            "idle_demand_forecast_source": (
                idle_demand_forecast.source if idle_demand_forecast is not None else None
            ),
            "idle_demand_forecast_reason": (
                idle_demand_forecast.reason if idle_demand_forecast is not None else None
            ),
            "idle_demand_forecast_weather_fetched_at": _isoformat(
                _normalize_runtime_datetime(RUNTIME_STATE.get("idle_demand_forecast_weather_fetched_at"))
            ),
            "weather_forecast_last_success_at": _isoformat(
                _normalize_runtime_datetime(RUNTIME_STATE.get("weather_forecast_last_success_at"))
            ),
            "idle_demand_forecast_weather_error": RUNTIME_STATE.get("idle_demand_forecast_weather_error"),
            "powerday_forecast_level": powerday_forecast.level if powerday_forecast is not None else None,
            "powerday_forecast_daytime_peak_celsius": (
                powerday_forecast.daytime_peak_celsius if powerday_forecast is not None else None
            ),
            "powerday_forecast_evening_minimum_celsius": (
                powerday_forecast.evening_minimum_celsius if powerday_forecast is not None else None
            ),
            "powerday_forecast_evening_maximum_humidity": (
                powerday_forecast.evening_maximum_humidity if powerday_forecast is not None else None
            ),
            "powerday_forecast_source": powerday_forecast.source if powerday_forecast is not None else None,
            "powerday_forecast_reason": powerday_forecast.reason if powerday_forecast is not None else None,
            "powerday_indoor_humidity": RUNTIME_STATE.get("powerday_indoor_humidity"),
            "powerday_dry_eligible": RUNTIME_STATE.get("powerday_dry_eligible", False),
            "powerday_dry_humidity_eligible": RUNTIME_STATE.get(
                "powerday_dry_humidity_eligible",
                False,
            ),
            "powerday_dry_coldest_enabled_zone_celsius": RUNTIME_STATE.get(
                "powerday_dry_coldest_enabled_zone_celsius"
            ),
            "powerday_dry_heat_forecast_safe": RUNTIME_STATE.get("powerday_dry_heat_forecast_safe"),
            "powerday_dry_cool_forecast_safe": RUNTIME_STATE.get("powerday_dry_cool_forecast_safe"),
            "powerday_dry_active": RUNTIME_STATE.get("powerday_dry_active", False),
            "powerday_dry_started_at": _isoformat(RUNTIME_STATE.get("powerday_dry_started_at")),
            "powerday_dry_completed": RUNTIME_STATE.get("powerday_dry_completed", False),
            "powerday_dry_completed_at": _isoformat(RUNTIME_STATE.get("powerday_dry_completed_at")),
            "powerday_dry_elapsed_seconds": RUNTIME_STATE.get("powerday_dry_elapsed_seconds", 0.0),
            "powerday_dry_heat_transition_started_at": _isoformat(
                RUNTIME_STATE.get("powerday_dry_heat_transition_started_at")
            ),
            "powerday_dry_heat_transition_pending_off": RUNTIME_STATE.get(
                "powerday_dry_heat_transition_pending_off",
                False,
            ),
            "powerday_dry_reason": RUNTIME_STATE.get("powerday_dry_reason"),
            "last_fan_speed_decrease_at": _isoformat(RUNTIME_STATE.get("last_fan_speed_decrease_at")),
            "hvac_off_started_at": _isoformat(RUNTIME_STATE.get("hvac_off_started_at")),
            "hvac_start_fan_ramp_started_at": _isoformat(RUNTIME_STATE.get("hvac_start_fan_ramp_started_at")),
            "heat_demand_fan_boost_level": RUNTIME_STATE.get("heat_demand_fan_boost_level", 0),
            "last_heat_demand_fan_boost_at": _isoformat(RUNTIME_STATE.get("last_heat_demand_fan_boost_at")),
            "max_power_demand_5m_kw": RUNTIME_STATE.get("max_power_demand_5m_kw"),
            "heat_demand_fan_boost_reason": RUNTIME_STATE.get("heat_demand_fan_boost_reason"),
            "powerday_export_average": RUNTIME_STATE.get("powerday_export_average"),
            "powerday_battery_remaining": RUNTIME_STATE.get("powerday_battery_remaining"),
            "powerday_battery_free_power_boost_active": RUNTIME_STATE.get(
                "powerday_battery_free_power_boost_active"
            ),
            "powerday_battery_free_power_boost_reason": RUNTIME_STATE.get(
                "powerday_battery_free_power_boost_reason"
            ),
            "powerday_heat_sink_started_at": _isoformat(RUNTIME_STATE.get("powerday_heat_sink_started_at")),
            "powerday_heat_sink_hold_until": _isoformat(RUNTIME_STATE.get("powerday_heat_sink_hold_until")),
            "powerday_heat_sink_active": RUNTIME_STATE.get("powerday_heat_sink_active"),
            "powerday_heat_sink_reason": RUNTIME_STATE.get("powerday_heat_sink_reason"),
            "powerday_pv_power_average": RUNTIME_STATE.get("powerday_pv_power_average"),
            "powerday_free_power_later_started_at": _isoformat(
                RUNTIME_STATE.get("powerday_free_power_later_started_at")
            ),
            "powerday_free_power_later_active": RUNTIME_STATE.get("powerday_free_power_later_active"),
            "powerday_free_power_later_reason": RUNTIME_STATE.get("powerday_free_power_later_reason"),
            "powerday_downstairs_priority_started_at": _isoformat(
                RUNTIME_STATE.get("powerday_downstairs_priority_started_at")
            ),
            "powerday_downstairs_priority_active": RUNTIME_STATE.get("powerday_downstairs_priority_active"),
            "powerday_downstairs_priority_gap": RUNTIME_STATE.get("powerday_downstairs_priority_gap"),
            "powerday_downstairs_priority_upstairs_zones": RUNTIME_STATE.get(
                "powerday_downstairs_priority_upstairs_zones"
            ),
            "powerday_downstairs_priority_reason": RUNTIME_STATE.get("powerday_downstairs_priority_reason"),
            "poweroff_battery_remaining": RUNTIME_STATE.get("poweroff_battery_remaining"),
            "poweroff_pv_power": RUNTIME_STATE.get("poweroff_pv_power"),
            "poweroff_activation_started_at": _isoformat(RUNTIME_STATE.get("poweroff_activation_started_at")),
            "poweroff_activation_hold_until": _isoformat(RUNTIME_STATE.get("poweroff_activation_hold_until")),
            "poweroff_active": RUNTIME_STATE.get("poweroff_active"),
            "poweroff_reason": RUNTIME_STATE.get("poweroff_reason"),
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


def _update_idle_heat_runtime_state(plan, now: datetime) -> None:
    if not (plan.idle and plan.hvac_mode == HVAC_HEAT and plan.open_zones):
        RUNTIME_STATE["idle_heat_step"] = None
        RUNTIME_STATE["idle_heat_step_changed_at"] = None
        RUNTIME_STATE["idle_heat_zone_key"] = None
        return

    zone_key = plan.open_zones[0]
    zone_changed = RUNTIME_STATE["idle_heat_zone_key"] != zone_key
    RUNTIME_STATE["idle_heat_zone_key"] = zone_key
    RUNTIME_STATE["idle_heat_step"] = plan.idle_heat_step
    if zone_changed or plan.idle_heat_step_changed:
        RUNTIME_STATE["idle_heat_step_changed_at"] = now


def _clear_idle_shutdown_runtime_state() -> None:
    RUNTIME_STATE["idle_shutdown_at"] = None
    RUNTIME_STATE["idle_shutdown_heat_step"] = None
    RUNTIME_STATE["idle_shutdown_zone_key"] = None


def _update_idle_shutdown_runtime_state(plan, now: datetime, *, current_hvac_mode: str | None) -> None:
    if plan.idle_shutdown:
        remembered_step = (
            plan.idle_heat_step
            if plan.idle_heat_step in {-11, -7, -6, -5, -4, -3, -2, -1}
            else RUNTIME_STATE.get("idle_heat_step")
        )
        remembered_zone_key = plan.open_zones[0] if plan.open_zones else RUNTIME_STATE.get("idle_heat_zone_key")
        if remembered_step in {-11, -7, -6, -5, -4, -3, -2, -1} and remembered_zone_key:
            RUNTIME_STATE["idle_shutdown_at"] = now
            RUNTIME_STATE["idle_shutdown_heat_step"] = remembered_step
            RUNTIME_STATE["idle_shutdown_zone_key"] = remembered_zone_key
        else:
            _clear_idle_shutdown_runtime_state()
        return

    if not plan.turn_off:
        _clear_idle_shutdown_runtime_state()
        return

    if (current_hvac_mode or "").lower() != "off":
        _clear_idle_shutdown_runtime_state()


def _normalize_runtime_datetime(value: object | None) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _observe_hvac_off_period(current_hvac_mode: str | None, now: datetime) -> None:
    """Track continuous periods in which the heat pump reports itself off."""
    if (current_hvac_mode or "").lower() != "off":
        RUNTIME_STATE["hvac_off_started_at"] = None
        return

    previous_started_at = _normalize_runtime_datetime(RUNTIME_STATE.get("hvac_off_started_at"))
    normalized_now = _normalize_runtime_datetime(now)
    if normalized_now is None:
        return
    if previous_started_at is None or previous_started_at > normalized_now:
        RUNTIME_STATE["hvac_off_started_at"] = normalized_now


def _resolve_hvac_start_fan_ramp_started_at(plan, current_hvac_mode: str | None, now: datetime) -> datetime | None:
    """Start or maintain a fan ramp for a heat or cool cycle following a long off period."""
    current_mode = (current_hvac_mode or "").lower()
    normalized_now = _normalize_runtime_datetime(now)
    previous_ramp_started_at = _normalize_runtime_datetime(RUNTIME_STATE.get("hvac_start_fan_ramp_started_at"))

    if plan.turn_off:
        if current_mode != "off" and normalized_now is not None:
            RUNTIME_STATE["hvac_off_started_at"] = normalized_now
        RUNTIME_STATE["hvac_start_fan_ramp_started_at"] = None
        return None

    if plan.hvac_mode not in {HVAC_HEAT, HVAC_COOL} or plan.idle:
        RUNTIME_STATE["hvac_start_fan_ramp_started_at"] = None
        return None

    if previous_ramp_started_at is not None and normalized_now is not None:
        if 0 <= (normalized_now - previous_ramp_started_at).total_seconds() <= HVAC_START_FAN_RAMP_DURATION_SECONDS:
            return previous_ramp_started_at
        RUNTIME_STATE["hvac_start_fan_ramp_started_at"] = None

    hvac_off_started_at = _normalize_runtime_datetime(RUNTIME_STATE.get("hvac_off_started_at"))
    if (
        current_mode != "off"
        or hvac_off_started_at is None
        or normalized_now is None
        or (normalized_now - hvac_off_started_at).total_seconds() < HVAC_START_FAN_RAMP_MIN_OFF_SECONDS
    ):
        return None

    RUNTIME_STATE["hvac_start_fan_ramp_started_at"] = normalized_now
    return normalized_now


@pyscript_executor
def _read_heat_demand_fan_boost_state(file_path):
    """Read the persisted boost and its filesystem modification timestamp."""
    import os

    try:
        with open(file_path, encoding="utf-8") as state_file:
            raw_level = state_file.read().strip()
        modified_at = os.path.getmtime(file_path)
    except OSError as exc:
        return None, None, str(exc)

    try:
        return int(raw_level), modified_at, None
    except ValueError:
        return None, modified_at, f"invalid fan boost value {raw_level!r}"


@pyscript_executor
def _write_heat_demand_fan_boost_state(file_path, fan_boost_level):
    """Persist one fan-boost level, refreshing the file modification time."""
    try:
        with open(file_path, "w", encoding="utf-8") as state_file:
            state_file.write(f"{int(fan_boost_level)}\n")
    except OSError as exc:
        return str(exc)
    return None


def _restore_heat_demand_fan_boost_state(now: datetime) -> None:
    if RUNTIME_STATE.get("heat_demand_fan_boost_restore_checked"):
        return
    RUNTIME_STATE["heat_demand_fan_boost_restore_checked"] = True

    persisted_level, modified_timestamp, read_error = _read_heat_demand_fan_boost_state(
        HEAT_DEMAND_FAN_BOOST_STATE_FILE
    )
    if read_error is not None:
        if modified_timestamp is not None:
            LOGGER.warning("FAN BOOST: ignoring persisted state: %s", read_error)
        return
    if persisted_level is None or modified_timestamp is None:
        return

    modified_at = datetime.fromtimestamp(modified_timestamp, tz=timezone.utc)
    normalized_now = _normalize_runtime_datetime(now) or _system_now().astimezone(timezone.utc)
    state_age = normalized_now - modified_at
    if state_age < timedelta(0) or state_age >= timedelta(seconds=HEAT_DEMAND_FAN_BOOST_INTERVAL_SECONDS):
        LOGGER.info(
            "FAN BOOST: persisted state is stale (modified %s); starting from zero",
            modified_at.isoformat(),
        )
        return

    restored_level = max(0, min(HEAT_DEMAND_FAN_BOOST_MAX_LEVEL, persisted_level))
    RUNTIME_STATE["heat_demand_fan_boost_level"] = restored_level
    RUNTIME_STATE["last_heat_demand_fan_boost_at"] = modified_at
    RUNTIME_STATE["heat_demand_fan_boost_reason"] = (
        f"restored boost={restored_level} from {modified_at.isoformat()}"
    )
    LOGGER.info(
        "FAN BOOST: restored boost=%s from state modified %s",
        restored_level,
        modified_at.isoformat(),
    )


def _persist_heat_demand_fan_boost_state(fan_boost_level: int) -> None:
    write_error = _write_heat_demand_fan_boost_state(
        HEAT_DEMAND_FAN_BOOST_STATE_FILE,
        fan_boost_level,
    )
    if write_error is not None:
        LOGGER.warning("FAN BOOST: failed to persist state: %s", write_error)


@pyscript_executor
def _read_powerday_dry_cycle_state(file_path):
    import json

    try:
        with open(file_path, encoding="utf-8") as state_file:
            payload = json.load(state_file)
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError, TypeError) as exc:
        return None, str(exc)
    if not isinstance(payload, dict):
        return None, "persisted dry-cycle state is not an object"
    return payload, None


@pyscript_executor
def _write_powerday_dry_cycle_state(file_path, payload):
    import json
    import os

    temporary_file_path = f"{file_path}.tmp"
    try:
        with open(temporary_file_path, "w", encoding="utf-8") as state_file:
            json.dump(payload, state_file, separators=(",", ":"), sort_keys=True)
            state_file.write("\n")
        os.replace(temporary_file_path, file_path)
    except OSError as exc:
        return str(exc)
    return None


def _parse_persisted_datetime(value: object | None) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _normalize_runtime_datetime(parsed)


def _persist_powerday_dry_cycle_state() -> None:
    payload = {
        "free_power_period_active": bool(RUNTIME_STATE.get("powerday_free_power_period_active")),
        "dry_active": bool(RUNTIME_STATE.get("powerday_dry_active")),
        "dry_started_at": _isoformat(
            _normalize_runtime_datetime(RUNTIME_STATE.get("powerday_dry_started_at"))
        ),
        "dry_completed": bool(RUNTIME_STATE.get("powerday_dry_completed")),
        "dry_completed_at": _isoformat(
            _normalize_runtime_datetime(RUNTIME_STATE.get("powerday_dry_completed_at"))
        ),
        "heat_transition_started_at": _isoformat(
            _normalize_runtime_datetime(RUNTIME_STATE.get("powerday_dry_heat_transition_started_at"))
        ),
        "heat_transition_pending_off": bool(
            RUNTIME_STATE.get("powerday_dry_heat_transition_pending_off")
        ),
    }
    write_error = _write_powerday_dry_cycle_state(POWERDAY_DRY_CYCLE_STATE_FILE, payload)
    if write_error is not None:
        LOGGER.warning("POWERDAY: unable to persist dry-cycle state: %s", write_error)


def _restore_powerday_dry_cycle_state() -> None:
    if RUNTIME_STATE.get("powerday_dry_restore_checked"):
        return
    RUNTIME_STATE["powerday_dry_restore_checked"] = True
    payload, read_error = _read_powerday_dry_cycle_state(POWERDAY_DRY_CYCLE_STATE_FILE)
    if read_error is not None:
        LOGGER.warning("POWERDAY: unable to restore dry-cycle state: %s", read_error)
        return
    if payload is None:
        return
    RUNTIME_STATE["powerday_free_power_period_active"] = bool(payload.get("free_power_period_active"))
    RUNTIME_STATE["powerday_dry_active"] = bool(payload.get("dry_active"))
    RUNTIME_STATE["powerday_dry_started_at"] = _parse_persisted_datetime(payload.get("dry_started_at"))
    RUNTIME_STATE["powerday_dry_completed"] = bool(payload.get("dry_completed"))
    RUNTIME_STATE["powerday_dry_completed_at"] = _parse_persisted_datetime(
        payload.get("dry_completed_at")
    )
    RUNTIME_STATE["powerday_dry_heat_transition_started_at"] = _parse_persisted_datetime(
        payload.get("heat_transition_started_at")
    )
    RUNTIME_STATE["powerday_dry_heat_transition_pending_off"] = bool(
        payload.get("heat_transition_pending_off")
    )


def _sync_powerday_free_power_period(free_power_available: bool) -> None:
    _restore_powerday_dry_cycle_state()
    previous_active = bool(RUNTIME_STATE.get("powerday_free_power_period_active"))
    if free_power_available == previous_active:
        return
    RUNTIME_STATE["powerday_free_power_period_active"] = free_power_available
    RUNTIME_STATE["powerday_dry_active"] = False
    RUNTIME_STATE["powerday_dry_started_at"] = None
    RUNTIME_STATE["powerday_dry_completed"] = False
    RUNTIME_STATE["powerday_dry_completed_at"] = None
    RUNTIME_STATE["powerday_dry_elapsed_seconds"] = 0.0
    RUNTIME_STATE["powerday_dry_heat_transition_started_at"] = None
    RUNTIME_STATE["powerday_dry_heat_transition_pending_off"] = False
    RUNTIME_STATE["powerday_dry_reason"] = (
        "new free-power period" if free_power_available else "free-power period ended"
    )
    _persist_powerday_dry_cycle_state()


def _set_powerday_dry_state(
    *,
    active: bool,
    started_at: datetime | None,
    completed: bool,
    completed_at: datetime | None,
    reason: str,
) -> None:
    previous_active = bool(RUNTIME_STATE.get("powerday_dry_active"))
    previous_completed = bool(RUNTIME_STATE.get("powerday_dry_completed"))
    previous_started_at = _normalize_runtime_datetime(RUNTIME_STATE.get("powerday_dry_started_at"))
    previous_completed_at = _normalize_runtime_datetime(RUNTIME_STATE.get("powerday_dry_completed_at"))
    normalized_started_at = _normalize_runtime_datetime(started_at)
    normalized_completed_at = _normalize_runtime_datetime(completed_at) if completed else None
    RUNTIME_STATE["powerday_dry_active"] = active
    RUNTIME_STATE["powerday_dry_started_at"] = normalized_started_at
    RUNTIME_STATE["powerday_dry_completed"] = completed
    RUNTIME_STATE["powerday_dry_completed_at"] = normalized_completed_at
    RUNTIME_STATE["powerday_dry_reason"] = reason
    if active != previous_active or completed != previous_completed:
        LOGGER.info(
            "POWERDAY: dry_active=%s completed=%s started_at=%s reason=%s",
            active,
            completed,
            _isoformat(normalized_started_at),
            reason,
        )
    if (
        active != previous_active
        or completed != previous_completed
        or normalized_started_at != previous_started_at
        or normalized_completed_at != previous_completed_at
    ):
        _persist_powerday_dry_cycle_state()


def _set_powerday_dry_heat_transition_started_at(
    started_at: datetime | None,
    *,
    pending_off: bool = False,
) -> None:
    previous = _normalize_runtime_datetime(RUNTIME_STATE.get("powerday_dry_heat_transition_started_at"))
    previous_pending_off = bool(RUNTIME_STATE.get("powerday_dry_heat_transition_pending_off"))
    normalized_started_at = _normalize_runtime_datetime(started_at)
    RUNTIME_STATE["powerday_dry_heat_transition_started_at"] = normalized_started_at
    RUNTIME_STATE["powerday_dry_heat_transition_pending_off"] = pending_off
    if previous != normalized_started_at or previous_pending_off != pending_off:
        _persist_powerday_dry_cycle_state()


def _forecast_safe_for_operation(
    snapshot,
    operation_mode: str,
    *,
    now: datetime,
    weather_points: tuple[WeatherForecastPoint, ...],
    current_outdoor_temperature: float | None,
) -> bool:
    forecast = forecast_idle_demand(
        snapshot,
        operation_mode=operation_mode,
        now=now,
        weather_points=weather_points,
        current_outdoor_temperature=current_outdoor_temperature,
        horizon_seconds=IDLE_DEMAND_FORECAST_HORIZON_SECONDS,
        step_seconds=IDLE_DEMAND_FORECAST_STEP_SECONDS,
    )
    return forecast.safe_to_turn_off


def _resolve_powerday_dry_request(
    controller: PyscriptController,
    snapshot,
    demand,
    assessment: PowerDayForecastAssessment,
    *,
    weather_points: tuple[WeatherForecastPoint, ...],
    supported_hvac_modes,
    current_hvac_mode: str | None,
    now: datetime,
) -> tuple[bool, bool, DehumidificationDecision]:
    indoor_humidity = parse_float(controller.get_state(POWERDAY_INDOOR_HUMIDITY_SENSOR))
    RUNTIME_STATE["powerday_indoor_humidity"] = indoor_humidity
    current_outdoor_temperature = resolve_outdoor_temperature(
        controller,
        DEFAULT_COMFORT_ADJUSTMENT_CONFIG,
        now=now,
    )
    heat_forecast_safe = _forecast_safe_for_operation(
        snapshot,
        HVAC_HEAT,
        now=now,
        weather_points=weather_points,
        current_outdoor_temperature=current_outdoor_temperature,
    )
    cool_forecast_safe = _forecast_safe_for_operation(
        snapshot,
        HVAC_COOL,
        now=now,
        weather_points=weather_points,
        current_outdoor_temperature=current_outdoor_temperature,
    )
    RUNTIME_STATE["powerday_dry_heat_forecast_safe"] = heat_forecast_safe
    RUNTIME_STATE["powerday_dry_cool_forecast_safe"] = cool_forecast_safe
    has_thermal_demand = bool(
        demand.heat_requested
        or demand.maintain_heat_mode
        or demand.cool_requested
        or demand.maintain_cool_mode
        or demand.fan_only_requested
    )
    if snapshot.selected_hvac_mode == CONTROL_HVAC_MODE_HEAT:
        has_thermal_demand = has_thermal_demand or bool(
            snapshot.heat_calling_zones or snapshot.continue_heating_zones
        )
    elif snapshot.selected_hvac_mode == CONTROL_HVAC_MODE_COOL:
        has_thermal_demand = has_thermal_demand or bool(
            snapshot.cool_calling_zones or snapshot.continue_cooling_zones
        )
    elif snapshot.selected_hvac_mode == CONTROL_HVAC_MODE_HEATCOOL:
        has_thermal_demand = has_thermal_demand or bool(
            snapshot.heat_calling_zones
            or snapshot.continue_heating_zones
            or snapshot.cool_calling_zones
            or snapshot.continue_cooling_zones
        )
    active = bool(RUNTIME_STATE.get("powerday_dry_active"))
    started_at = _normalize_runtime_datetime(RUNTIME_STATE.get("powerday_dry_started_at"))
    completed = bool(RUNTIME_STATE.get("powerday_dry_completed"))
    completed_at = _normalize_runtime_datetime(RUNTIME_STATE.get("powerday_dry_completed_at"))
    normalized_now = _normalize_runtime_datetime(now) or now
    current_mode = (current_hvac_mode or "").lower()
    if active and started_at is not None:
        elapsed_seconds = max(0.0, (normalized_now - started_at).total_seconds())
    elif completed and started_at is not None and completed_at is not None:
        elapsed_seconds = max(0.0, (completed_at - started_at).total_seconds())
    else:
        elapsed_seconds = 0.0
    RUNTIME_STATE["powerday_dry_elapsed_seconds"] = elapsed_seconds

    decision = resolve_powerday_dehumidification(
        snapshot,
        assessment,
        indoor_humidity=indoor_humidity,
        supported_hvac_modes=supported_hvac_modes,
        has_thermal_demand=has_thermal_demand,
        heat_forecast_safe=heat_forecast_safe,
        cool_forecast_safe=cool_forecast_safe,
        cycle_completed=completed,
        currently_active=active,
    )
    RUNTIME_STATE["powerday_dry_eligible"] = decision.eligible
    RUNTIME_STATE["powerday_dry_humidity_eligible"] = decision.humidity_eligible
    RUNTIME_STATE["powerday_dry_coldest_enabled_zone_celsius"] = (
        decision.coldest_enabled_zone_celsius
    )

    if active and started_at is None:
        decision = DehumidificationDecision(
            eligible=False,
            humidity_eligible=decision.humidity_eligible,
            coldest_enabled_zone_celsius=decision.coldest_enabled_zone_celsius,
            reason="persisted dry cycle has no valid start time",
        )
        RUNTIME_STATE["powerday_dry_eligible"] = False
    elif active and current_mode == HVAC_HEAT:
        decision = DehumidificationDecision(
            eligible=False,
            humidity_eligible=decision.humidity_eligible,
            coldest_enabled_zone_celsius=decision.coldest_enabled_zone_celsius,
            reason="heatpump entered heat while automatic dry was active",
        )
        RUNTIME_STATE["powerday_dry_eligible"] = False
    elif active and elapsed_seconds >= POWERDAY_DRY_MAX_SECONDS:
        decision = DehumidificationDecision(
            eligible=False,
            humidity_eligible=decision.humidity_eligible,
            coldest_enabled_zone_celsius=decision.coldest_enabled_zone_celsius,
            reason=f"maximum dry runtime of {POWERDAY_DRY_MAX_SECONDS // 60} minutes elapsed",
        )
        RUNTIME_STATE["powerday_dry_eligible"] = False

    if active:
        if decision.eligible:
            RUNTIME_STATE["powerday_dry_reason"] = decision.reason
            return True, False, decision
        _set_powerday_dry_state(
            active=False,
            started_at=started_at,
            completed=True,
            completed_at=normalized_now,
            reason=decision.reason,
        )
        _set_powerday_dry_heat_transition_started_at(None, pending_off=True)
        return False, False, decision

    if not decision.eligible:
        RUNTIME_STATE["powerday_dry_reason"] = decision.reason
        if not completed and (current_hvac_mode or "").lower() != HVAC_DRY:
            _set_powerday_dry_heat_transition_started_at(None)
        return False, False, decision

    transition_started_at = _normalize_runtime_datetime(
        RUNTIME_STATE.get("powerday_dry_heat_transition_started_at")
    )
    transition_pending_off = bool(RUNTIME_STATE.get("powerday_dry_heat_transition_pending_off"))
    if current_mode == HVAC_HEAT:
        if transition_started_at is not None or not transition_pending_off:
            _set_powerday_dry_heat_transition_started_at(None, pending_off=True)
        RUNTIME_STATE["powerday_dry_reason"] = "waiting for heat-to-dry reversing hold"
        return False, True, decision
    if (
        current_mode == HVAC_OFF
        and transition_started_at is None
        and not transition_pending_off
        and str(RUNTIME_STATE.get("last_active_hvac_mode") or "").lower() == HVAC_HEAT
    ):
        heat_off_started_at = _normalize_runtime_datetime(RUNTIME_STATE.get("hvac_off_started_at"))
        transition_started_at = heat_off_started_at or normalized_now
        transition_age = (normalized_now - transition_started_at).total_seconds()
        if transition_age < POWERDAY_DRY_HEAT_TRANSITION_SECONDS:
            _set_powerday_dry_heat_transition_started_at(transition_started_at)
            RUNTIME_STATE["powerday_dry_reason"] = "waiting for heat-to-dry reversing hold"
            return False, True, decision
    if transition_pending_off:
        if current_mode != HVAC_OFF:
            RUNTIME_STATE["powerday_dry_reason"] = "waiting for heatpump to report off before reversing hold"
            return False, True, decision
        transition_started_at = normalized_now
        _set_powerday_dry_heat_transition_started_at(transition_started_at)
        RUNTIME_STATE["powerday_dry_reason"] = "waiting for heat-to-dry reversing hold"
        return False, True, decision
    if transition_started_at is not None:
        if current_mode != HVAC_OFF:
            RUNTIME_STATE["powerday_dry_reason"] = "waiting for heatpump to remain off during reversing hold"
            return False, True, decision
        transition_age = (normalized_now - transition_started_at).total_seconds()
        if transition_age < POWERDAY_DRY_HEAT_TRANSITION_SECONDS:
            RUNTIME_STATE["powerday_dry_reason"] = "waiting for heat-to-dry reversing hold"
            return False, True, decision

    _set_powerday_dry_heat_transition_started_at(None)
    _set_powerday_dry_state(
        active=True,
        started_at=normalized_now,
        completed=False,
        completed_at=None,
        reason=decision.reason,
    )
    RUNTIME_STATE["powerday_dry_elapsed_seconds"] = 0.0
    return True, False, decision


def _apply_dry_to_heat_transition_hold(
    plan: DispatchPlan,
    *,
    current_hvac_mode: str | None,
    now: datetime,
) -> DispatchPlan:
    if plan.hvac_mode == HVAC_COOL:
        _set_powerday_dry_heat_transition_started_at(None)
        return plan

    normalized_now = _normalize_runtime_datetime(now) or now
    current_mode = (current_hvac_mode or "").lower()
    transition_started_at = _normalize_runtime_datetime(
        RUNTIME_STATE.get("powerday_dry_heat_transition_started_at")
    )
    transition_pending_off = bool(RUNTIME_STATE.get("powerday_dry_heat_transition_pending_off"))
    if current_mode == HVAC_DRY and plan.hvac_mode != HVAC_DRY:
        transition_started_at = None
        transition_pending_off = True
        _set_powerday_dry_heat_transition_started_at(None, pending_off=True)
    elif transition_pending_off and current_mode == HVAC_OFF:
        transition_started_at = normalized_now
        transition_pending_off = False
        _set_powerday_dry_heat_transition_started_at(transition_started_at)

    if plan.hvac_mode != HVAC_HEAT:
        return plan
    if transition_pending_off:
        return DispatchPlan(
            turn_off=True,
            open_zones=plan.open_zones,
            reason="waiting for heatpump to report off before dry-to-heat reversing hold",
        )
    if transition_started_at is None:
        return plan
    elapsed_seconds = (normalized_now - transition_started_at).total_seconds()
    if current_mode == HVAC_HEAT or elapsed_seconds >= POWERDAY_DRY_HEAT_TRANSITION_SECONDS:
        _set_powerday_dry_heat_transition_started_at(None)
        return plan
    return DispatchPlan(
        turn_off=True,
        open_zones=plan.open_zones,
        reason="waiting for dry-to-heat reversing hold",
    )


@pyscript_executor
def _read_comfort_score_semantics_version(file_path):
    """Read the helper-value convention used by the previous app version."""
    try:
        with open(file_path, encoding="utf-8") as state_file:
            raw_version = state_file.read().strip()
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, str(exc)
    try:
        return int(raw_version), None
    except ValueError:
        return None, f"invalid semantics version {raw_version!r}"


@pyscript_executor
def _write_comfort_score_semantics_version(file_path, version):
    """Persist the convention only after all helpers have been reseeded."""
    try:
        with open(file_path, "w", encoding="utf-8") as state_file:
            state_file.write(f"{int(version)}\n")
    except OSError as exc:
        return str(exc)
    return None


def _comfort_score_semantics_requires_migration() -> bool:
    """Return whether existing helper values may use an old score polarity."""
    if not RUNTIME_STATE.get("comfort_score_semantics_restore_checked"):
        RUNTIME_STATE["comfort_score_semantics_restore_checked"] = True
        persisted_version, read_error = _read_comfort_score_semantics_version(
            COMFORT_SCORE_SEMANTICS_STATE_FILE
        )
        if read_error is not None:
            LOGGER.warning("COMFORT ADJUSTMENT: unable to read score semantics state: %s", read_error)
        RUNTIME_STATE["comfort_score_semantics_version"] = persisted_version
    return RUNTIME_STATE.get("comfort_score_semantics_version") != COMFORT_SCORE_SEMANTICS_VERSION


def _persist_comfort_score_semantics_version() -> None:
    """Mark the helpers safe only after a forced score reseed completed."""
    write_error = _write_comfort_score_semantics_version(
        COMFORT_SCORE_SEMANTICS_STATE_FILE,
        COMFORT_SCORE_SEMANTICS_VERSION,
    )
    if write_error is not None:
        LOGGER.warning("COMFORT ADJUSTMENT: unable to persist score semantics state: %s", write_error)
    # Keep this app lifetime safe even if the disk state cannot be updated; on
    # the next reload it will conservatively reseed again.
    RUNTIME_STATE["comfort_score_semantics_version"] = COMFORT_SCORE_SEMANTICS_VERSION


def _is_free_power_price_state(value: object | None) -> bool:
    value_text = str(value).strip()
    if value_text == "0":
        return True

    parsed_value = parse_float(value)
    return parsed_value is not None and parsed_value == 0.0


def _runtime_numeric_samples(sample_key: str) -> list[tuple[datetime, float]]:
    samples = RUNTIME_STATE.setdefault(sample_key, [])
    if not isinstance(samples, list):
        samples = []
        RUNTIME_STATE[sample_key] = samples
    return samples


def _normalized_runtime_numeric_samples(sample_key: str, now: datetime) -> list[tuple[datetime, float]]:
    normalized_samples: list[tuple[datetime, float]] = []
    for sample in _runtime_numeric_samples(sample_key):
        if not isinstance(sample, (list, tuple)) or len(sample) != 2:
            continue
        sample_time = _normalize_runtime_datetime(sample[0])
        sample_value = parse_float(sample[1])
        if sample_time is None or sample_value is None or sample_time > now:
            continue
        normalized_samples.append((sample_time, sample_value))

    normalized_samples.sort(key=lambda sample: sample[0])
    return normalized_samples


def _prune_runtime_numeric_samples(sample_key: str, now: datetime, window_seconds: int) -> list[tuple[datetime, float]]:
    window_start = now - timedelta(seconds=window_seconds)
    previous_sample: tuple[datetime, float] | None = None
    kept_samples: list[tuple[datetime, float]] = []

    for sample in _normalized_runtime_numeric_samples(sample_key, now):
        if sample[0] <= window_start:
            previous_sample = sample
        else:
            kept_samples.append(sample)

    if previous_sample is not None:
        kept_samples.insert(0, previous_sample)

    RUNTIME_STATE[sample_key] = kept_samples
    return kept_samples


def _record_runtime_numeric_sample(
    sample_key: str,
    now: datetime,
    value: float | None,
    window_seconds: int,
) -> list[tuple[datetime, float]]:
    samples = _normalized_runtime_numeric_samples(sample_key, now)
    if value is not None:
        if samples and now < samples[-1][0]:
            samples = []
        if samples and now == samples[-1][0]:
            samples[-1] = (now, value)
        else:
            samples.append((now, value))
        RUNTIME_STATE[sample_key] = samples

    return _prune_runtime_numeric_samples(sample_key, now, window_seconds)


def _time_weighted_runtime_average(
    samples: list[tuple[datetime, float]],
    now: datetime,
    window_seconds: int,
) -> float | None:
    if window_seconds <= 0:
        return None

    window_start = now - timedelta(seconds=window_seconds)
    anchor_sample: tuple[datetime, float] | None = None
    window_samples: list[tuple[datetime, float]] = []
    for sample_time, sample_value in samples:
        if sample_time <= window_start:
            anchor_sample = (sample_time, sample_value)
        elif sample_time <= now:
            window_samples.append((sample_time, sample_value))

    if anchor_sample is None:
        return None

    cursor = window_start
    current_value = anchor_sample[1]
    weighted_total = 0.0
    for sample_time, sample_value in window_samples:
        if sample_time > cursor:
            weighted_total += current_value * (sample_time - cursor).total_seconds()
            cursor = sample_time
        current_value = sample_value

    if now > cursor:
        weighted_total += current_value * (now - cursor).total_seconds()

    return weighted_total / window_seconds


def _record_powerday_export_power_sample(now: datetime, export_power: float | None) -> list[tuple[datetime, float]]:
    return _record_runtime_numeric_sample(
        "powerday_export_power_samples",
        now,
        export_power,
        POWERDAY_EXPORT_AVERAGE_WINDOW_SECONDS,
    )


def _time_weighted_powerday_export_average(samples: list[tuple[datetime, float]], now: datetime) -> float | None:
    return _time_weighted_runtime_average(samples, now, POWERDAY_EXPORT_AVERAGE_WINDOW_SECONDS)


def _set_powerday_battery_free_power_boost_runtime_state(*, active: bool, reason: str) -> bool:
    previous_active = bool(RUNTIME_STATE.get("powerday_battery_free_power_boost_active"))
    RUNTIME_STATE["powerday_battery_free_power_boost_active"] = active
    RUNTIME_STATE["powerday_battery_free_power_boost_reason"] = reason

    if active != previous_active:
        LOGGER.info(
            "POWERDAY: battery_free_power_boost_active=%s battery=%s reason=%s",
            active,
            RUNTIME_STATE.get("powerday_battery_remaining"),
            reason,
        )
    return active


def _update_powerday_battery_free_power_boost_runtime_state(controller: PyscriptController) -> bool:
    battery_remaining = parse_float(controller.get_state(GOODWE_BATTERY_REMAINING_SENSOR))
    RUNTIME_STATE["powerday_battery_remaining"] = battery_remaining

    selected_comfort_mode = str(controller.get_state(DEFAULT_SYSTEM_CONFIG.comfort_mode_entity) or "")
    if selected_comfort_mode != COMFORT_MODE_POWER_DAY:
        return _set_powerday_battery_free_power_boost_runtime_state(
            active=False,
            reason="comfort mode is not PowerDay",
        )
    if battery_remaining is None:
        return _set_powerday_battery_free_power_boost_runtime_state(
            active=False,
            reason="missing battery remaining",
        )

    if bool(RUNTIME_STATE.get("powerday_battery_free_power_boost_active")):
        if battery_remaining >= POWERDAY_BATTERY_FREE_POWER_BOOST_RELEASE_THRESHOLD:
            return _set_powerday_battery_free_power_boost_runtime_state(
                active=True,
                reason=(
                    f"battery {battery_remaining:.1f} >= "
                    f"{POWERDAY_BATTERY_FREE_POWER_BOOST_RELEASE_THRESHOLD:.1f}; holding initial boost"
                ),
            )
        return _set_powerday_battery_free_power_boost_runtime_state(
            active=False,
            reason=(
                f"battery {battery_remaining:.1f} < "
                f"{POWERDAY_BATTERY_FREE_POWER_BOOST_RELEASE_THRESHOLD:.1f}"
            ),
        )

    if battery_remaining >= POWERDAY_BATTERY_THRESHOLD:
        return _set_powerday_battery_free_power_boost_runtime_state(
            active=True,
            reason=f"battery {battery_remaining:.1f} >= {POWERDAY_BATTERY_THRESHOLD:.1f}; starting initial boost",
        )
    return _set_powerday_battery_free_power_boost_runtime_state(
        active=False,
        reason=f"battery {battery_remaining:.1f} < {POWERDAY_BATTERY_THRESHOLD:.1f}",
    )


def _powerday_eligibility_reason(
    *,
    battery_remaining: float | None,
    export_power: float | None,
    export_average: float | None,
) -> str:
    if battery_remaining is None:
        return "missing battery remaining"
    if battery_remaining <= POWERDAY_BATTERY_THRESHOLD:
        return f"battery {battery_remaining:.1f} <= {POWERDAY_BATTERY_THRESHOLD:.1f}"
    if export_power is None:
        return "missing export power"
    if export_average is None:
        return f"export average lacks {POWERDAY_EXPORT_AVERAGE_WINDOW_SECONDS // 60}-minute coverage"
    if export_average >= POWERDAY_EXPORT_POWER_THRESHOLD:
        return f"export average {export_average:.2f} >= {POWERDAY_EXPORT_POWER_THRESHOLD:.2f}"
    return f"battery {battery_remaining:.1f} and export average {export_average:.2f}"


def _set_powerday_heat_sink_runtime_state(
    *,
    active: bool,
    started_at: datetime | None,
    hold_until: datetime | None,
    reason: str,
) -> bool:
    previous_active = bool(RUNTIME_STATE.get("powerday_heat_sink_active"))
    RUNTIME_STATE["powerday_heat_sink_started_at"] = started_at
    RUNTIME_STATE["powerday_heat_sink_hold_until"] = hold_until
    RUNTIME_STATE["powerday_heat_sink_active"] = active
    RUNTIME_STATE["powerday_heat_sink_reason"] = reason

    if active != previous_active:
        LOGGER.info(
            "POWERDAY: export_heat_sink_active=%s battery=%s export_average=%s hold_until=%s reason=%s",
            active,
            RUNTIME_STATE.get("powerday_battery_remaining"),
            RUNTIME_STATE.get("powerday_export_average"),
            _isoformat(hold_until),
            reason,
        )

    return active


def _update_powerday_heat_sink_runtime_state(controller: PyscriptController, now: datetime) -> bool:
    normalized_now = _normalize_runtime_datetime(now) or _system_now()
    export_power = parse_float(controller.get_state(EAGLE_200_POWER_DEMAND_SENSOR))
    battery_remaining = parse_float(controller.get_state(GOODWE_BATTERY_REMAINING_SENSOR))
    samples = _record_powerday_export_power_sample(normalized_now, export_power)
    export_average = _time_weighted_powerday_export_average(samples, normalized_now)

    RUNTIME_STATE["powerday_export_average"] = export_average
    RUNTIME_STATE["powerday_battery_remaining"] = battery_remaining

    selected_comfort_mode = str(controller.get_state(DEFAULT_SYSTEM_CONFIG.comfort_mode_entity) or "")
    if selected_comfort_mode != COMFORT_MODE_POWER_DAY:
        return _set_powerday_heat_sink_runtime_state(
            active=False,
            started_at=None,
            hold_until=None,
            reason="comfort mode is not PowerDay",
        )

    if _is_free_power_price_state(controller.get_state(GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR)):
        return _set_powerday_heat_sink_runtime_state(
            active=False,
            started_at=None,
            hold_until=None,
            reason="free power is already available",
        )

    eligibility_reason = _powerday_eligibility_reason(
        battery_remaining=battery_remaining,
        export_power=export_power,
        export_average=export_average,
    )
    eligible = (
        battery_remaining is not None
        and battery_remaining > POWERDAY_BATTERY_THRESHOLD
        and export_power is not None
        and export_average is not None
        and export_average < POWERDAY_EXPORT_POWER_THRESHOLD
    )

    started_at = _normalize_runtime_datetime(RUNTIME_STATE.get("powerday_heat_sink_started_at"))
    hold_until = _normalize_runtime_datetime(RUNTIME_STATE.get("powerday_heat_sink_hold_until"))
    if eligible:
        if started_at is None:
            started_at = normalized_now
        hold_until = started_at + timedelta(seconds=POWERDAY_HEAT_SINK_MIN_SECONDS)
        return _set_powerday_heat_sink_runtime_state(
            active=True,
            started_at=started_at,
            hold_until=hold_until,
            reason=eligibility_reason,
        )

    if started_at is not None:
        hold_until = hold_until or started_at + timedelta(seconds=POWERDAY_HEAT_SINK_MIN_SECONDS)
        if normalized_now < hold_until:
            return _set_powerday_heat_sink_runtime_state(
                active=True,
                started_at=started_at,
                hold_until=hold_until,
                reason=f"minimum hold until {hold_until.isoformat()}; {eligibility_reason}",
            )

    return _set_powerday_heat_sink_runtime_state(
        active=False,
        started_at=None,
        hold_until=None,
        reason=eligibility_reason,
    )


def _record_powerday_pv_power_sample(now: datetime, pv_power: float | None) -> list[tuple[datetime, float]]:
    return _record_runtime_numeric_sample(
        "powerday_pv_power_samples",
        now,
        pv_power,
        POWERDAY_FREE_POWER_PV_AVERAGE_WINDOW_SECONDS,
    )


def _time_weighted_powerday_pv_power_average(samples: list[tuple[datetime, float]], now: datetime) -> float | None:
    return _time_weighted_runtime_average(samples, now, POWERDAY_FREE_POWER_PV_AVERAGE_WINDOW_SECONDS)


def _set_powerday_free_power_later_runtime_state(
    *,
    active: bool,
    started_at: datetime | None,
    reason: str,
) -> bool:
    previous_active = bool(RUNTIME_STATE.get("powerday_free_power_later_active"))
    RUNTIME_STATE["powerday_free_power_later_started_at"] = started_at
    RUNTIME_STATE["powerday_free_power_later_active"] = active
    RUNTIME_STATE["powerday_free_power_later_reason"] = reason

    if active != previous_active:
        LOGGER.info(
            "POWERDAY: free_power_later_active=%s pv_average=%s started_at=%s reason=%s",
            active,
            RUNTIME_STATE.get("powerday_pv_power_average"),
            _isoformat(started_at),
            reason,
        )

    return active


def _update_powerday_free_power_later_runtime_state(
    controller: PyscriptController,
    now: datetime,
    *,
    heat_soak_level: str = POWERDAY_HEATSOAK_FULL,
) -> bool:
    """Promote PowerDay to its later boost early when free-power PV is sustained."""
    normalized_now = _normalize_runtime_datetime(now) or _system_now()
    pv_power = parse_float(controller.get_state(GOODWE_PV_POWER_SENSOR))
    samples = _record_powerday_pv_power_sample(normalized_now, pv_power)
    pv_average = _time_weighted_powerday_pv_power_average(samples, normalized_now)
    RUNTIME_STATE["powerday_pv_power_average"] = pv_average

    selected_comfort_mode = str(controller.get_state(DEFAULT_SYSTEM_CONFIG.comfort_mode_entity) or "")
    if selected_comfort_mode != COMFORT_MODE_POWER_DAY:
        return _set_powerday_free_power_later_runtime_state(
            active=False,
            started_at=None,
            reason="comfort mode is not PowerDay",
        )

    if not _is_free_power_price_state(controller.get_state(GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR)):
        return _set_powerday_free_power_later_runtime_state(
            active=False,
            started_at=None,
            reason="free power is not available",
        )
    if heat_soak_level != POWERDAY_HEATSOAK_FULL:
        return _set_powerday_free_power_later_runtime_state(
            active=False,
            started_at=None,
            reason=f"free-power heatsoak level is {heat_soak_level}",
        )

    started_at = _normalize_runtime_datetime(RUNTIME_STATE.get("powerday_free_power_later_started_at"))
    if started_at is not None:
        return _set_powerday_free_power_later_runtime_state(
            active=True,
            started_at=started_at,
            reason="already started; holding until free power ends",
        )

    if normalized_now.time() < POWERDAY_FREE_POWER_START_TIME:
        return _set_powerday_free_power_later_runtime_state(
            active=False,
            started_at=None,
            reason=f"before free power start {POWERDAY_FREE_POWER_START_TIME.isoformat(timespec='minutes')}",
        )

    if pv_power is None:
        return _set_powerday_free_power_later_runtime_state(
            active=False,
            started_at=None,
            reason="missing PV power",
        )

    if pv_average is None:
        return _set_powerday_free_power_later_runtime_state(
            active=False,
            started_at=None,
            reason=f"PV average lacks {POWERDAY_FREE_POWER_PV_AVERAGE_WINDOW_SECONDS // 60}-minute coverage",
        )

    if pv_average <= POWERDAY_FREE_POWER_PV_POWER_THRESHOLD:
        return _set_powerday_free_power_later_runtime_state(
            active=False,
            started_at=None,
            reason=f"PV average {pv_average:.2f} <= {POWERDAY_FREE_POWER_PV_POWER_THRESHOLD:.2f}",
        )

    return _set_powerday_free_power_later_runtime_state(
        active=True,
        started_at=normalized_now,
        reason=f"PV average {pv_average:.2f} > {POWERDAY_FREE_POWER_PV_POWER_THRESHOLD:.2f}",
    )


def _has_active_heatcool_request(snapshot, operating_mode: str | None) -> bool:
    if operating_mode == HVAC_HEAT:
        return bool(snapshot.heat_calling_zones)
    if operating_mode == HVAC_COOL:
        return bool(snapshot.cool_calling_zones)
    return False


def _downstairs_startup_priority_entry_eligibility(
    snapshot,
    operating_mode: str | None,
    last_heatcool_request_at: datetime | None,
    now: datetime,
) -> tuple[bool, str]:
    if snapshot.poweroff_forced_off:
        return False, "PowerOff is waiting for its activation conditions"
    if not _has_active_heatcool_request(snapshot, operating_mode):
        return False, "there is no active heat or cool request"
    if last_heatcool_request_at is None:
        return False, "the heat/cool request gap has not been observed yet"
    if now - last_heatcool_request_at <= timedelta(seconds=DOWNSTAIRS_STARTUP_PRIORITY_INACTIVE_SECONDS):
        return False, (
            f"heat/cool request gap is not longer than {DOWNSTAIRS_STARTUP_PRIORITY_INACTIVE_SECONDS // 60} minutes"
        )

    for zone_key in DOWNSTAIRS_STARTUP_PRIORITY_UPSTAIRS_ZONE_KEYS:
        upstairs_zone = snapshot.zones.get(zone_key)
        if upstairs_zone is not None and upstairs_zone.switch_is_on:
            return False, f"{zone_key} is already open"

    downstairs_zone = snapshot.zones.get(DOWNSTAIRS_STARTUP_PRIORITY_ZONE_KEY)
    if downstairs_zone is None:
        return False, "downstairs zone is unavailable"
    if not downstairs_zone.is_enabled_by_mode:
        return False, "downstairs zone is disabled"
    if operating_mode == HVAC_HEAT and downstairs_zone.current_temp >= downstairs_zone.scheme.continue_until:
        return False, "downstairs is at or above its continue-until target"
    if operating_mode == HVAC_COOL and downstairs_zone.current_temp <= downstairs_zone.cool_scheme.continue_until:
        return False, "downstairs is at or below its continue-until target"
    return True, "an extended heat/cool request gap can start downstairs first"


def _set_downstairs_startup_priority_runtime_state(
    *,
    active: bool,
    started_at: datetime | None,
    reason: str,
) -> bool:
    previous_active = bool(RUNTIME_STATE.get("downstairs_startup_priority_active"))
    RUNTIME_STATE["downstairs_startup_priority_started_at"] = started_at
    RUNTIME_STATE["downstairs_startup_priority_active"] = active
    RUNTIME_STATE["downstairs_startup_priority_reason"] = reason

    if active != previous_active:
        LOGGER.info(
            "DISPATCH: downstairs_startup_priority_active=%s started_at=%s reason=%s",
            active,
            _isoformat(started_at),
            reason,
        )
    return active


def _update_downstairs_startup_priority_runtime_state(snapshot, operating_mode: str | None, now: datetime) -> bool:
    normalized_now = _normalize_runtime_datetime(now) or _system_now()
    active_request = _has_active_heatcool_request(snapshot, operating_mode)
    last_heatcool_request_at = _normalize_runtime_datetime(RUNTIME_STATE.get("last_heatcool_request_at"))
    previously_active = bool(RUNTIME_STATE.get("downstairs_startup_priority_active"))
    started_at = _normalize_runtime_datetime(RUNTIME_STATE.get("downstairs_startup_priority_started_at"))

    if previously_active and started_at is not None:
        downstairs_zone = snapshot.zones.get(DOWNSTAIRS_STARTUP_PRIORITY_ZONE_KEY)
        if snapshot.poweroff_forced_off or downstairs_zone is None or not downstairs_zone.is_enabled_by_mode:
            result = _set_downstairs_startup_priority_runtime_state(
                active=False,
                started_at=None,
                reason="downstairs startup priority is no longer safe to apply",
            )
        elif not active_request:
            result = _set_downstairs_startup_priority_runtime_state(
                active=False,
                started_at=None,
                reason="the active heat or cool request ended",
            )
        elif normalized_now < started_at + timedelta(seconds=DOWNSTAIRS_STARTUP_PRIORITY_MIN_SECONDS):
            result = _set_downstairs_startup_priority_runtime_state(
                active=True,
                started_at=started_at,
                reason=(
                    "holding downstairs before opening other zones until "
                    f"{(started_at + timedelta(seconds=DOWNSTAIRS_STARTUP_PRIORITY_MIN_SECONDS)).isoformat()}"
                ),
            )
        else:
            result = _set_downstairs_startup_priority_runtime_state(
                active=False,
                started_at=None,
                reason="minimum downstairs-only startup period elapsed",
            )
    else:
        eligible, reason = _downstairs_startup_priority_entry_eligibility(
            snapshot,
            operating_mode,
            last_heatcool_request_at,
            normalized_now,
        )
        result = _set_downstairs_startup_priority_runtime_state(
            active=eligible,
            started_at=normalized_now if eligible else None,
            reason="started: " + reason if eligible else reason,
        )

    if active_request or last_heatcool_request_at is None:
        RUNTIME_STATE["last_heatcool_request_at"] = normalized_now
    return result


def _powerday_downstairs_priority_base_eligibility(snapshot, operating_mode: str | None) -> tuple[bool, str]:
    if snapshot.comfort_mode != COMFORT_MODE_POWER_DAY:
        return False, "comfort mode is not PowerDay"
    if not snapshot.free_power_available:
        return False, "free power is not available"
    if snapshot.free_power_heat_soak_level != POWERDAY_HEATSOAK_FULL:
        return False, f"free-power heatsoak level is {snapshot.free_power_heat_soak_level}"
    if operating_mode != HVAC_HEAT:
        return False, "heating is not active"

    downstairs_zone = snapshot.zones.get(POWERDAY_DOWNSTAIRS_PRIORITY_ZONE_KEY)
    if downstairs_zone is None or not downstairs_zone.is_enabled_by_mode:
        return False, "downstairs zone is disabled"
    if downstairs_zone.current_temp >= downstairs_zone.scheme.continue_until:
        return False, "downstairs is at or above its continue-until target"
    return True, "PowerDay free-power heat soak is eligible"


def _powerday_downstairs_priority_entry_eligibility(snapshot, operating_mode: str | None) -> tuple[bool, float | None, tuple[str, ...], str]:
    base_eligible, base_reason = _powerday_downstairs_priority_base_eligibility(snapshot, operating_mode)
    if not base_eligible:
        return False, None, (), base_reason

    open_upstairs_zone_keys: list[str] = []
    coldest_upstairs_temp: float | None = None
    for zone_key in POWERDAY_DOWNSTAIRS_PRIORITY_UPSTAIRS_ZONE_KEYS:
        upstairs_zone = snapshot.zones.get(zone_key)
        if upstairs_zone is None or not upstairs_zone.switch_is_on:
            continue
        if not upstairs_zone.is_enabled_by_mode:
            return False, None, (), f"{zone_key} is disabled"
        if upstairs_zone.current_temp <= upstairs_zone.scheme.enable_outside:
            return (
                False,
                None,
                (),
                f"{zone_key} {upstairs_zone.current_temp:.1f}C <= enable threshold {upstairs_zone.scheme.enable_outside:.1f}C",
            )
        open_upstairs_zone_keys.append(zone_key)
        if coldest_upstairs_temp is None or upstairs_zone.current_temp < coldest_upstairs_temp:
            coldest_upstairs_temp = upstairs_zone.current_temp

    if not open_upstairs_zone_keys or coldest_upstairs_temp is None:
        return False, None, (), "no upstairs vents are open"

    downstairs_temp = snapshot.zones[POWERDAY_DOWNSTAIRS_PRIORITY_ZONE_KEY].current_temp
    temperature_gap = coldest_upstairs_temp - downstairs_temp
    if temperature_gap < POWERDAY_DOWNSTAIRS_PRIORITY_ENTER_GAP:
        return (
            False,
            temperature_gap,
            tuple(open_upstairs_zone_keys),
            f"upstairs/downstairs gap {temperature_gap:.1f}C < {POWERDAY_DOWNSTAIRS_PRIORITY_ENTER_GAP:.1f}C",
        )
    return (
        True,
        temperature_gap,
        tuple(open_upstairs_zone_keys),
        f"upstairs/downstairs gap {temperature_gap:.1f}C >= {POWERDAY_DOWNSTAIRS_PRIORITY_ENTER_GAP:.1f}C",
    )


def _set_powerday_downstairs_priority_runtime_state(
    *,
    active: bool,
    started_at: datetime | None,
    temperature_gap: float | None,
    upstairs_zone_keys: tuple[str, ...],
    reason: str,
) -> bool:
    previous_active = bool(RUNTIME_STATE.get("powerday_downstairs_priority_active"))
    RUNTIME_STATE["powerday_downstairs_priority_started_at"] = started_at
    RUNTIME_STATE["powerday_downstairs_priority_active"] = active
    RUNTIME_STATE["powerday_downstairs_priority_gap"] = temperature_gap
    RUNTIME_STATE["powerday_downstairs_priority_upstairs_zones"] = upstairs_zone_keys
    RUNTIME_STATE["powerday_downstairs_priority_reason"] = reason

    if active != previous_active:
        LOGGER.info(
            "POWERDAY: downstairs_priority_active=%s gap=%s upstairs=%s reason=%s",
            active,
            temperature_gap,
            ",".join(upstairs_zone_keys) if upstairs_zone_keys else "none",
            reason,
        )
    return active


def _update_powerday_downstairs_priority_runtime_state(snapshot, operating_mode: str | None, now: datetime) -> bool:
    normalized_now = _normalize_runtime_datetime(now) or _system_now()
    previously_active = bool(RUNTIME_STATE.get("powerday_downstairs_priority_active"))
    started_at = _normalize_runtime_datetime(RUNTIME_STATE.get("powerday_downstairs_priority_started_at"))

    if not previously_active or started_at is None:
        eligible, temperature_gap, upstairs_zone_keys, reason = _powerday_downstairs_priority_entry_eligibility(
            snapshot,
            operating_mode,
        )
        if not eligible:
            return _set_powerday_downstairs_priority_runtime_state(
                active=False,
                started_at=None,
                temperature_gap=temperature_gap,
                upstairs_zone_keys=(),
                reason=reason,
            )
        return _set_powerday_downstairs_priority_runtime_state(
            active=True,
            started_at=normalized_now,
            temperature_gap=temperature_gap,
            upstairs_zone_keys=upstairs_zone_keys,
            reason=f"started: {reason}",
        )

    base_eligible, base_reason = _powerday_downstairs_priority_base_eligibility(snapshot, operating_mode)
    if not base_eligible:
        return _set_powerday_downstairs_priority_runtime_state(
            active=False,
            started_at=None,
            temperature_gap=None,
            upstairs_zone_keys=(),
            reason=base_reason,
        )

    stored_upstairs_zone_keys = RUNTIME_STATE.get("powerday_downstairs_priority_upstairs_zones", ())
    if not isinstance(stored_upstairs_zone_keys, (list, tuple)):
        stored_upstairs_zone_keys = ()

    coldest_upstairs_temp: float | None = None
    tracked_upstairs_zone_keys: list[str] = []
    for zone_key in stored_upstairs_zone_keys:
        upstairs_zone = snapshot.zones.get(str(zone_key))
        if upstairs_zone is None:
            continue
        tracked_upstairs_zone_keys.append(str(zone_key))
        if coldest_upstairs_temp is None or upstairs_zone.current_temp < coldest_upstairs_temp:
            coldest_upstairs_temp = upstairs_zone.current_temp

    if coldest_upstairs_temp is None:
        return _set_powerday_downstairs_priority_runtime_state(
            active=False,
            started_at=None,
            temperature_gap=None,
            upstairs_zone_keys=(),
            reason="tracked upstairs zones are unavailable",
        )

    downstairs_temp = snapshot.zones[POWERDAY_DOWNSTAIRS_PRIORITY_ZONE_KEY].current_temp
    temperature_gap = coldest_upstairs_temp - downstairs_temp
    hold_until = started_at + timedelta(seconds=POWERDAY_DOWNSTAIRS_PRIORITY_MIN_SECONDS)
    if normalized_now < hold_until:
        return _set_powerday_downstairs_priority_runtime_state(
            active=True,
            started_at=started_at,
            temperature_gap=temperature_gap,
            upstairs_zone_keys=tuple(tracked_upstairs_zone_keys),
            reason=f"minimum priority hold until {hold_until.isoformat()}; gap {temperature_gap:.1f}C",
        )

    if temperature_gap <= POWERDAY_DOWNSTAIRS_PRIORITY_EXIT_GAP:
        return _set_powerday_downstairs_priority_runtime_state(
            active=False,
            started_at=None,
            temperature_gap=temperature_gap,
            upstairs_zone_keys=(),
            reason=f"upstairs/downstairs gap {temperature_gap:.1f}C <= {POWERDAY_DOWNSTAIRS_PRIORITY_EXIT_GAP:.1f}C",
        )

    return _set_powerday_downstairs_priority_runtime_state(
        active=True,
        started_at=started_at,
        temperature_gap=temperature_gap,
        upstairs_zone_keys=tuple(tracked_upstairs_zone_keys),
        reason=f"holding priority; gap {temperature_gap:.1f}C >= {POWERDAY_DOWNSTAIRS_PRIORITY_EXIT_GAP:.1f}C",
    )


def _powerday_downstairs_free_power_fan_boost(
    snapshot,
    demand,
    predicted_open_zones: tuple[str, ...],
    operating_mode: str | None,
) -> int:
    """Return the physical fan-level boost for active free-power downstairs heating."""
    if snapshot.comfort_mode != COMFORT_MODE_POWER_DAY:
        return 0
    if (
        not snapshot.free_power_available
        or snapshot.free_power_heat_soak_level != POWERDAY_HEATSOAK_FULL
        or operating_mode != HVAC_HEAT
    ):
        return 0
    if not demand.heat_requested:
        return 0
    if POWERDAY_DOWNSTAIRS_PRIORITY_ZONE_KEY not in predicted_open_zones:
        return 0
    return POWERDAY_DOWNSTAIRS_FREE_POWER_FAN_BOOST_LEVELS


def _set_poweroff_runtime_state(
    *,
    active: bool,
    started_at: datetime | None,
    hold_until: datetime | None,
    reason: str,
) -> bool:
    previous_active = bool(RUNTIME_STATE.get("poweroff_active"))
    RUNTIME_STATE["poweroff_activation_started_at"] = started_at
    RUNTIME_STATE["poweroff_activation_hold_until"] = hold_until
    RUNTIME_STATE["poweroff_active"] = active
    RUNTIME_STATE["poweroff_reason"] = reason

    if active != previous_active:
        LOGGER.info(
            "POWEROFF: active=%s battery=%s pv_power=%s hold_until=%s reason=%s",
            active,
            RUNTIME_STATE.get("poweroff_battery_remaining"),
            RUNTIME_STATE.get("poweroff_pv_power"),
            _isoformat(hold_until),
            reason,
        )

    return active


def _update_poweroff_runtime_state(controller: PyscriptController, now: datetime) -> bool:
    normalized_now = _normalize_runtime_datetime(now) or _system_now()
    selected_comfort_mode = str(controller.get_state(DEFAULT_SYSTEM_CONFIG.comfort_mode_entity) or "")
    battery_remaining = parse_float(controller.get_state(GOODWE_BATTERY_REMAINING_SENSOR))
    pv_power = parse_float(controller.get_state(GOODWE_PV_POWER_SENSOR))
    RUNTIME_STATE["poweroff_battery_remaining"] = battery_remaining
    RUNTIME_STATE["poweroff_pv_power"] = pv_power

    if selected_comfort_mode != COMFORT_MODE_POWER_OFF:
        return _set_poweroff_runtime_state(
            active=False,
            started_at=None,
            hold_until=None,
            reason="comfort mode is not PowerOff",
        )

    selected_hvac_mode = str(controller.get_state(DEFAULT_SYSTEM_CONFIG.hvac_mode_entity) or "")
    if selected_hvac_mode.strip().lower() == "off":
        return _set_poweroff_runtime_state(
            active=False,
            started_at=None,
            hold_until=None,
            reason="HVAC mode is Off",
        )

    started_at = _normalize_runtime_datetime(RUNTIME_STATE.get("poweroff_activation_started_at"))
    if started_at is None:
        if battery_remaining is None:
            return _set_poweroff_runtime_state(
                active=False,
                started_at=None,
                hold_until=None,
                reason="missing battery remaining",
            )
        if battery_remaining <= POWEROFF_ACTIVATION_BATTERY_THRESHOLD:
            return _set_poweroff_runtime_state(
                active=False,
                started_at=None,
                hold_until=None,
                reason=(
                    f"battery {battery_remaining:.1f} <= "
                    f"{POWEROFF_ACTIVATION_BATTERY_THRESHOLD:.1f}"
                ),
            )
        if pv_power is None:
            return _set_poweroff_runtime_state(
                active=False,
                started_at=None,
                hold_until=None,
                reason="missing PV power",
            )
        if pv_power <= POWEROFF_PV_POWER_THRESHOLD:
            return _set_poweroff_runtime_state(
                active=False,
                started_at=None,
                hold_until=None,
                reason=f"PV power {pv_power:.2f} <= {POWEROFF_PV_POWER_THRESHOLD:.2f}",
            )

        started_at = normalized_now
        hold_until = started_at + timedelta(seconds=POWEROFF_MIN_ACTIVATION_SECONDS)
        return _set_poweroff_runtime_state(
            active=True,
            started_at=started_at,
            hold_until=hold_until,
            reason=(
                f"battery {battery_remaining:.1f} > {POWEROFF_ACTIVATION_BATTERY_THRESHOLD:.1f}; "
                f"PV power {pv_power:.2f} > {POWEROFF_PV_POWER_THRESHOLD:.2f}"
            ),
        )

    hold_until = started_at + timedelta(seconds=POWEROFF_MIN_ACTIVATION_SECONDS)
    if battery_remaining is not None and battery_remaining < POWEROFF_DEACTIVATION_BATTERY_THRESHOLD:
        if normalized_now >= hold_until:
            return _set_poweroff_runtime_state(
                active=False,
                started_at=None,
                hold_until=None,
                reason=(
                    f"battery {battery_remaining:.1f} < "
                    f"{POWEROFF_DEACTIVATION_BATTERY_THRESHOLD:.1f} after minimum activation"
                ),
            )
        return _set_poweroff_runtime_state(
            active=True,
            started_at=started_at,
            hold_until=hold_until,
            reason=(
                f"minimum activation hold until {hold_until.isoformat()}; "
                f"battery {battery_remaining:.1f} < {POWEROFF_DEACTIVATION_BATTERY_THRESHOLD:.1f}"
            ),
        )

    return _set_poweroff_runtime_state(
        active=True,
        started_at=started_at,
        hold_until=hold_until,
        reason=f"active; battery {battery_remaining if battery_remaining is not None else 'unknown'} has not fallen below {POWEROFF_DEACTIVATION_BATTERY_THRESHOLD:.1f}",
    )


def run_control_pass(*, reason: str, comfort_mode_changed: bool = False) -> None:
    task.unique(CONTROL_PASS_TASK_NAME)

    controller = PyscriptController()
    now = _system_now()
    startup_reconcile = RUNTIME_STATE["last_successful_control_pass"] is None and not comfort_mode_changed
    RUNTIME_STATE.setdefault("idle_heat_step", None)
    RUNTIME_STATE.setdefault("idle_heat_step_changed_at", None)
    RUNTIME_STATE.setdefault("idle_heat_zone_key", None)
    RUNTIME_STATE.setdefault("idle_shutdown_at", None)
    RUNTIME_STATE.setdefault("idle_shutdown_heat_step", None)
    RUNTIME_STATE.setdefault("idle_shutdown_zone_key", None)
    RUNTIME_STATE.setdefault("immediate_shutdown_zone_close_not_before", None)
    RUNTIME_STATE.setdefault("last_heatcool_request_at", None)
    RUNTIME_STATE.setdefault("downstairs_startup_priority_started_at", None)
    RUNTIME_STATE.setdefault("downstairs_startup_priority_active", False)
    RUNTIME_STATE.setdefault("downstairs_startup_priority_reason", None)
    RUNTIME_STATE.setdefault("last_fan_speed_decrease_at", None)
    RUNTIME_STATE.setdefault("hvac_off_started_at", None)
    RUNTIME_STATE.setdefault("hvac_start_fan_ramp_started_at", None)
    RUNTIME_STATE.setdefault("heat_demand_fan_boost_level", 0)
    RUNTIME_STATE.setdefault("last_heat_demand_fan_boost_at", None)
    RUNTIME_STATE.setdefault("heat_demand_fan_boost_restore_checked", False)
    RUNTIME_STATE.setdefault("max_power_demand_5m_kw", None)
    RUNTIME_STATE.setdefault("heat_demand_fan_boost_reason", None)
    RUNTIME_STATE.setdefault("powerday_downstairs_priority_started_at", None)
    RUNTIME_STATE.setdefault("powerday_downstairs_priority_active", False)
    RUNTIME_STATE.setdefault("powerday_downstairs_priority_gap", None)
    RUNTIME_STATE.setdefault("powerday_downstairs_priority_upstairs_zones", ())
    RUNTIME_STATE.setdefault("powerday_downstairs_priority_reason", None)
    RUNTIME_STATE.setdefault("powerday_battery_free_power_boost_active", False)
    RUNTIME_STATE.setdefault("powerday_battery_free_power_boost_reason", None)
    RUNTIME_STATE.setdefault("poweroff_battery_remaining", None)
    RUNTIME_STATE.setdefault("poweroff_pv_power", None)
    RUNTIME_STATE.setdefault("poweroff_activation_started_at", None)
    RUNTIME_STATE.setdefault("poweroff_activation_hold_until", None)
    RUNTIME_STATE.setdefault("poweroff_active", False)
    RUNTIME_STATE.setdefault("poweroff_reason", None)
    RUNTIME_STATE.setdefault("idle_demand_forecast_weather_points", ())
    RUNTIME_STATE.setdefault("idle_demand_forecast_weather_fetched_at", None)
    RUNTIME_STATE.setdefault("weather_forecast_last_success_at", None)
    RUNTIME_STATE.setdefault("idle_demand_forecast_weather_error", None)
    RUNTIME_STATE.setdefault("idle_demand_forecast", None)
    RUNTIME_STATE.setdefault("powerday_forecast_assessment", None)
    RUNTIME_STATE.setdefault("powerday_indoor_humidity", None)
    RUNTIME_STATE.setdefault("powerday_dry_eligible", False)
    RUNTIME_STATE.setdefault("powerday_dry_humidity_eligible", False)
    RUNTIME_STATE.setdefault("powerday_dry_coldest_enabled_zone_celsius", None)
    RUNTIME_STATE.setdefault("powerday_dry_heat_forecast_safe", None)
    RUNTIME_STATE.setdefault("powerday_dry_cool_forecast_safe", None)
    RUNTIME_STATE.setdefault("powerday_dry_active", False)
    RUNTIME_STATE.setdefault("powerday_dry_started_at", None)
    RUNTIME_STATE.setdefault("powerday_dry_completed", False)
    RUNTIME_STATE.setdefault("powerday_dry_completed_at", None)
    RUNTIME_STATE.setdefault("powerday_dry_elapsed_seconds", 0.0)
    RUNTIME_STATE.setdefault("powerday_dry_reason", None)
    RUNTIME_STATE.setdefault("powerday_dry_restore_checked", False)
    RUNTIME_STATE.setdefault("powerday_free_power_period_active", False)
    RUNTIME_STATE.setdefault("powerday_dry_heat_transition_started_at", None)
    RUNTIME_STATE.setdefault("powerday_dry_heat_transition_pending_off", False)
    RUNTIME_STATE["idle_demand_forecast"] = None
    RUNTIME_STATE["last_trigger"] = reason
    if comfort_mode_changed:
        # A new user selection supersedes any earlier fan-rundown delay.
        RUNTIME_STATE["immediate_shutdown_zone_close_not_before"] = None
    normalized_now = _normalize_runtime_datetime(now) or now
    immediate_shutdown_zone_close_not_before = _normalize_runtime_datetime(
        RUNTIME_STATE.get("immediate_shutdown_zone_close_not_before")
    )
    immediate_shutdown_zone_close_hold_active = (
        immediate_shutdown_zone_close_not_before is not None
        and normalized_now < immediate_shutdown_zone_close_not_before
    )
    immediate_shutdown_zone_close_due = (
        immediate_shutdown_zone_close_not_before is not None
        and normalized_now >= immediate_shutdown_zone_close_not_before
    )
    zone_reconciliation_changed = comfort_mode_changed or immediate_shutdown_zone_close_due
    _restore_heat_demand_fan_boost_state(now)
    _reconcile_pending_zone_state(controller, now)
    selected_comfort_mode = str(controller.get_state(DEFAULT_SYSTEM_CONFIG.comfort_mode_entity) or "")
    raw_free_power_available = _is_free_power_price_state(
        controller.get_state(GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR)
    )
    _sync_powerday_free_power_period(raw_free_power_available)
    weather_points = _fresh_cached_weather_points(now)
    powerday_forecast_assessment = None
    free_power_heat_soak_level = POWERDAY_HEATSOAK_FULL
    if selected_comfort_mode == COMFORT_MODE_POWER_DAY:
        powerday_forecast_assessment = _resolve_powerday_forecast_assessment(controller, now)
        free_power_heat_soak_level = powerday_forecast_assessment.level
        weather_points = _fresh_cached_weather_points(now)
    else:
        RUNTIME_STATE["powerday_forecast_assessment"] = None
    powerday_heat_sink_active = _update_powerday_heat_sink_runtime_state(controller, now)
    powerday_battery_free_power_boost_active = _update_powerday_battery_free_power_boost_runtime_state(controller)
    powerday_free_power_later_active = _update_powerday_free_power_later_runtime_state(
        controller,
        now,
        heat_soak_level=free_power_heat_soak_level,
    )
    poweroff_active = _update_poweroff_runtime_state(controller, now)

    snapshot = build_snapshot(
        controller,
        config=DEFAULT_SYSTEM_CONFIG,
        last_switch_changes=RUNTIME_STATE["last_zone_change"],
        pending_switch_states=RUNTIME_STATE["pending_zone_state"],
        heat_sink_available=powerday_heat_sink_active,
        battery_free_power_boost_available=powerday_battery_free_power_boost_active,
        free_power_later_available=powerday_free_power_later_active,
        free_power_heat_soak_level=free_power_heat_soak_level,
        poweroff_active=poweroff_active,
        now=now,
    )

    climate_entity = DEFAULT_SYSTEM_CONFIG.climate_entity
    current_hvac_mode = controller.get_state(climate_entity)
    current_fan_mode = controller.get_attr(climate_entity, "fan_mode")
    supported_fan_modes = controller.get_attr(climate_entity, "fan_modes")
    if not isinstance(supported_fan_modes, (list, tuple, set)):
        supported_fan_modes = None
    supported_hvac_modes = controller.get_attr(climate_entity, "hvac_modes")
    if not isinstance(supported_hvac_modes, (list, tuple, set)):
        supported_hvac_modes = None
    current_setpoint = controller.get_attr(climate_entity, "temperature")
    target_temp_step = controller.get_attr(climate_entity, "target_temp_step")
    current_hvac_mode_str = str(current_hvac_mode) if current_hvac_mode is not None else None
    _observe_hvac_off_period(current_hvac_mode_str, now)

    operating_mode, operating_mode_reason = resolve_operating_mode(
        snapshot,
        current_hvac_mode=current_hvac_mode_str,
        last_active_hvac_mode=RUNTIME_STATE["last_active_hvac_mode"],
        last_heatcool_transition=RUNTIME_STATE["last_heatcool_transition"],
        now=now,
    )
    powerday_downstairs_priority_active = _update_powerday_downstairs_priority_runtime_state(
        snapshot,
        operating_mode,
        now,
    )
    downstairs_startup_priority_active = _update_downstairs_startup_priority_runtime_state(
        snapshot,
        operating_mode,
        now,
    )

    if snapshot.selected_hvac_mode == CONTROL_HVAC_MODE_MANUAL:
        RUNTIME_STATE["powerday_dry_eligible"] = False
        RUNTIME_STATE["powerday_dry_reason"] = "Manual HVAC selection disables automatic dry mode"
        automatic_dry_was_active = (
            bool(RUNTIME_STATE.get("powerday_dry_active"))
            or (current_hvac_mode_str or "").lower() == HVAC_DRY
        )
        if automatic_dry_was_active:
            _set_powerday_dry_state(
                active=False,
                started_at=_normalize_runtime_datetime(RUNTIME_STATE.get("powerday_dry_started_at")),
                completed=True,
                completed_at=normalized_now,
                reason="Manual HVAC selection stopped automatic dry mode",
            )
            _set_powerday_dry_heat_transition_started_at(None, pending_off=True)
            apply_dispatch_plan(
                controller,
                DispatchPlan(turn_off=True, reason="Manual selection stopped automatic dry mode"),
                config=DEFAULT_SYSTEM_CONFIG,
                current_hvac_mode=current_hvac_mode_str,
                current_fan_mode=str(current_fan_mode) if current_fan_mode is not None else None,
                current_setpoint=current_setpoint,
            )
        LOGGER.info(
            "DISPATCH: manual mode selected; %s",
            "automatic dry mode stopped" if automatic_dry_was_active else "leaving zones and heatpump unchanged",
        )
        RUNTIME_STATE["last_error"] = None
        RUNTIME_STATE["last_successful_control_pass"] = now
        _publish_runtime_state("manual")
        return

    zone_actions, predicted_open_zones = resolve_zone_actions(
        snapshot,
        now,
        operation_mode=operating_mode,
        comfort_mode_changed=zone_reconciliation_changed,
        startup_reconcile=startup_reconcile,
        downstairs_priority_active=powerday_downstairs_priority_active,
        downstairs_zone_key=POWERDAY_DOWNSTAIRS_PRIORITY_ZONE_KEY,
        upstairs_zone_keys=POWERDAY_DOWNSTAIRS_PRIORITY_UPSTAIRS_ZONE_KEYS,
        downstairs_startup_priority_active=downstairs_startup_priority_active,
        downstairs_startup_priority_zone_key=DOWNSTAIRS_STARTUP_PRIORITY_ZONE_KEY,
        hold_closing_zones=immediate_shutdown_zone_close_hold_active,
    )
    demand = resolve_equipment_demand(
        snapshot,
        predicted_open_zones,
        operation_mode=operating_mode,
        allowed_zone_keys=(POWERDAY_DOWNSTAIRS_PRIORITY_ZONE_KEY,) if powerday_downstairs_priority_active else None,
    )
    max_power_demand_5m_kw = parse_float(controller.get_state(EAGLE_200_MAX_POWER_DEMAND_5M_SENSOR))
    (
        preliminary_heat_demand_fan_boost_level,
        _,
        _,
    ) = resolve_heat_demand_fan_boost(
        snapshot,
        demand,
        predicted_open_zones,
        max_power_demand_5m_kw,
        previous_boost_level=RUNTIME_STATE["heat_demand_fan_boost_level"],
        last_boost_at=RUNTIME_STATE["last_heat_demand_fan_boost_at"],
        now=now,
    )
    preliminary_downstairs_free_power_fan_boost = _powerday_downstairs_free_power_fan_boost(
        snapshot,
        demand,
        predicted_open_zones,
        operating_mode,
    )

    dispatch_plan_kwargs = {
        "current_hvac_mode": current_hvac_mode_str,
        "current_fan_mode": str(current_fan_mode) if current_fan_mode is not None else None,
        "current_setpoint": current_setpoint,
        "target_temp_step": target_temp_step,
        "operation_mode": operating_mode,
        "comfort_mode_changed": comfort_mode_changed,
        "idle_started_at": RUNTIME_STATE["idle_started_at"],
        "idle_heat_step": RUNTIME_STATE["idle_heat_step"],
        "idle_heat_step_changed_at": RUNTIME_STATE["idle_heat_step_changed_at"],
        "idle_heat_zone_key": RUNTIME_STATE["idle_heat_zone_key"],
        "idle_shutdown_at": RUNTIME_STATE["idle_shutdown_at"],
        "idle_shutdown_heat_step": RUNTIME_STATE["idle_shutdown_heat_step"],
        "idle_shutdown_zone_key": RUNTIME_STATE["idle_shutdown_zone_key"],
        "supported_fan_modes": supported_fan_modes,
        "fan_speed_decrease_at": RUNTIME_STATE["last_fan_speed_decrease_at"],
        "base_fan_boost": preliminary_heat_demand_fan_boost_level,
        "additional_fan_levels": max(
            preliminary_downstairs_free_power_fan_boost,
            POWERDAY_DOWNSTAIRS_PRIORITY_FAN_BOOST_LEVELS if powerday_downstairs_priority_active else 0,
        ),
        "now": now,
    }
    provisional_plan = build_dispatch_plan(snapshot, demand, predicted_open_zones, **dispatch_plan_kwargs)
    zone_actions, predicted_open_zones = resolve_zone_actions(
        snapshot,
        now,
        operation_mode=operating_mode,
        comfort_mode_changed=zone_reconciliation_changed,
        startup_reconcile=startup_reconcile,
        downstairs_priority_active=powerday_downstairs_priority_active,
        downstairs_zone_key=POWERDAY_DOWNSTAIRS_PRIORITY_ZONE_KEY,
        upstairs_zone_keys=POWERDAY_DOWNSTAIRS_PRIORITY_UPSTAIRS_ZONE_KEYS,
        downstairs_startup_priority_active=downstairs_startup_priority_active,
        downstairs_startup_priority_zone_key=DOWNSTAIRS_STARTUP_PRIORITY_ZONE_KEY,
        requested_fan_speed_level=fan_speed_level(provisional_plan.fan_mode),
        hold_closing_zones=immediate_shutdown_zone_close_hold_active,
    )
    demand = resolve_equipment_demand(
        snapshot,
        predicted_open_zones,
        operation_mode=operating_mode,
        allowed_zone_keys=(POWERDAY_DOWNSTAIRS_PRIORITY_ZONE_KEY,) if powerday_downstairs_priority_active else None,
    )
    (
        heat_demand_fan_boost_level,
        last_heat_demand_fan_boost_at,
        heat_demand_fan_boost_reason,
    ) = resolve_heat_demand_fan_boost(
        snapshot,
        demand,
        predicted_open_zones,
        max_power_demand_5m_kw,
        previous_boost_level=RUNTIME_STATE["heat_demand_fan_boost_level"],
        last_boost_at=RUNTIME_STATE["last_heat_demand_fan_boost_at"],
        now=now,
    )
    downstairs_free_power_fan_boost = _powerday_downstairs_free_power_fan_boost(
        snapshot,
        demand,
        predicted_open_zones,
        operating_mode,
    )
    dispatch_plan_kwargs["base_fan_boost"] = heat_demand_fan_boost_level
    dispatch_plan_kwargs["additional_fan_levels"] = max(
        downstairs_free_power_fan_boost,
        POWERDAY_DOWNSTAIRS_PRIORITY_FAN_BOOST_LEVELS if powerday_downstairs_priority_active else 0,
    )
    dry_requested = False
    dry_transition_turn_off = False
    dry_decision = None
    assessment_for_dry = powerday_forecast_assessment
    if assessment_for_dry is None:
        assessment_for_dry = PowerDayForecastAssessment(
            generated_at=now,
            level=POWERDAY_HEATSOAK_FULL,
            daytime_peak_celsius=None,
            evening_minimum_celsius=None,
            evening_maximum_humidity=None,
            source="unavailable",
            reason="PowerDay forecast is not active",
        )
    if (
        selected_comfort_mode == COMFORT_MODE_POWER_DAY
        or bool(RUNTIME_STATE.get("powerday_dry_active"))
        or (current_hvac_mode_str or "").lower() == HVAC_DRY
    ):
        dry_requested, dry_transition_turn_off, dry_decision = _resolve_powerday_dry_request(
            controller,
            snapshot,
            demand,
            assessment_for_dry,
            weather_points=weather_points,
            supported_hvac_modes=supported_hvac_modes,
            current_hvac_mode=current_hvac_mode_str,
            now=now,
        )
    else:
        RUNTIME_STATE["powerday_indoor_humidity"] = parse_float(
            controller.get_state(POWERDAY_INDOOR_HUMIDITY_SENSOR)
        )
        RUNTIME_STATE["powerday_dry_eligible"] = False
        RUNTIME_STATE["powerday_dry_humidity_eligible"] = False
        RUNTIME_STATE["powerday_dry_coldest_enabled_zone_celsius"] = None
        RUNTIME_STATE["powerday_dry_heat_forecast_safe"] = None
        RUNTIME_STATE["powerday_dry_cool_forecast_safe"] = None

    if dry_requested:
        zone_actions, predicted_open_zones = resolve_dry_zone_actions(snapshot)
        demand = EquipmentDemand(
            dry_requested=True,
            requested_by_zones=predicted_open_zones,
            reason=dry_decision.reason if dry_decision is not None else "PowerDay dry mode",
        )
        heat_demand_fan_boost_level = 0
        last_heat_demand_fan_boost_at = None
        heat_demand_fan_boost_reason = "dry mode disables heat-demand fan boost"
        dispatch_plan_kwargs["base_fan_boost"] = 0
        dispatch_plan_kwargs["additional_fan_levels"] = 0

    idle_demand_forecast = None
    if not comfort_mode_changed and not dry_requested and not dry_transition_turn_off:
        idle_demand_forecast = _resolve_idle_demand_forecast(
            controller,
            snapshot,
            demand,
            current_hvac_mode=current_hvac_mode_str,
            operation_mode=operating_mode,
            now=now,
        )
    RUNTIME_STATE["idle_demand_forecast"] = idle_demand_forecast
    dispatch_plan_kwargs["idle_demand_forecast"] = idle_demand_forecast
    if dry_transition_turn_off:
        zone_actions = [action for action in zone_actions if action.turn_on]
        predicted_open_zones = _reported_open_zones(snapshot)
        plan = DispatchPlan(
            turn_off=True,
            open_zones=predicted_open_zones,
            reason="waiting for heat-to-dry reversing hold",
        )
    else:
        plan = build_dispatch_plan(snapshot, demand, predicted_open_zones, **dispatch_plan_kwargs)
    plan = _apply_dry_to_heat_transition_hold(
        plan,
        current_hvac_mode=current_hvac_mode_str,
        now=now,
    )

    dry_stopping = (
        (current_hvac_mode_str or "").lower() == HVAC_DRY
        and plan.hvac_mode != HVAC_DRY
    )
    if (
        (comfort_mode_changed or dry_stopping)
        and plan.turn_off
        and (current_hvac_mode_str or "").lower() != HVAC_OFF
    ):
        deferred_zone_closures = [action for action in zone_actions if not action.turn_on]
        if deferred_zone_closures:
            close_not_before = normalized_now + timedelta(seconds=IMMEDIATE_SHUTDOWN_ZONE_CLOSE_DELAY_SECONDS)
            RUNTIME_STATE["immediate_shutdown_zone_close_not_before"] = close_not_before
            zone_actions = [action for action in zone_actions if action.turn_on]
            immediate_shutdown_zone_close_hold_active = True
            predicted_open_zones = tuple(
                sorted(set(predicted_open_zones) | {action.zone_key for action in deferred_zone_closures})
            )
            LOGGER.info(
                "ZONES: deferring %s immediate-shutdown closure(s) until %s for heatpump fan rundown",
                len(deferred_zone_closures),
                close_not_before.isoformat(),
            )

    RUNTIME_STATE["heat_demand_fan_boost_level"] = heat_demand_fan_boost_level
    RUNTIME_STATE["last_heat_demand_fan_boost_at"] = last_heat_demand_fan_boost_at
    RUNTIME_STATE["max_power_demand_5m_kw"] = max_power_demand_5m_kw
    RUNTIME_STATE["heat_demand_fan_boost_reason"] = heat_demand_fan_boost_reason
    _persist_heat_demand_fan_boost_state(heat_demand_fan_boost_level)

    zone_diagnostics = describe_zone_predictions(
        snapshot,
        now,
        predicted_open_zones,
        operation_mode=operating_mode,
        comfort_mode_changed=zone_reconciliation_changed,
        startup_reconcile=startup_reconcile,
        hold_closing_zones=immediate_shutdown_zone_close_hold_active,
    )
    if startup_reconcile:
        LOGGER.info(
            "ZONES: startup_reconcile reported_open=%s desired_open=%s",
            _describe_open_zones(_reported_open_zones(snapshot)),
            _describe_open_zones(predicted_open_zones),
        )

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

    if immediate_shutdown_zone_close_due:
        RUNTIME_STATE["immediate_shutdown_zone_close_not_before"] = None

    if not predicted_open_zones:
        LOGGER.warning("ZONES: predicted_open=none details=%s", " | ".join(zone_diagnostics))

    hvac_start_fan_ramp_started_at = _resolve_hvac_start_fan_ramp_started_at(
        plan,
        current_hvac_mode_str,
        now,
    )
    if hvac_start_fan_ramp_started_at is not None:
        plan = build_dispatch_plan(
            snapshot,
            demand,
            predicted_open_zones,
            **dispatch_plan_kwargs,
            hvac_start_fan_ramp_started_at=hvac_start_fan_ramp_started_at,
        )

    apply_dispatch_plan(
        controller,
        plan,
        config=DEFAULT_SYSTEM_CONFIG,
        current_hvac_mode=current_hvac_mode_str,
        current_fan_mode=str(current_fan_mode) if current_fan_mode is not None else None,
        current_setpoint=current_setpoint,
    )
    if is_fan_speed_decrease(
        str(current_fan_mode) if current_fan_mode is not None else None,
        plan.fan_mode,
    ):
        RUNTIME_STATE["last_fan_speed_decrease_at"] = now

    previous_valid_mode = (current_hvac_mode_str or "").lower()
    if previous_valid_mode not in {HVAC_HEAT, HVAC_COOL}:
        previous_valid_mode = None
    if previous_valid_mode and plan.hvac_mode in {HVAC_HEAT, HVAC_COOL} and previous_valid_mode != plan.hvac_mode:
        RUNTIME_STATE["last_heatcool_transition"] = now
    if plan.hvac_mode in {HVAC_HEAT, HVAC_COOL}:
        RUNTIME_STATE["last_active_hvac_mode"] = plan.hvac_mode
    _update_idle_shutdown_runtime_state(plan, now, current_hvac_mode=current_hvac_mode_str)
    RUNTIME_STATE["idle_started_at"] = resolve_idle_started_at(
        RUNTIME_STATE["idle_started_at"],
        plan,
        current_hvac_mode=current_hvac_mode_str,
        now=now,
    )
    _update_idle_heat_runtime_state(plan, now)

    LOGGER.info(
        "DISPATCH: selector_mode=%s operating_mode=%s mode_reason=%s reason=%s requested_by_zones=%s hvac_mode=%s idle=%s forecast=%s fan_mode=%s fan_boost=%s setpoint=%s open_zones=%s temp=%s comfort_adjustments=%s trigger=%s",
        snapshot.selected_hvac_mode,
        operating_mode or "none",
        operating_mode_reason,
        plan.reason,
        ",".join(plan.requested_by_zones) if plan.requested_by_zones else "none",
        plan.hvac_mode or "off",
        plan.idle,
        idle_demand_forecast.reason if idle_demand_forecast is not None else "not evaluated",
        plan.fan_mode,
        heat_demand_fan_boost_level,
        plan.setpoint,
        _describe_open_zones(plan.open_zones),
        _format_zone_temps(snapshot, plan),
        ",".join(
            [
                f"{zone_key}={zone.comfort_adjustment:+.1f}"
                for zone_key, zone in snapshot.zones.items()
                if zone.comfort_adjustment != 0.0
            ]
        )
        or "none",
        RUNTIME_STATE["last_trigger"],
    )
    RUNTIME_STATE["last_error"] = None
    RUNTIME_STATE["last_successful_control_pass"] = now
    _publish_runtime_state("running")


def _ensure_comfort_score_semantics_migrated() -> bool:
    """Reseed old-polarity helpers before a control snapshot can read them."""
    # Helpers created by a previous target-offset polarity must never reach a
    # control snapshot.  The independent publisher is run synchronously once
    # to replace all five values before this pass reads them.
    if RUNTIME_STATE.get("comfort_score_semantics_migration_in_progress"):
        return False
    if _comfort_score_semantics_requires_migration():
        return _run_comfort_adjustment_pass(reason="score-semantics migration before control")
    return True


def _run_enabled_control_pass(*, reason: str, comfort_mode_changed: bool = False) -> None:
    if not _control_is_enabled():
        LOGGER.info("TempTamer control is disabled; skipping trigger '%s'", reason)
        _publish_runtime_state("stopped")
        return

    if RUNTIME_STATE.get("comfort_score_semantics_migration_in_progress"):
        LOGGER.info("COMFORT ADJUSTMENT: deferring control while score helpers are being reseeded")
        return
    if not _ensure_comfort_score_semantics_migrated():
        LOGGER.warning("COMFORT ADJUSTMENT: control deferred until score-semantics reseed succeeds")
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
    if not _ensure_comfort_score_semantics_migrated():
        LOGGER.warning("COMFORT ADJUSTMENT: manual control deferred until score-semantics reseed succeeds")
        return
    run_control_pass(reason=reason)


@time_trigger("startup")
def temptamer_initialize() -> None:
    RUNTIME_STATE["last_trigger"] = "startup"
    if _control_is_enabled():
        _publish_runtime_state("starting")
        _run_enabled_control_pass(reason="startup")
        return

    _publish_runtime_state("stopped")


@time_trigger("startup")
def temptamer_initialize_comfort_adjustments() -> None:
    _run_comfort_adjustment_pass(reason="startup")


@time_trigger(f"period(now, {CONTROL_INTERVAL_SECONDS}s)")
def temptamer_periodic_control_pass() -> None:
    _run_enabled_control_pass(reason="periodic trigger")


@time_trigger(f"period(now, {CONTROL_INTERVAL_SECONDS}s)")
def temptamer_periodic_comfort_adjustments() -> None:
    _run_comfort_adjustment_pass(reason="periodic trigger")


@state_trigger(*IMMEDIATE_RECONCILIATION_TRIGGER_ENTITIES)
def temptamer_immediate_reconciliation_requested(*_args, **_kwargs) -> None:
    """Reconcile user-selected comfort/HVAC changes without anti-flap delays."""
    _invalidate_pending_normal_recalculation()
    _run_enabled_control_pass(reason="immediate comfort/HVAC selection reconciliation", comfort_mode_changed=True)


def _next_normal_recalculation_generation() -> int:
    generation = int(RUNTIME_STATE.get("normal_recalculation_generation", 0)) + 1
    RUNTIME_STATE["normal_recalculation_generation"] = generation
    return generation


def _invalidate_pending_normal_recalculation() -> None:
    _next_normal_recalculation_generation()


def _run_debounced_normal_recalculation(generation: int) -> None:
    task.sleep(NORMAL_RECALCULATION_DEBOUNCE_SECONDS)
    if generation != RUNTIME_STATE.get("normal_recalculation_generation"):
        return
    _run_enabled_control_pass(reason="normal state recalculation")


@state_trigger(*NORMAL_RECALCULATION_TRIGGER_ENTITIES)
def temptamer_normal_recalculation_requested(*_args, **_kwargs) -> None:
    """Recalculate for telemetry and adjustment changes while retaining idle safeguards."""
    generation = _next_normal_recalculation_generation()
    task.create(_run_debounced_normal_recalculation, generation)


@state_trigger(*COMFORT_ADJUSTMENT_TRIGGER_ENTITIES)
def temptamer_comfort_adjustment_input_changed(*_args, **_kwargs) -> None:
    _run_comfort_adjustment_pass(reason="comfort adjustment input changed")
