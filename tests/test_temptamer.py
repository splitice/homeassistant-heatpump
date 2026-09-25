from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import logging
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from pyscript.apps.temptamer.comfort_modes import (
    DefaultComfortMode,
    FreePowerSetpointBoost,
    NightComfortMode,
    PowerComfortMode,
    PowerOffComfortMode,
    ScheduledComfortMode,
)
from pyscript.apps.temptamer.config import (
    COMFORT_ADJUSTMENT_TRIGGER_ENTITIES,
    DEFAULT_COMFORT_ADJUSTMENT_CONFIG,
    DEFAULT_SYSTEM_CONFIG,
    DOWNSTAIRS_STARTUP_PRIORITY_INACTIVE_SECONDS,
    DOWNSTAIRS_STARTUP_PRIORITY_MIN_SECONDS,
    DOWNSTAIRS_STARTUP_PRIORITY_ZONE_KEY,
    EAGLE_200_MAX_POWER_DEMAND_5M_SENSOR,
    EAGLE_200_POWER_DEMAND_SENSOR,
    GLOBAL_SETPOINT_ADJUSTMENT_ENTITY,
    GOODWE_BATTERY_REMAINING_SENSOR,
    GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR,
    GOODWE_PV_POWER_SENSOR,
    IMMEDIATE_RECONCILIATION_TRIGGER_ENTITIES,
    NORMAL_RECALCULATION_TRIGGER_ENTITIES,
    POWERDAY_EXPORT_AVERAGE_WINDOW_SECONDS,
    POWERDAY_DOWNSTAIRS_FREE_POWER_FAN_BOOST_LEVELS,
    POWERDAY_DOWNSTAIRS_FREE_POWER_DIRECT_TARGET_BOOST,
    POWERDAY_DOWNSTAIRS_PRIORITY_FAN_BOOST_LEVELS,
    POWERDAY_DOWNSTAIRS_PRIORITY_MIN_SECONDS,
    POWERDAY_DOWNSTAIRS_PRIORITY_UPSTAIRS_ZONE_KEYS,
    POWERDAY_DOWNSTAIRS_PRIORITY_ZONE_KEY,
    POWERDAY_FREE_POWER_PV_AVERAGE_WINDOW_SECONDS,
    POWERDAY_HEAT_SINK_MIN_SECONDS,
    POWERDAY_DRY_HEAT_TRANSITION_SECONDS,
    POWERDAY_DRY_MAX_SECONDS,
    POWEROFF_MIN_ACTIVATION_SECONDS,
    TEMPTAMER_LOGGING_CATEGORIES,
    IDLE_DEMAND_FORECAST_WEATHER_ENTITY,
    WEATHER_FORECAST_MAX_AGE_SECONDS,
)
from pyscript.apps.temptamer.comfort_adjustments import (
    apply_adjustment_hysteresis,
    calculate_comfort_adjustments,
    calculate_humidex_adjustment,
    filter_outdoor_temperature,
    resolve_operating_mode as resolve_comfort_adjustment_operating_mode,
    resolve_solar_index,
)
from pyscript.apps.temptamer.constants import (
    COMFORT_MODE_NIGHT,
    COMFORT_MODE_POWER_DAY,
    COMFORT_MODE_POWER_OFF,
    FAN_LOW,
    HVAC_COOL,
    HVAC_DRY,
    HVAC_FAN_ONLY,
    HVAC_HEAT,
    POWERDAY_HEATSOAK_FULL,
    POWERDAY_HEATSOAK_REDUCED,
    POWERDAY_HEATSOAK_SUPPRESSED,
    HEAT_DEMAND_FAN_BOOST_MAX_LEVEL,
    HVAC_START_FAN_RAMP_DURATION_SECONDS,
    HVAC_START_FAN_RAMP_MIN_OFF_SECONDS,
    IDLE_HEAT_UNWIND_SECONDS,
    MIN_IDLE_SECONDS,
    SCHEME_BATHROOM,
    SCHEME_BEDROOM,
    SCHEME_DAY_LIVING,
    SCHEME_DOWNSTAIRS,
    SCHEME_DINING_BASIC,
    SCHEME_NIGHT,
    SCHEME_NIGHT_BEDROOM,
    SCHEME_OFF,
)
from pyscript.apps.temptamer.demand_resolver import resolve_equipment_demand, resolve_operating_mode
from pyscript.apps.temptamer.heatpump_dispatcher import (
    apply_dispatch_plan,
    build_dispatch_plan,
    normalize_cool_setpoint,
    normalize_heat_setpoint,
    normalize_setpoint,
    resolve_fan_mode,
    resolve_heat_demand_fan_boost,
    resolve_hvac_start_fan_ramp_mode,
    resolve_idle_started_at,
)
from pyscript.apps.temptamer.idle_demand_forecast import (
    IdleDemandForecast,
    WeatherForecastPoint,
    forecast_idle_demand,
    forecast_temperature_at,
    parse_hourly_weather_forecast,
)
from pyscript.apps.temptamer.logging_control import (
    get_temptamer_logger,
    install_temptamer_log_filter,
    is_temptamer_log_enabled,
)
from pyscript.apps.temptamer.powerday_forecast import (
    PowerDayForecastAssessment,
    assess_powerday_forecast,
    resolve_powerday_dehumidification,
)
from pyscript.apps.temptamer.models import ControlScheme, DispatchPlan, EquipmentDemand, SystemConfig
import pyscript.apps.temptamer.main as temptamer_main
from pyscript.apps.temptamer.state_reader import build_snapshot
from pyscript.apps.temptamer.zone_control import (
    describe_zone_predictions,
    resolve_high_fan_office_closure,
    resolve_dry_zone_actions,
    resolve_zone_actions,
)


TEST_CLIMATE_ENTITY = "climate.wt32_hpctrl_e8dbd0_heatpump"


class FakeReader:
    def __init__(self, state_map, attr_map=None):
        self.state_map = state_map
        self.attr_map = attr_map or {}

    def get_state(self, entity_id):
        return self.state_map.get(entity_id)

    def get_attr(self, entity_id, attr_name):
        return self.attr_map.get(entity_id, {}).get(attr_name)

def base_state_map(**overrides):
    state_map = {
        "input_select.temptamer_comfort_mode": "Day",
        "input_select.temptamer_hvac_mode": "Heat",
        "input_select.temptamer_comfort_mode_office": "Auto",
        "input_select.temptamer_comfort_mode_dining": "Auto",
        "input_select.temptamer_comfort_mode_downstairs": "Off",
        "input_select.temptamer_comfort_mode_bed12": "Auto",
        "input_select.temptamer_comfort_mode_bed34": "Auto",
        "sensor.home_temperature": "18.0",
        TEST_CLIMATE_ENTITY: "off",
        "sensor.office_average_temperature": "18.0",
        "sensor.average_dining_zone_temp": "18.0",
        "sensor.downstairs_zone_average_temperature": "18.0",
        "sensor.average_bed1_2_zone_temp": "18.0",
        "sensor.average_bed3_4_zone_temp": "18.0",
        "sensor.bathroom_motion_temperature": "18.0",
        GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "1",
        GOODWE_BATTERY_REMAINING_SENSOR: "50",
        GOODWE_PV_POWER_SENSOR: "0",
        EAGLE_200_POWER_DEMAND_SENSOR: "0",
    EAGLE_200_MAX_POWER_DEMAND_5M_SENSOR: "14.0",
        "input_number.temptamer_setpoint_adjustment": "0.0",
        "switch.wt32_hpctrl_e8dbd0_office": "off",
        "switch.wt32_hpctrl_e8dbd0_dining": "off",
        "switch.roof_wt32_hpctrl_e8dbd0_downstairs": "off",
        "switch.wt32_hpctrl_e8dbd0_bed_12": "off",
        "switch.wt32_hpctrl_e8dbd0_bed_34": "off",
    }
    state_map.update(overrides)
    return state_map


def base_attr_map(current_temperature="19.0", temperature=None, target_temp_step=None):
    attrs = {
        "current_temperature": current_temperature,
        "temperature": current_temperature if temperature is None else temperature,
    }
    if target_temp_step is not None:
        attrs["target_temp_step"] = target_temp_step
    return {TEST_CLIMATE_ENTITY: attrs}


COMFORT_ADJUSTMENT_COVER_FACADES = {
    "cover.bed1shutters": "e",
    "cover.bed_2_shutters": "e",
    "cover.bed_3_shutters": "e",
    "cover.bed_4_shutters": "e",
    "cover.officeshutters": "s",
    "cover.kitchen_shutters": "w",
    "cover.lego_shutters": "w",
}


def comfort_adjustment_state_map(**overrides):
    state_map = {
        "input_select.heatpump_mode_user": "Heat",
        TEST_CLIMATE_ENTITY: "heat",
        "sensor.home_temperature": "20.0",
        "sensor.rumpus_average_temperature": "20.0",
        "sensor.develco_products_a_s_moszb_140_temperature_2": "20.0",
        "sensor.kitchen_average_temperature": "22.0",
        "sensor.kitchen_motion_temperature": "22.0",
        "sensor.downstairs_zone_average_temperature": "21.0",
        "sensor.bedroom_1_average_temperature": "20.0",
        "sensor.bedroom_2_average_temperature": "22.0",
        "sensor.average_bed1_2_zone_temp": "21.0",
        "sensor.bedroom_3_average_temperature": "20.0",
        "sensor.bedroom_4_average_temperature": "22.0",
        "sensor.bathroom_average_temperature": "21.0",
        "sensor.bathroom_motion_temperature": "21.0",
        "sensor.average_bed3_4_zone_temp": "21.0",
        "sensor.office_average_temperature": "21.0",
        "sensor.dining_average_temperature": "20.0",
        "sensor.lego_room_average_temperature": "22.0",
        "sensor.average_dining_zone_temp": "21.0",
        "sensor.gw3000c_outdoor_temperature": "11.0",
        "sensor.gw3000c_solar_radiation": "75.0",
        "weather.epping": "sunny",
        "input_number.awning_min_sun_elevation": "10.0",
        "input_number.awning_exposure_half_band": "45.0",
    }
    state_map.update(overrides)
    return state_map


def comfort_adjustment_attr_map(**overrides):
    attr_map = {
        TEST_CLIMATE_ENTITY: {"hvac_action": "idle"},
        "weather.epping": {"temperature": "11.0"},
        "sun.sun": {"elevation": 30.0, "azimuth": 90.0},
        "cover.bed1shutters": {"current_position": 100.0},
        "cover.bed_2_shutters": {"current_position": 100.0},
        "cover.bed_3_shutters": {"current_position": 100.0},
        "cover.bed_4_shutters": {"current_position": 100.0},
        "cover.officeshutters": {"current_position": 100.0},
        "cover.kitchen_shutters": {"current_position": 100.0},
        "cover.lego_shutters": {"current_position": 100.0},
    }
    attr_map.update(overrides)
    return attr_map


TEST_HEAT_CONTROL_SCHEMES = {
    SCHEME_OFF: ControlScheme(name=SCHEME_OFF, enable_outside=0.0, continue_until=0.0, ideal_target=0.0),
    SCHEME_NIGHT: DEFAULT_SYSTEM_CONFIG.heat_control_schemes[SCHEME_NIGHT],
    SCHEME_NIGHT_BEDROOM: DEFAULT_SYSTEM_CONFIG.heat_control_schemes[SCHEME_NIGHT_BEDROOM],
    SCHEME_DAY_LIVING: ControlScheme(name=SCHEME_DAY_LIVING, enable_outside=20.0, continue_until=22.0, ideal_target=21.0),
    SCHEME_DOWNSTAIRS: ControlScheme(name=SCHEME_DOWNSTAIRS, enable_outside=20.5, continue_until=22.5, ideal_target=21.5),
    SCHEME_DINING_BASIC: DEFAULT_SYSTEM_CONFIG.heat_control_schemes[SCHEME_DINING_BASIC],
    SCHEME_BEDROOM: ControlScheme(name=SCHEME_BEDROOM, enable_outside=14.0, continue_until=16.0, ideal_target=14.0),
    SCHEME_BATHROOM: ControlScheme(name=SCHEME_BATHROOM, enable_outside=14.0, continue_until=16.0, ideal_target=14.0),
}

TEST_COOL_CONTROL_SCHEMES = {
    SCHEME_OFF: ControlScheme(name=SCHEME_OFF, enable_outside=0.0, continue_until=0.0, ideal_target=0.0),
    SCHEME_NIGHT: ControlScheme(name=SCHEME_NIGHT, enable_outside=17.0, continue_until=15.0, ideal_target=16.0),
    SCHEME_NIGHT_BEDROOM: DEFAULT_SYSTEM_CONFIG.cool_control_schemes[SCHEME_NIGHT_BEDROOM],
    SCHEME_DAY_LIVING: ControlScheme(name=SCHEME_DAY_LIVING, enable_outside=22.0, continue_until=20.0, ideal_target=21.0),
    SCHEME_DOWNSTAIRS: ControlScheme(name=SCHEME_DOWNSTAIRS, enable_outside=22.5, continue_until=20.5, ideal_target=21.5),
    SCHEME_DINING_BASIC: ControlScheme(name=SCHEME_DINING_BASIC, enable_outside=16.0, continue_until=13.0, ideal_target=15.0),
    SCHEME_BEDROOM: ControlScheme(name=SCHEME_BEDROOM, enable_outside=16.0, continue_until=12.0, ideal_target=14.0),
    SCHEME_BATHROOM: ControlScheme(name=SCHEME_BATHROOM, enable_outside=16.0, continue_until=12.0, ideal_target=14.0),
}

TEST_SYSTEM_CONFIG = SystemConfig(
    house_temperature_sensor=DEFAULT_SYSTEM_CONFIG.house_temperature_sensor,
    comfort_mode_entity=DEFAULT_SYSTEM_CONFIG.comfort_mode_entity,
    hvac_mode_entity=DEFAULT_SYSTEM_CONFIG.hvac_mode_entity,
    climate_entity=DEFAULT_SYSTEM_CONFIG.climate_entity,
    zones=DEFAULT_SYSTEM_CONFIG.zones,
    zone_comfort_mode_entities=DEFAULT_SYSTEM_CONFIG.zone_comfort_mode_entities,
    comfort_modes=DEFAULT_SYSTEM_CONFIG.comfort_modes,
    heat_control_schemes=TEST_HEAT_CONTROL_SCHEMES,
    cool_control_schemes=TEST_COOL_CONTROL_SCHEMES,
    zone_comfort_adjustment_entities=DEFAULT_SYSTEM_CONFIG.zone_comfort_adjustment_entities,
    global_setpoint_adjustment_entity=DEFAULT_SYSTEM_CONFIG.global_setpoint_adjustment_entity,
)


def build_behavior_snapshot(
    reader,
    *,
    last_switch_changes=None,
    pending_switch_states=None,
    heat_sink_available=False,
    battery_free_power_boost_available=False,
    free_power_later_available=False,
    free_power_heat_soak_level=POWERDAY_HEATSOAK_FULL,
    poweroff_active=False,
    now=None,
):
    return build_snapshot(
        reader,
        config=TEST_SYSTEM_CONFIG,
        last_switch_changes=last_switch_changes,
        pending_switch_states=pending_switch_states,
        heat_sink_available=heat_sink_available,
        battery_free_power_boost_available=battery_free_power_boost_available,
        free_power_later_available=free_power_later_available,
        free_power_heat_soak_level=free_power_heat_soak_level,
        poweroff_active=poweroff_active,
        now=now,
    )


def powerday_downstairs_priority_state_map(**overrides):
    return base_state_map(
        **{
            "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
            "input_select.temptamer_comfort_mode_downstairs": "Auto",
            GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "0",
            "sensor.office_average_temperature": "24.0",
            "sensor.average_dining_zone_temp": "24.0",
            "sensor.downstairs_zone_average_temperature": "18.0",
            "sensor.average_bed1_2_zone_temp": "24.0",
            "sensor.average_bed3_4_zone_temp": "24.0",
            "switch.wt32_hpctrl_e8dbd0_office": "on",
            "switch.wt32_hpctrl_e8dbd0_dining": "on",
            "switch.roof_wt32_hpctrl_e8dbd0_downstairs": "off",
            "switch.wt32_hpctrl_e8dbd0_bed_12": "on",
            "switch.wt32_hpctrl_e8dbd0_bed_34": "on",
            **overrides,
        }
    )


class ComfortAdjustmentTests(unittest.TestCase):
    def calculate(
        self,
        state_overrides=None,
        attr_overrides=None,
        cover_facades=None,
        reference_zone_targets=None,
        now=None,
        config=None,
        **calculation_options,
    ):
        state_overrides = state_overrides or {}
        attr_overrides = attr_overrides or {}
        return calculate_comfort_adjustments(
            FakeReader(
                comfort_adjustment_state_map(**state_overrides),
                comfort_adjustment_attr_map(**attr_overrides),
            ),
            config=DEFAULT_COMFORT_ADJUSTMENT_CONFIG if config is None else config,
            cover_facades=COMFORT_ADJUSTMENT_COVER_FACADES if cover_facades is None else cover_facades,
            reference_zone_targets=reference_zone_targets,
            now=now,
            **calculation_options,
        )

    def test_uses_comfort_weighted_room_operative_adjustments(self):
        result = self.calculate()

        self.assertEqual(result.zone_temperatures["downstairs"], 21.0)
        self.assertEqual(result.zone_temperatures["bedroom_1_2"], 21.0)
        self.assertEqual(result.zone_temperatures["bedroom_3_4"], 21.0)
        self.assertEqual(result.zone_temperatures["office"], 21.0)
        self.assertEqual(result.zone_temperatures["dining"], 21.0)
        self.assertEqual(result.reference_temperatures["office"], 20.0)
        self.assertEqual(result.adjustments["downstairs"], -1.0)
        self.assertEqual(result.adjustments["bedroom_1_2"], -0.9)
        self.assertEqual(result.adjustments["bedroom_3_4"], -0.9)
        self.assertEqual(result.adjustments["office"], -0.9)
        self.assertEqual(result.adjustments["dining"], -0.9)
        self.assertEqual(result.zone_diagnostics["bedroom_1_2"]["room_aggregation"], "comfort_weighted_mean")

    def test_humidex_adjustment_matches_known_warm_weather_value(self):
        self.assertAlmostEqual(calculate_humidex_adjustment(23.0, 50.0), 2.2459, places=4)

    def test_zone_humidity_uses_local_sensor_then_house_fallback(self):
        result = self.calculate(
            state_overrides={
                "sensor.rumpus_white_clock_humidity": "50.0",
                "sensor.air_monitor_lite_c705_humidity": "55.0",
                "sensor.climate_indoor_humidity": "65.0",
            }
        )

        self.assertEqual(result.zone_humidities["downstairs"], 50.0)
        self.assertEqual(result.zone_humidity_sources["downstairs"], "zone_sensor")
        self.assertEqual(
            result.zone_humidity_entity_ids["downstairs"],
            "sensor.rumpus_white_clock_humidity",
        )
        self.assertEqual(result.zone_humidities["office"], 55.0)
        self.assertEqual(result.zone_humidity_sources["office"], "zone_sensor")
        self.assertEqual(result.zone_humidities["dining"], 65.0)
        self.assertEqual(result.zone_humidity_sources["dining"], "house_fallback")
        self.assertEqual(
            result.zone_humidity_entity_ids["dining"],
            "sensor.climate_indoor_humidity",
        )
        self.assertAlmostEqual(
            result.humidity_adjustments["office"],
            calculate_humidex_adjustment(21.0, 55.0),
        )
        self.assertAlmostEqual(
            result.raw_adjustments["office"],
            result.envelope_adjustments["office"]
            + result.solar_adjustments["office"]
            + result.fabric_solar_scores["office"]
            + result.humidity_adjustments["office"],
        )

    def test_humidity_composition_is_clamped_and_rounded_to_expanded_score_range(self):
        humid = self.calculate(
            state_overrides={
                "sensor.office_average_temperature": "23.0",
                "sensor.air_monitor_lite_c705_humidity": "100.0",
            }
        )
        dry = self.calculate(
            state_overrides={
                "sensor.office_average_temperature": "20.0",
                "sensor.air_monitor_lite_c705_humidity": "0.0",
            }
        )

        self.assertEqual(humid.raw_adjustments["office"], 3.0)
        self.assertEqual(humid.adjustments["office"], 3.0)
        self.assertEqual(dry.raw_adjustments["office"], -3.0)
        self.assertEqual(dry.adjustments["office"], -3.0)

    def test_humidex_contribution_is_the_same_in_heat_and_cool_modes(self):
        state_overrides = {
            "sensor.office_average_temperature": "23.0",
            "sensor.air_monitor_lite_c705_humidity": "50.0",
        }
        heating = self.calculate(state_overrides=state_overrides)
        cooling = self.calculate(
            state_overrides={
                **state_overrides,
                "input_select.heatpump_mode_user": "Cool",
                TEST_CLIMATE_ENTITY: "cool",
            }
        )

        self.assertAlmostEqual(heating.humidity_adjustments["office"], 2.2459, places=4)
        self.assertEqual(
            cooling.humidity_adjustments["office"],
            heating.humidity_adjustments["office"],
        )

    def test_warm_day_coefficients_produce_three_quarters_envelope_and_one_quarter_solar_effect(self):
        result = self.calculate(
            state_overrides={
                "sensor.gw3000c_outdoor_temperature": "23.0",
                "sensor.gw3000c_solar_radiation": "600.0",
            },
            attr_overrides={"sun.sun": {"elevation": 30.0, "azimuth": 180.0}},
            reference_zone_targets={"office": (20.0, 20.0)},
        )

        self.assertAlmostEqual(result.envelope_adjustments["office"], 1.5, places=2)
        self.assertEqual(result.solar_adjustments["office"], 0.5)
        office_room = result.zone_diagnostics["office"]["room_values"][0]
        self.assertEqual(office_room["wall_surface_resistance"], 0.415)
        self.assertEqual(
            office_room["windows"][0]["surface_resistance"],
            0.415,
        )

        cool_day = self.calculate(
            state_overrides={"sensor.gw3000c_outdoor_temperature": "19.0"},
            reference_zone_targets={"office": (20.0, 20.0)},
        )
        cool_room = cool_day.zone_diagnostics["office"]["room_values"][0]
        self.assertEqual(cool_room["wall_surface_resistance"], 0.12)
        self.assertEqual(cool_room["windows"][0]["surface_resistance"], 0.12)

        midpoint = self.calculate(
            state_overrides={"sensor.gw3000c_outdoor_temperature": "21.5"},
            reference_zone_targets={"office": (20.0, 20.0)},
        )
        midpoint_room = midpoint.zone_diagnostics["office"]["room_values"][0]
        self.assertAlmostEqual(midpoint_room["wall_surface_resistance"], 0.2675)

    def test_invalid_or_stale_local_humidity_falls_back_to_house(self):
        now = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
        result = self.calculate(
            state_overrides={
                "sensor.rumpus_white_clock_humidity": "101.0",
                "sensor.air_monitor_lite_c705_humidity": "50.0",
                "sensor.climate_indoor_humidity": "60.0",
            },
            attr_overrides={
                "sensor.air_monitor_lite_c705_humidity": {
                    "last_updated": now - timedelta(seconds=DEFAULT_COMFORT_ADJUSTMENT_CONFIG.input_stale_after_seconds + 1)
                }
            },
            now=now,
        )

        self.assertEqual(result.zone_humidities["downstairs"], 60.0)
        self.assertEqual(result.zone_humidity_sources["downstairs"], "house_fallback")
        self.assertIn(
            "sensor.rumpus_white_clock_humidity:rejected_implausible",
            result.zone_diagnostics["downstairs"]["input_issues"],
        )
        self.assertEqual(result.zone_humidities["office"], 60.0)
        self.assertEqual(result.zone_humidity_sources["office"], "house_fallback")
        self.assertIn(
            "sensor.air_monitor_lite_c705_humidity:stale",
            result.zone_diagnostics["office"]["input_issues"],
        )

        nonnumeric = self.calculate(
            state_overrides={
                "sensor.air_monitor_lite_c705_humidity": "not-a-number",
                "sensor.climate_indoor_humidity": "58.0",
            }
        )
        self.assertEqual(nonnumeric.zone_humidities["office"], 58.0)
        self.assertEqual(nonnumeric.zone_humidity_sources["office"], "house_fallback")
        self.assertIn(
            "sensor.air_monitor_lite_c705_humidity:rejected_non_finite_or_not_numeric",
            nonnumeric.zone_diagnostics["office"]["input_issues"],
        )

    def test_missing_humidity_contributes_zero_without_invalidating_score(self):
        result = self.calculate()

        self.assertIsNone(result.zone_humidities["office"])
        self.assertEqual(result.zone_humidity_sources["office"], "unavailable")
        self.assertIsNone(result.zone_humidity_entity_ids["office"])
        self.assertEqual(result.humidity_adjustments["office"], 0.0)
        self.assertTrue(result.calculation_validity["office"])
        self.assertEqual(result.zone_diagnostics["office"]["humidity_adjustment"], 0.0)

    def test_temperature_gap_uses_unadjusted_zone_target_but_mode_uses_room_temperature(self):
        result = self.calculate(
            {
                "input_select.heatpump_mode_user": "HeatCool",
                "sensor.office_average_temperature": "22.4",
                "sensor.gw3000c_outdoor_temperature": "16.3",
                "sensor.gw3000c_solar_radiation": "75.0",
            },
            reference_zone_targets={"office": (19.7, 20.5)},
        )

        self.assertEqual(result.operating_modes["office"], "heat")
        self.assertEqual(result.reference_temperatures["office"], 19.7)
        self.assertAlmostEqual(result.raw_adjustments["office"], -0.314, places=3)
        self.assertEqual(result.adjustments["office"], -0.3)

    def test_office_comfort_score_combines_inverse_operative_terms_and_filtered_fabric_solar(self):
        result = self.calculate(
            {
                "sensor.gw3000c_outdoor_temperature": "16.3",
                "sensor.gw3000c_solar_radiation": "90.0",
            },
            reference_zone_targets={"office": (19.7, 20.5)},
            filtered_solar_irradiances={"office": 90.0},
        )

        self.assertLess(result.envelope_adjustments["office"], 0.0)
        self.assertGreaterEqual(result.solar_adjustments["office"], 0.0)
        self.assertAlmostEqual(result.fabric_solar_scores["office"], 0.315)
        self.assertEqual(result.fabric_solar_scores["dining"], 0.0)
        self.assertAlmostEqual(
            result.raw_adjustments["office"],
            result.envelope_adjustments["office"]
            + result.solar_adjustments["office"]
            + result.fabric_solar_scores["office"],
        )

        capped = self.calculate(filtered_solar_irradiances={"office": 125.0})
        dim = self.calculate(filtered_solar_irradiances={"office": 25.0})
        self.assertEqual(capped.fabric_solar_scores["office"], 0.4)
        self.assertAlmostEqual(dim.fabric_solar_scores["office"], 0.0225)

    def test_uses_one_global_operating_mode_and_reports_its_source(self):
        result = self.calculate(
            {
                "input_select.heatpump_mode_user": "HeatCool",
                TEST_CLIMATE_ENTITY: "heat",
                "sensor.home_temperature": "20.0",
                "sensor.gw3000c_outdoor_temperature": "20.0",
                "sensor.office_average_temperature": "12.0",
                "sensor.dining_average_temperature": "30.0",
                "sensor.lego_room_average_temperature": "30.0",
            }
        )

        self.assertEqual(result.operating_mode, "heat")
        self.assertEqual(result.operating_mode_source, "configured_hvac_mode")
        self.assertEqual(set(result.operating_modes.values()), {"heat"})

    def test_controller_heat_selection_overrides_cached_cool_mode(self):
        now = datetime(2026, 9, 24, 11, 23, tzinfo=timezone.utc)
        result = self.calculate(
            state_overrides={
                "input_select.heatpump_mode_user": "HeatCool",
                "input_select.temptamer_hvac_mode": "Heat",
                TEST_CLIMATE_ENTITY: "off",
            },
            now=now,
            last_valid_operating_mode="cool",
            last_valid_operating_mode_at=now - timedelta(minutes=1),
        )

        self.assertEqual(result.operating_mode, "heat")
        self.assertEqual(result.operating_mode_source, "controller_hvac_mode")
        self.assertEqual(set(result.operating_modes.values()), {"heat"})

    def test_operative_diagnostics_expose_components_room_weights_and_physical_shutter(self):
        result = self.calculate(
            {"sensor.gw3000c_solar_radiation": "600"},
            {"cover.officeshutters": {"current_position": 50.0}},
        )
        office_room = result.zone_diagnostics["office"]["room_values"][0]

        self.assertAlmostEqual(
            result.raw_adjustments["office"],
            result.envelope_adjustments["office"] + result.solar_adjustments["office"],
        )
        self.assertEqual(result.effective_window_outdoor_temperatures["office"], 11.0)
        self.assertEqual(result.effective_wall_outdoor_temperatures["office"], 11.0)
        self.assertEqual(office_room["temperature_weight"], 1.0)
        self.assertEqual(office_room["comfort_weight"], 1.0)
        self.assertAlmostEqual(office_room["window_k"], 0.14436, places=5)
        self.assertAlmostEqual(office_room["wall_k"], 0.027, places=5)
        self.assertGreater(office_room["operative_denominator"], 0.55)
        window = office_room["windows"][0]
        self.assertEqual(window["cover_state"], "partial")
        self.assertAlmostEqual(window["effective_u_value"], 6.0151, places=4)
        self.assertAlmostEqual(window["effective_direct_shgc"], 0.3927, places=4)
        self.assertEqual(window["effective_u_value_model"], "physical_shutter_resistance")

    def test_all_rooms_have_static_envelopes_and_missing_sensor_keeps_its_room_contribution(self):
        for zone in DEFAULT_COMFORT_ADJUSTMENT_CONFIG.zones:
            for room in zone.rooms:
                self.assertIsNotNone(room.envelope)
                self.assertGreater(room.envelope.comfort_weight, 0.0)
                self.assertTrue(room.envelope.windows)
                self.assertIn(room.envelope.construction_profile, DEFAULT_COMFORT_ADJUSTMENT_CONFIG.construction_profiles)

        result = self.calculate(
            {
                "sensor.bathroom_average_temperature": "unavailable",
                "sensor.bathroom_motion_temperature": "unavailable",
            }
        )
        bathroom = result.zone_diagnostics["bedroom_3_4"]["room_values"][2]
        self.assertIsNone(bathroom["temperature"])
        self.assertEqual(bathroom["temperature_weight"], 0.0)
        self.assertEqual(bathroom["comfort_weight"], 0.2)
        self.assertIn("room_raw_adjustment", bathroom)

    def test_window_and_wall_temperature_drives_are_separate(self):
        same_temperature = self.calculate(
            {"sensor.gw3000c_solar_radiation": "75"},
            effective_window_outdoor_temperatures={"office": 11.0},
            effective_wall_outdoor_temperatures={"office": 11.0},
        )
        warmer_wall = self.calculate(
            {"sensor.gw3000c_solar_radiation": "75"},
            effective_window_outdoor_temperatures={"office": 11.0},
            effective_wall_outdoor_temperatures={"office": 17.0},
        )
        room = warmer_wall.zone_diagnostics["office"]["room_values"][0]

        self.assertEqual(warmer_wall.effective_window_outdoor_temperatures["office"], 11.0)
        self.assertEqual(warmer_wall.effective_wall_outdoor_temperatures["office"], 17.0)
        self.assertGreater(warmer_wall.envelope_adjustments["office"], same_temperature.envelope_adjustments["office"])
        self.assertGreater(room["window_envelope_adjustment"], room["wall_envelope_adjustment"])

    def test_closed_shutter_reduces_only_its_window_conductive_and_solar_terms(self):
        state_overrides = {"sensor.gw3000c_solar_radiation": "600"}
        south_sun = {"sun.sun": {"elevation": 30.0, "azimuth": 180.0}}
        open_result = self.calculate(
            state_overrides,
            {"cover.officeshutters": {"current_position": 100.0}, **south_sun},
        )
        closed_result = self.calculate(
            state_overrides,
            {"cover.officeshutters": {"current_position": 0.0}, **south_sun},
        )
        open_window = open_result.zone_diagnostics["office"]["room_values"][0]["windows"][0]
        closed_window = closed_result.zone_diagnostics["office"]["room_values"][0]["windows"][0]

        self.assertAlmostEqual(closed_window["effective_u_value"], 5.1301, places=4)
        self.assertLess(closed_window["effective_u_value"], open_window["effective_u_value"])
        self.assertLess(closed_window["effective_direct_shgc"], open_window["effective_direct_shgc"])
        self.assertLess(abs(closed_result.solar_adjustments["office"]), abs(open_result.solar_adjustments["office"]))
        self.assertGreater(closed_result.envelope_adjustments["office"], open_result.envelope_adjustments["office"])

    def test_low_sun_retains_direct_beam_and_zeros_solar_below_horizon(self):
        result = self.calculate(
            {
                "sensor.gw3000c_solar_radiation": "600",
                "input_number.awning_min_sun_elevation": "0",
            },
            {
                "cover.officeshutters": {"current_position": 100.0},
                "sun.sun": {"elevation": 5.0, "azimuth": 180.0},
            },
        )
        window = result.zone_diagnostics["office"]["room_values"][0]["windows"][0]
        self.assertEqual(
            result.zone_diagnostics["office"]["room_values"][0]["solar_irradiance_source"],
            "global_horizontal_irradiance_estimate",
        )
        self.assertGreater(window["direct_irradiance"], 0.0)
        self.assertLessEqual(
            window["direct_irradiance"],
            DEFAULT_COMFORT_ADJUSTMENT_CONFIG.solar_maximum_direct_normal_irradiance,
        )
        self.assertLessEqual(result.solar_adjustments["office"], 0.5)

        below_horizon = self.calculate(
            {
                "sensor.gw3000c_solar_radiation": "600",
                "input_number.awning_min_sun_elevation": "-2",
            },
            {
                "cover.officeshutters": {"current_position": 100.0},
                "sun.sun": {"elevation": -1.0, "azimuth": 180.0},
            },
            filtered_solar_irradiances={"office": 90.0},
        )
        below_window = below_horizon.zone_diagnostics["office"]["room_values"][0]["windows"][0]
        self.assertEqual(below_window["direct_irradiance"], 0.0)
        self.assertEqual(
            below_horizon.zone_diagnostics["office"]["room_values"][0]["solar_irradiance_source"],
            "below_horizon",
        )
        self.assertEqual(below_horizon.fabric_solar_scores["office"], 0.0)

    def test_legacy_calculation_model_remains_an_immediate_rollback(self):
        legacy = self.calculate(config=replace(DEFAULT_COMFORT_ADJUSTMENT_CONFIG, calculation_model="legacy"))
        operative = self.calculate()

        self.assertTrue(legacy.calculation_validity["office"])
        self.assertIn("legacy_envelope_transmission", legacy.zone_diagnostics["office"]["room_values"][0])
        self.assertNotEqual(legacy.raw_adjustments["office"], operative.raw_adjustments["office"])

    def test_invalid_envelope_configuration_is_rejected_for_last_valid_runtime_handling(self):
        configured_zones = list(DEFAULT_COMFORT_ADJUSTMENT_CONFIG.zones)
        office_index = next(index for index, zone in enumerate(configured_zones) if zone.key == "office")
        office_zone = configured_zones[office_index]
        office_room = office_zone.rooms[0]
        invalid_window = replace(office_room.envelope.windows[0], u_value=0.0)
        invalid_envelope = replace(office_room.envelope, windows=(invalid_window,))
        configured_zones[office_index] = replace(office_zone, rooms=(replace(office_room, envelope=invalid_envelope),))
        invalid_config = replace(DEFAULT_COMFORT_ADJUSTMENT_CONFIG, zones=tuple(configured_zones))

        result = self.calculate(config=invalid_config)

        self.assertFalse(result.calculation_validity["office"])
        self.assertEqual(result.adjustments["office"], 0.0)
        self.assertEqual(result.zone_diagnostics["office"]["calculation_status"], "invalid_operative_calculation")

    def test_rejects_non_finite_implausible_and_stale_temperatures(self):
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        result = self.calculate(
            {
                "sensor.home_temperature": "nan",
                "sensor.office_average_temperature": "100.0",
                "sensor.gw3000c_outdoor_temperature": "inf",
            },
            {
                "weather.epping": {"temperature": "90.0"},
            },
            now=now,
        )

        self.assertFalse(result.calculation_validity["office"])
        self.assertEqual(result.adjustments["office"], 0.0)
        self.assertIn("sensor.office_average_temperature:rejected_implausible", result.zone_diagnostics["office"]["input_issues"])
        self.assertIn("sensor.gw3000c_outdoor_temperature:rejected_non_finite_or_not_numeric", result.global_input_issues)
        self.assertIn("weather.epping:rejected_implausible", result.global_input_issues)

        stale = self.calculate(
            attr_overrides={
                "sensor.office_average_temperature": {"last_updated": "2026-08-24T10:00:00+00:00"},
            },
            now=now,
        )
        self.assertIn("sensor.office_average_temperature:stale", stale.zone_diagnostics["office"]["input_issues"])

    def test_room_source_and_zone_fallbacks_exclude_unusable_temperatures(self):
        result = self.calculate(
            {
                "sensor.rumpus_average_temperature": "unavailable",
                "sensor.develco_products_a_s_moszb_140_temperature_2": "20.0",
                "sensor.bedroom_3_average_temperature": "unknown",
                "sensor.bedroom_4_average_temperature": "unknown",
                "sensor.bathroom_average_temperature": "unknown",
                "sensor.bathroom_motion_temperature": "unknown",
                "sensor.average_bed3_4_zone_temp": "19.5",
                "sensor.office_average_temperature": "unknown",
                "sensor.home_temperature": "20.0",
            }
        )

        self.assertEqual(result.zone_temperatures["downstairs"], 21.0)
        self.assertEqual(result.zone_temperatures["bedroom_3_4"], 19.5)
        self.assertEqual(result.zone_temperatures["office"], 20.0)

    def test_missing_room_temperature_does_not_remove_static_envelope_contribution(self):
        no_indoor = self.calculate(
            {
                "sensor.office_average_temperature": "unavailable",
                "sensor.home_temperature": "unknown",
            }
        )
        self.assertTrue(no_indoor.calculation_validity["office"])
        self.assertLess(no_indoor.adjustments["office"], 0.0)
        self.assertIsNone(no_indoor.zone_temperatures["office"])

        no_outdoor = self.calculate(
            {
                "sensor.gw3000c_outdoor_temperature": "unavailable",
            },
            {"weather.epping": {"temperature": "unknown"}},
        )
        self.assertEqual(set(no_outdoor.adjustments.values()), {0.0})

    def test_operating_mode_precedence_and_deadband(self):
        self.assertEqual(
            resolve_comfort_adjustment_operating_mode(
                user_mode="Cool",
                hvac_action="heating",
                configured_hvac_mode="heat",
                zone_temperature=20.0,
                outdoor_temperature=10.0,
            ),
            "cool",
        )
        self.assertEqual(
            resolve_comfort_adjustment_operating_mode(
                user_mode="HeatCool",
                hvac_action="cooling",
                configured_hvac_mode="heat",
                zone_temperature=20.0,
                outdoor_temperature=10.0,
            ),
            "cool",
        )
        self.assertEqual(
            resolve_comfort_adjustment_operating_mode(
                user_mode=None,
                hvac_action="idle",
                configured_hvac_mode="cool",
                zone_temperature=20.0,
                outdoor_temperature=20.0,
            ),
            "cool",
        )
        self.assertEqual(
            resolve_comfort_adjustment_operating_mode(
                user_mode=None,
                hvac_action="idle",
                configured_hvac_mode="heat",
                zone_temperature=20.0,
                outdoor_temperature=20.0,
            ),
            "heat",
        )
        self.assertEqual(
            resolve_comfort_adjustment_operating_mode(
                user_mode=None,
                hvac_action="idle",
                configured_hvac_mode="off",
                zone_temperature=20.0,
                outdoor_temperature=20.1,
            ),
            "cool",
        )

        self.assertEqual(
            resolve_comfort_adjustment_operating_mode(
                user_mode=None,
                hvac_action="idle",
                configured_hvac_mode="off",
                last_valid_mode="heat",
                last_valid_mode_at=datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc),
                now=datetime(2026, 8, 24, 12, 1, tzinfo=timezone.utc),
                last_valid_mode_hold_seconds=5 * 60,
                zone_temperature=20.0,
                outdoor_temperature=25.0,
            ),
            "heat",
        )
        self.assertEqual(
            resolve_comfort_adjustment_operating_mode(
                user_mode=None,
                hvac_action="idle",
                configured_hvac_mode="off",
                last_valid_mode="heat",
                last_valid_mode_at=datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc),
                now=datetime(2026, 8, 24, 12, 6, tzinfo=timezone.utc),
                last_valid_mode_hold_seconds=5 * 60,
                zone_temperature=20.0,
                outdoor_temperature=25.0,
            ),
            "cool",
        )

    def test_solar_index_uses_measurement_or_weather_elevation_fallback(self):
        measured = resolve_solar_index(
            FakeReader(
                comfort_adjustment_state_map(**{"sensor.gw3000c_solar_radiation": "600"}),
                comfort_adjustment_attr_map(),
            ),
            DEFAULT_COMFORT_ADJUSTMENT_CONFIG,
        )
        fallback = resolve_solar_index(
            FakeReader(
                comfort_adjustment_state_map(**{"sensor.gw3000c_solar_radiation": "unavailable"}),
                comfort_adjustment_attr_map(),
            ),
            DEFAULT_COMFORT_ADJUSTMENT_CONFIG,
        )
        below_horizon = resolve_solar_index(
            FakeReader(
                comfort_adjustment_state_map(**{"sensor.gw3000c_solar_radiation": "600"}),
                comfort_adjustment_attr_map(**{"sun.sun": {"elevation": -1.0, "azimuth": 90.0}}),
            ),
            DEFAULT_COMFORT_ADJUSTMENT_CONFIG,
        )

        self.assertEqual(measured, 1.0)
        self.assertAlmostEqual(fallback, 0.5)
        self.assertEqual(below_horizon, 0.0)

    def test_shutter_position_and_facade_exposure_change_solar_adjustment(self):
        state_overrides = {"sensor.gw3000c_solar_radiation": "600"}
        attr_overrides = {"cover.officeshutters": {"current_position": 50.0}}
        exposed = self.calculate(
            state_overrides,
            {
                "cover.officeshutters": {"current_position": 50.0},
                "sun.sun": {"elevation": 30.0, "azimuth": 180.0},
            },
        )
        tapered = self.calculate(
            state_overrides,
            {
                "cover.officeshutters": {"current_position": 50.0},
                "sun.sun": {"elevation": 30.0, "azimuth": 157.5},
            },
        )
        band_edge = self.calculate(
            state_overrides,
            {
                "cover.officeshutters": {"current_position": 50.0},
                "sun.sun": {"elevation": 30.0, "azimuth": 135.0},
            },
        )
        self.assertEqual(exposed.adjustments["office"], -0.3)
        self.assertEqual(tapered.adjustments["office"], -0.5)
        self.assertEqual(band_edge.adjustments["office"], -0.8)
        self.assertGreater(exposed.solar_adjustments["office"], tapered.solar_adjustments["office"])
        self.assertGreater(tapered.solar_adjustments["office"], band_edge.solar_adjustments["office"])
        exposed_window = exposed.zone_diagnostics["office"]["room_values"][0]["windows"][0]
        tapered_window = tapered.zone_diagnostics["office"]["room_values"][0]["windows"][0]
        self.assertAlmostEqual(exposed_window["direct_incidence_factor"], 0.8660, places=4)
        self.assertAlmostEqual(tapered_window["direct_incidence_factor"], 0.8001, places=4)
        self.assertAlmostEqual(tapered_window["awning_shading_factor"], 0.5)

    def test_missing_cover_label_treats_shuttered_window_as_unexposed(self):
        state_overrides = {"sensor.gw3000c_solar_radiation": "600"}
        attr_overrides = {
            "cover.officeshutters": {"current_position": 100.0},
            "sun.sun": {"elevation": 30.0, "azimuth": 180.0},
        }
        labelled = self.calculate(state_overrides, attr_overrides)
        missing_labels = dict(COMFORT_ADJUSTMENT_COVER_FACADES)
        missing_labels["cover.officeshutters"] = None
        unexposed = self.calculate(state_overrides, attr_overrides, missing_labels)
        window = unexposed.zone_diagnostics["office"]["room_values"][0]["windows"][0]

        self.assertIsNone(window["facade"])
        self.assertEqual(window["facade_source"], "cover_device_label")
        self.assertGreater(labelled.solar_adjustments["office"], unexposed.solar_adjustments["office"])

    def test_window_and_wall_filters_have_separate_thermal_responses(self):
        window_effective_temperature = filter_outdoor_temperature(
            raw_temperature=10.1,
            previous_temperature=10.0,
            elapsed_seconds=120.0,
            time_constant_seconds=DEFAULT_COMFORT_ADJUSTMENT_CONFIG.window_outdoor_filter_time_constant_seconds,
        )
        upstairs_wall_temperature = filter_outdoor_temperature(
            raw_temperature=10.1,
            previous_temperature=10.0,
            elapsed_seconds=120.0,
            time_constant_seconds=DEFAULT_COMFORT_ADJUSTMENT_CONFIG.construction_profiles[
                "upstairs_brick_veneer"
            ].wall_filter_time_constant_seconds,
        )
        downstairs_wall_temperature = filter_outdoor_temperature(
            raw_temperature=10.1,
            previous_temperature=10.0,
            elapsed_seconds=120.0,
            time_constant_seconds=DEFAULT_COMFORT_ADJUSTMENT_CONFIG.construction_profiles[
                "downstairs_double_brick"
            ].wall_filter_time_constant_seconds,
        )

        self.assertAlmostEqual(window_effective_temperature, 10.01248, places=4)
        self.assertAlmostEqual(upstairs_wall_temperature, 10.0011, places=4)
        self.assertAlmostEqual(downstairs_wall_temperature, 10.0004, places=4)
        self.assertLess(downstairs_wall_temperature, upstairs_wall_temperature)
        self.assertLess(upstairs_wall_temperature, window_effective_temperature)

    def test_output_hysteresis_holds_changes_close_to_a_rounding_boundary(self):
        self.assertEqual(apply_adjustment_hysteresis(0.56, 0.5, 0.025), 0.5)
        self.assertEqual(apply_adjustment_hysteresis(0.58, 0.5, 0.025), 0.6)
        self.assertEqual(apply_adjustment_hysteresis(0.42, 0.5, 0.025), 0.4)

    def test_adjustment_trigger_inputs_exclude_output_input_numbers(self):
        self.assertIn("input_select.heatpump_mode_user", COMFORT_ADJUSTMENT_TRIGGER_ENTITIES)
        self.assertIn("input_select.temptamer_hvac_mode", COMFORT_ADJUSTMENT_TRIGGER_ENTITIES)
        self.assertIn(f"{TEST_CLIMATE_ENTITY}.hvac_action", COMFORT_ADJUSTMENT_TRIGGER_ENTITIES)
        self.assertIn("cover.officeshutters.current_position", COMFORT_ADJUSTMENT_TRIGGER_ENTITIES)
        self.assertIn("sensor.climate_indoor_humidity", COMFORT_ADJUSTMENT_TRIGGER_ENTITIES)
        self.assertIn("sensor.rumpus_white_clock_humidity", COMFORT_ADJUSTMENT_TRIGGER_ENTITIES)
        self.assertIn("sensor.air_monitor_lite_c705_humidity", COMFORT_ADJUSTMENT_TRIGGER_ENTITIES)
        for zone in DEFAULT_COMFORT_ADJUSTMENT_CONFIG.zones:
            self.assertNotIn(zone.output_entity_id, COMFORT_ADJUSTMENT_TRIGGER_ENTITIES)
            self.assertIn(zone.output_entity_id, NORMAL_RECALCULATION_TRIGGER_ENTITIES)
            self.assertNotIn(zone.output_entity_id, IMMEDIATE_RECONCILIATION_TRIGGER_ENTITIES)

    def test_bedroom_1_fallback_facade_matches_its_south_awning_label(self):
        bedroom_1_2 = next(
            zone for zone in DEFAULT_COMFORT_ADJUSTMENT_CONFIG.zones if zone.key == "bedroom_1_2"
        )
        bedroom_1_window = bedroom_1_2.rooms[0].envelope.windows[0]
        cover_facades = dict(COMFORT_ADJUSTMENT_COVER_FACADES)
        cover_facades.pop("cover.bed1shutters")

        result = self.calculate(cover_facades=cover_facades)
        resolved_window = result.zone_diagnostics["bedroom_1_2"]["room_values"][0]["windows"][0]

        self.assertEqual(bedroom_1_window.facade, "s")
        self.assertEqual(resolved_window["facade"], "s")
        self.assertEqual(resolved_window["facade_source"], "configured_fallback")


class ComfortAdjustmentRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.original_state_values = dict(temptamer_main.state._values)
        self.original_state_attrs = deepcopy(temptamer_main.state._attrs)
        self.original_runtime_state = deepcopy(temptamer_main.RUNTIME_STATE)
        self.original_service_call = temptamer_main.service.call

    def tearDown(self):
        temptamer_main.state._values = dict(self.original_state_values)
        temptamer_main.state._attrs = dict(self.original_state_attrs)
        temptamer_main.RUNTIME_STATE.clear()
        temptamer_main.RUNTIME_STATE.update(self.original_runtime_state)
        temptamer_main.service.call = self.original_service_call

    def test_cover_facades_are_read_from_cover_device_labels(self):
        entity_entries = SimpleNamespace(
            async_get=lambda _entity_id: SimpleNamespace(device_id="cover-device"),
        )
        device_entries = SimpleNamespace(
            async_get=lambda _device_id: SimpleNamespace(labels={"awning_e"}),
        )
        entity_registry_module = ModuleType("homeassistant.helpers.entity_registry")
        entity_registry_module.async_get = lambda _hass: entity_entries
        device_registry_module = ModuleType("homeassistant.helpers.device_registry")
        device_registry_module.async_get = lambda _hass: device_entries
        helpers_module = ModuleType("homeassistant.helpers")
        helpers_module.entity_registry = entity_registry_module
        helpers_module.device_registry = device_registry_module
        homeassistant_module = ModuleType("homeassistant")
        homeassistant_module.helpers = helpers_module

        with (
            patch.object(temptamer_main, "hass", object(), create=True),
            patch.dict(
                sys.modules,
                {
                    "homeassistant": homeassistant_module,
                    "homeassistant.helpers": helpers_module,
                    "homeassistant.helpers.device_registry": device_registry_module,
                    "homeassistant.helpers.entity_registry": entity_registry_module,
                },
            ),
        ):
            facades = temptamer_main._resolve_cover_facades()

        self.assertEqual(set(facades.values()), {"e"})

    def test_runtime_uses_separate_filters_and_reports_restart_warmup(self):
        temptamer_main.RUNTIME_STATE["comfort_adjustment_outdoor_filter_values"] = {}
        temptamer_main.RUNTIME_STATE["comfort_adjustment_outdoor_filter_updated_at"] = {}
        temptamer_main.RUNTIME_STATE["comfort_adjustment_outdoor_filter_seeded_at"] = {}
        first_now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        second_now = first_now + timedelta(minutes=15)
        third_now = first_now + timedelta(hours=2)

        first_window, first_wall, first_warming_up = temptamer_main._resolve_effective_outdoor_temperatures(11.0, first_now)
        second_window, second_wall, second_warming_up = temptamer_main._resolve_effective_outdoor_temperatures(20.0, second_now)
        _third_window, _third_wall, third_warming_up = temptamer_main._resolve_effective_outdoor_temperatures(20.0, third_now)

        self.assertTrue(all(first_warming_up.values()))
        self.assertTrue(any(second_warming_up.values()))
        self.assertFalse(third_warming_up["office"])
        self.assertTrue(third_warming_up["downstairs"])
        self.assertEqual(first_window["office"], 11.0)
        self.assertEqual(first_wall["downstairs"], 11.0)
        self.assertGreater(second_window["office"], second_wall["office"])
        self.assertGreater(second_wall["office"], second_wall["downstairs"])

    def test_fabric_solar_filter_is_configured_and_tracked_per_zone(self):
        first_now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        second_now = first_now + timedelta(minutes=30)
        for key in (
            "comfort_adjustment_fabric_solar_filter_values",
            "comfort_adjustment_fabric_solar_filter_updated_at",
            "comfort_adjustment_fabric_solar_filter_seeded_at",
        ):
            temptamer_main.RUNTIME_STATE[key] = {}
        controller = FakeReader(
            {DEFAULT_COMFORT_ADJUSTMENT_CONFIG.solar_radiation_entity_id: "20.0"}
        )

        first = temptamer_main._resolve_filtered_solar_irradiances(controller, first_now)
        controller.state_map[DEFAULT_COMFORT_ADJUSTMENT_CONFIG.solar_radiation_entity_id] = "100.0"
        second = temptamer_main._resolve_filtered_solar_irradiances(controller, second_now)

        office = next(zone for zone in DEFAULT_COMFORT_ADJUSTMENT_CONFIG.zones if zone.key == "office")
        self.assertIsNotNone(office.fabric_solar)
        self.assertEqual(
            {
                zone.key: (
                    zone.fabric_solar.filter_time_constant_seconds,
                    zone.fabric_solar.irradiance_threshold,
                    zone.fabric_solar.score_coefficient,
                    zone.fabric_solar.score_limit,
                )
                for zone in DEFAULT_COMFORT_ADJUSTMENT_CONFIG.zones
            },
            {
                "downstairs": (120 * 60, 30.0, 0.0015, 0.25),
                "bedroom_1_2": (45 * 60, 30.0, 0.0012, 0.20),
                "bedroom_3_4": (45 * 60, 25.0, 0.0020, 0.30),
                "office": (30 * 60, 20.0, 0.0045, 0.40),
                "dining": (60 * 60, 25.0, 0.0025, 0.40),
            },
        )
        self.assertEqual(first["office"], 20.0)
        self.assertEqual(first["dining"], 20.0)
        self.assertAlmostEqual(second["office"], 70.57, places=2)
        self.assertAlmostEqual(second["downstairs"], 37.70, places=2)
        self.assertAlmostEqual(second["bedroom_1_2"], 58.93, places=2)
        self.assertAlmostEqual(second["bedroom_3_4"], 58.93, places=2)
        self.assertAlmostEqual(second["dining"], 51.48, places=2)

    def test_fabric_solar_filter_discards_stale_samples_and_sunset_memory(self):
        first_now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        stale_now = first_now + timedelta(
            seconds=DEFAULT_COMFORT_ADJUSTMENT_CONFIG.input_stale_after_seconds + 1
        )
        solar_entity = DEFAULT_COMFORT_ADJUSTMENT_CONFIG.solar_radiation_entity_id
        sun_entity = DEFAULT_COMFORT_ADJUSTMENT_CONFIG.sun_entity_id
        controller = FakeReader(
            {solar_entity: "180.0"},
            {
                solar_entity: {"last_updated": first_now},
                sun_entity: {"elevation": 25.0},
            },
        )

        with tempfile.TemporaryDirectory() as temporary_directory, patch.object(
            temptamer_main,
            "COMFORT_FABRIC_SOLAR_FILTER_STATE_FILE",
            str(Path(temporary_directory) / "fabric_solar.json"),
        ):
            temptamer_main.RUNTIME_STATE["comfort_adjustment_fabric_solar_filter_values"] = {}
            temptamer_main.RUNTIME_STATE["comfort_adjustment_fabric_solar_filter_updated_at"] = {}
            temptamer_main.RUNTIME_STATE["comfort_adjustment_fabric_solar_filter_seeded_at"] = {}
            temptamer_main.RUNTIME_STATE["comfort_adjustment_fabric_solar_filter_restore_checked"] = False

            live = temptamer_main._resolve_filtered_solar_irradiances(controller, first_now)
            stale = temptamer_main._resolve_filtered_solar_irradiances(controller, stale_now)

            controller.attr_map[solar_entity]["last_updated"] = stale_now
            controller.attr_map[sun_entity]["elevation"] = -0.1
            below_horizon = temptamer_main._resolve_filtered_solar_irradiances(controller, stale_now)

        self.assertGreater(live["office"], 0.0)
        self.assertTrue(all(value is None for value in stale.values()))
        self.assertTrue(all(value is None for value in below_horizon.values()))
        self.assertEqual(temptamer_main.RUNTIME_STATE["comfort_adjustment_fabric_solar_filter_values"], {})

    def test_fabric_solar_filter_restores_after_a_pyscript_reload(self):
        first_now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        solar_entity = DEFAULT_COMFORT_ADJUSTMENT_CONFIG.solar_radiation_entity_id
        sun_entity = DEFAULT_COMFORT_ADJUSTMENT_CONFIG.sun_entity_id
        controller = FakeReader(
            {solar_entity: "90.0"},
            {
                solar_entity: {"last_updated": first_now},
                sun_entity: {"elevation": 25.0},
            },
        )

        with tempfile.TemporaryDirectory() as temporary_directory, patch.object(
            temptamer_main,
            "COMFORT_FABRIC_SOLAR_FILTER_STATE_FILE",
            str(Path(temporary_directory) / "fabric_solar.json"),
        ):
            temptamer_main.RUNTIME_STATE["comfort_adjustment_fabric_solar_filter_values"] = {}
            temptamer_main.RUNTIME_STATE["comfort_adjustment_fabric_solar_filter_updated_at"] = {}
            temptamer_main.RUNTIME_STATE["comfort_adjustment_fabric_solar_filter_seeded_at"] = {}
            temptamer_main.RUNTIME_STATE["comfort_adjustment_fabric_solar_filter_restore_checked"] = False
            saved = temptamer_main._resolve_filtered_solar_irradiances(controller, first_now)

            # Simulate module reload: RUNTIME_STATE is empty but the state
            # file remains, so the delayed solar warmth must be retained.
            temptamer_main.RUNTIME_STATE["comfort_adjustment_fabric_solar_filter_values"] = {}
            temptamer_main.RUNTIME_STATE["comfort_adjustment_fabric_solar_filter_updated_at"] = {}
            temptamer_main.RUNTIME_STATE["comfort_adjustment_fabric_solar_filter_seeded_at"] = {}
            temptamer_main.RUNTIME_STATE["comfort_adjustment_fabric_solar_filter_restore_checked"] = False
            restored = temptamer_main._current_filtered_solar_irradiances(
                controller,
                first_now + timedelta(seconds=1),
            )

        self.assertEqual(restored["office"], saved["office"])

    def test_cover_position_holds_last_valid_before_configured_fallback(self):
        first_now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        temptamer_main.RUNTIME_STATE["comfort_adjustment_last_valid_cover_positions"] = {}
        temptamer_main.state._values.clear()
        temptamer_main.state._values.update(comfort_adjustment_state_map())
        temptamer_main.state._attrs.clear()
        temptamer_main.state._attrs.update(comfort_adjustment_attr_map())
        controller = temptamer_main.PyscriptController()

        live = temptamer_main._resolve_cover_position_overrides(controller, first_now)
        temptamer_main.state._attrs["cover.officeshutters"] = {}
        held = temptamer_main._resolve_cover_position_overrides(controller, first_now + timedelta(seconds=60))
        expired = temptamer_main._resolve_cover_position_overrides(controller, first_now + timedelta(seconds=301))

        self.assertEqual(live["cover.officeshutters"], (1.0, "live"))
        self.assertEqual(held["cover.officeshutters"], (1.0, "held_last_valid"))
        self.assertNotIn("cover.officeshutters", expired)

    def test_publisher_rate_limits_each_zone_after_filter_warmup(self):
        first_now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        second_now = first_now + timedelta(minutes=15)
        temptamer_main.RUNTIME_STATE["comfort_adjustment_outdoor_filter_values"] = {}
        temptamer_main.RUNTIME_STATE["comfort_adjustment_outdoor_filter_updated_at"] = {}
        temptamer_main.RUNTIME_STATE["comfort_adjustment_last_published_adjustments"] = {}
        temptamer_main.RUNTIME_STATE["comfort_adjustment_last_valid"] = {}
        temptamer_main.state._values.clear()
        temptamer_main.state._values.update(comfort_adjustment_state_map())
        temptamer_main.state._values[temptamer_main.ENABLED_ENTITY_ID] = "off"
        temptamer_main.state._attrs.clear()
        temptamer_main.state._attrs.update(comfort_adjustment_attr_map())
        service_call = Mock()
        temptamer_main.service.call = service_call

        with (
            patch.object(temptamer_main, "_resolve_cover_facades", return_value=COMFORT_ADJUSTMENT_COVER_FACADES),
            patch.object(temptamer_main, "_system_now", side_effect=[first_now, second_now]),
        ):
            temptamer_main.run_comfort_adjustment_pass(reason="first")
            temptamer_main.state._values["sensor.gw3000c_outdoor_temperature"] = "-20.0"
            temptamer_main.run_comfort_adjustment_pass(reason="large outdoor change")

        first_office = service_call.call_args_list[3].kwargs["value"]
        second_office = service_call.call_args_list[8].kwargs["value"]
        self.assertLessEqual(
            abs(second_office - first_office),
            DEFAULT_COMFORT_ADJUSTMENT_CONFIG.output_rate_limit_celsius + 1e-9,
        )

    def test_publisher_preserves_unrounded_rate_limit_progress_between_minute_passes(self):
        first_now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        zone_keys = [zone.key for zone in DEFAULT_COMFORT_ADJUSTMENT_CONFIG.zones]
        result = SimpleNamespace(
            calculation_validity={zone_key: True for zone_key in zone_keys},
            raw_adjustments={zone_key: 1.0 for zone_key in zone_keys},
            adjustments={zone_key: 1.0 for zone_key in zone_keys},
            filter_warming_up={zone_key: False for zone_key in zone_keys},
        )
        controller = FakeReader(
            {
                zone.output_entity_id: "0.0"
                for zone in DEFAULT_COMFORT_ADJUSTMENT_CONFIG.zones
            }
        )
        temptamer_main.RUNTIME_STATE["comfort_adjustment_last_published_adjustments"] = {
            zone_key: {"at": first_now, "unrounded_value": 0.0, "value": 0.0}
            for zone_key in zone_keys
        }
        temptamer_main.RUNTIME_STATE["comfort_adjustment_last_valid"] = {}

        for minute in range(1, 21):
            publications = temptamer_main._resolve_published_comfort_adjustments(
                result,
                first_now + timedelta(minutes=minute),
                controller,
            )

        self.assertEqual(publications["office"]["filtered_adjustment"], 0.3)
        self.assertAlmostEqual(
            temptamer_main.RUNTIME_STATE["comfort_adjustment_last_published_adjustments"]["office"]["unrounded_value"],
            20.0 * DEFAULT_COMFORT_ADJUSTMENT_CONFIG.output_rate_limit_celsius / 15.0,
            places=6,
        )

    def test_score_semantics_migration_reseeds_old_helpers_without_rate_limiting(self):
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        temptamer_main.state._values.clear()
        temptamer_main.state._values.update(comfort_adjustment_state_map())
        temptamer_main.state._values[temptamer_main.ENABLED_ENTITY_ID] = "off"
        for zone in DEFAULT_COMFORT_ADJUSTMENT_CONFIG.zones:
            # This simulates a persisted legacy target-offset value, whose
            # positive polarity would request more heat if control read it.
            temptamer_main.state._values[zone.output_entity_id] = "1.5"
        temptamer_main.state._attrs.clear()
        temptamer_main.state._attrs.update(comfort_adjustment_attr_map())
        temptamer_main.service.call = Mock()
        temptamer_main.RUNTIME_STATE["comfort_score_semantics_restore_checked"] = False
        temptamer_main.RUNTIME_STATE["comfort_score_semantics_version"] = None
        temptamer_main.RUNTIME_STATE["comfort_score_semantics_migration_in_progress"] = False
        temptamer_main.RUNTIME_STATE["comfort_adjustment_last_published_adjustments"] = {}
        temptamer_main.RUNTIME_STATE["comfort_adjustment_last_valid"] = {}
        calculations = []

        def capture_calculation(*args, **kwargs):
            result = calculate_comfort_adjustments(*args, **kwargs)
            calculations.append(result)
            return result

        with tempfile.TemporaryDirectory() as temporary_directory:
            semantics_state_file = Path(temporary_directory) / "score_semantics.state"
            fabric_state_file = Path(temporary_directory) / "fabric_solar.json"
            with (
                patch.object(temptamer_main, "COMFORT_SCORE_SEMANTICS_STATE_FILE", str(semantics_state_file)),
                patch.object(temptamer_main, "COMFORT_FABRIC_SOLAR_FILTER_STATE_FILE", str(fabric_state_file)),
                patch.object(temptamer_main, "_resolve_cover_facades", return_value=COMFORT_ADJUSTMENT_COVER_FACADES),
                patch.object(temptamer_main, "_system_now", return_value=now),
                patch.object(
                    temptamer_main,
                    "calculate_comfort_adjustments",
                    side_effect=capture_calculation,
                ),
            ):
                self.assertTrue(temptamer_main._run_comfort_adjustment_pass(reason="score migration"))

            published_office = next(
                service_call.kwargs["value"]
                for service_call in temptamer_main.service.call.call_args_list
                if service_call.kwargs["entity_id"] == "input_number.comfort_adjustment_office"
            )
            calculated_office = calculations[0].adjustments["office"]

            self.assertEqual(semantics_state_file.read_text(encoding="utf-8"), "5\n")

        self.assertEqual(published_office, calculated_office)
        self.assertNotEqual(published_office, 1.5)
        self.assertEqual(
            temptamer_main.RUNTIME_STATE["comfort_score_semantics_version"],
            temptamer_main.COMFORT_SCORE_SEMANTICS_VERSION,
        )

    def test_control_is_deferred_when_score_semantics_reseed_fails(self):
        temptamer_main.state._values[temptamer_main.ENABLED_ENTITY_ID] = "on"
        temptamer_main.RUNTIME_STATE["comfort_score_semantics_restore_checked"] = True
        temptamer_main.RUNTIME_STATE["comfort_score_semantics_version"] = None
        temptamer_main.RUNTIME_STATE["comfort_score_semantics_migration_in_progress"] = False

        with (
            patch.object(temptamer_main, "_run_comfort_adjustment_pass", return_value=False) as reseed,
            patch.object(temptamer_main, "run_control_pass") as control_pass,
        ):
            temptamer_main._run_enabled_control_pass(reason="migration test")

        reseed.assert_called_once_with(reason="score-semantics migration before control")
        control_pass.assert_not_called()

    def test_runtime_retains_last_valid_mode_after_configured_mode_becomes_unavailable(self):
        first_now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        second_now = first_now + timedelta(minutes=1)
        third_now = first_now + timedelta(minutes=6)
        temptamer_main.RUNTIME_STATE["comfort_adjustment_last_valid_mode"] = None
        temptamer_main.RUNTIME_STATE["comfort_adjustment_outdoor_filter_values"] = {}
        temptamer_main.RUNTIME_STATE["comfort_adjustment_outdoor_filter_updated_at"] = {}
        temptamer_main.RUNTIME_STATE["comfort_adjustment_outdoor_filter_seeded_at"] = {}
        temptamer_main.state._values.clear()
        temptamer_main.state._values.update(comfort_adjustment_state_map())
        temptamer_main.state._values[temptamer_main.ENABLED_ENTITY_ID] = "off"
        temptamer_main.state._attrs.clear()
        temptamer_main.state._attrs.update(comfort_adjustment_attr_map())
        temptamer_main.service.call = Mock()
        results = []

        def capture_calculation(*args, **kwargs):
            result = calculate_comfort_adjustments(*args, **kwargs)
            results.append(result)
            return result

        with (
            patch.object(temptamer_main, "_resolve_cover_facades", return_value=COMFORT_ADJUSTMENT_COVER_FACADES),
            patch.object(temptamer_main, "_system_now", side_effect=[first_now, second_now, third_now]),
            patch.object(temptamer_main, "calculate_comfort_adjustments", side_effect=capture_calculation),
        ):
            temptamer_main.run_comfort_adjustment_pass(reason="valid heat mode")
            temptamer_main.state._values["input_select.heatpump_mode_user"] = "HeatCool"
            temptamer_main.state._values[TEST_CLIMATE_ENTITY] = "off"
            temptamer_main.run_comfort_adjustment_pass(reason="configured mode unavailable")
            temptamer_main.state._values["sensor.gw3000c_outdoor_temperature"] = "30.0"
            temptamer_main.run_comfort_adjustment_pass(reason="expired configured-mode hold")

        self.assertEqual(
            temptamer_main.RUNTIME_STATE["comfort_adjustment_last_valid_mode"],
            {"mode": "cool", "at": third_now},
        )
        self.assertEqual(results[1].operating_mode, "heat")
        self.assertEqual(results[1].operating_mode_source, "last_valid_mode")
        self.assertEqual(results[2].operating_mode, "cool")
        self.assertEqual(results[2].operating_mode_source, "temperature_inference")

    def test_publisher_updates_only_input_numbers_while_control_is_disabled(self):
        temptamer_main.state._values.clear()
        temptamer_main.state._values.update(comfort_adjustment_state_map())
        temptamer_main.state._values[temptamer_main.ENABLED_ENTITY_ID] = "off"
        temptamer_main.state._attrs.clear()
        temptamer_main.state._attrs.update(comfort_adjustment_attr_map())
        service_call = Mock()
        temptamer_main.service.call = service_call

        with patch.object(temptamer_main, "_resolve_cover_facades", return_value=COMFORT_ADJUSTMENT_COVER_FACADES):
            temptamer_main.run_comfort_adjustment_pass(reason="test")

        self.assertEqual(
            service_call.call_args_list,
            [
                call(
                    "input_number",
                    "set_value",
                    blocking=True,
                    entity_id="input_number.comfort_adjustment_downstairs",
                    value=-0.2,
                ),
                call(
                    "input_number",
                    "set_value",
                    blocking=True,
                    entity_id="input_number.comfort_adjustment_bed_1_2",
                    value=-0.2,
                ),
                call(
                    "input_number",
                    "set_value",
                    blocking=True,
                    entity_id="input_number.comfort_adjustment_bed_3_4",
                    value=-0.2,
                ),
                call(
                    "input_number",
                    "set_value",
                    blocking=True,
                    entity_id="input_number.comfort_adjustment_office",
                    value=-0.2,
                ),
                call(
                    "input_number",
                    "set_value",
                    blocking=True,
                    entity_id="input_number.comfort_adjustment_dining",
                    value=-0.2,
                ),
            ],
        )

    def test_publisher_skips_unchanged_input_number(self):
        controller = SimpleNamespace(
            get_state=Mock(return_value="-0.5"),
            call_service=Mock(),
        )

        changed = temptamer_main._publish_input_number_if_changed(
            controller,
            "input_number.comfort_adjustment_office",
            -0.5,
        )

        self.assertFalse(changed)
        controller.call_service.assert_not_called()

    def test_publisher_updates_changed_input_number(self):
        controller = SimpleNamespace(
            get_state=Mock(return_value="-0.4"),
            call_service=Mock(),
        )

        changed = temptamer_main._publish_input_number_if_changed(
            controller,
            "input_number.comfort_adjustment_office",
            -0.5,
        )

        self.assertTrue(changed)
        controller.call_service.assert_called_once_with(
            "input_number",
            "set_value",
            entity_id="input_number.comfort_adjustment_office",
            value=-0.5,
        )

    def test_publisher_uses_live_unadjusted_control_targets_when_enabled(self):
        state_values = base_state_map()
        state_values.update(
            comfort_adjustment_state_map(
                **{
                    "sensor.office_average_temperature": "22.4",
                    "sensor.gw3000c_outdoor_temperature": "16.7",
                    "input_number.comfort_adjustment_office": "1.5",
                }
            )
        )
        temptamer_main.state._values.clear()
        temptamer_main.state._values.update(state_values)
        temptamer_main.state._values[temptamer_main.ENABLED_ENTITY_ID] = "on"
        state_attrs = base_attr_map()
        state_attrs.update(comfort_adjustment_attr_map())
        temptamer_main.state._attrs.clear()
        temptamer_main.state._attrs.update(state_attrs)
        service_call = Mock()
        temptamer_main.service.call = service_call

        with (
            patch.object(temptamer_main, "_resolve_cover_facades", return_value=COMFORT_ADJUSTMENT_COVER_FACADES),
            patch.object(temptamer_main, "calculate_comfort_adjustments", wraps=calculate_comfort_adjustments) as calculate,
        ):
            temptamer_main.run_comfort_adjustment_pass(reason="test")

        self.assertEqual(calculate.call_args.kwargs["reference_zone_targets"]["office"], (19.7, 20.5))

        self.assertIn(
            call(
                "input_number",
                "set_value",
                blocking=True,
                entity_id="input_number.comfort_adjustment_office",
                value=1.3,
            ),
            service_call.call_args_list,
        )

    def test_publisher_holds_last_valid_value_briefly_for_unavailable_zone_input(self):
        first_now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        second_now = first_now + timedelta(seconds=60)
        temptamer_main.state._values.clear()
        temptamer_main.state._values.update(comfort_adjustment_state_map())
        temptamer_main.state._values[temptamer_main.ENABLED_ENTITY_ID] = "off"
        temptamer_main.state._attrs.clear()
        temptamer_main.state._attrs.update(comfort_adjustment_attr_map())
        service_call = Mock()
        temptamer_main.service.call = service_call

        with (
            patch.object(temptamer_main, "_resolve_cover_facades", return_value=COMFORT_ADJUSTMENT_COVER_FACADES),
            patch.object(temptamer_main, "_system_now", side_effect=[first_now, second_now]),
        ):
            temptamer_main.run_comfort_adjustment_pass(reason="valid input")
            temptamer_main.state._values["sensor.office_average_temperature"] = "unavailable"
            temptamer_main.state._values["sensor.home_temperature"] = "unavailable"
            temptamer_main.run_comfort_adjustment_pass(reason="office unavailable")

        office_first_value = service_call.call_args_list[3].kwargs["value"]
        office_held_value = service_call.call_args_list[8].kwargs["value"]
        self.assertEqual(office_held_value, office_first_value)
        self.assertEqual(
            temptamer_main.RUNTIME_STATE["comfort_adjustment_last_valid"]["office"]["filtered_adjustment"],
            office_first_value,
        )


class TempTamerLoggingTests(unittest.TestCase):
    def setUp(self):
        self.original_categories = dict(TEMPTAMER_LOGGING_CATEGORIES)

    def tearDown(self):
        TEMPTAMER_LOGGING_CATEGORIES.clear()
        TEMPTAMER_LOGGING_CATEGORIES.update(self.original_categories)

    def test_configurable_log_filter_honours_each_known_category(self):
        category_messages = {
            "lifecycle": "TempTamer control is already enabled",
            "comfort_adjustment": "COMFORT ADJUSTMENT: pass complete",
            "comfort_adjustment_diagnostics": "COMFORT DIAGNOSTICS: zone=office",
            "fan_boost": "FAN BOOST: restored",
            "powerday": "POWERDAY: active",
            "poweroff": "POWEROFF: active",
            "zones": "ZONES: open",
            "dispatch": "DISPATCH: planned",
            "setpoint": "SETPOINT: selected",
        }
        for category, message in category_messages.items():
            TEMPTAMER_LOGGING_CATEGORIES[category] = False
            self.assertFalse(is_temptamer_log_enabled(message), category)
            TEMPTAMER_LOGGING_CATEGORIES[category] = True
            self.assertTrue(is_temptamer_log_enabled(message), category)

    def test_phase_one_diagnostics_emit_only_when_enabled(self):
        logger = get_temptamer_logger()
        TEMPTAMER_LOGGING_CATEGORIES["comfort_adjustment_diagnostics"] = False
        with self.assertNoLogs("pyscript.temptamer", level="INFO"):
            logger.info("COMFORT DIAGNOSTICS: zone=office hidden")

        TEMPTAMER_LOGGING_CATEGORIES["comfort_adjustment_diagnostics"] = True
        with self.assertLogs("pyscript.temptamer", level="INFO") as captured:
            logger.info("COMFORT DIAGNOSTICS: zone=office visible")
        self.assertIn("COMFORT DIAGNOSTICS: zone=office visible", captured.output[0])

    def test_comfort_diagnostics_report_humidity_value_source_entity_and_score(self):
        now = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
        result = calculate_comfort_adjustments(
            FakeReader(
                comfort_adjustment_state_map(
                    **{
                        "sensor.air_monitor_lite_c705_humidity": "50.0",
                        "sensor.climate_indoor_humidity": "60.0",
                    }
                ),
                comfort_adjustment_attr_map(),
            ),
            config=DEFAULT_COMFORT_ADJUSTMENT_CONFIG,
            cover_facades=COMFORT_ADJUSTMENT_COVER_FACADES,
            now=now,
        )
        publications = {
            zone.key: {
                "raw_adjustment": result.raw_adjustments[zone.key],
                "rounded_adjustment": result.adjustments[zone.key],
                "unrounded_rate_limited_adjustment": result.adjustments[zone.key],
                "filtered_adjustment": result.adjustments[zone.key],
                "calculation_status": "calculated",
                "last_valid_age_seconds": 0.0,
            }
            for zone in DEFAULT_COMFORT_ADJUSTMENT_CONFIG.zones
        }
        TEMPTAMER_LOGGING_CATEGORIES["comfort_adjustment_diagnostics"] = True

        with self.assertLogs("pyscript.temptamer", level="INFO") as captured:
            temptamer_main._log_comfort_adjustment_diagnostics(result, publications, now)

        office_log = next(message for message in captured.output if "zone=office" in message)
        self.assertIn("humidity=50.0", office_log)
        self.assertIn("humidity_source=zone_sensor", office_log)
        self.assertIn("humidity_entity=sensor.air_monitor_lite_c705_humidity", office_log)
        self.assertIn("humidity_score=", office_log)
        calibration = temptamer_main._comfort_adjustment_calibration_parameters()
        self.assertEqual(calibration["score_range"], (-3.0, 3.0))
        self.assertEqual(calibration["warm_indoor_surface_resistance"], 0.415)
        self.assertEqual(
            calibration["warm_surface_resistance_full_effect_delta_celsius"],
            3.0,
        )
        self.assertEqual(calibration["solar_mrt_coefficient"], 0.015)
        self.assertEqual(calibration["solar_adjustment_limit"], 0.5)
        self.assertEqual(
            calibration["zone_humidity_entities"]["downstairs"],
            "sensor.rumpus_white_clock_humidity",
        )

    def test_reload_removes_legacy_pyscript_logging_filter_without_installing_a_callback(self):
        raw_logger = logging.getLogger("pyscript.temptamer")
        legacy_filter = type("_TempTamerCategoryFilter", (logging.Filter,), {})()
        raw_logger.addFilter(legacy_filter)
        raw_logger._temptamer_category_filter_installed = True

        install_temptamer_log_filter()

        self.assertNotIn(legacy_filter, raw_logger.filters)
        self.assertFalse(hasattr(raw_logger, "_temptamer_category_filter_installed"))


class TempTamerTests(unittest.TestCase):
    def setUp(self):
        self.original_state_values = dict(temptamer_main.state._values)
        self.original_state_attrs = deepcopy(temptamer_main.state._attrs)
        self.original_runtime_state = deepcopy(temptamer_main.RUNTIME_STATE)
        self.original_service_call = temptamer_main.service.call
        self.original_fan_boost_state_file = temptamer_main.HEAT_DEMAND_FAN_BOOST_STATE_FILE
        self.fan_boost_state_directory = tempfile.TemporaryDirectory()
        temptamer_main.HEAT_DEMAND_FAN_BOOST_STATE_FILE = str(
            Path(self.fan_boost_state_directory.name) / "fan_boost.state"
        )

    def tearDown(self):
        temptamer_main.state._values = dict(self.original_state_values)
        temptamer_main.state._attrs = dict(self.original_state_attrs)
        temptamer_main.RUNTIME_STATE.clear()
        temptamer_main.RUNTIME_STATE.update(self.original_runtime_state)
        temptamer_main.service.call = self.original_service_call
        temptamer_main.HEAT_DEMAND_FAN_BOOST_STATE_FILE = self.original_fan_boost_state_file
        self.fan_boost_state_directory.cleanup()

    def test_build_snapshot_uses_house_sensor_fallback_and_zone_overrides(self):
        reader = FakeReader(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": "Night",
                    "input_select.temptamer_hvac_mode": "Cool",
                    "input_select.temptamer_comfort_mode_office": "DayLiving",
                    "sensor.home_temperature": "18.5",
                    "sensor.office_average_temperature": "unavailable",
                    "sensor.average_dining_zone_temp": "17.0",
                    "sensor.average_bed1_2_zone_temp": "16.5",
                    "sensor.average_bed3_4_zone_temp": "unknown",
                    "switch.wt32_hpctrl_e8dbd0_dining": "on",
                    "switch.wt32_hpctrl_e8dbd0_bed_34": "on",
                }
            ),
            base_attr_map("23.4"),
        )

        snapshot = build_snapshot(reader)

        self.assertEqual(snapshot.selected_hvac_mode, "Cool")
        self.assertEqual(snapshot.zones["office"].current_temp, 18.5)
        self.assertEqual(snapshot.zones["bedroom_3_4"].current_temp, 18.5)
        self.assertEqual(snapshot.inlet_temp, 23.4)
        self.assertEqual(snapshot.zones["office"].applied_comfort_mode, "DayLiving")
        self.assertEqual(snapshot.zones["office"].scheme.name, "DayLiving")
        self.assertEqual(snapshot.zones["dining"].scheme.name, "Night")
        self.assertEqual(snapshot.zones["bedroom_1_2"].scheme.name, SCHEME_NIGHT_BEDROOM)

    def test_positive_comfort_score_lowers_every_zone_threshold_and_delays_heat_demand(self):
        baseline = build_snapshot(
            FakeReader(
                base_state_map(**{"sensor.office_average_temperature": "18.4"}),
                base_attr_map(),
            )
        )
        adjusted = build_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "sensor.office_average_temperature": "18.4",
                        "input_number.comfort_adjustment_office": "0.5",
                    }
                ),
                base_attr_map(),
            )
        )
        baseline_zone = baseline.zones["office"]
        adjusted_zone = adjusted.zones["office"]

        self.assertEqual(adjusted.base_zone_targets["office"], (19.7, 20.5))
        self.assertEqual(adjusted_zone.comfort_adjustment, 0.5)
        self.assertEqual(adjusted_zone.scheme.enable_outside, baseline_zone.scheme.enable_outside - 0.5)
        self.assertEqual(adjusted_zone.scheme.continue_until, baseline_zone.scheme.continue_until - 0.5)
        self.assertEqual(adjusted_zone.scheme.ideal_target, baseline_zone.scheme.ideal_target - 0.5)
        self.assertEqual(adjusted_zone.cool_scheme.enable_outside, baseline_zone.cool_scheme.enable_outside - 0.5)
        self.assertEqual(adjusted_zone.cool_scheme.continue_until, baseline_zone.cool_scheme.continue_until - 0.5)
        self.assertEqual(adjusted_zone.cool_scheme.ideal_target, baseline_zone.cool_scheme.ideal_target - 0.5)
        self.assertIn("office", baseline.heat_calling_zones)
        self.assertNotIn("office", adjusted.heat_calling_zones)

    def test_adjusted_bedroom_cooling_band_is_translated_above_room_safety_floor(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                        "input_select.temptamer_hvac_mode": "Cool",
                        GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "1",
                        "input_number.comfort_adjustment_bed_3_4": "3.0",
                        "input_number.temptamer_setpoint_adjustment": "-1.0",
                        "sensor.average_bed3_4_zone_temp": "20.8",
                    }
                ),
                base_attr_map("21.0"),
            )
        )

        bedroom = snapshot.zones["bedroom_3_4"]
        self.assertEqual(snapshot.base_zone_targets["bedroom_3_4"][1], 14.0)
        self.assertEqual(bedroom.cool_scheme.enable_outside, 18.0)
        self.assertEqual(bedroom.cool_scheme.ideal_target, 16.0)
        self.assertEqual(bedroom.cool_scheme.continue_until, 14.0)
        self.assertGreaterEqual(
            bedroom.cool_scheme.enable_outside,
            bedroom.cool_scheme.ideal_target,
        )
        self.assertGreaterEqual(
            bedroom.cool_scheme.ideal_target,
            bedroom.cool_scheme.continue_until,
        )
        demand = resolve_equipment_demand(snapshot, ("bedroom_3_4",), operation_mode=HVAC_COOL)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("bedroom_3_4",),
            current_hvac_mode="cool",
            current_fan_mode="low",
            current_setpoint="17.0",
        )
        self.assertTrue(demand.cool_requested)
        self.assertEqual(plan.setpoint, 19)

    def test_comfort_adjustment_defaults_and_clamps_invalid_helper_values(self):
        unavailable = build_snapshot(
            FakeReader(
                base_state_map(**{"input_number.comfort_adjustment_office": "unavailable"}),
                base_attr_map(),
            )
        )
        positive = build_snapshot(
            FakeReader(
                base_state_map(**{"input_number.comfort_adjustment_office": "9.0"}),
                base_attr_map(),
            )
        )
        negative = build_snapshot(
            FakeReader(
                base_state_map(**{"input_number.comfort_adjustment_office": "-9.0"}),
                base_attr_map(),
            )
        )

        self.assertEqual(unavailable.zones["office"].comfort_adjustment, 0.0)
        self.assertEqual(positive.zones["office"].comfort_adjustment, 3.0)
        self.assertEqual(negative.zones["office"].comfort_adjustment, -3.0)
        self.assertEqual(positive.zones["office"].scheme.ideal_target, 16.7)
        self.assertEqual(negative.zones["office"].cool_scheme.ideal_target, 23.5)

    def test_global_setpoint_adjustment_shifts_all_zone_thresholds_and_composes(self):
        baseline = build_snapshot(FakeReader(base_state_map(), base_attr_map()))
        adjusted = build_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_number.temptamer_setpoint_adjustment": "0.4",
                        "input_number.comfort_adjustment_office": "0.2",
                    }
                ),
                base_attr_map(),
            )
        )

        self.assertEqual(adjusted.global_setpoint_adjustment, 0.4)
        for zone_key, baseline_zone in baseline.zones.items():
            adjusted_zone = adjusted.zones[zone_key]
            expected_offset = 0.2 if zone_key == "office" else 0.4
            self.assertEqual(adjusted_zone.scheme.enable_outside, baseline_zone.scheme.enable_outside + expected_offset)
            self.assertEqual(adjusted_zone.scheme.continue_until, baseline_zone.scheme.continue_until + expected_offset)
            self.assertEqual(adjusted_zone.scheme.ideal_target, baseline_zone.scheme.ideal_target + expected_offset)
            self.assertEqual(adjusted_zone.cool_scheme.enable_outside, baseline_zone.cool_scheme.enable_outside + expected_offset)
            self.assertEqual(adjusted_zone.cool_scheme.continue_until, baseline_zone.cool_scheme.continue_until + expected_offset)
            self.assertEqual(adjusted_zone.cool_scheme.ideal_target, baseline_zone.cool_scheme.ideal_target + expected_offset)
            self.assertEqual(adjusted.base_zone_targets[zone_key], baseline.base_zone_targets[zone_key])
        self.assertEqual(adjusted.zones["office"].comfort_adjustment, 0.2)

    def test_manual_adjustment_is_unclamped_before_cooling_safety_bound(self):
        baseline = build_snapshot(FakeReader(base_state_map(), base_attr_map()))
        for manual in (-2.5, 2.5):
            for automatic in (-9.0, 9.0):
                with self.subTest(manual=manual, automatic=automatic):
                    adjusted = build_snapshot(FakeReader(base_state_map(**{
                        "input_number.temptamer_setpoint_adjustment": str(manual),
                        "input_number.comfort_adjustment_office": str(automatic),
                    }), base_attr_map()))
                    clamped_auto = -3.0 if automatic < 0 else 3.0
                    self.assertEqual(adjusted.global_setpoint_adjustment, manual)
                    self.assertEqual(adjusted.zones["office"].comfort_adjustment, clamped_auto)
                    for key, zone in adjusted.zones.items():
                        correction = (clamped_auto if key == "office" else 0.0) - manual
                        for scheme_attr in ("scheme", "cool_scheme"):
                            before = getattr(baseline.zones[key], scheme_attr)
                            after = getattr(zone, scheme_attr)
                            expected_values = tuple(
                                getattr(before, threshold) - correction
                                for threshold in ("enable_outside", "continue_until", "ideal_target")
                            )
                            if (
                                scheme_attr == "cool_scheme"
                                and after.name != SCHEME_OFF
                                and min(expected_values) < 14.0
                            ):
                                self.assertGreaterEqual(after.continue_until, 14.0)
                                self.assertGreaterEqual(after.enable_outside, after.ideal_target)
                                self.assertGreaterEqual(after.ideal_target, after.continue_until)
                                continue
                            for threshold in ("enable_outside", "continue_until", "ideal_target"):
                                self.assertAlmostEqual(
                                    getattr(after, threshold), getattr(before, threshold) - correction,
                                )

    def test_manual_adjustment_defaults_to_zero_for_invalid_values(self):
        for value in (None, "unavailable", "invalid", "nan", "inf", "-inf"):
            with self.subTest(value=value):
                snapshot = build_snapshot(FakeReader(base_state_map(**{
                    "input_number.temptamer_setpoint_adjustment": value,
                }), base_attr_map()))
                self.assertEqual(snapshot.global_setpoint_adjustment, 0.0)

    def test_build_snapshot_auto_zone_override_falls_back_to_global_mode(self):
        snapshot = build_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "input_select.temptamer_comfort_mode_office": "Auto",
                        "input_select.temptamer_comfort_mode_downstairs": "Auto",
                    }
                ),
                base_attr_map("21.0", temperature="18.0"),
            )
        )

        self.assertEqual(snapshot.zones["office"].applied_comfort_mode, "Office")
        self.assertEqual(snapshot.zones["office"].scheme.name, "DayLiving")
        self.assertEqual(snapshot.zones["dining"].scheme.name, "DiningBasic")
        self.assertEqual(snapshot.zones["downstairs"].scheme.name, "DiningBasic")

    def test_day_mode_uses_downstairs_scheme_before_4pm(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Day",
                        "input_select.temptamer_comfort_mode_downstairs": "Auto",
                        "sensor.downstairs_zone_average_temperature": "15.4",
                    }
                ),
                base_attr_map("21.0"),
            ),
            now=datetime(2026, 7, 25, 15, 59, tzinfo=timezone.utc),
        )

        self.assertEqual(snapshot.zones["downstairs"].current_temp, 15.4)
        self.assertEqual(snapshot.zones["downstairs"].scheme.name, SCHEME_DOWNSTAIRS)

    def test_day_mode_uses_downstairs_scheme_from_4pm(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Day",
                        "input_select.temptamer_comfort_mode_downstairs": "Auto",
                        "sensor.downstairs_zone_average_temperature": "18.4",
                    }
                ),
                base_attr_map("21.0"),
            ),
            now=datetime(2026, 7, 25, 16, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(snapshot.zones["downstairs"].current_temp, 18.4)
        self.assertEqual(snapshot.zones["downstairs"].scheme.name, SCHEME_DOWNSTAIRS)

    def test_downstairs_scheme_is_half_a_degree_above_day_living_for_heat_and_cool(self):
        for control_schemes in (
            DEFAULT_SYSTEM_CONFIG.heat_control_schemes,
            DEFAULT_SYSTEM_CONFIG.cool_control_schemes,
        ):
            day_living_scheme = control_schemes[SCHEME_DAY_LIVING]
            downstairs_scheme = control_schemes[SCHEME_DOWNSTAIRS]
            self.assertEqual(downstairs_scheme.enable_outside, day_living_scheme.enable_outside + 0.5)
            self.assertEqual(downstairs_scheme.continue_until, day_living_scheme.continue_until + 0.5)
            self.assertEqual(downstairs_scheme.ideal_target, day_living_scheme.ideal_target + 0.5)

    def test_default_comfort_modes_are_mode_objects(self):
        day_mode = DEFAULT_SYSTEM_CONFIG.comfort_modes["Day"]
        night_mode = DEFAULT_SYSTEM_CONFIG.comfort_modes[COMFORT_MODE_NIGHT]
        power_mode = DEFAULT_SYSTEM_CONFIG.comfort_modes[COMFORT_MODE_POWER_DAY]
        poweroff_mode = DEFAULT_SYSTEM_CONFIG.comfort_modes[COMFORT_MODE_POWER_OFF]

        self.assertIsInstance(day_mode, DefaultComfortMode)
        self.assertIsInstance(day_mode, ScheduledComfortMode)
        self.assertIsInstance(night_mode, NightComfortMode)
        self.assertIsInstance(power_mode, PowerComfortMode)
        self.assertIsInstance(poweroff_mode, PowerOffComfortMode)
        self.assertEqual(day_mode.fan_speed_level(2.6, 1, current_speed_level=1), 1)
        self.assertEqual(day_mode.fan_speed_level(2.6, 1, current_speed_level=1, starting=True), 2)
        self.assertEqual(night_mode.fan_speed_level(4.1, 1, current_speed_level=1), 1)
        self.assertEqual(night_mode.fan_speed_level(6.1, 1, current_speed_level=1), 2)
        self.assertEqual(power_mode.fan_speed_level(2.6, 1, current_speed_level=1), 1)
        self.assertEqual(power_mode.fan_speed_level(2.6, 1, current_speed_level=1, free_power_available=True), 2)
        self.assertIn(GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR, NORMAL_RECALCULATION_TRIGGER_ENTITIES)
        self.assertIn(GOODWE_BATTERY_REMAINING_SENSOR, NORMAL_RECALCULATION_TRIGGER_ENTITIES)
        self.assertIn(GOODWE_PV_POWER_SENSOR, NORMAL_RECALCULATION_TRIGGER_ENTITIES)
        self.assertIn(EAGLE_200_POWER_DEMAND_SENSOR, NORMAL_RECALCULATION_TRIGGER_ENTITIES)
        self.assertIn(EAGLE_200_MAX_POWER_DEMAND_5M_SENSOR, NORMAL_RECALCULATION_TRIGGER_ENTITIES)

    def test_poweroff_uses_powerday_during_the_day_and_night_outside_it(self):
        day_snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_OFF,
                        "input_select.temptamer_comfort_mode_downstairs": "Auto",
                    }
                ),
                base_attr_map("21.0"),
            ),
            poweroff_active=True,
            now=datetime(2026, 7, 25, 8, 0, tzinfo=timezone.utc),
        )
        night_snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_OFF,
                        "input_select.temptamer_comfort_mode_downstairs": "Auto",
                    }
                ),
                base_attr_map("21.0"),
            ),
            poweroff_active=True,
            now=datetime(2026, 7, 25, 22, 0, tzinfo=timezone.utc),
        )

        self.assertIsInstance(day_snapshot.comfort_mode_behavior, PowerComfortMode)
        self.assertEqual(day_snapshot.zones["office"].scheme.name, SCHEME_DAY_LIVING)
        self.assertEqual(day_snapshot.zones["downstairs"].scheme.name, SCHEME_NIGHT)
        self.assertIsInstance(night_snapshot.comfort_mode_behavior, NightComfortMode)
        self.assertTrue(
            all(
                zone.scheme.name
                == (SCHEME_NIGHT_BEDROOM if zone_key in {"bedroom_1_2", "bedroom_3_4"} else SCHEME_NIGHT)
                for zone_key, zone in night_snapshot.zones.items()
            )
        )

    def test_poweroff_forces_the_heatpump_off_until_activation(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_OFF,
                        TEST_CLIMATE_ENTITY: "heat",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                    }
                ),
                base_attr_map("21.0"),
            ),
            poweroff_active=False,
            now=datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc),
        )
        actions, predicted_open = resolve_zone_actions(
            snapshot,
            datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc),
            operation_mode=HVAC_HEAT,
        )
        plan = build_dispatch_plan(
            snapshot,
            EquipmentDemand(),
            predicted_open,
            current_hvac_mode="heat",
            current_fan_mode="low",
        )

        self.assertTrue(snapshot.poweroff_forced_off)
        self.assertEqual([(action.zone_key, action.turn_on) for action in actions], [("office", False)])
        self.assertEqual(predicted_open, ())
        self.assertTrue(plan.turn_off)

    def test_poweroff_latches_for_fifteen_minutes_then_stops_below_ninety_percent(self):
        start = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
        active_reader = FakeReader(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_OFF,
                    GOODWE_BATTERY_REMAINING_SENSOR: "95.1",
                    GOODWE_PV_POWER_SENSOR: "1.1",
                }
            )
        )

        self.assertTrue(temptamer_main._update_poweroff_runtime_state(active_reader, start))
        self.assertEqual(temptamer_main.RUNTIME_STATE["poweroff_activation_started_at"], start)
        self.assertEqual(
            temptamer_main.RUNTIME_STATE["poweroff_activation_hold_until"],
            start + timedelta(seconds=POWEROFF_MIN_ACTIVATION_SECONDS),
        )

        low_battery_reader = FakeReader(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_OFF,
                    GOODWE_BATTERY_REMAINING_SENSOR: "89.9",
                    GOODWE_PV_POWER_SENSOR: "0",
                }
            )
        )
        self.assertTrue(temptamer_main._update_poweroff_runtime_state(low_battery_reader, start + timedelta(minutes=14)))
        self.assertFalse(
            temptamer_main._update_poweroff_runtime_state(
                low_battery_reader,
                start + timedelta(seconds=POWEROFF_MIN_ACTIVATION_SECONDS),
            )
        )
        self.assertIsNone(temptamer_main.RUNTIME_STATE["poweroff_activation_started_at"])
        self.assertIn("after minimum activation", temptamer_main.RUNTIME_STATE["poweroff_reason"])

    def test_poweroff_clears_its_activation_immediately_when_another_comfort_mode_is_selected(self):
        start = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
        poweroff_reader = FakeReader(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_OFF,
                    GOODWE_BATTERY_REMAINING_SENSOR: "96",
                    GOODWE_PV_POWER_SENSOR: "2",
                }
            )
        )
        self.assertTrue(temptamer_main._update_poweroff_runtime_state(poweroff_reader, start))

        other_mode_reader = FakeReader(base_state_map(**{"input_select.temptamer_comfort_mode": "Day"}))
        self.assertFalse(temptamer_main._update_poweroff_runtime_state(other_mode_reader, start + timedelta(minutes=1)))
        self.assertIsNone(temptamer_main.RUNTIME_STATE["poweroff_activation_started_at"])
        self.assertEqual(temptamer_main.RUNTIME_STATE["poweroff_reason"], "comfort mode is not PowerOff")

    def test_powerday_uses_office_mapping_when_power_is_not_free(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                        "input_select.temptamer_comfort_mode_downstairs": "Auto",
                        GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "1",
                    }
                ),
                base_attr_map("21.0"),
            )
        )

        self.assertEqual(snapshot.comfort_mode, COMFORT_MODE_POWER_DAY)
        self.assertEqual(snapshot.zones["office"].scheme.name, SCHEME_DAY_LIVING)
        self.assertEqual(snapshot.zones["dining"].scheme.name, SCHEME_DINING_BASIC)
        self.assertEqual(snapshot.zones["downstairs"].scheme.name, SCHEME_DOWNSTAIRS)
        self.assertEqual(snapshot.zones["bedroom_1_2"].scheme.name, SCHEME_BEDROOM)
        self.assertEqual(snapshot.zones["bedroom_3_4"].scheme.name, SCHEME_BEDROOM)
        self.assertEqual(
            snapshot.zones["office"].scheme.continue_until,
            TEST_SYSTEM_CONFIG.heat_control_schemes[SCHEME_DAY_LIVING].continue_until,
        )
        self.assertEqual(
            snapshot.zones["office"].scheme.ideal_target,
            TEST_SYSTEM_CONFIG.heat_control_schemes[SCHEME_DAY_LIVING].ideal_target,
        )
        self.assertFalse(snapshot.free_power_available)
        self.assertFalse(snapshot.heat_sink_available)
        self.assertEqual(
            snapshot.comfort_mode_behavior.fan_speed_level(
                2.6,
                1,
                current_speed_level=1,
                free_power_available=snapshot.heat_sink_available,
            ),
            1,
        )

    def test_powerday_downstairs_priority_starts_only_for_free_power_heat_soak(self):
        now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(powerday_downstairs_priority_state_map()),
            now=now,
        )

        active = temptamer_main._update_powerday_downstairs_priority_runtime_state(snapshot, HVAC_HEAT, now)

        self.assertTrue(active)
        self.assertEqual(temptamer_main.RUNTIME_STATE["powerday_downstairs_priority_started_at"], now)
        self.assertEqual(
            temptamer_main.RUNTIME_STATE["powerday_downstairs_priority_upstairs_zones"],
            POWERDAY_DOWNSTAIRS_PRIORITY_UPSTAIRS_ZONE_KEYS,
        )
        self.assertEqual(temptamer_main.RUNTIME_STATE["powerday_downstairs_priority_gap"], 6.0)

        paid_snapshot = build_behavior_snapshot(
            FakeReader(powerday_downstairs_priority_state_map(**{GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "1"})),
            now=now,
        )
        self.assertFalse(
            temptamer_main._update_powerday_downstairs_priority_runtime_state(paid_snapshot, HVAC_HEAT, now)
        )
        self.assertIn("free power", temptamer_main.RUNTIME_STATE["powerday_downstairs_priority_reason"])

        poweroff_snapshot = build_behavior_snapshot(
            FakeReader(
                powerday_downstairs_priority_state_map(
                    **{"input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_OFF}
                )
            ),
            poweroff_active=True,
            now=now,
        )
        self.assertFalse(
            temptamer_main._update_powerday_downstairs_priority_runtime_state(poweroff_snapshot, HVAC_HEAT, now)
        )
        self.assertIn("not PowerDay", temptamer_main.RUNTIME_STATE["powerday_downstairs_priority_reason"])

        self.assertFalse(
            temptamer_main._update_powerday_downstairs_priority_runtime_state(snapshot, HVAC_COOL, now)
        )

    def test_powerday_downstairs_priority_enters_at_one_point_seven_five_degrees(self):
        now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                powerday_downstairs_priority_state_map(
                    **{
                        "sensor.office_average_temperature": "20.75",
                        "sensor.downstairs_zone_average_temperature": "19.0",
                        "switch.wt32_hpctrl_e8dbd0_dining": "off",
                        "switch.wt32_hpctrl_e8dbd0_bed_12": "off",
                        "switch.wt32_hpctrl_e8dbd0_bed_34": "off",
                    }
                )
            ),
            now=now,
        )

        self.assertTrue(temptamer_main._update_powerday_downstairs_priority_runtime_state(snapshot, HVAC_HEAT, now))
        self.assertEqual(temptamer_main.RUNTIME_STATE["powerday_downstairs_priority_gap"], 1.75)

    def test_powerday_downstairs_priority_holds_for_ten_minutes_then_releases_at_one_degree(self):
        start = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
        start_snapshot = build_behavior_snapshot(
            FakeReader(powerday_downstairs_priority_state_map()),
            now=start,
        )
        self.assertTrue(
            temptamer_main._update_powerday_downstairs_priority_runtime_state(start_snapshot, HVAC_HEAT, start)
        )

        balanced_snapshot = build_behavior_snapshot(
            FakeReader(
                powerday_downstairs_priority_state_map(
                    **{
                        "sensor.office_average_temperature": "19.0",
                        "sensor.average_dining_zone_temp": "19.0",
                        "sensor.average_bed1_2_zone_temp": "19.0",
                        "sensor.average_bed3_4_zone_temp": "19.0",
                        "switch.wt32_hpctrl_e8dbd0_office": "off",
                        "switch.wt32_hpctrl_e8dbd0_dining": "off",
                        "switch.wt32_hpctrl_e8dbd0_bed_12": "off",
                        "switch.wt32_hpctrl_e8dbd0_bed_34": "off",
                    }
                )
            ),
            now=start + timedelta(minutes=9),
        )
        self.assertTrue(
            temptamer_main._update_powerday_downstairs_priority_runtime_state(
                balanced_snapshot,
                HVAC_HEAT,
                start + timedelta(minutes=9),
            )
        )
        self.assertFalse(
            temptamer_main._update_powerday_downstairs_priority_runtime_state(
                balanced_snapshot,
                HVAC_HEAT,
                start + timedelta(seconds=POWERDAY_DOWNSTAIRS_PRIORITY_MIN_SECONDS),
            )
        )
        self.assertIn("gap 1.0C <= 1.0C", temptamer_main.RUNTIME_STATE["powerday_downstairs_priority_reason"])

    def test_powerday_downstairs_priority_ends_immediately_when_downstairs_is_satisfied(self):
        start = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
        active_snapshot = build_behavior_snapshot(
            FakeReader(powerday_downstairs_priority_state_map()),
            now=start,
        )
        self.assertTrue(
            temptamer_main._update_powerday_downstairs_priority_runtime_state(active_snapshot, HVAC_HEAT, start)
        )

        satisfied_snapshot = build_behavior_snapshot(
            FakeReader(powerday_downstairs_priority_state_map(**{"sensor.downstairs_zone_average_temperature": "24.0"})),
            now=start + timedelta(minutes=1),
        )
        self.assertFalse(
            temptamer_main._update_powerday_downstairs_priority_runtime_state(
                satisfied_snapshot,
                HVAC_HEAT,
                start + timedelta(minutes=1),
            )
        )
        self.assertIn("downstairs is at or above", temptamer_main.RUNTIME_STATE["powerday_downstairs_priority_reason"])

    def test_powerday_downstairs_priority_forces_downstairs_open_and_closes_upstairs_together(self):
        now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(powerday_downstairs_priority_state_map()),
            last_switch_changes={zone_key: now for zone_key in POWERDAY_DOWNSTAIRS_PRIORITY_UPSTAIRS_ZONE_KEYS},
            now=now,
        )

        actions, predicted_open = resolve_zone_actions(
            snapshot,
            now,
            operation_mode=HVAC_HEAT,
            downstairs_priority_active=True,
            downstairs_zone_key=POWERDAY_DOWNSTAIRS_PRIORITY_ZONE_KEY,
            upstairs_zone_keys=POWERDAY_DOWNSTAIRS_PRIORITY_UPSTAIRS_ZONE_KEYS,
        )

        self.assertEqual(predicted_open, (POWERDAY_DOWNSTAIRS_PRIORITY_ZONE_KEY,))
        self.assertCountEqual(
            [(action.zone_key, action.turn_on) for action in actions],
            [(POWERDAY_DOWNSTAIRS_PRIORITY_ZONE_KEY, True)]
            + [(zone_key, False) for zone_key in POWERDAY_DOWNSTAIRS_PRIORITY_UPSTAIRS_ZONE_KEYS],
        )
        self.assertTrue(all(not action.discretionary for action in actions))

    def test_downstairs_startup_priority_runs_downstairs_only_after_an_extended_request_gap(self):
        now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode_downstairs": "Auto",
                        "sensor.office_average_temperature": "17.0",
                        "sensor.downstairs_zone_average_temperature": "20.0",
                    }
                ),
                base_attr_map("20.0"),
            ),
            now=now,
        )
        temptamer_main.RUNTIME_STATE["last_heatcool_request_at"] = now - timedelta(
            seconds=DOWNSTAIRS_STARTUP_PRIORITY_INACTIVE_SECONDS + 1
        )

        self.assertTrue(
            temptamer_main._update_downstairs_startup_priority_runtime_state(snapshot, HVAC_HEAT, now)
        )
        self.assertEqual(temptamer_main.RUNTIME_STATE["downstairs_startup_priority_started_at"], now)

        actions, predicted_open = resolve_zone_actions(
            snapshot,
            now,
            operation_mode=HVAC_HEAT,
            downstairs_startup_priority_active=True,
            downstairs_startup_priority_zone_key=DOWNSTAIRS_STARTUP_PRIORITY_ZONE_KEY,
        )

        self.assertEqual(predicted_open, (DOWNSTAIRS_STARTUP_PRIORITY_ZONE_KEY,))
        self.assertEqual(
            [(action.zone_key, action.turn_on, action.discretionary) for action in actions],
            [(DOWNSTAIRS_STARTUP_PRIORITY_ZONE_KEY, True, False)],
        )

    def test_downstairs_startup_priority_holds_for_three_minutes_then_releases(self):
        now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode_downstairs": "Auto",
                        "sensor.office_average_temperature": "17.0",
                        "sensor.downstairs_zone_average_temperature": "20.0",
                    }
                ),
                base_attr_map("20.0"),
            ),
            now=now,
        )
        temptamer_main.RUNTIME_STATE["last_heatcool_request_at"] = now - timedelta(
            seconds=DOWNSTAIRS_STARTUP_PRIORITY_INACTIVE_SECONDS + 1
        )

        self.assertTrue(
            temptamer_main._update_downstairs_startup_priority_runtime_state(snapshot, HVAC_HEAT, now)
        )
        self.assertTrue(
            temptamer_main._update_downstairs_startup_priority_runtime_state(
                snapshot,
                HVAC_HEAT,
                now + timedelta(seconds=DOWNSTAIRS_STARTUP_PRIORITY_MIN_SECONDS - 1),
            )
        )
        self.assertFalse(
            temptamer_main._update_downstairs_startup_priority_runtime_state(
                snapshot,
                HVAC_HEAT,
                now + timedelta(seconds=DOWNSTAIRS_STARTUP_PRIORITY_MIN_SECONDS),
            )
        )
        self.assertIn(
            "minimum downstairs-only startup period elapsed",
            temptamer_main.RUNTIME_STATE["downstairs_startup_priority_reason"],
        )

    def test_downstairs_startup_priority_also_applies_to_a_new_cool_request(self):
        now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_hvac_mode": "Cool",
                        "input_select.temptamer_comfort_mode_downstairs": "Auto",
                        "sensor.office_average_temperature": "24.0",
                        "sensor.downstairs_zone_average_temperature": "22.0",
                    }
                ),
                base_attr_map("22.0"),
            ),
            now=now,
        )
        temptamer_main.RUNTIME_STATE["last_heatcool_request_at"] = now - timedelta(
            seconds=DOWNSTAIRS_STARTUP_PRIORITY_INACTIVE_SECONDS + 1
        )

        self.assertTrue(
            temptamer_main._update_downstairs_startup_priority_runtime_state(snapshot, HVAC_COOL, now)
        )

    def test_downstairs_startup_priority_control_pass_opens_only_downstairs_first(self):
        now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
        real_system_now = temptamer_main._system_now
        temptamer_main.state._values.clear()
        temptamer_main.state._attrs.clear()
        temptamer_main.RUNTIME_STATE.clear()
        temptamer_main.RUNTIME_STATE.update(deepcopy(self.original_runtime_state))
        temptamer_main.RUNTIME_STATE["last_successful_control_pass"] = now - timedelta(minutes=1)
        temptamer_main.RUNTIME_STATE["last_heatcool_request_at"] = now - timedelta(
            seconds=DOWNSTAIRS_STARTUP_PRIORITY_INACTIVE_SECONDS + 1
        )
        temptamer_main.state._values.update(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode_downstairs": "Auto",
                    "sensor.office_average_temperature": "17.0",
                    "sensor.downstairs_zone_average_temperature": "20.0",
                    TEST_CLIMATE_ENTITY: "off",
                }
            )
        )
        temptamer_main.state._attrs[TEST_CLIMATE_ENTITY] = {
            "fan_mode": "low",
            "temperature": 20,
            "current_temperature": 20,
        }
        service_call = Mock()
        temptamer_main.service.call = service_call
        temptamer_main._system_now = lambda: now

        try:
            temptamer_main.run_control_pass(reason="downstairs startup priority test")
        finally:
            temptamer_main._system_now = real_system_now

        self.assertTrue(temptamer_main.RUNTIME_STATE["downstairs_startup_priority_active"])
        self.assertIn(
            call(
                "switch",
                "turn_on",
                blocking=True,
                entity_id=DEFAULT_SYSTEM_CONFIG.zones[DOWNSTAIRS_STARTUP_PRIORITY_ZONE_KEY].switch_entity_id,
            ),
            service_call.call_args_list,
        )
        for zone_key, zone_config in DEFAULT_SYSTEM_CONFIG.zones.items():
            if zone_key == DOWNSTAIRS_STARTUP_PRIORITY_ZONE_KEY:
                continue
            self.assertNotIn(
                call("switch", "turn_on", blocking=True, entity_id=zone_config.switch_entity_id),
                service_call.call_args_list,
            )

    def test_downstairs_startup_priority_requires_an_eligible_downstairs_zone(self):
        now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
        state_map = base_state_map(
            **{
                "input_select.temptamer_comfort_mode_downstairs": "Auto",
                "sensor.office_average_temperature": "17.0",
                "sensor.downstairs_zone_average_temperature": "22.5",
            }
        )
        snapshot = build_behavior_snapshot(FakeReader(state_map, base_attr_map("20.0")), now=now)
        temptamer_main.RUNTIME_STATE["last_heatcool_request_at"] = now - timedelta(
            seconds=DOWNSTAIRS_STARTUP_PRIORITY_INACTIVE_SECONDS + 1
        )

        self.assertFalse(
            temptamer_main._update_downstairs_startup_priority_runtime_state(snapshot, HVAC_HEAT, now)
        )
        self.assertIn(
            "downstairs is at or above",
            temptamer_main.RUNTIME_STATE["downstairs_startup_priority_reason"],
        )

        state_map["input_select.temptamer_comfort_mode_downstairs"] = SCHEME_OFF
        disabled_snapshot = build_behavior_snapshot(FakeReader(state_map, base_attr_map("20.0")), now=now)
        temptamer_main.RUNTIME_STATE["last_heatcool_request_at"] = now - timedelta(
            seconds=DOWNSTAIRS_STARTUP_PRIORITY_INACTIVE_SECONDS + 1
        )
        self.assertFalse(
            temptamer_main._update_downstairs_startup_priority_runtime_state(disabled_snapshot, HVAC_HEAT, now)
        )
        self.assertIn("downstairs zone is disabled", temptamer_main.RUNTIME_STATE["downstairs_startup_priority_reason"])

    def test_powerday_downstairs_priority_control_pass_publishes_state_and_dispatches_downstairs_only(self):
        now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
        real_system_now = temptamer_main._system_now
        temptamer_main.state._values.clear()
        temptamer_main.state._attrs.clear()
        temptamer_main.RUNTIME_STATE.clear()
        temptamer_main.RUNTIME_STATE.update(deepcopy(self.original_runtime_state))
        temptamer_main.RUNTIME_STATE["last_successful_control_pass"] = now - timedelta(minutes=1)
        temptamer_main.state._values.update(
            powerday_downstairs_priority_state_map(**{TEST_CLIMATE_ENTITY: "heat"})
        )
        temptamer_main.state._attrs[TEST_CLIMATE_ENTITY] = {
            "fan_mode": "Level 1",
            "fan_modes": [f"Level {level}" for level in range(1, 7)],
            "temperature": 20,
            "current_temperature": 20,
        }
        service_call = Mock()
        temptamer_main.service.call = service_call
        temptamer_main._system_now = lambda: now

        try:
            temptamer_main.run_control_pass(reason="PowerDay downstairs priority test")
        finally:
            temptamer_main._system_now = real_system_now

        self.assertTrue(temptamer_main.RUNTIME_STATE["powerday_downstairs_priority_active"])
        status_attributes = temptamer_main.state.getattr(temptamer_main.STATUS_ENTITY_ID)
        self.assertTrue(status_attributes["powerday_downstairs_priority_active"])
        self.assertEqual(
            status_attributes["powerday_downstairs_priority_upstairs_zones"],
            POWERDAY_DOWNSTAIRS_PRIORITY_UPSTAIRS_ZONE_KEYS,
        )
        self.assertIn(
            call(
                "switch",
                "turn_on",
                blocking=True,
                entity_id="switch.roof_wt32_hpctrl_e8dbd0_downstairs",
            ),
            service_call.call_args_list,
        )
        for zone_key in POWERDAY_DOWNSTAIRS_PRIORITY_UPSTAIRS_ZONE_KEYS:
            self.assertIn(
                call(
                    "switch",
                    "turn_off",
                    blocking=True,
                    entity_id=DEFAULT_SYSTEM_CONFIG.zones[zone_key].switch_entity_id,
                ),
                service_call.call_args_list,
            )
        self.assertIn(
            call(
                "climate",
                "set_fan_mode",
                blocking=True,
                entity_id=TEST_CLIMATE_ENTITY,
                fan_mode="Level 6",
            ),
            service_call.call_args_list,
        )

    def test_powerday_downstairs_priority_adds_two_physical_fan_levels_after_existing_boost(self):
        supported_fan_modes = tuple(f"Level {level}" for level in range(1, 10))
        demand = EquipmentDemand(heat_requested=True, max_temperature_deficit=1.0)

        self.assertEqual(
            resolve_fan_mode(
                "Level 1",
                "heat",
                demand,
                open_zone_count=4,
                supported_fan_modes=supported_fan_modes,
                base_fan_boost=1,
                additional_fan_levels=POWERDAY_DOWNSTAIRS_PRIORITY_FAN_BOOST_LEVELS,
            ),
            "Level 8",
        )
        self.assertEqual(
            resolve_fan_mode(
                "Level 1",
                "heat",
                demand,
                open_zone_count=4,
                supported_fan_modes=supported_fan_modes[:7],
                base_fan_boost=1,
                additional_fan_levels=POWERDAY_DOWNSTAIRS_PRIORITY_FAN_BOOST_LEVELS,
            ),
            "Level 7",
        )

    def test_powerday_free_power_downstairs_heat_adds_one_physical_fan_level(self):
        free_power_snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                        "input_select.temptamer_comfort_mode_downstairs": "Auto",
                        GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "0",
                    }
                )
            )
        )
        heat_demand = EquipmentDemand(heat_requested=True, max_temperature_deficit=1.0)

        fan_boost = temptamer_main._powerday_downstairs_free_power_fan_boost(
            free_power_snapshot,
            heat_demand,
            ("downstairs",),
            HVAC_HEAT,
        )

        self.assertEqual(fan_boost, POWERDAY_DOWNSTAIRS_FREE_POWER_FAN_BOOST_LEVELS)
        self.assertEqual(
            resolve_fan_mode(
                "Level 1",
                "heat",
                heat_demand,
                supported_fan_modes=("Level 1", "Level 2", "Level 3"),
                additional_fan_levels=fan_boost,
            ),
            "Level 2",
        )
        self.assertEqual(
            temptamer_main._powerday_downstairs_free_power_fan_boost(
                free_power_snapshot,
                EquipmentDemand(maintain_heat_mode=True),
                ("downstairs",),
                HVAC_HEAT,
            ),
            0,
        )
        self.assertEqual(
            temptamer_main._powerday_downstairs_free_power_fan_boost(
                free_power_snapshot,
                heat_demand,
                ("office",),
                HVAC_HEAT,
            ),
            0,
        )
        self.assertEqual(
            temptamer_main._powerday_downstairs_free_power_fan_boost(
                free_power_snapshot,
                heat_demand,
                ("downstairs",),
                HVAC_COOL,
            ),
            0,
        )

        paid_snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                        "input_select.temptamer_comfort_mode_downstairs": "Auto",
                        GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "1",
                    }
                )
            )
        )
        self.assertEqual(
            temptamer_main._powerday_downstairs_free_power_fan_boost(
                paid_snapshot,
                heat_demand,
                ("downstairs",),
                HVAC_HEAT,
            ),
            0,
        )
        self.assertEqual(
            max(fan_boost, POWERDAY_DOWNSTAIRS_PRIORITY_FAN_BOOST_LEVELS),
            POWERDAY_DOWNSTAIRS_PRIORITY_FAN_BOOST_LEVELS,
        )

    def test_powerday_free_power_downstairs_heat_dispatches_the_one_level_fan_boost(self):
        now = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)
        real_system_now = temptamer_main._system_now
        temptamer_main.state._values.clear()
        temptamer_main.state._attrs.clear()
        temptamer_main.RUNTIME_STATE.clear()
        temptamer_main.RUNTIME_STATE.update(deepcopy(self.original_runtime_state))
        temptamer_main.RUNTIME_STATE["last_successful_control_pass"] = now - timedelta(minutes=1)
        temptamer_main.state._values.update(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                    "input_select.temptamer_hvac_mode": "Heat",
                    "input_select.temptamer_comfort_mode_downstairs": "Auto",
                    GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "0",
                    "sensor.office_average_temperature": "24.0",
                    "sensor.average_dining_zone_temp": "24.0",
                    "sensor.downstairs_zone_average_temperature": "20.0",
                    "sensor.average_bed1_2_zone_temp": "24.0",
                    "sensor.average_bed3_4_zone_temp": "24.0",
                    "switch.roof_wt32_hpctrl_e8dbd0_downstairs": "on",
                    TEST_CLIMATE_ENTITY: "heat",
                }
            )
        )
        temptamer_main.state._attrs[TEST_CLIMATE_ENTITY] = {
            "fan_mode": "Level 1",
            "fan_modes": ["Level 1", "Level 2", "Level 3"],
            "temperature": 20,
            "current_temperature": 20,
        }
        service_call = Mock()
        temptamer_main.service.call = service_call
        temptamer_main._system_now = lambda: now

        try:
            temptamer_main.run_control_pass(reason="PowerDay downstairs fan boost test")
        finally:
            temptamer_main._system_now = real_system_now

        self.assertFalse(temptamer_main.RUNTIME_STATE["powerday_downstairs_priority_active"])
        self.assertIn(
            call(
                "climate",
                "set_fan_mode",
                blocking=True,
                entity_id=TEST_CLIMATE_ENTITY,
                fan_mode="Level 2",
            ),
            service_call.call_args_list,
        )

    def test_powerday_downstairs_priority_release_restores_upstairs_through_normal_antiflap(self):
        changed_at = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                powerday_downstairs_priority_state_map(
                    **{
                        "sensor.office_average_temperature": "20.0",
                        "sensor.average_dining_zone_temp": "20.0",
                        "sensor.average_bed1_2_zone_temp": "20.0",
                        "sensor.average_bed3_4_zone_temp": "20.0",
                        "switch.wt32_hpctrl_e8dbd0_office": "off",
                        "switch.wt32_hpctrl_e8dbd0_dining": "off",
                        "switch.roof_wt32_hpctrl_e8dbd0_downstairs": "on",
                        "switch.wt32_hpctrl_e8dbd0_bed_12": "off",
                        "switch.wt32_hpctrl_e8dbd0_bed_34": "off",
                    }
                )
            ),
            last_switch_changes={zone_key: changed_at for zone_key in POWERDAY_DOWNSTAIRS_PRIORITY_UPSTAIRS_ZONE_KEYS},
            now=changed_at + timedelta(minutes=5),
        )

        actions, _predicted_open = resolve_zone_actions(
            snapshot,
            changed_at + timedelta(minutes=5),
            operation_mode=HVAC_HEAT,
        )

        opened_upstairs = [action for action in actions if action.zone_key in POWERDAY_DOWNSTAIRS_PRIORITY_UPSTAIRS_ZONE_KEYS]
        self.assertEqual(len(opened_upstairs), 1)
        self.assertTrue(opened_upstairs[0].turn_on)

    def test_powerday_uses_zone_specific_initial_free_power_boosts(self):
        power_mode = TEST_SYSTEM_CONFIG.comfort_modes[COMFORT_MODE_POWER_DAY]
        self.assertEqual(
            power_mode.free_power_setpoint_boost,
            FreePowerSetpointBoost(initial=1.0, later=2.0),
        )
        self.assertEqual(
            power_mode.free_power_zone_setpoint_boosts,
            {
                "office": FreePowerSetpointBoost(initial=0.5, later=1.0),
                "bedroom_1_2": FreePowerSetpointBoost(initial=0.25, later=0.25),
                "downstairs": FreePowerSetpointBoost(initial=1.5, later=2.25),
            },
        )
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                        "input_select.temptamer_comfort_mode_downstairs": "Auto",
                        GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "0",
                        "sensor.downstairs_zone_average_temperature": "20.0",
                    }
                ),
                base_attr_map("21.0"),
            ),
            now=datetime(2026, 7, 25, 12, 59, tzinfo=timezone.utc),
        )

        base_day_living_scheme = TEST_SYSTEM_CONFIG.heat_control_schemes[SCHEME_DAY_LIVING]
        expected_day_living_boosts = {
            "office": 0.5,
            "dining": 1.0,
            "bedroom_1_2": 0.25,
            "bedroom_3_4": 1.0,
        }
        for zone_key, boost in expected_day_living_boosts.items():
            adjusted_continue_until = base_day_living_scheme.continue_until + boost
            self.assertEqual(snapshot.zones[zone_key].scheme.continue_until, adjusted_continue_until)
            self.assertEqual(snapshot.zones[zone_key].scheme.enable_outside, adjusted_continue_until - 0.75)
            self.assertEqual(snapshot.zones[zone_key].scheme.ideal_target, adjusted_continue_until - 0.5)

        downstairs_continue_until = TEST_SYSTEM_CONFIG.heat_control_schemes[SCHEME_DOWNSTAIRS].continue_until + 1.5
        self.assertEqual(snapshot.zones["downstairs"].scheme.continue_until, downstairs_continue_until)
        self.assertEqual(
            snapshot.zones["downstairs"].scheme.enable_outside,
            downstairs_continue_until - 0.75 + power_mode.free_power_downstairs_enable_outside_supplement,
        )
        self.assertEqual(snapshot.zones["downstairs"].scheme.ideal_target, downstairs_continue_until - 0.5)

    def test_powerday_applies_office_and_dining_free_power_supplement_only_above_19_downstairs(self):
        base_day_living_scheme = TEST_SYSTEM_CONFIG.heat_control_schemes[SCHEME_DAY_LIVING]

        at_threshold_snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                        "input_select.temptamer_comfort_mode_downstairs": "Auto",
                        GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "0",
                        "sensor.downstairs_zone_average_temperature": "19.0",
                    }
                )
            ),
            now=datetime(2026, 7, 25, 12, 59, tzinfo=timezone.utc),
        )
        above_threshold_snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                        "input_select.temptamer_comfort_mode_downstairs": "Auto",
                        GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "0",
                        "sensor.downstairs_zone_average_temperature": "19.1",
                    }
                )
            ),
            now=datetime(2026, 7, 25, 12, 59, tzinfo=timezone.utc),
        )

        for zone_key, boost in {"office": 0.5, "dining": 1.0}.items():
            adjusted_continue_until = base_day_living_scheme.continue_until + boost
            self.assertEqual(at_threshold_snapshot.zones[zone_key].scheme, base_day_living_scheme)
            self.assertEqual(above_threshold_snapshot.zones[zone_key].scheme.continue_until, adjusted_continue_until)
            self.assertEqual(above_threshold_snapshot.zones[zone_key].scheme.enable_outside, adjusted_continue_until - 0.75)
            self.assertEqual(above_threshold_snapshot.zones[zone_key].scheme.ideal_target, adjusted_continue_until - 0.5)
            self.assertEqual(
                above_threshold_snapshot.zones[zone_key].cool_scheme,
                TEST_SYSTEM_CONFIG.cool_control_schemes[SCHEME_DAY_LIVING],
            )

    def test_powerday_uses_zone_specific_later_free_power_boosts_at_1pm(self):
        power_mode = TEST_SYSTEM_CONFIG.comfort_modes[COMFORT_MODE_POWER_DAY]
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                        "input_select.temptamer_comfort_mode_downstairs": "Auto",
                        GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "0",
                        "sensor.downstairs_zone_average_temperature": "20.0",
                    }
                ),
                base_attr_map("21.0"),
            ),
            now=datetime(2026, 7, 25, 13, 0, tzinfo=timezone.utc),
        )

        base_day_living_scheme = TEST_SYSTEM_CONFIG.heat_control_schemes[SCHEME_DAY_LIVING]
        expected_day_living_boosts = {
            "office": 1.0,
            "dining": 2.0,
            "bedroom_1_2": 0.25,
            "bedroom_3_4": 2.0,
        }
        for zone_key, boost in expected_day_living_boosts.items():
            adjusted_continue_until = base_day_living_scheme.continue_until + boost
            self.assertEqual(snapshot.zones[zone_key].scheme.continue_until, adjusted_continue_until)
            self.assertEqual(snapshot.zones[zone_key].scheme.enable_outside, adjusted_continue_until - 0.75)
            self.assertEqual(snapshot.zones[zone_key].scheme.ideal_target, adjusted_continue_until - 0.5)

        downstairs_continue_until = TEST_SYSTEM_CONFIG.heat_control_schemes[SCHEME_DOWNSTAIRS].continue_until + 2.25
        self.assertEqual(snapshot.zones["downstairs"].scheme.continue_until, downstairs_continue_until)
        self.assertEqual(
            snapshot.zones["downstairs"].scheme.enable_outside,
            downstairs_continue_until - 0.75 + power_mode.free_power_downstairs_enable_outside_supplement,
        )

    def test_powerday_pv_promotes_zone_specific_later_boosts_before_1pm(self):
        now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
        temptamer_main.RUNTIME_STATE["powerday_pv_power_samples"] = [
            (now - timedelta(seconds=POWERDAY_FREE_POWER_PV_AVERAGE_WINDOW_SECONDS), 6.5)
        ]
        reader = FakeReader(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                    "input_select.temptamer_comfort_mode_downstairs": "Auto",
                    GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "0",
                    GOODWE_PV_POWER_SENSOR: "6.5",
                    "sensor.downstairs_zone_average_temperature": "20.0",
                }
            ),
            base_attr_map("21.0"),
        )

        later_active = temptamer_main._update_powerday_free_power_later_runtime_state(reader, now)
        snapshot = build_behavior_snapshot(
            reader,
            free_power_later_available=later_active,
            now=now,
        )

        self.assertTrue(later_active)
        self.assertTrue(snapshot.free_power_later_available)
        self.assertEqual(
            snapshot.zones["office"].scheme.continue_until,
            TEST_SYSTEM_CONFIG.heat_control_schemes[SCHEME_DAY_LIVING].continue_until + 1.0,
        )
        self.assertEqual(
            snapshot.zones["bedroom_1_2"].scheme.continue_until,
            TEST_SYSTEM_CONFIG.heat_control_schemes[SCHEME_DAY_LIVING].continue_until + 0.25,
        )
        self.assertEqual(
            snapshot.zones["downstairs"].scheme.continue_until,
            TEST_SYSTEM_CONFIG.heat_control_schemes[SCHEME_DOWNSTAIRS].continue_until + 2.25,
        )

    def test_powerday_pv_later_boost_holds_until_free_power_ends(self):
        start = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
        temptamer_main.RUNTIME_STATE["powerday_pv_power_samples"] = [
            (start - timedelta(seconds=POWERDAY_FREE_POWER_PV_AVERAGE_WINDOW_SECONDS), 6.5)
        ]
        active_reader = FakeReader(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                    GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "0",
                    GOODWE_PV_POWER_SENSOR: "6.5",
                }
            )
        )
        self.assertTrue(temptamer_main._update_powerday_free_power_later_runtime_state(active_reader, start))

        lower_pv_reader = FakeReader(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                    GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "0",
                    GOODWE_PV_POWER_SENSOR: "0",
                }
            )
        )
        self.assertTrue(
            temptamer_main._update_powerday_free_power_later_runtime_state(
                lower_pv_reader,
                start + timedelta(minutes=10),
            )
        )

        paid_power_reader = FakeReader(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                    GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "1",
                    GOODWE_PV_POWER_SENSOR: "0",
                }
            )
        )
        self.assertFalse(
            temptamer_main._update_powerday_free_power_later_runtime_state(
                paid_power_reader,
                start + timedelta(minutes=20),
            )
        )

    def test_powerday_uses_night_scheme_downstairs_before_free_power_in_heat_mode(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                        "input_select.temptamer_comfort_mode_downstairs": "Auto",
                    }
                )
            ),
            now=datetime(2026, 7, 25, 10, 59, tzinfo=timezone.utc),
        )

        self.assertEqual(snapshot.zones["downstairs"].scheme.name, SCHEME_NIGHT)

    def test_powerday_uses_downstairs_scheme_at_free_power_start_in_heat_mode(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                        "input_select.temptamer_comfort_mode_downstairs": "Auto",
                    }
                )
            ),
            now=datetime(2026, 7, 25, 11, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(snapshot.zones["downstairs"].scheme.name, SCHEME_DOWNSTAIRS)

    def test_powerday_keeps_downstairs_scheme_before_free_power_in_cool_mode(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                        "input_select.temptamer_hvac_mode": "Cool",
                        "input_select.temptamer_comfort_mode_downstairs": "Auto",
                    }
                )
            ),
            now=datetime(2026, 7, 25, 10, 59, tzinfo=timezone.utc),
        )

        self.assertEqual(snapshot.zones["downstairs"].scheme.name, SCHEME_DOWNSTAIRS)
        self.assertEqual(snapshot.zones["downstairs"].cool_scheme.name, SCHEME_DOWNSTAIRS)

    def test_powerday_heat_soaks_when_battery_export_heat_sink_is_active(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                        "input_select.temptamer_comfort_mode_downstairs": "Auto",
                        GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "1",
                        "sensor.downstairs_zone_average_temperature": "20.0",
                    }
                ),
                base_attr_map("21.0"),
            ),
            heat_sink_available=True,
            now=datetime(2026, 7, 25, 12, 59, tzinfo=timezone.utc),
        )

        self.assertFalse(snapshot.free_power_available)
        self.assertTrue(snapshot.heat_sink_available)
        self.assertEqual(snapshot.zones["dining"].scheme.name, SCHEME_DAY_LIVING)
        self.assertEqual(snapshot.zones["downstairs"].scheme.name, SCHEME_DOWNSTAIRS)
        self.assertEqual(snapshot.zones["bedroom_1_2"].scheme.name, SCHEME_DAY_LIVING)
        self.assertEqual(snapshot.zones["bedroom_3_4"].scheme.name, SCHEME_DAY_LIVING)
        adjusted_continue_until = (
            TEST_SYSTEM_CONFIG.heat_control_schemes[SCHEME_DAY_LIVING].continue_until
            + 1.0
        )
        self.assertEqual(snapshot.zones["dining"].scheme.continue_until, adjusted_continue_until)
        downstairs_continue_until = (
            TEST_SYSTEM_CONFIG.heat_control_schemes[SCHEME_DOWNSTAIRS].continue_until
            + 1.5
        )
        self.assertEqual(snapshot.zones["downstairs"].scheme.continue_until, downstairs_continue_until)
        self.assertEqual(snapshot.zones["downstairs"].scheme.enable_outside, downstairs_continue_until - 0.75)
        self.assertEqual(
            snapshot.comfort_mode_behavior.fan_speed_level(
                2.6,
                1,
                current_speed_level=1,
                free_power_available=snapshot.heat_sink_available,
            ),
            2,
        )

    def test_powerday_battery_export_heat_sink_activates_outside_free_period(self):
        now = datetime(2026, 7, 25, 12, 10, tzinfo=timezone.utc)
        temptamer_main.RUNTIME_STATE["powerday_export_power_samples"] = [
            (now - timedelta(seconds=POWERDAY_EXPORT_AVERAGE_WINDOW_SECONDS), -1.2)
        ]
        reader = FakeReader(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                    GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "1",
                    GOODWE_BATTERY_REMAINING_SENSOR: "96",
                    EAGLE_200_POWER_DEMAND_SENSOR: "-1.2",
                }
            ),
            base_attr_map("21.0"),
        )

        active = temptamer_main._update_powerday_heat_sink_runtime_state(reader, now)
        snapshot = build_behavior_snapshot(reader, heat_sink_available=active, now=now)

        self.assertTrue(active)
        self.assertFalse(snapshot.free_power_available)
        self.assertTrue(snapshot.heat_sink_available)
        self.assertEqual(snapshot.zones["dining"].scheme.name, SCHEME_DAY_LIVING)
        self.assertAlmostEqual(temptamer_main.RUNTIME_STATE["powerday_export_average"], -1.2)
        self.assertEqual(temptamer_main.RUNTIME_STATE["powerday_heat_sink_started_at"], now)
        self.assertEqual(
            temptamer_main.RUNTIME_STATE["powerday_heat_sink_hold_until"],
            now + timedelta(seconds=POWERDAY_HEAT_SINK_MIN_SECONDS),
        )

    def test_powerday_full_battery_applies_the_initial_free_power_setpoint_boost(self):
        now = datetime(2026, 7, 25, 13, 30, tzinfo=timezone.utc)
        reader = FakeReader(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                    "input_select.temptamer_comfort_mode_downstairs": "Auto",
                    GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "1",
                    GOODWE_BATTERY_REMAINING_SENSOR: "95",
                    "sensor.downstairs_zone_average_temperature": "20.0",
                }
            ),
            base_attr_map("21.0"),
        )

        active = temptamer_main._update_powerday_battery_free_power_boost_runtime_state(reader)
        snapshot = build_behavior_snapshot(
            reader,
            battery_free_power_boost_available=active,
            now=now,
        )
        power_mode = TEST_SYSTEM_CONFIG.comfort_modes[COMFORT_MODE_POWER_DAY]

        self.assertTrue(active)
        self.assertFalse(snapshot.free_power_available)
        self.assertFalse(snapshot.heat_sink_available)
        self.assertTrue(snapshot.battery_free_power_boost_available)
        self.assertEqual(
            snapshot.zones["downstairs"].scheme.continue_until,
            TEST_SYSTEM_CONFIG.heat_control_schemes[SCHEME_DOWNSTAIRS].continue_until
            + power_mode.free_power_zone_setpoint_boosts["downstairs"].initial,
        )

    def test_powerday_full_battery_initial_boost_holds_at_ninety_and_releases_below(self):
        active_reader = FakeReader(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                    GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "1",
                    GOODWE_BATTERY_REMAINING_SENSOR: "95",
                }
            )
        )
        self.assertTrue(temptamer_main._update_powerday_battery_free_power_boost_runtime_state(active_reader))

        hold_reader = FakeReader(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                    GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "1",
                    GOODWE_BATTERY_REMAINING_SENSOR: "90",
                }
            )
        )
        self.assertTrue(temptamer_main._update_powerday_battery_free_power_boost_runtime_state(hold_reader))
        self.assertIn("holding initial boost", temptamer_main.RUNTIME_STATE["powerday_battery_free_power_boost_reason"])

        free_price_hold_reader = FakeReader(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                    GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "0",
                    GOODWE_BATTERY_REMAINING_SENSOR: "90",
                }
            )
        )
        self.assertTrue(temptamer_main._update_powerday_battery_free_power_boost_runtime_state(free_price_hold_reader))

        release_reader = FakeReader(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                    GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "1",
                    GOODWE_BATTERY_REMAINING_SENSOR: "89.9",
                }
            )
        )
        self.assertFalse(temptamer_main._update_powerday_battery_free_power_boost_runtime_state(release_reader))
        self.assertIn("battery 89.9 < 90.0", temptamer_main.RUNTIME_STATE["powerday_battery_free_power_boost_reason"])

    def test_powerday_battery_export_heat_sink_requires_battery_above_threshold(self):
        now = datetime(2026, 7, 25, 12, 10, tzinfo=timezone.utc)
        temptamer_main.RUNTIME_STATE["powerday_export_power_samples"] = [
            (now - timedelta(seconds=POWERDAY_EXPORT_AVERAGE_WINDOW_SECONDS), -1.2)
        ]
        reader = FakeReader(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                    GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "1",
                    GOODWE_BATTERY_REMAINING_SENSOR: "95",
                    EAGLE_200_POWER_DEMAND_SENSOR: "-1.2",
                }
            ),
            base_attr_map("21.0"),
        )

        active = temptamer_main._update_powerday_heat_sink_runtime_state(reader, now)

        self.assertFalse(active)
        self.assertIsNone(temptamer_main.RUNTIME_STATE["powerday_heat_sink_started_at"])
        self.assertIn("battery 95.0 <= 95.0", temptamer_main.RUNTIME_STATE["powerday_heat_sink_reason"])

    def test_powerday_battery_export_heat_sink_requires_export_average_below_threshold(self):
        now = datetime(2026, 7, 25, 12, 10, tzinfo=timezone.utc)
        temptamer_main.RUNTIME_STATE["powerday_export_power_samples"] = [
            (now - timedelta(seconds=POWERDAY_EXPORT_AVERAGE_WINDOW_SECONDS), -1.0)
        ]
        reader = FakeReader(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                    GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "1",
                    GOODWE_BATTERY_REMAINING_SENSOR: "96",
                    EAGLE_200_POWER_DEMAND_SENSOR: "-1.0",
                }
            ),
            base_attr_map("21.0"),
        )

        active = temptamer_main._update_powerday_heat_sink_runtime_state(reader, now)

        self.assertFalse(active)
        self.assertAlmostEqual(temptamer_main.RUNTIME_STATE["powerday_export_average"], -1.0)
        self.assertIn("export average -1.00 >= -1.00", temptamer_main.RUNTIME_STATE["powerday_heat_sink_reason"])

    def test_powerday_battery_export_heat_sink_requires_full_sample_coverage(self):
        now = datetime(2026, 7, 25, 12, 10, tzinfo=timezone.utc)
        temptamer_main.RUNTIME_STATE["powerday_export_power_samples"] = [(now - timedelta(minutes=9), -1.2)]
        reader = FakeReader(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                    GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "1",
                    GOODWE_BATTERY_REMAINING_SENSOR: "96",
                    EAGLE_200_POWER_DEMAND_SENSOR: "-1.2",
                }
            ),
            base_attr_map("21.0"),
        )

        active = temptamer_main._update_powerday_heat_sink_runtime_state(reader, now)

        self.assertFalse(active)
        self.assertIsNone(temptamer_main.RUNTIME_STATE["powerday_export_average"])
        self.assertIn("lacks 10-minute coverage", temptamer_main.RUNTIME_STATE["powerday_heat_sink_reason"])

    def test_powerday_battery_export_heat_sink_holds_for_minimum_duration_when_eligibility_drops(self):
        start = datetime(2026, 7, 25, 12, 10, tzinfo=timezone.utc)
        temptamer_main.RUNTIME_STATE["powerday_export_power_samples"] = [
            (start - timedelta(seconds=POWERDAY_EXPORT_AVERAGE_WINDOW_SECONDS), -1.2)
        ]
        active_reader = FakeReader(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                    GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "1",
                    GOODWE_BATTERY_REMAINING_SENSOR: "96",
                    EAGLE_200_POWER_DEMAND_SENSOR: "-1.2",
                }
            )
        )
        self.assertTrue(temptamer_main._update_powerday_heat_sink_runtime_state(active_reader, start))

        hold_reader = FakeReader(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                    GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "1",
                    GOODWE_BATTERY_REMAINING_SENSOR: "unknown",
                    EAGLE_200_POWER_DEMAND_SENSOR: "unavailable",
                }
            )
        )
        active = temptamer_main._update_powerday_heat_sink_runtime_state(hold_reader, start + timedelta(minutes=5))

        self.assertTrue(active)
        self.assertEqual(temptamer_main.RUNTIME_STATE["powerday_heat_sink_started_at"], start)
        self.assertEqual(
            temptamer_main.RUNTIME_STATE["powerday_heat_sink_hold_until"],
            start + timedelta(seconds=POWERDAY_HEAT_SINK_MIN_SECONDS),
        )
        self.assertIn("minimum hold until", temptamer_main.RUNTIME_STATE["powerday_heat_sink_reason"])

    def test_powerday_battery_export_heat_sink_clears_after_minimum_duration_when_eligibility_is_false(self):
        start = datetime(2026, 7, 25, 12, 10, tzinfo=timezone.utc)
        temptamer_main.RUNTIME_STATE["powerday_export_power_samples"] = [
            (start - timedelta(seconds=POWERDAY_EXPORT_AVERAGE_WINDOW_SECONDS), -1.2)
        ]
        active_reader = FakeReader(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                    GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "1",
                    GOODWE_BATTERY_REMAINING_SENSOR: "96",
                    EAGLE_200_POWER_DEMAND_SENSOR: "-1.2",
                }
            )
        )
        self.assertTrue(temptamer_main._update_powerday_heat_sink_runtime_state(active_reader, start))

        inactive_reader = FakeReader(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                    GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "1",
                    GOODWE_BATTERY_REMAINING_SENSOR: "unknown",
                    EAGLE_200_POWER_DEMAND_SENSOR: "unavailable",
                }
            )
        )
        active = temptamer_main._update_powerday_heat_sink_runtime_state(
            inactive_reader,
            start + timedelta(seconds=POWERDAY_HEAT_SINK_MIN_SECONDS),
        )

        self.assertFalse(active)
        self.assertIsNone(temptamer_main.RUNTIME_STATE["powerday_heat_sink_started_at"])
        self.assertIsNone(temptamer_main.RUNTIME_STATE["powerday_heat_sink_hold_until"])

    def test_comfort_mode_name_zone_override_uses_selected_comfort_mode_for_zone(self):
        snapshot = build_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "input_select.temptamer_comfort_mode_bed34": "Day",
                    }
                ),
                base_attr_map("21.0"),
            )
        )

        self.assertEqual(snapshot.zones["bedroom_3_4"].applied_comfort_mode, "Day")
        self.assertEqual(snapshot.zones["bedroom_3_4"].scheme.name, "Bedroom")

    def test_scheme_name_zone_override_uses_selected_scheme_for_bedroom_heat_demand(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "input_select.temptamer_comfort_mode_bed34": "DayLiving",
                        "sensor.office_average_temperature": "21.5",
                        "sensor.average_dining_zone_temp": "18.0",
                        "sensor.average_bed1_2_zone_temp": "19.5",
                        "sensor.average_bed3_4_zone_temp": "16.3",
                    }
                ),
                base_attr_map("16.4"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("bedroom_3_4",), operation_mode=HVAC_HEAT)

        self.assertEqual(snapshot.zones["bedroom_3_4"].applied_comfort_mode, "DayLiving")
        self.assertEqual(snapshot.zones["bedroom_3_4"].scheme.name, "DayLiving")
        self.assertEqual(snapshot.zones["bedroom_1_2"].applied_comfort_mode, "Office")
        self.assertEqual(snapshot.zones["bedroom_1_2"].scheme.name, "Bedroom")
        self.assertNotIn("bedroom_1_2", demand.requested_by_zones)
        self.assertEqual(demand.requested_by_zones, ("bedroom_3_4",))
        self.assertTrue(demand.heat_requested)

    def test_bathroom_override_uses_bathroom_sensor_for_bedroom_3_4(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "input_select.temptamer_comfort_mode_bed34": "Bathroom",
                        "sensor.office_average_temperature": "21.5",
                        "sensor.average_bed3_4_zone_temp": "18.0",
                        "sensor.bathroom_motion_temperature": "13.5",
                    }
                ),
                base_attr_map("21.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("bedroom_3_4",), operation_mode=HVAC_HEAT)

        self.assertEqual(snapshot.zones["bedroom_3_4"].applied_comfort_mode, "Bathroom")
        self.assertEqual(snapshot.zones["bedroom_3_4"].scheme.name, "Bathroom")
        self.assertEqual(snapshot.zones["bedroom_3_4"].current_temp, 13.5)
        self.assertTrue(demand.heat_requested)
        self.assertEqual(demand.requested_by_zones, ("bedroom_3_4",))

    def test_zone_off_override_closes_already_open_bedroom_3_4(self):
        now = datetime(2026, 7, 4, 17, 24, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "input_select.temptamer_comfort_mode_bed34": SCHEME_OFF,
                        "sensor.office_average_temperature": "19.8",
                        "sensor.average_dining_zone_temp": "21.0",
                        "sensor.average_bed1_2_zone_temp": "21.0",
                        "sensor.average_bed3_4_zone_temp": "18.2",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                        "switch.wt32_hpctrl_e8dbd0_bed_34": "on",
                    }
                ),
                base_attr_map("21.5", temperature="18.0"),
            ),
            now=now,
        )

        actions, predicted_open = resolve_zone_actions(snapshot, now, operation_mode=HVAC_HEAT)

        self.assertEqual(snapshot.zones["bedroom_3_4"].scheme.name, SCHEME_OFF)
        self.assertFalse(snapshot.zones["bedroom_3_4"].is_enabled_by_mode)
        self.assertIn(
            ("bedroom_3_4", False, "mode disabled by scheme Off"),
            [(action.zone_key, action.turn_on, action.reason) for action in actions],
        )
        self.assertEqual(predicted_open, ("office",))

        demand = resolve_equipment_demand(snapshot, predicted_open, operation_mode=HVAC_HEAT)
        self.assertEqual(demand.requested_by_zones, ("office",))
        self.assertNotIn("bedroom_3_4", predicted_open)

    def test_zone_off_override_is_not_reopened_for_safety(self):
        now = datetime(2026, 7, 4, 17, 24, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "input_select.temptamer_comfort_mode_office": SCHEME_OFF,
                        "input_select.temptamer_comfort_mode_dining": SCHEME_OFF,
                        "input_select.temptamer_comfort_mode_bed12": SCHEME_OFF,
                        "input_select.temptamer_comfort_mode_bed34": SCHEME_OFF,
                        "switch.wt32_hpctrl_e8dbd0_bed_34": "on",
                    }
                ),
                base_attr_map("21.5"),
            ),
            now=now,
        )

        actions, predicted_open = resolve_zone_actions(snapshot, now, operation_mode=HVAC_HEAT)

        self.assertEqual(
            [(action.zone_key, action.turn_on) for action in actions],
            [("bedroom_3_4", False)],
        )
        self.assertEqual(predicted_open, ())

    def test_default_zone_setpoint_deltas_are_configured_per_zone(self):
        self.assertEqual(DEFAULT_SYSTEM_CONFIG.zones["office"].setpoint_delta_from_inlet, -2.0)
        self.assertEqual(DEFAULT_SYSTEM_CONFIG.zones["dining"].setpoint_delta_from_inlet, -1.0)
        self.assertEqual(DEFAULT_SYSTEM_CONFIG.zones["downstairs"].setpoint_delta_from_inlet, -1.0)
        self.assertEqual(
            DEFAULT_SYSTEM_CONFIG.zones["downstairs"].sensor_entity_id,
            "sensor.downstairs_zone_average_temperature",
        )
        self.assertEqual(
            DEFAULT_SYSTEM_CONFIG.zones["downstairs"].switch_entity_id,
            "switch.roof_wt32_hpctrl_e8dbd0_downstairs",
        )
        self.assertEqual(
            DEFAULT_SYSTEM_CONFIG.zone_comfort_mode_entities["downstairs"],
            "input_select.temptamer_comfort_mode_downstairs",
        )
        self.assertIn(
            "input_select.temptamer_comfort_mode_downstairs",
            IMMEDIATE_RECONCILIATION_TRIGGER_ENTITIES,
        )
        self.assertEqual(DEFAULT_SYSTEM_CONFIG.global_setpoint_adjustment_entity, GLOBAL_SETPOINT_ADJUSTMENT_ENTITY)
        self.assertIn(GLOBAL_SETPOINT_ADJUSTMENT_ENTITY, NORMAL_RECALCULATION_TRIGGER_ENTITIES)

    def test_default_bedroom_cooling_band_uses_habitable_room_targets(self):
        scheme = DEFAULT_SYSTEM_CONFIG.cool_control_schemes[SCHEME_BEDROOM]

        self.assertEqual(scheme.enable_outside, 22.0)
        self.assertEqual(scheme.ideal_target, 21.5)
        self.assertEqual(scheme.continue_until, 20.0)

    def test_control_trigger_sets_separate_user_reconciliation_from_normal_recalculation(self):
        self.assertIn(DEFAULT_SYSTEM_CONFIG.comfort_mode_entity, IMMEDIATE_RECONCILIATION_TRIGGER_ENTITIES)
        self.assertIn(DEFAULT_SYSTEM_CONFIG.hvac_mode_entity, IMMEDIATE_RECONCILIATION_TRIGGER_ENTITIES)
        self.assertTrue(
            set(DEFAULT_SYSTEM_CONFIG.zone_comfort_mode_entities.values())
            <= set(IMMEDIATE_RECONCILIATION_TRIGGER_ENTITIES)
        )
        self.assertTrue(
            set(IMMEDIATE_RECONCILIATION_TRIGGER_ENTITIES).isdisjoint(NORMAL_RECALCULATION_TRIGGER_ENTITIES)
        )
        self.assertIn(DEFAULT_SYSTEM_CONFIG.zones["office"].sensor_entity_id, NORMAL_RECALCULATION_TRIGGER_ENTITIES)

    def test_control_trigger_handlers_use_their_respective_reconciliation_behavior(self):
        with patch.object(temptamer_main, "_run_enabled_control_pass") as run_control_pass:
            temptamer_main.temptamer_immediate_reconciliation_requested()

        run_control_pass.assert_called_once_with(
            reason="immediate comfort/HVAC selection reconciliation",
            comfort_mode_changed=True,
        )

        with patch.object(temptamer_main, "_run_enabled_control_pass") as run_control_pass:
            temptamer_main.temptamer_normal_recalculation_requested()

        run_control_pass.assert_called_once_with(reason="normal state recalculation")

    def test_normal_recalculation_triggers_are_coalesced(self):
        temptamer_main.RUNTIME_STATE["normal_recalculation_generation"] = 0
        scheduled = []

        with patch.object(temptamer_main.task, "create", side_effect=lambda func, *args: scheduled.append((func, args))):
            temptamer_main.temptamer_normal_recalculation_requested()
            temptamer_main.temptamer_normal_recalculation_requested()

        self.assertEqual(len(scheduled), 2)
        with (
            patch.object(temptamer_main.task, "sleep"),
            patch.object(temptamer_main, "_run_enabled_control_pass") as run_control_pass,
        ):
            scheduled[0][0](*scheduled[0][1])
            run_control_pass.assert_not_called()
            scheduled[1][0](*scheduled[1][1])

        run_control_pass.assert_called_once_with(reason="normal state recalculation")

    def test_immediate_reconciliation_invalidates_pending_normal_recalculation(self):
        temptamer_main.RUNTIME_STATE["normal_recalculation_generation"] = 0
        scheduled = []
        with patch.object(temptamer_main.task, "create", side_effect=lambda func, *args: scheduled.append((func, args))):
            temptamer_main.temptamer_normal_recalculation_requested()

        with patch.object(temptamer_main, "_run_enabled_control_pass") as run_control_pass:
            temptamer_main.temptamer_immediate_reconciliation_requested()
            scheduled[0][0](*scheduled[0][1])

        run_control_pass.assert_called_once_with(
            reason="immediate comfort/HVAC selection reconciliation",
            comfort_mode_changed=True,
        )

    def test_fan_rundown_hold_keeps_zones_open_when_hvac_is_explicitly_off(self):
        now = datetime(2026, 8, 30, 7, 46, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_hvac_mode": "Off",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                    }
                ),
                base_attr_map("22.0"),
            ),
            now=now,
        )

        actions, predicted_open = resolve_zone_actions(
            snapshot,
            now,
            operation_mode=HVAC_HEAT,
            hold_closing_zones=True,
        )

        self.assertEqual(actions, [])
        self.assertEqual(predicted_open, ("office",))

    def test_build_snapshot_propagates_zone_setpoint_deltas(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(),
                base_attr_map("19.0"),
            )
        )

        self.assertEqual(snapshot.zones["office"].setpoint_delta_from_inlet, -2.0)
        self.assertEqual(snapshot.zones["dining"].setpoint_delta_from_inlet, -1.0)

    def test_build_snapshot_uses_climate_current_temperature_when_house_sensor_missing(self):
        snapshot = build_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Night",
                        "sensor.home_temperature": "unknown",
                        "sensor.office_average_temperature": "unavailable",
                        "sensor.average_dining_zone_temp": "16.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "unknown",
                        "switch.wt32_hpctrl_e8dbd0_dining": "on",
                        "switch.wt32_hpctrl_e8dbd0_bed_34": "on",
                    }
                ),
                base_attr_map("22.1"),
            )
        )

        self.assertEqual(snapshot.inlet_temp, 22.1)
        self.assertEqual(snapshot.zones["office"].current_temp, 22.1)
        self.assertEqual(snapshot.zones["bedroom_3_4"].current_temp, 22.1)

    def test_min_sensor_can_trigger_heat_when_max_is_at_continue_until(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.office_average_temperature": "20.5",
                        "sensor.office_minimum_temperature": "19.5",
                        "sensor.office_maximum_temperature": "22.0",
                    }
                ),
                base_attr_map("20.0"),
            )
        )

        self.assertIn("office", snapshot.heat_calling_zones)

    def test_min_sensor_heat_request_is_suppressed_when_max_exceeds_continue_until(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.office_average_temperature": "20.5",
                        "sensor.office_minimum_temperature": "19.5",
                        "sensor.office_maximum_temperature": "22.1",
                    }
                ),
                base_attr_map("20.0"),
            )
        )

        self.assertNotIn("office", snapshot.heat_calling_zones)

    def test_heating_continuation_still_uses_average_sensor_when_below_continue_until(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.office_average_temperature": "21.8",
                        "sensor.office_minimum_temperature": "19.0",
                        "sensor.office_maximum_temperature": "22.5",
                    }
                ),
                base_attr_map("20.0"),
            )
        )

        self.assertIn("office", snapshot.continue_heating_zones)

    def test_heating_continuation_from_min_sensor_is_suppressed_when_max_exceeds_continue_until(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.office_average_temperature": "22.0",
                        "sensor.office_minimum_temperature": "21.5",
                        "sensor.office_maximum_temperature": "22.1",
                    }
                ),
                base_attr_map("20.0"),
            )
        )

        self.assertNotIn("office", snapshot.continue_heating_zones)

    def test_missing_max_sensor_preserves_min_sensor_heating_behavior(self):
        state_map = base_state_map(
            **{
                "input_select.temptamer_comfort_mode": "Office",
                "sensor.office_average_temperature": "20.5",
                "sensor.office_minimum_temperature": "19.5",
                "sensor.office_maximum_temperature": None,
            }
        )
        reader = FakeReader(state_map, base_attr_map("20.0"))

        snapshot = build_behavior_snapshot(reader)

        self.assertIn("office", snapshot.heat_calling_zones)

        state_map["sensor.office_average_temperature"] = "22.0"
        state_map["sensor.office_minimum_temperature"] = "21.5"
        continuation_snapshot = build_behavior_snapshot(reader)

        self.assertIn("office", continuation_snapshot.continue_heating_zones)

    def test_zone_actions_respect_antiflap_but_allow_mode_change(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "sensor.home_temperature": "18.0",
                        "sensor.office_average_temperature": "17.0",
                        "sensor.average_dining_zone_temp": "22.0",
                        "sensor.average_bed1_2_zone_temp": "20.0",
                        "sensor.average_bed3_4_zone_temp": "20.0",
                        "switch.wt32_hpctrl_e8dbd0_dining": "on",
                    }
                ),
                base_attr_map("19.0"),
            ),
            last_switch_changes={
                "office": datetime(2026, 1, 1, 12, 0, 0),
                "dining": datetime(2026, 1, 1, 12, 0, 0),
            },
        )
        now = datetime(2026, 1, 1, 12, 2, 0)

        actions, predicted_open = resolve_zone_actions(snapshot, now, operation_mode=HVAC_HEAT, comfort_mode_changed=False)

        self.assertEqual(actions, [])
        self.assertEqual(predicted_open, ("dining",))

        actions, predicted_open = resolve_zone_actions(snapshot, now, operation_mode=HVAC_HEAT, comfort_mode_changed=True)

        self.assertCountEqual(
            [(action.zone_key, action.turn_on) for action in actions],
            [("dining", False), ("office", True)],
        )
        self.assertEqual(predicted_open, ("office",))

    def test_mode_change_closes_the_last_satisfied_zone_without_creating_a_safety_open_zone(self):
        now = datetime(2026, 5, 7, 12, 1, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Night",
                        "input_select.temptamer_comfort_mode_office": "Auto",
                        "sensor.office_average_temperature": "20.0",
                        "sensor.average_dining_zone_temp": "18.0",
                        "sensor.average_bed1_2_zone_temp": "18.0",
                        "sensor.average_bed3_4_zone_temp": "18.0",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                    }
                ),
                base_attr_map("22.0"),
            ),
            now=now,
        )

        self.assertEqual(snapshot.zones["office"].scheme.name, SCHEME_NIGHT)

        actions, predicted_open = resolve_zone_actions(
            snapshot,
            now,
            operation_mode=HVAC_HEAT,
            comfort_mode_changed=True,
        )

        self.assertEqual([(action.zone_key, action.turn_on) for action in actions], [("office", False)])
        self.assertEqual(predicted_open, ())

    def test_zone_actions_force_safety_open_with_realistic_temperatures(self):
        now = datetime(2026, 5, 7, 12, 1, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "sensor.home_temperature": "18.0",
                        "sensor.office_average_temperature": "17.5",
                        "sensor.average_dining_zone_temp": "17.8",
                    }
                ),
                base_attr_map("18.5"),
            ),
            last_switch_changes={
                "office": datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc),
                "dining": datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc),
            },
            now=now,
        )

        actions, predicted_open = resolve_zone_actions(snapshot, now, operation_mode=HVAC_HEAT, comfort_mode_changed=False)

        self.assertEqual(predicted_open, ("office",))
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].zone_key, "office")
        self.assertTrue(actions[0].turn_on)
        self.assertTrue(actions[0].safety_required)
        self.assertFalse(actions[0].discretionary)
        self.assertIn("overriding anti-flap delay", actions[0].reason)

    def test_zone_actions_keep_same_safety_zone_when_multiple_zones_need_heat(self):
        first_now = datetime(2026, 5, 7, 12, 3, 0, tzinfo=timezone.utc)
        first_snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.office_average_temperature": "18.5",
                        "sensor.average_dining_zone_temp": "18.5",
                        "sensor.average_bed1_2_zone_temp": "18.0",
                        "sensor.average_bed3_4_zone_temp": "18.0",
                    }
                ),
                base_attr_map("18.5"),
            ),
            last_switch_changes={
                "office": datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc),
                "dining": datetime(2026, 5, 7, 11, 59, 0, tzinfo=timezone.utc),
            },
            now=first_now,
        )

        first_actions, first_predicted_open = resolve_zone_actions(
            first_snapshot,
            first_now,
            operation_mode=HVAC_HEAT,
            comfort_mode_changed=False,
        )

        self.assertEqual(first_predicted_open, ("office",))
        self.assertEqual(len(first_actions), 1)
        self.assertEqual(first_actions[0].zone_key, "office")
        self.assertTrue(first_actions[0].safety_required)

        second_now = datetime(2026, 5, 7, 12, 3, 30, tzinfo=timezone.utc)
        second_snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.office_average_temperature": "18.5",
                        "sensor.average_dining_zone_temp": "18.5",
                        "sensor.average_bed1_2_zone_temp": "18.0",
                        "sensor.average_bed3_4_zone_temp": "18.0",
                    }
                ),
                base_attr_map("18.5"),
            ),
            last_switch_changes={
                "office": first_now,
                "dining": datetime(2026, 5, 7, 11, 59, 0, tzinfo=timezone.utc),
            },
            now=second_now,
        )

        second_actions, second_predicted_open = resolve_zone_actions(
            second_snapshot,
            second_now,
            operation_mode=HVAC_HEAT,
            comfort_mode_changed=False,
        )

        self.assertEqual(second_predicted_open, ("office",))
        self.assertEqual(len(second_actions), 1)
        self.assertEqual(second_actions[0].zone_key, "office")
        self.assertTrue(second_actions[0].safety_required)

    def test_high_fan_does_not_close_office_before_its_continue_until_target(self):
        now = datetime(2026, 8, 24, 12, 0, 0, tzinfo=timezone.utc)
        shared_overrides = {
            "input_select.temptamer_comfort_mode": "Office",
            "input_select.temptamer_comfort_mode_downstairs": SCHEME_OFF,
            "input_select.temptamer_comfort_mode_bed12": SCHEME_OFF,
            "input_select.temptamer_comfort_mode_bed34": SCHEME_OFF,
            "sensor.average_dining_zone_temp": "13.0",
            "switch.wt32_hpctrl_e8dbd0_office": "on",
            "switch.wt32_hpctrl_e8dbd0_dining": "on",
        }
        heating_snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        **shared_overrides,
                        "sensor.office_average_temperature": "21.0",
                    }
                ),
                base_attr_map("20.0"),
            ),
            now=now,
        )

        heating_actions, heating_predicted_open = resolve_zone_actions(
            heating_snapshot,
            now,
            operation_mode=HVAC_HEAT,
            requested_fan_speed_level=6,
        )

        self.assertEqual(heating_actions, [])
        self.assertEqual(heating_predicted_open, ("dining", "office"))

        threshold_actions, threshold_predicted_open = resolve_zone_actions(
            heating_snapshot,
            now,
            operation_mode=HVAC_HEAT,
            requested_fan_speed_level=5,
        )

        self.assertEqual(threshold_actions, [])
        self.assertEqual(threshold_predicted_open, ("dining", "office"))

        cooling_snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        **shared_overrides,
                        "input_select.temptamer_hvac_mode": "Cool",
                        "sensor.office_average_temperature": "21.0",
                        "sensor.average_dining_zone_temp": "16.5",
                    }
                ),
                base_attr_map("20.0"),
            ),
            now=now,
        )

        cooling_actions, cooling_predicted_open = resolve_zone_actions(
            cooling_snapshot,
            now,
            operation_mode=HVAC_COOL,
            requested_fan_speed_level=6,
        )

        self.assertEqual(cooling_actions, [])
        self.assertEqual(cooling_predicted_open, ("dining", "office"))

    def test_high_fan_office_closure_uses_the_continue_until_target(self):
        now = datetime(2026, 8, 24, 12, 0, 0, tzinfo=timezone.utc)
        shared_overrides = {
            "input_select.temptamer_comfort_mode": "Office",
            "input_select.temptamer_comfort_mode_downstairs": SCHEME_OFF,
            "input_select.temptamer_comfort_mode_bed12": SCHEME_OFF,
            "input_select.temptamer_comfort_mode_bed34": SCHEME_OFF,
            "sensor.average_dining_zone_temp": "13.0",
            "switch.wt32_hpctrl_e8dbd0_office": "on",
            "switch.wt32_hpctrl_e8dbd0_dining": "on",
        }
        heating_snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        **shared_overrides,
                        "sensor.office_average_temperature": "22.0",
                    }
                ),
                base_attr_map("20.0"),
            ),
            now=now,
        )
        cooling_snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        **shared_overrides,
                        "input_select.temptamer_hvac_mode": "Cool",
                        "sensor.office_average_temperature": "20.0",
                        "sensor.average_dining_zone_temp": "21.0",
                    }
                ),
                base_attr_map("20.0"),
            ),
            now=now,
        )

        heating_action = resolve_high_fan_office_closure(
            heating_snapshot,
            ("dining", "office"),
            operation_mode=HVAC_HEAT,
            requested_fan_speed_level=6,
        )
        cooling_action = resolve_high_fan_office_closure(
            cooling_snapshot,
            ("dining", "office"),
            operation_mode=HVAC_COOL,
            requested_fan_speed_level=6,
        )

        self.assertIsNotNone(heating_action)
        self.assertIsNotNone(cooling_action)
        self.assertFalse(heating_action.turn_on)
        self.assertFalse(cooling_action.turn_on)
        self.assertIn("22.0>=22.0", heating_action.reason)
        self.assertIn("20.0<=20.0", cooling_action.reason)

    def test_high_fan_office_closure_requires_continue_until_and_another_open_zone(self):
        now = datetime(2026, 8, 24, 12, 0, 0, tzinfo=timezone.utc)
        shared_overrides = {
            "input_select.temptamer_comfort_mode": "Office",
            "input_select.temptamer_comfort_mode_downstairs": SCHEME_OFF,
            "input_select.temptamer_comfort_mode_bed12": SCHEME_OFF,
            "input_select.temptamer_comfort_mode_bed34": SCHEME_OFF,
            "switch.wt32_hpctrl_e8dbd0_office": "on",
        }
        below_margin_snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        **shared_overrides,
                        "sensor.office_average_temperature": "20.9",
                        "sensor.average_dining_zone_temp": "16.0",
                    }
                ),
                base_attr_map("20.0"),
            ),
            now=now,
        )
        only_open_snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        **shared_overrides,
                        "sensor.office_average_temperature": "21.0",
                        "sensor.average_dining_zone_temp": "16.0",
                    }
                ),
                base_attr_map("20.0"),
            ),
            now=now,
        )

        for snapshot in (below_margin_snapshot, only_open_snapshot):
            actions, predicted_open = resolve_zone_actions(
                snapshot,
                now,
                operation_mode=HVAC_HEAT,
                requested_fan_speed_level=6,
            )

            self.assertEqual(actions, [])
            self.assertEqual(predicted_open, ("office",))

    def test_zone_prediction_diagnostics_explain_anti_flap_decisions(self):
        now = datetime(2026, 5, 7, 12, 1, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "sensor.home_temperature": "18.0",
                        "sensor.office_average_temperature": "17.5",
                        "sensor.average_dining_zone_temp": "17.8",
                    }
                ),
                base_attr_map("18.5"),
            ),
            last_switch_changes={
                "office": datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc),
                "dining": datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc),
            },
            now=now,
        )

        _actions, predicted_open = resolve_zone_actions(snapshot, now, operation_mode=HVAC_HEAT, comfort_mode_changed=False)
        diagnostics = describe_zone_predictions(
            snapshot,
            now,
            predicted_open,
            operation_mode=HVAC_HEAT,
            comfort_mode_changed=False,
        )

        self.assertEqual(len(diagnostics), 5)
        self.assertTrue(any("office:" in entry and "predicted=open" in entry for entry in diagnostics))
        self.assertTrue(any("dining:" in entry and "held closed by anti-flap delay" in entry for entry in diagnostics))

    def test_heating_zone_hysteresis_holds_closed_between_ideal_and_continue(self):
        now = datetime(2026, 5, 7, 12, 10, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Night",
                        "sensor.average_dining_zone_temp": "16.5",
                        "sensor.office_average_temperature": "18.0",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                    }
                ),
                base_attr_map("18.5"),
            ),
            now=now,
        )

        actions, predicted_open = resolve_zone_actions(snapshot, now, operation_mode=HVAC_HEAT, comfort_mode_changed=False)

        self.assertEqual(actions, [])
        self.assertEqual(predicted_open, ("office",))

    def test_heating_zone_hysteresis_holds_open_between_ideal_and_continue(self):
        now = datetime(2026, 5, 7, 12, 10, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Night",
                        "sensor.average_dining_zone_temp": "16.5",
                        "switch.wt32_hpctrl_e8dbd0_dining": "on",
                    }
                ),
                base_attr_map("18.5"),
            ),
            now=now,
        )

        actions, predicted_open = resolve_zone_actions(snapshot, now, operation_mode=HVAC_HEAT, comfort_mode_changed=False)

        self.assertEqual(actions, [])
        self.assertEqual(predicted_open, ("dining",))

    def test_closed_heating_zone_in_continue_band_does_not_drive_maintain_heat(self):
        now = datetime(2026, 5, 7, 12, 10, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Night",
                        "sensor.office_average_temperature": "18.0",
                        "sensor.average_dining_zone_temp": "17.0",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                    }
                ),
                base_attr_map("18.5"),
            ),
            now=now,
        )

        actions, predicted_open = resolve_zone_actions(snapshot, now, operation_mode=HVAC_HEAT, comfort_mode_changed=False)
        demand = resolve_equipment_demand(snapshot, predicted_open, operation_mode=HVAC_HEAT)

        self.assertEqual(actions, [])
        self.assertEqual(predicted_open, ("office",))
        self.assertFalse(demand.maintain_heat_mode)
        self.assertEqual(demand.requested_by_zones, ())

    def test_heating_zone_hysteresis_holds_closed_above_midpoint_between_enable_and_ideal(self):
        now = datetime(2026, 5, 7, 12, 10, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Night",
                        "sensor.average_dining_zone_temp": "15.6",
                        "sensor.office_average_temperature": "18.0",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                    }
                ),
                base_attr_map("18.5"),
            ),
            now=now,
        )

        actions, predicted_open = resolve_zone_actions(snapshot, now, operation_mode=HVAC_HEAT, comfort_mode_changed=False)

        self.assertEqual(actions, [])
        self.assertEqual(predicted_open, ("office",))

    def test_heating_zone_hysteresis_opens_below_midpoint_between_enable_and_ideal(self):
        now = datetime(2026, 5, 7, 12, 10, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Night",
                        "sensor.average_dining_zone_temp": "15.4",
                        "sensor.office_average_temperature": "18.0",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                    }
                ),
                base_attr_map("18.5"),
            ),
            now=now,
        )

        actions, predicted_open = resolve_zone_actions(snapshot, now, operation_mode=HVAC_HEAT, comfort_mode_changed=False)

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].zone_key, "dining")
        self.assertTrue(actions[0].turn_on)
        self.assertIn("below heat reopen threshold 15.5", actions[0].reason)
        self.assertEqual(predicted_open, ("dining", "office"))

    def test_cooling_zone_hysteresis_holds_closed_between_continue_and_ideal(self):
        now = datetime(2026, 5, 7, 12, 10, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_hvac_mode": "Cool",
                        "sensor.office_average_temperature": "20.5",
                        "sensor.average_dining_zone_temp": "13.0",
                        "sensor.average_bed1_2_zone_temp": "12.0",
                        "sensor.average_bed3_4_zone_temp": "12.0",
                        "switch.wt32_hpctrl_e8dbd0_dining": "on",
                    }
                ),
                base_attr_map("20.5"),
            ),
            now=now,
        )

        actions, predicted_open = resolve_zone_actions(snapshot, now, operation_mode=HVAC_COOL, comfort_mode_changed=False)

        self.assertEqual(actions, [])
        self.assertEqual(predicted_open, ("dining",))

    def test_cooling_zone_hysteresis_holds_open_between_continue_and_ideal(self):
        now = datetime(2026, 5, 7, 12, 10, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_hvac_mode": "Cool",
                        "sensor.office_average_temperature": "20.5",
                        "sensor.average_dining_zone_temp": "13.0",
                        "sensor.average_bed1_2_zone_temp": "12.0",
                        "sensor.average_bed3_4_zone_temp": "12.0",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                    }
                ),
                base_attr_map("20.5"),
            ),
            now=now,
        )

        actions, predicted_open = resolve_zone_actions(snapshot, now, operation_mode=HVAC_COOL, comfort_mode_changed=False)

        self.assertEqual(actions, [])
        self.assertEqual(predicted_open, ("office",))

    def test_antiflap_closed_cooling_zone_does_not_drive_maintain_cool(self):
        now = datetime(2026, 5, 7, 12, 2, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_hvac_mode": "Cool",
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.office_average_temperature": "21.5",
                        "sensor.average_dining_zone_temp": "13.0",
                        "sensor.average_bed1_2_zone_temp": "12.0",
                        "sensor.average_bed3_4_zone_temp": "12.0",
                        "switch.wt32_hpctrl_e8dbd0_dining": "on",
                    }
                ),
                base_attr_map("20.5"),
            ),
            last_switch_changes={
                "office": datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc),
                "dining": datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc),
            },
            now=now,
        )

        actions, predicted_open = resolve_zone_actions(snapshot, now, operation_mode=HVAC_COOL, comfort_mode_changed=False)
        demand = resolve_equipment_demand(snapshot, predicted_open, operation_mode=HVAC_COOL)

        self.assertEqual(actions, [])
        self.assertEqual(predicted_open, ("dining",))
        self.assertFalse(demand.maintain_cool_mode)
        self.assertEqual(demand.requested_by_zones, ())

    def test_equipment_demand_and_dispatch_plan_choose_heat_setpoint(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "18.0",
                        "sensor.office_average_temperature": "17.0",
                        "sensor.average_dining_zone_temp": "21.0",
                        "sensor.average_bed1_2_zone_temp": "19.5",
                        "sensor.average_bed3_4_zone_temp": "19.5",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                    }
                ),
                base_attr_map("16.4"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="off",
            current_fan_mode="low",
        )

        self.assertTrue(demand.heat_requested)
        self.assertEqual(demand.requested_by_zones, ("office",))
        self.assertEqual(plan.hvac_mode, "heat")
        self.assertEqual(plan.requested_by_zones, ("office",))
        self.assertEqual(plan.setpoint, 19)
        self.assertEqual(plan.fan_mode, "medium")

    def test_non_office_heat_request_stays_above_inlet_and_rounds_down(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Day",
                        "sensor.home_temperature": "18.0",
                        "sensor.office_average_temperature": "21.0",
                        "sensor.average_dining_zone_temp": "18.0",
                        "sensor.average_bed1_2_zone_temp": "19.5",
                        "sensor.average_bed3_4_zone_temp": "19.5",
                        "switch.wt32_hpctrl_e8dbd0_dining": "on",
                    }
                ),
                base_attr_map("18.6"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("dining",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("dining",),
            current_hvac_mode="off",
            current_fan_mode="low",
        )

        self.assertTrue(demand.heat_requested)
        self.assertEqual(demand.requested_by_zones, ("dining",))
        self.assertEqual(plan.hvac_mode, "heat")
        self.assertEqual(plan.requested_by_zones, ("dining",))
        self.assertEqual(plan.setpoint, 20)

    def test_heat_request_uses_half_degree_target_temp_step(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "18.0",
                        "sensor.office_average_temperature": "17.0",
                        "sensor.average_dining_zone_temp": "21.0",
                        "sensor.average_bed1_2_zone_temp": "19.5",
                        "sensor.average_bed3_4_zone_temp": "19.5",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                    }
                ),
                base_attr_map("19.6", target_temp_step=0.5),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="off",
            current_fan_mode="low",
            target_temp_step=0.5,
        )

        self.assertTrue(demand.heat_requested)
        self.assertEqual(plan.hvac_mode, "heat")
        self.assertEqual(plan.setpoint, 22.5)

    def test_heat_request_raises_setpoint_above_hot_inlet_for_powerday_heat_soak(self):
        snapshot = build_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                        GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "0",
                        "sensor.office_average_temperature": "15.8",
                        "sensor.average_dining_zone_temp": "15.4",
                        "sensor.average_bed1_2_zone_temp": "15.6",
                        "sensor.average_bed3_4_zone_temp": "15.3",
                        "switch.wt32_hpctrl_e8dbd0_bed_12": "on",
                    }
                ),
                base_attr_map("21.5", target_temp_step=0.5),
            ),
            now=datetime(2026, 7, 25, 13, 1, tzinfo=timezone.utc),
        )

        demand = resolve_equipment_demand(snapshot, ("bedroom_1_2",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("bedroom_1_2",),
            current_hvac_mode="heat",
            current_fan_mode="Level 1",
            target_temp_step=0.5,
        )

        self.assertTrue(demand.heat_requested)
        self.assertEqual(snapshot.zones["bedroom_1_2"].scheme.name, SCHEME_DAY_LIVING)
        self.assertEqual(plan.hvac_mode, "heat")
        self.assertGreater(plan.setpoint, snapshot.inlet_temp)
        self.assertEqual(plan.setpoint, 25)

    def test_powerday_free_power_downstairs_open_adds_direct_heat_target_boost(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                        "input_select.temptamer_comfort_mode_downstairs": "Auto",
                        GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "0",
                        "sensor.office_average_temperature": "20.0",
                        "sensor.downstairs_zone_average_temperature": "24.0",
                    }
                ),
                base_attr_map("20.0", target_temp_step=0.5),
            )
        )
        demand = EquipmentDemand(heat_requested=True, requested_by_zones=("office",))
        predicted_open_zones = ("office", "downstairs")

        boosted_plan = build_dispatch_plan(
            snapshot,
            demand,
            predicted_open_zones,
            current_hvac_mode="heat",
            current_fan_mode="low",
            target_temp_step=0.5,
        )
        unboosted_plan = build_dispatch_plan(
            replace(snapshot, free_power_available=False),
            demand,
            predicted_open_zones,
            current_hvac_mode="heat",
            current_fan_mode="low",
            target_temp_step=0.5,
        )

        self.assertEqual(boosted_plan.setpoint, unboosted_plan.setpoint + POWERDAY_DOWNSTAIRS_FREE_POWER_DIRECT_TARGET_BOOST)

    def test_powerday_free_power_downstairs_open_lowers_direct_cool_target_boost(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_hvac_mode": "Cool",
                        "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                        "input_select.temptamer_comfort_mode_downstairs": "Auto",
                        GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "0",
                    }
                ),
                base_attr_map("22.0", target_temp_step=0.5),
            )
        )
        demand = EquipmentDemand(cool_requested=True, requested_by_zones=("office",))
        predicted_open_zones = ("office", "downstairs")

        boosted_plan = build_dispatch_plan(
            snapshot,
            demand,
            predicted_open_zones,
            current_hvac_mode="cool",
            current_fan_mode="low",
            target_temp_step=0.5,
        )
        unboosted_plan = build_dispatch_plan(
            replace(snapshot, free_power_available=False),
            demand,
            predicted_open_zones,
            current_hvac_mode="cool",
            current_fan_mode="low",
            target_temp_step=0.5,
        )

        self.assertEqual(boosted_plan.setpoint, unboosted_plan.setpoint - POWERDAY_DOWNSTAIRS_FREE_POWER_DIRECT_TARGET_BOOST)

    def test_powerday_direct_target_boost_requires_downstairs_to_be_planned_open(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                        "input_select.temptamer_comfort_mode_downstairs": "Auto",
                        GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "0",
                        "sensor.office_average_temperature": "20.0",
                    }
                ),
                base_attr_map("20.0", target_temp_step=0.5),
            )
        )
        demand = EquipmentDemand(heat_requested=True, requested_by_zones=("office",))

        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            target_temp_step=0.5,
        )
        unboosted_plan = build_dispatch_plan(
            replace(snapshot, free_power_available=False),
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            target_temp_step=0.5,
        )

        self.assertEqual(plan.setpoint, unboosted_plan.setpoint)

    def test_equipment_demand_excludes_guarded_min_sensor_heat_request(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.office_average_temperature": "20.5",
                        "sensor.office_minimum_temperature": "19.5",
                        "sensor.office_maximum_temperature": "22.1",
                        "sensor.average_dining_zone_temp": "22.0",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("20.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)

        self.assertFalse(demand.heat_requested)
        self.assertTrue(demand.maintain_heat_mode)
        self.assertEqual(demand.requested_by_zones, ("office",))
        self.assertEqual(demand.reason, "office is below continue-until threshold")

    def test_equipment_demand_excludes_guarded_min_sensor_continue_request(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.office_average_temperature": "22.0",
                        "sensor.office_minimum_temperature": "21.5",
                        "sensor.office_maximum_temperature": "22.1",
                        "sensor.average_dining_zone_temp": "22.0",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("20.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)

        self.assertFalse(demand.maintain_heat_mode)
        self.assertNotIn("office", demand.requested_by_zones)

    def test_dispatch_plan_logs_setpoint_calculation(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "18.0",
                        "sensor.office_average_temperature": "17.0",
                        "sensor.average_dining_zone_temp": "21.0",
                        "sensor.average_bed1_2_zone_temp": "19.5",
                        "sensor.average_bed3_4_zone_temp": "19.5",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                    }
                ),
                base_attr_map("14.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)

        with self.assertLogs("pyscript.temptamer", level="INFO") as captured:
            plan = build_dispatch_plan(
                snapshot,
                demand,
                ("office",),
                current_hvac_mode="off",
                current_fan_mode="low",
            )

        self.assertEqual(plan.setpoint, 17)
        self.assertTrue(
            any(
                "SETPOINT: inlet_temp=14.0 zone=office enable_outside=20.0" in message
                and "raw=17.0" in message
                and "normalized=17" in message
                for message in captured.output
            )
        )

    def test_cooling_mode_dispatches_cool_plan(self):
        now = datetime(2026, 5, 7, 12, 1, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_hvac_mode": "Cool",
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "22.0",
                        "sensor.office_average_temperature": "24.5",
                        "sensor.average_dining_zone_temp": "14.0",
                        "sensor.average_bed1_2_zone_temp": "13.0",
                        "sensor.average_bed3_4_zone_temp": "13.0",
                    }
                ),
                base_attr_map("25.0"),
            )
        )

        operating_mode, reason = resolve_operating_mode(
            snapshot,
            current_hvac_mode="off",
            last_active_hvac_mode=None,
            last_heatcool_transition=None,
            now=now,
        )
        actions, predicted_open = resolve_zone_actions(snapshot, now, operation_mode=operating_mode, comfort_mode_changed=False)
        demand = resolve_equipment_demand(snapshot, predicted_open, operation_mode=operating_mode)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            predicted_open,
            current_hvac_mode="off",
            current_fan_mode="low",
        )

        self.assertEqual(operating_mode, HVAC_COOL)
        self.assertEqual(reason, "hvac mode is Cool")
        self.assertEqual([(action.zone_key, action.turn_on) for action in actions], [("office", True)])
        self.assertEqual(predicted_open, ("office",))
        self.assertTrue(demand.cool_requested)
        self.assertEqual(demand.requested_by_zones, ("office",))
        self.assertEqual(plan.hvac_mode, "cool")
        self.assertEqual(plan.requested_by_zones, ("office",))
        self.assertEqual(plan.setpoint, 23)

    def test_active_cooling_mirrors_heat_with_inlet_relative_minimum_drive(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_hvac_mode": "Cool",
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "22.0",
                        "sensor.office_average_temperature": "22.2",
                        "sensor.average_dining_zone_temp": "14.0",
                        "sensor.average_bed1_2_zone_temp": "13.0",
                        "sensor.average_bed3_4_zone_temp": "13.0",
                    }
                ),
                base_attr_map("25.0"),
            )
        )
        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_COOL)

        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="cool",
            current_fan_mode="low",
        )

        self.assertTrue(demand.cool_requested)
        self.assertEqual(plan.setpoint, 24)

    def test_maximum_sensor_continues_cooling_without_increasing_active_drive(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_hvac_mode": "Cool",
                        "input_select.temptamer_comfort_mode": "Office",
                        "input_number.comfort_adjustment_office": "2.7",
                        "input_number.temptamer_setpoint_adjustment": "-1.0",
                        "sensor.office_average_temperature": "21.5",
                        "sensor.office_minimum_temperature": "20.7",
                        "sensor.office_maximum_temperature": "22.3",
                        "sensor.average_dining_zone_temp": "14.0",
                        "sensor.average_bed1_2_zone_temp": "13.0",
                        "sensor.average_bed3_4_zone_temp": "13.0",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                        "switch.wt32_hpctrl_e8dbd0_bed_12": "on",
                        "switch.wt32_hpctrl_e8dbd0_bed_34": "on",
                        "switch.roof_wt32_hpctrl_e8dbd0_downstairs": "on",
                    }
                ),
                base_attr_map("22.0", target_temp_step=0.5),
            )
        )
        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_COOL)

        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="cool",
            current_fan_mode="low",
            target_temp_step=0.5,
            supported_fan_modes=tuple(f"Level {level}" for level in range(1, 7)),
        )

        self.assertTrue(demand.cool_requested)
        self.assertAlmostEqual(demand.max_temperature_deficit, 0.5)
        self.assertEqual(plan.setpoint, 19)
        self.assertEqual(plan.fan_mode, "Level 3")

    def test_maintain_cooling_uses_inlet_setpoint_when_zone_is_closer_to_ideal(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_hvac_mode": "Cool",
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "21.0",
                        "sensor.office_average_temperature": "21.5",
                        "sensor.average_dining_zone_temp": "14.0",
                        "sensor.average_bed1_2_zone_temp": "13.0",
                        "sensor.average_bed3_4_zone_temp": "13.0",
                    }
                ),
                base_attr_map("25.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_COOL)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="cool",
            current_fan_mode="low",
            current_setpoint="22.0",
        )

        self.assertFalse(demand.cool_requested)
        self.assertTrue(demand.maintain_cool_mode)
        self.assertEqual(demand.requested_by_zones, ("office",))
        self.assertEqual(demand.reason, "office is above ideal target")
        self.assertEqual(plan.setpoint, 25)

    def test_maintain_heat_preserves_current_lower_negative_step(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "18.0",
                        "sensor.office_average_temperature": "21.2",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("22.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="18.0",
        )

        self.assertTrue(demand.maintain_heat_mode)
        self.assertEqual(plan.hvac_mode, "heat")
        self.assertEqual(plan.setpoint, 18)

    def test_maintain_heat_from_off_uses_primary_zone_delta_and_rounds_down(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "18.0",
                        "sensor.office_average_temperature": "21.5",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("24.5"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="off",
            current_fan_mode="low",
        )

        self.assertTrue(demand.maintain_heat_mode)
        self.assertEqual(demand.requested_by_zones, ("office",))
        self.assertEqual(plan.hvac_mode, "heat")
        self.assertEqual(plan.setpoint, 22)

    def test_maintain_heat_preserves_tracked_idle_step_when_setpoint_has_not_caught_up(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "18.0",
                        "sensor.office_average_temperature": "21.2",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("22.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="21.0",
            idle_heat_step=-4,
        )

        self.assertTrue(demand.maintain_heat_mode)
        self.assertEqual(plan.hvac_mode, "heat")
        self.assertEqual(plan.setpoint, 18)

    def test_maintain_heat_reduces_setpoint_when_zone_is_closer_to_continue_until(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "18.0",
                        "sensor.office_average_temperature": "21.8",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("20.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
        )

        self.assertTrue(demand.maintain_heat_mode)
        self.assertEqual(plan.hvac_mode, "heat")
        self.assertEqual(plan.setpoint, 18)

    def test_maintain_heat_below_minimum_setpoint_falls_back_to_fan_only(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "18.0",
                        "sensor.office_average_temperature": "21.8",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("17.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="medium",
        )

        self.assertTrue(demand.maintain_heat_mode)
        self.assertEqual(plan.hvac_mode, HVAC_FAN_ONLY)
        self.assertEqual(plan.fan_mode, FAN_LOW)
        self.assertIsNone(plan.setpoint)

    def test_maintain_cooling_rounds_up_fractional_setpoint(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_hvac_mode": "Cool",
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "21.0",
                        "sensor.office_average_temperature": "21.8",
                        "sensor.average_dining_zone_temp": "14.0",
                        "sensor.average_bed1_2_zone_temp": "13.0",
                        "sensor.average_bed3_4_zone_temp": "13.0",
                    }
                ),
                base_attr_map("22.5"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_COOL)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="cool",
            current_fan_mode="low",
            current_setpoint="22.0",
        )

        self.assertTrue(demand.maintain_cool_mode)
        self.assertEqual(plan.hvac_mode, "cool")
        self.assertEqual(plan.setpoint, 23)

    def test_maintain_cooling_increases_setpoint_when_open_zones_are_closer_to_continue_until(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_hvac_mode": "Cool",
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "20.0",
                        "sensor.office_average_temperature": "21.2",
                        "sensor.average_dining_zone_temp": "12.5",
                        "sensor.average_bed1_2_zone_temp": "13.0",
                        "sensor.average_bed3_4_zone_temp": "13.0",
                        "switch.wt32_hpctrl_e8dbd0_dining": "on",
                    }
                ),
                base_attr_map("22.0", temperature="17.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office", "dining"), operation_mode=HVAC_COOL)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("dining", "office"),
            current_hvac_mode="cool",
            current_fan_mode="low",
        )

        self.assertTrue(demand.maintain_cool_mode)
        self.assertEqual(plan.hvac_mode, "cool")
        self.assertEqual(plan.setpoint, 23)

    def test_maintain_cooling_uses_half_degree_target_temp_step(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_hvac_mode": "Cool",
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "20.0",
                        "sensor.office_average_temperature": "21.2",
                        "sensor.average_dining_zone_temp": "12.5",
                        "sensor.average_bed1_2_zone_temp": "13.0",
                        "sensor.average_bed3_4_zone_temp": "13.0",
                        "switch.wt32_hpctrl_e8dbd0_dining": "on",
                    }
                ),
                base_attr_map("22.2", temperature="17.0", target_temp_step=0.5),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office", "dining"), operation_mode=HVAC_COOL)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("dining", "office"),
            current_hvac_mode="cool",
            current_fan_mode="low",
            target_temp_step=0.5,
        )

        self.assertTrue(demand.maintain_cool_mode)
        self.assertEqual(plan.hvac_mode, "cool")
        self.assertEqual(plan.setpoint, 23.5)

    def test_maintain_cooling_never_reduces_previous_release_setpoint(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_hvac_mode": "Cool",
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "20.0",
                        "sensor.office_average_temperature": "21.2",
                        "sensor.average_dining_zone_temp": "14.0",
                        "sensor.average_bed1_2_zone_temp": "13.0",
                        "sensor.average_bed3_4_zone_temp": "13.0",
                    }
                ),
                base_attr_map("21.0", temperature="22.0"),
            )
        )
        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_COOL)

        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="cool",
            current_fan_mode="low",
            current_setpoint="22.0",
            previous_cool_release_setpoint=24.0,
        )

        self.assertTrue(demand.maintain_cool_mode)
        self.assertEqual(plan.setpoint, 24)
        self.assertEqual(plan.cool_release_setpoint, 24)

    def test_open_zone_maximum_sensor_continues_cooling_until_hot_room_recovers(self):
        now = datetime(2026, 5, 7, 12, 10, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_hvac_mode": "Cool",
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.office_average_temperature": "20.5",
                        "sensor.office_minimum_temperature": "20.1",
                        "sensor.office_maximum_temperature": "22.1",
                        "sensor.average_dining_zone_temp": "13.0",
                        "sensor.average_bed1_2_zone_temp": "13.0",
                        "sensor.average_bed3_4_zone_temp": "13.0",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                    }
                ),
                base_attr_map("21.0"),
            ),
            now=now,
        )

        actions, predicted_open = resolve_zone_actions(snapshot, now, operation_mode=HVAC_COOL)
        demand = resolve_equipment_demand(snapshot, predicted_open, operation_mode=HVAC_COOL)

        self.assertEqual(actions, [])
        self.assertEqual(predicted_open, ("office",))
        self.assertTrue(demand.maintain_cool_mode)
        self.assertEqual(demand.requested_by_zones, ("office",))
        self.assertEqual(demand.max_temperature_deficit, 0.0)

    def test_cooling_ignores_maximum_sensor_when_minimum_is_below_continue_threshold(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_hvac_mode": "Cool",
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.office_average_temperature": "20.5",
                        "sensor.office_minimum_temperature": "19.9",
                        "sensor.office_maximum_temperature": "22.1",
                        "sensor.average_dining_zone_temp": "13.0",
                        "sensor.average_bed1_2_zone_temp": "13.0",
                        "sensor.average_bed3_4_zone_temp": "13.0",
                    }
                ),
                base_attr_map("21.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_COOL)

        self.assertNotIn("office", snapshot.cool_calling_zones)
        self.assertNotIn("office", snapshot.above_ideal_zones)
        self.assertFalse(demand.cool_requested)
        self.assertFalse(demand.maintain_cool_mode)

    def test_no_heat_demand_enters_idle_once_all_zones_reach_continue_threshold(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "23.0",
                        "sensor.office_average_temperature": "22.5",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("22.0", temperature="17.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="17.0",
        )

        self.assertEqual(demand.reason, "all enabled zones are at or above continue-until threshold")
        self.assertTrue(plan.idle)
        self.assertFalse(plan.turn_off)
        self.assertEqual(plan.hvac_mode, "heat")
        self.assertEqual(plan.setpoint, 17)

    def test_heat_idle_switches_stale_cooling_hvac_mode_to_heat(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_hvac_mode": "Heat",
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.office_average_temperature": "22.5",
                        "sensor.average_dining_zone_temp": "19.0",
                        "sensor.average_bed1_2_zone_temp": "18.0",
                        "sensor.average_bed3_4_zone_temp": "18.0",
                        "switch.wt32_hpctrl_e8dbd0_dining": "on",
                    }
                ),
                base_attr_map("23.0", temperature="17.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("dining",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("dining",),
            current_hvac_mode="cool",
            current_fan_mode="Level 1",
            current_setpoint="17.0",
            operation_mode=HVAC_HEAT,
        )

        self.assertEqual(demand.reason, "all enabled zones are at or above continue-until threshold")
        self.assertTrue(plan.idle)
        self.assertEqual(plan.hvac_mode, "heat")
        self.assertEqual(plan.setpoint, 17)

    def test_heat_idle_does_not_start_from_off_without_heat_demand(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_hvac_mode": "Heat",
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.office_average_temperature": "22.5",
                        "sensor.average_dining_zone_temp": "19.0",
                        "sensor.average_bed1_2_zone_temp": "18.0",
                        "sensor.average_bed3_4_zone_temp": "18.0",
                        "switch.wt32_hpctrl_e8dbd0_dining": "on",
                    }
                ),
                base_attr_map("23.0", temperature="17.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("dining",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("dining",),
            current_hvac_mode="off",
            current_fan_mode="Level 1",
            current_setpoint="17.0",
            operation_mode=HVAC_HEAT,
        )

        self.assertTrue(plan.turn_off)
        self.assertFalse(plan.idle)

    def test_initial_heating_idle_with_large_stale_target_clamps_to_midpoint(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Night",
                        "sensor.home_temperature": "18.0",
                        "sensor.office_average_temperature": "18.0",
                        "sensor.average_dining_zone_temp": "18.5",
                        "sensor.average_bed1_2_zone_temp": "18.5",
                        "sensor.average_bed3_4_zone_temp": "18.5",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                    }
                ),
                base_attr_map("22.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="22.0",
        )

        self.assertTrue(plan.idle)
        self.assertEqual(plan.setpoint, 20)
        self.assertEqual(plan.idle_heat_step, 0)

    def test_initial_heating_idle_with_small_gap_preserves_current_target(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Night",
                        "sensor.home_temperature": "19.5",
                        "sensor.office_average_temperature": "19.5",
                        "sensor.average_dining_zone_temp": "19.8",
                        "sensor.average_bed1_2_zone_temp": "19.8",
                        "sensor.average_bed3_4_zone_temp": "19.8",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                    }
                ),
                base_attr_map("21.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="21.0",
        )

        self.assertTrue(plan.idle)
        self.assertEqual(plan.setpoint, 21)
        self.assertEqual(plan.idle_heat_step, 0)

    def test_initial_heating_idle_uses_coldest_predicted_open_zone_as_anchor(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Night",
                        "sensor.home_temperature": "19.0",
                        "sensor.office_average_temperature": "20.0",
                        "sensor.average_dining_zone_temp": "18.0",
                        "sensor.average_bed1_2_zone_temp": "18.5",
                        "sensor.average_bed3_4_zone_temp": "18.5",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                        "switch.wt32_hpctrl_e8dbd0_dining": "on",
                    }
                ),
                base_attr_map("22.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office", "dining"), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office", "dining"),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="22.0",
        )

        self.assertTrue(plan.idle)
        self.assertEqual(plan.setpoint, 20)
        self.assertEqual(plan.idle_heat_step, 0)

    def test_comfort_mode_drop_turns_off_instead_of_entering_idle(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Night",
                        "sensor.home_temperature": "18.0",
                        "sensor.office_average_temperature": "18.0",
                        "sensor.average_dining_zone_temp": "18.5",
                        "sensor.average_bed1_2_zone_temp": "18.5",
                        "sensor.average_bed3_4_zone_temp": "18.5",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                    }
                ),
                base_attr_map("22.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)

        self.assertEqual(demand.reason, "all enabled zones are at or above continue-until threshold")

        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="22.0",
            comfort_mode_changed=True,
        )

        self.assertTrue(plan.turn_off)
        self.assertFalse(plan.idle)
        self.assertIn("mode change removed all heating and cooling demand", plan.reason)

    def test_heating_idle_preserves_current_setpoint_on_entry(self):
        now = datetime(2026, 1, 1, 12, 1, 59, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "23.0",
                        "sensor.office_average_temperature": "22.5",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("22.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="22.0",
            idle_started_at=now - timedelta(minutes=1, seconds=59),
            now=now,
        )

        self.assertTrue(plan.idle)
        self.assertEqual(plan.setpoint, 22)
        self.assertEqual(plan.idle_heat_step, 0)

    def test_heating_idle_stage_1_applies_after_three_minutes_above_continue_until(self):
        now = datetime(2026, 1, 1, 12, 3, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "23.0",
                        "sensor.office_average_temperature": "22.5",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("22.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="22.0",
            idle_started_at=now - timedelta(minutes=3),
            now=now,
        )

        self.assertTrue(plan.idle)
        self.assertEqual(plan.setpoint, 20)
        self.assertEqual(plan.idle_heat_step, -2)

    def test_heating_idle_stage_1_uses_half_degree_target_temp_step(self):
        now = datetime(2026, 1, 1, 12, 3, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "23.0",
                        "sensor.office_average_temperature": "22.5",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("22.5", target_temp_step=0.5),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="22.0",
            target_temp_step=0.5,
            idle_started_at=now - timedelta(minutes=3),
            now=now,
        )

        self.assertTrue(plan.idle)
        self.assertEqual(plan.setpoint, 20.5)
        self.assertEqual(plan.idle_heat_step, -2)

    def test_heating_idle_stage_1_does_not_raise_existing_inlet_minus_2_setpoint(self):
        now = datetime(2026, 1, 1, 12, 3, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "24.0",
                        "sensor.office_average_temperature": "22.5",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("22.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="20.0",
            idle_started_at=now - timedelta(minutes=3),
            now=now,
        )

        self.assertTrue(plan.idle)
        self.assertEqual(plan.setpoint, 20)
        self.assertEqual(plan.idle_heat_step, -2)

    def test_heating_idle_stage_2_applies_after_six_minutes_above_continue_until(self):
        now = datetime(2026, 1, 1, 12, 6, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "23.0",
                        "sensor.office_average_temperature": "22.5",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("22.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="22.0",
            idle_started_at=now - timedelta(minutes=6),
            now=now,
        )

        self.assertTrue(plan.idle)
        self.assertEqual(plan.setpoint, 20)
        self.assertEqual(plan.idle_heat_step, -2)

    def test_heating_idle_stage_2_preserves_existing_inlet_minus_3_setpoint(self):
        now = datetime(2026, 1, 1, 12, 6, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "24.0",
                        "sensor.office_average_temperature": "22.5",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("22.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="19.0",
            idle_started_at=now - timedelta(minutes=6),
            now=now,
        )

        self.assertTrue(plan.idle)
        self.assertEqual(plan.setpoint, 19)
        self.assertEqual(plan.idle_heat_step, -3)

    def test_heating_idle_stage_2_waits_until_six_minutes(self):
        now = datetime(2026, 1, 1, 12, 4, 59, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "23.0",
                        "sensor.office_average_temperature": "22.5",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("22.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="22.0",
            idle_started_at=now - timedelta(minutes=4, seconds=59),
            now=now,
        )

        self.assertTrue(plan.idle)
        self.assertEqual(plan.setpoint, 20)
        self.assertEqual(plan.idle_heat_step, -2)

    def test_heating_idle_stage_3_applies_after_twelve_minutes_above_continue_until(self):
        now = datetime(2026, 1, 1, 12, 12, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "23.0",
                        "sensor.office_average_temperature": "22.5",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("22.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="18.0",
            idle_started_at=now - timedelta(minutes=12),
            now=now,
        )

        self.assertTrue(plan.idle)
        self.assertEqual(plan.setpoint, 18)
        self.assertEqual(plan.idle_heat_step, -4)

    def test_cooling_idle_release_raises_setpoint_in_stages_without_ratcheting_down(self):
        now = datetime(2026, 1, 1, 12, 5, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_hvac_mode": "Cool",
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "19.0",
                        "sensor.office_average_temperature": "19.5",
                        "sensor.average_dining_zone_temp": "12.0",
                        "sensor.average_bed1_2_zone_temp": "11.0",
                        "sensor.average_bed3_4_zone_temp": "11.0",
                    }
                ),
                base_attr_map("21.0", temperature="21.0"),
            )
        )
        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_COOL)

        five_minute_plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="cool",
            current_fan_mode="low",
            current_setpoint="21.0",
            previous_cool_release_setpoint=21.0,
            idle_started_at=now - timedelta(minutes=5),
            now=now,
        )
        falling_inlet_plan = build_dispatch_plan(
            replace(snapshot, inlet_temp=18.0),
            demand,
            ("office",),
            current_hvac_mode="cool",
            current_fan_mode="low",
            current_setpoint="21.0",
            previous_cool_release_setpoint=five_minute_plan.cool_release_setpoint,
            idle_started_at=now - timedelta(minutes=6),
            now=now,
        )

        self.assertTrue(five_minute_plan.idle)
        self.assertEqual(five_minute_plan.setpoint, 23)
        self.assertEqual(falling_inlet_plan.setpoint, 23)

    def test_heating_idle_stage_3_does_not_raise_existing_inlet_minus_4_setpoint(self):
        now = datetime(2026, 1, 1, 12, 12, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "24.0",
                        "sensor.office_average_temperature": "22.5",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("22.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="22.0",
            idle_started_at=now - timedelta(minutes=12),
            now=now,
        )

        self.assertTrue(plan.idle)
        self.assertEqual(plan.setpoint, 19)
        self.assertEqual(plan.idle_heat_step, -3)

    def test_heating_idle_preserves_current_setpoint_when_zone_is_at_continue_until(self):
        now = datetime(2026, 1, 1, 12, 15, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "22.0",
                        "sensor.office_average_temperature": "22.0",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("22.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="22.0",
            idle_started_at=now - timedelta(minutes=15),
            now=now,
        )

        self.assertTrue(plan.idle)
        self.assertEqual(plan.setpoint, 22)
        self.assertEqual(plan.idle_heat_step, 0)

    def test_heating_idle_preserves_current_setpoint_without_open_zone(self):
        now = datetime(2026, 1, 1, 12, 15, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "23.0",
                        "sensor.office_average_temperature": "22.5",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("22.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, (), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            (),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="22.0",
            idle_started_at=now - timedelta(minutes=15),
            now=now,
        )

        self.assertTrue(plan.idle)
        self.assertEqual(plan.setpoint, 22)
        self.assertIsNone(plan.idle_heat_step)

    def test_heating_idle_returns_none_when_current_setpoint_missing(self):
        now = datetime(2026, 1, 1, 12, 15, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "23.0",
                        "sensor.office_average_temperature": "22.5",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("22.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="unknown",
            idle_started_at=now - timedelta(minutes=15),
            now=now,
        )

        self.assertTrue(plan.idle)
        self.assertIsNone(plan.setpoint)
        self.assertIsNone(plan.idle_heat_step)

    def test_heating_idle_stage_3_holds_at_minimum_heat_setpoint(self):
        now = datetime(2026, 1, 1, 12, 12, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "23.0",
                        "sensor.office_average_temperature": "22.5",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("19.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="18.0",
            idle_started_at=now - timedelta(minutes=12),
            now=now,
        )

        self.assertFalse(plan.turn_off)
        self.assertTrue(plan.idle)
        self.assertEqual(plan.setpoint, 17)
        self.assertEqual(plan.idle_heat_step, -3)

    def test_heating_idle_stage_4_applies_after_twenty_four_minutes_above_continue_until(self):
        now = datetime(2026, 1, 1, 12, 15, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "25.0",
                        "sensor.office_average_temperature": "22.5",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("24.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="22.0",
            idle_started_at=now - timedelta(minutes=15),
            now=now,
        )

        self.assertTrue(plan.idle)
        self.assertEqual(plan.setpoint, 20)
        self.assertEqual(plan.idle_heat_step, -4)

    def test_heating_idle_stage_5_applies_before_twenty_five_minutes_above_continue_until(self):
        now = datetime(2026, 1, 1, 12, 24, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "26.0",
                        "sensor.office_average_temperature": "22.5",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("25.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="22.0",
            idle_started_at=now - timedelta(minutes=24),
            now=now,
        )

        self.assertTrue(plan.idle)
        self.assertEqual(plan.setpoint, 18)
        self.assertEqual(plan.idle_heat_step, -7)

    def test_heating_idle_stage_6_applies_at_twenty_five_minutes_above_continue_until(self):
        now = datetime(2026, 1, 1, 12, 25, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "30.0",
                        "sensor.office_average_temperature": "22.5",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("29.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="22.0",
            idle_started_at=now - timedelta(minutes=25),
            now=now,
        )

        self.assertTrue(plan.idle)
        self.assertEqual(plan.setpoint, 18)
        self.assertEqual(plan.idle_heat_step, -11)

    def test_heating_idle_stage_6_holds_minimum_heat_setpoint_until_idle_minimum_elapses(self):
        now = datetime(2026, 1, 1, 12, 25, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "24.0",
                        "sensor.office_average_temperature": "22.5",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("23.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="22.0",
            idle_started_at=now - timedelta(minutes=25),
            now=now,
        )

        self.assertFalse(plan.turn_off)
        self.assertTrue(plan.idle)
        self.assertEqual(plan.setpoint, 17)
        self.assertEqual(plan.idle_heat_step, -11)

        plan_after_minimum_idle = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="22.0",
            idle_started_at=now - timedelta(seconds=MIN_IDLE_SECONDS),
            now=now,
        )

        self.assertTrue(plan_after_minimum_idle.turn_off)
        self.assertTrue(plan_after_minimum_idle.idle_shutdown)

    def test_heating_idle_unwinds_from_minus_4_to_minus_3_after_unwind_interval(self):
        now = datetime(2026, 1, 1, 12, 10, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "22.0",
                        "sensor.office_average_temperature": "22.0",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("22.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="18.0",
            idle_started_at=now - timedelta(minutes=20),
            idle_heat_step=-4,
            idle_heat_step_changed_at=now - timedelta(seconds=IDLE_HEAT_UNWIND_SECONDS),
            idle_heat_zone_key="office",
            now=now,
        )

        self.assertTrue(plan.idle)
        self.assertEqual(plan.setpoint, 19)
        self.assertEqual(plan.idle_heat_step, -3)
        self.assertTrue(plan.idle_heat_step_changed)

    def test_heating_idle_unwinds_from_minus_3_to_minus_2_after_unwind_interval(self):
        now = datetime(2026, 1, 1, 12, 10, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "22.0",
                        "sensor.office_average_temperature": "22.0",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("22.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="19.0",
            idle_started_at=now - timedelta(minutes=20),
            idle_heat_step=-3,
            idle_heat_step_changed_at=now - timedelta(seconds=IDLE_HEAT_UNWIND_SECONDS),
            idle_heat_zone_key="office",
            now=now,
        )

        self.assertTrue(plan.idle)
        self.assertEqual(plan.setpoint, 20)
        self.assertEqual(plan.idle_heat_step, -2)
        self.assertTrue(plan.idle_heat_step_changed)

    def test_heating_idle_holds_zone_minimum_step_at_minus_2_after_unwind_interval(self):
        now = datetime(2026, 1, 1, 12, 10, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "22.0",
                        "sensor.office_average_temperature": "22.0",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("22.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="20.0",
            idle_started_at=now - timedelta(minutes=20),
            idle_heat_step=-2,
            idle_heat_step_changed_at=now - timedelta(seconds=IDLE_HEAT_UNWIND_SECONDS),
            idle_heat_zone_key="office",
            now=now,
        )

        self.assertTrue(plan.idle)
        self.assertEqual(plan.setpoint, 20)
        self.assertEqual(plan.idle_heat_step, -2)
        self.assertFalse(plan.idle_heat_step_changed)

    def test_heating_idle_unwinds_from_minus_11_to_minus_7_after_unwind_interval(self):
        now = datetime(2026, 1, 1, 12, 10, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "30.0",
                        "sensor.office_average_temperature": "22.0",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("29.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="18.0",
            idle_started_at=now - timedelta(minutes=35),
            idle_heat_step=-11,
            idle_heat_step_changed_at=now - timedelta(seconds=IDLE_HEAT_UNWIND_SECONDS),
            idle_heat_zone_key="office",
            now=now,
        )

        self.assertTrue(plan.idle)
        self.assertEqual(plan.setpoint, 22)
        self.assertEqual(plan.idle_heat_step, -7)
        self.assertTrue(plan.idle_heat_step_changed)

    def test_heating_idle_unwinds_from_minus_6_to_minus_5_after_unwind_interval(self):
        now = datetime(2026, 1, 1, 12, 10, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "24.0",
                        "sensor.office_average_temperature": "22.0",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("24.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="18.0",
            idle_started_at=now - timedelta(minutes=58),
            idle_heat_step=-6,
            idle_heat_step_changed_at=now - timedelta(seconds=IDLE_HEAT_UNWIND_SECONDS),
            idle_heat_zone_key="office",
            now=now,
        )

        self.assertTrue(plan.idle)
        self.assertEqual(plan.setpoint, 19)
        self.assertEqual(plan.idle_heat_step, -5)
        self.assertTrue(plan.idle_heat_step_changed)

    def test_heating_idle_does_not_unwind_before_unwind_interval(self):
        now = datetime(2026, 1, 1, 12, 10, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "22.0",
                        "sensor.office_average_temperature": "22.0",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("22.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint="18.0",
            idle_started_at=now - timedelta(minutes=20),
            idle_heat_step=-4,
            idle_heat_step_changed_at=now - timedelta(seconds=IDLE_HEAT_UNWIND_SECONDS - 1),
            idle_heat_zone_key="office",
            now=now,
        )

        self.assertTrue(plan.idle)
        self.assertEqual(plan.setpoint, 18)
        self.assertEqual(plan.idle_heat_step, -4)
        self.assertFalse(plan.idle_heat_step_changed)

    def test_heating_continues_until_all_zones_reach_continue_threshold(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "18.0",
                        "sensor.office_average_temperature": "21.5",
                        "sensor.average_dining_zone_temp": "17.0",
                        "sensor.average_bed1_2_zone_temp": "16.0",
                        "sensor.average_bed3_4_zone_temp": "16.0",
                    }
                ),
                base_attr_map("20.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
        )

        self.assertTrue(demand.maintain_heat_mode)
        self.assertEqual(demand.reason, "office is below continue-until threshold")
        self.assertFalse(plan.turn_off)
        self.assertEqual(plan.hvac_mode, "heat")
        self.assertEqual(plan.setpoint, 18)

    def test_idle_shutdown_restart_reuses_remembered_backoff_for_same_zone_maintain_heat(self):
        now = datetime(2026, 1, 1, 12, 5, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "22.0",
                        "sensor.office_average_temperature": "21.5",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("22.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="off",
            current_fan_mode="low",
            idle_shutdown_at=now - timedelta(minutes=5),
            idle_shutdown_heat_step=-4,
            idle_shutdown_zone_key="office",
            now=now,
        )

        self.assertFalse(plan.turn_off)
        self.assertFalse(plan.idle)
        self.assertEqual(plan.hvac_mode, "heat")
        self.assertEqual(plan.setpoint, 18)

    def test_idle_shutdown_restart_backoff_decays_while_system_is_off(self):
        now = datetime(2026, 1, 1, 12, 20, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "22.0",
                        "sensor.office_average_temperature": "21.5",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("22.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="off",
            current_fan_mode="low",
            idle_shutdown_at=now - timedelta(seconds=IDLE_HEAT_UNWIND_SECONDS * 2),
            idle_shutdown_heat_step=-4,
            idle_shutdown_zone_key="office",
            now=now,
        )

        self.assertEqual(plan.hvac_mode, "heat")
        self.assertEqual(plan.setpoint, 20)

    def test_idle_shutdown_restart_is_not_used_for_true_heat_request(self):
        now = datetime(2026, 1, 1, 12, 5, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Day",
                        "sensor.home_temperature": "18.0",
                        "sensor.office_average_temperature": "17.0",
                        "sensor.average_dining_zone_temp": "18.0",
                        "sensor.average_bed1_2_zone_temp": "19.5",
                        "sensor.average_bed3_4_zone_temp": "19.5",
                    }
                ),
                base_attr_map("16.4"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office", "dining"), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office", "dining"),
            current_hvac_mode="off",
            current_fan_mode="low",
            idle_shutdown_at=now - timedelta(minutes=5),
            idle_shutdown_heat_step=-4,
            idle_shutdown_zone_key="office",
            now=now,
        )

        self.assertTrue(demand.heat_requested)
        self.assertEqual(plan.hvac_mode, "heat")
        self.assertEqual(plan.setpoint, 19)

    def test_idle_shutdown_runtime_state_is_captured_and_cleared_on_restart(self):
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        temptamer_main.RUNTIME_STATE["idle_heat_step"] = -4
        temptamer_main.RUNTIME_STATE["idle_heat_zone_key"] = "office"
        temptamer_main.RUNTIME_STATE["idle_shutdown_at"] = None
        temptamer_main.RUNTIME_STATE["idle_shutdown_heat_step"] = None
        temptamer_main.RUNTIME_STATE["idle_shutdown_zone_key"] = None

        temptamer_main._update_idle_shutdown_runtime_state(
            temptamer_main.build_dispatch_plan(
                build_behavior_snapshot(
                    FakeReader(
                        base_state_map(
                            **{
                                "input_select.temptamer_comfort_mode": "Office",
                                "sensor.home_temperature": "23.0",
                                "sensor.office_average_temperature": "22.5",
                                "sensor.average_dining_zone_temp": "17.5",
                                "sensor.average_bed1_2_zone_temp": "16.5",
                                "sensor.average_bed3_4_zone_temp": "16.5",
                            }
                        ),
                        base_attr_map("22.0"),
                    )
                ),
                EquipmentDemand(reason="all enabled zones are at or above continue-until threshold"),
                ("office",),
                current_hvac_mode="heat",
                current_fan_mode="low",
                idle_started_at=now - timedelta(hours=1),
                now=now,
            ),
            now,
            current_hvac_mode="heat",
        )

        self.assertEqual(temptamer_main.RUNTIME_STATE["idle_shutdown_at"], now)
        self.assertEqual(temptamer_main.RUNTIME_STATE["idle_shutdown_heat_step"], -4)
        self.assertEqual(temptamer_main.RUNTIME_STATE["idle_shutdown_zone_key"], "office")

        temptamer_main._update_idle_shutdown_runtime_state(
            temptamer_main.build_dispatch_plan(
                build_behavior_snapshot(
                    FakeReader(
                        base_state_map(
                            **{
                                "input_select.temptamer_comfort_mode": "Office",
                                "sensor.home_temperature": "22.0",
                                "sensor.office_average_temperature": "21.5",
                                "sensor.average_dining_zone_temp": "17.5",
                                "sensor.average_bed1_2_zone_temp": "16.5",
                                "sensor.average_bed3_4_zone_temp": "16.5",
                            }
                        ),
                        base_attr_map("22.0"),
                    )
                ),
                EquipmentDemand(
                    maintain_heat_mode=True,
                    requested_by_zones=("office",),
                    max_temperature_deficit=0.5,
                    reason="office is below continue-until threshold",
                ),
                ("office",),
                current_hvac_mode="off",
                current_fan_mode="low",
                idle_shutdown_at=now,
                idle_shutdown_heat_step=-4,
                idle_shutdown_zone_key="office",
                now=now + timedelta(minutes=5),
            ),
            now + timedelta(minutes=5),
            current_hvac_mode="off",
        )

        self.assertIsNone(temptamer_main.RUNTIME_STATE["idle_shutdown_at"])
        self.assertIsNone(temptamer_main.RUNTIME_STATE["idle_shutdown_heat_step"])
        self.assertIsNone(temptamer_main.RUNTIME_STATE["idle_shutdown_zone_key"])

    def test_no_cool_demand_enters_idle_once_all_zones_drop_to_ideal_target(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_hvac_mode": "Cool",
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "19.0",
                        "sensor.office_average_temperature": "19.5",
                        "sensor.average_dining_zone_temp": "12.0",
                        "sensor.average_bed1_2_zone_temp": "11.0",
                        "sensor.average_bed3_4_zone_temp": "11.0",
                    }
                ),
                base_attr_map("21.0", temperature="18.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_COOL)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="cool",
            current_fan_mode="low",
            current_setpoint="18.0",
        )

        self.assertEqual(demand.reason, "all enabled zones are at or below ideal target")
        self.assertTrue(plan.idle)
        self.assertFalse(plan.turn_off)
        self.assertEqual(plan.hvac_mode, "cool")
        self.assertEqual(plan.setpoint, 21)
        self.assertEqual(plan.cool_release_setpoint, 21)

    def test_cooling_continues_until_all_zones_drop_to_ideal_target(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_hvac_mode": "Cool",
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "21.5",
                        "sensor.office_average_temperature": "21.5",
                        "sensor.average_dining_zone_temp": "14.0",
                        "sensor.average_bed1_2_zone_temp": "13.0",
                        "sensor.average_bed3_4_zone_temp": "13.0",
                    }
                ),
                base_attr_map("22.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_COOL)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="cool",
            current_fan_mode="low",
        )

        self.assertTrue(demand.maintain_cool_mode)
        self.assertEqual(demand.reason, "office is above ideal target")
        self.assertFalse(plan.turn_off)
        self.assertEqual(plan.hvac_mode, "cool")
        self.assertEqual(plan.setpoint, 22)

    def test_idle_dispatch_turns_system_off_after_one_hour(self):
        now = datetime(2026, 1, 1, 13, 0, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "23.0",
                        "sensor.office_average_temperature": "22.5",
                        "sensor.average_dining_zone_temp": "17.5",
                        "sensor.average_bed1_2_zone_temp": "16.5",
                        "sensor.average_bed3_4_zone_temp": "16.5",
                    }
                ),
                base_attr_map("22.0"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            idle_started_at=now - timedelta(hours=1),
            now=now,
        )

        self.assertEqual(demand.reason, "all enabled zones are at or above continue-until threshold")
        self.assertTrue(plan.turn_off)
        self.assertFalse(plan.idle)

    def test_idle_timer_is_preserved_until_hvac_reports_off(self):
        started_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        plan = build_dispatch_plan(
            build_behavior_snapshot(
                FakeReader(
                    base_state_map(
                        **{
                            "input_select.temptamer_comfort_mode": "Office",
                            "sensor.home_temperature": "23.0",
                            "sensor.office_average_temperature": "22.5",
                            "sensor.average_dining_zone_temp": "17.5",
                            "sensor.average_bed1_2_zone_temp": "16.5",
                            "sensor.average_bed3_4_zone_temp": "16.5",
                        }
                    ),
                    base_attr_map("22.0"),
                )
            ),
            EquipmentDemand(reason="all enabled zones are at or above continue-until threshold"),
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            idle_started_at=started_at,
            now=started_at + timedelta(hours=1),
        )

        self.assertTrue(plan.turn_off)
        self.assertEqual(
            resolve_idle_started_at(
                started_at,
                plan,
                current_hvac_mode="heat",
                now=started_at + timedelta(hours=1, minutes=5),
            ),
            started_at,
        )
        self.assertIsNone(
            resolve_idle_started_at(
                started_at,
                plan,
                current_hvac_mode="off",
                now=started_at + timedelta(hours=1, minutes=5),
            )
        )

    def test_equipment_demand_lists_all_requesting_zones(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Day",
                        "sensor.office_average_temperature": "17.0",
                        "sensor.average_dining_zone_temp": "18.0",
                        "sensor.average_bed1_2_zone_temp": "19.5",
                        "sensor.average_bed3_4_zone_temp": "19.5",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                        "switch.wt32_hpctrl_e8dbd0_dining": "on",
                    }
                ),
                base_attr_map("16.4"),
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office", "dining"), operation_mode=HVAC_HEAT)
        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office", "dining"),
            current_hvac_mode="off",
            current_fan_mode="low",
        )

        self.assertEqual(demand.requested_by_zones, ("office", "dining"))
        self.assertEqual(plan.requested_by_zones, ("office", "dining"))
        self.assertEqual(plan.setpoint, 19)

    def test_heatcool_mode_holds_current_mode_during_antiflap_window(self):
        now = datetime(2026, 5, 7, 12, 1, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_hvac_mode": "HeatCool",
                        "sensor.home_temperature": "22.0",
                        "sensor.office_average_temperature": "24.5",
                        "sensor.average_dining_zone_temp": "20.0",
                        "sensor.average_bed1_2_zone_temp": "20.0",
                        "sensor.average_bed3_4_zone_temp": "20.0",
                    }
                ),
                base_attr_map("24.0"),
            )
        )

        operating_mode, reason = resolve_operating_mode(
            snapshot,
            current_hvac_mode="heat",
            last_active_hvac_mode="heat",
            last_heatcool_transition=now - timedelta(minutes=30),
            now=now,
        )

        self.assertEqual(operating_mode, HVAC_HEAT)
        self.assertIn("anti-flap", reason)

    def test_heatcool_mode_ignores_backward_time_skew_for_antiflap(self):
        now = datetime(2026, 5, 7, 12, 1, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_hvac_mode": "HeatCool",
                        "sensor.home_temperature": "22.0",
                        "sensor.office_average_temperature": "24.5",
                        "sensor.average_dining_zone_temp": "20.0",
                        "sensor.average_bed1_2_zone_temp": "20.0",
                        "sensor.average_bed3_4_zone_temp": "20.0",
                    }
                ),
                base_attr_map("24.0"),
            )
        )

        operating_mode, reason = resolve_operating_mode(
            snapshot,
            current_hvac_mode="heat",
            last_active_hvac_mode="heat",
            last_heatcool_transition=now + timedelta(minutes=5),
            now=now,
        )

        self.assertEqual(operating_mode, HVAC_HEAT)
        self.assertIn("anti-flap", reason)

    def test_manual_mode_skips_zone_and_heatpump_changes(self):
        temptamer_main.state._values.clear()
        temptamer_main.state._attrs.clear()
        temptamer_main.RUNTIME_STATE.clear()
        temptamer_main.RUNTIME_STATE.update(
            {
                "last_successful_control_pass": None,
                "last_zone_change": {},
                "pending_zone_state": {},
                "last_error": None,
                "last_heatcool_transition": None,
                "last_active_hvac_mode": None,
                "idle_started_at": None,
                "last_trigger": None,
            }
        )
        temptamer_main.state._values.update(
            base_state_map(
                **{
                    "input_select.temptamer_hvac_mode": "Manual",
                    "switch.wt32_hpctrl_e8dbd0_office": "on",
                    TEST_CLIMATE_ENTITY: "heat",
                }
            )
        )
        temptamer_main.state._attrs[TEST_CLIMATE_ENTITY] = {
            "fan_mode": "low",
            "temperature": 19,
            "current_temperature": 19,
        }
        service_call = Mock()
        temptamer_main.service.call = service_call

        temptamer_main.run_control_pass(reason="manual test")

        self.assertFalse(service_call.called)
        self.assertEqual(temptamer_main.state.get(temptamer_main.STATUS_ENTITY_ID), "manual")

    def test_run_control_pass_uses_supported_level_fan_modes(self):
        temptamer_main.state._values.clear()
        temptamer_main.state._attrs.clear()
        temptamer_main.RUNTIME_STATE.clear()
        temptamer_main.RUNTIME_STATE.update(
            {
                "last_successful_control_pass": datetime(2026, 5, 7, 11, 59, 0, tzinfo=timezone.utc),
                "last_zone_change": {},
                "pending_zone_state": {},
                "last_error": None,
                "last_heatcool_transition": None,
                "last_active_hvac_mode": None,
                "idle_started_at": None,
                "last_trigger": None,
            }
        )
        temptamer_main.state._values.update(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": "Office",
                    "input_select.temptamer_hvac_mode": "Heat",
                    "sensor.office_average_temperature": "14.5",
                    "sensor.average_dining_zone_temp": "18.0",
                    "sensor.average_bed1_2_zone_temp": "18.0",
                    "sensor.average_bed3_4_zone_temp": "18.0",
                    "switch.wt32_hpctrl_e8dbd0_office": "on",
                    TEST_CLIMATE_ENTITY: "heat",
                }
            )
        )
        temptamer_main.state._attrs[TEST_CLIMATE_ENTITY] = {
            "fan_mode": "Level 1",
            "fan_modes": ["Level 1", "Level 2", "Level 3"],
            "temperature": 18,
            "current_temperature": 20,
        }
        service_call = Mock()
        temptamer_main.service.call = service_call

        temptamer_main.run_control_pass(reason="level fan test")

        self.assertEqual(
            service_call.call_args_list,
            [
                call(
                    "climate",
                    "set_fan_mode",
                    blocking=True,
                    entity_id=TEST_CLIMATE_ENTITY,
                    fan_mode="Level 2",
                ),
                call(
                    "climate",
                    "set_temperature",
                    blocking=True,
                    entity_id=TEST_CLIMATE_ENTITY,
                    temperature=24,
                ),
            ],
        )

    def test_run_control_pass_closes_office_when_high_fan_is_requested(self):
        now = datetime(2026, 8, 24, 12, 0, 0, tzinfo=timezone.utc)
        real_system_now = temptamer_main._system_now
        temptamer_main.state._values.clear()
        temptamer_main.state._attrs.clear()
        temptamer_main.RUNTIME_STATE.clear()
        temptamer_main.RUNTIME_STATE.update(deepcopy(self.original_runtime_state))
        temptamer_main.RUNTIME_STATE["last_successful_control_pass"] = now - timedelta(minutes=1)
        temptamer_main.state._values.update(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": "Day",
                    "input_select.temptamer_hvac_mode": "Heat",
                    "input_select.temptamer_comfort_mode_downstairs": "Auto",
                    "sensor.office_average_temperature": "21.0",
                    "sensor.average_dining_zone_temp": "15.0",
                    "sensor.downstairs_zone_average_temperature": "15.0",
                    "sensor.average_bed1_2_zone_temp": "12.0",
                    "switch.wt32_hpctrl_e8dbd0_office": "on",
                    "switch.wt32_hpctrl_e8dbd0_dining": "on",
                    "switch.roof_wt32_hpctrl_e8dbd0_downstairs": "on",
                    "switch.wt32_hpctrl_e8dbd0_bed_12": "on",
                    TEST_CLIMATE_ENTITY: "heat",
                }
            )
        )
        temptamer_main.state._attrs[TEST_CLIMATE_ENTITY] = {
            "fan_mode": "Level 1",
            "fan_modes": [f"Level {level}" for level in range(1, 7)],
            "temperature": 20,
            "current_temperature": 20,
        }
        service_call = Mock()
        temptamer_main.service.call = service_call
        temptamer_main._system_now = lambda: now

        try:
            temptamer_main.run_control_pass(reason="high fan office closure test")
        finally:
            temptamer_main._system_now = real_system_now

        self.assertIn(
            call(
                "switch",
                "turn_off",
                blocking=True,
                entity_id="switch.wt32_hpctrl_e8dbd0_office",
            ),
            service_call.call_args_list,
        )
        self.assertIn(
            call(
                "climate",
                "set_fan_mode",
                blocking=True,
                entity_id=TEST_CLIMATE_ENTITY,
                fan_mode="Level 6",
            ),
            service_call.call_args_list,
        )

    def test_run_control_pass_records_each_fan_speed_reduction(self):
        now = datetime(2026, 8, 17, 12, 16, 30, tzinfo=timezone.utc)
        real_system_now = temptamer_main._system_now
        temptamer_main.state._values.clear()
        temptamer_main.state._attrs.clear()
        temptamer_main.RUNTIME_STATE.clear()
        temptamer_main.RUNTIME_STATE.update(deepcopy(self.original_runtime_state))
        temptamer_main.RUNTIME_STATE["last_successful_control_pass"] = now - timedelta(minutes=1)
        temptamer_main.state._values.update(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": "Office",
                    "input_select.temptamer_hvac_mode": "Heat",
                    "sensor.office_average_temperature": "14.5",
                    "sensor.average_dining_zone_temp": "18.0",
                    "sensor.average_bed1_2_zone_temp": "18.0",
                    "sensor.average_bed3_4_zone_temp": "18.0",
                    "switch.wt32_hpctrl_e8dbd0_office": "on",
                    TEST_CLIMATE_ENTITY: "heat",
                }
            )
        )
        temptamer_main.state._attrs[TEST_CLIMATE_ENTITY] = {
            "fan_mode": "Level 6",
            "fan_modes": [f"Level {level}" for level in range(1, 7)],
            "temperature": 18,
            "current_temperature": 20,
        }
        service_call = Mock()
        temptamer_main.service.call = service_call
        temptamer_main._system_now = lambda: now

        try:
            temptamer_main.run_control_pass(reason="fan decrease test")
        finally:
            temptamer_main._system_now = real_system_now

        self.assertIn(
            call(
                "climate",
                "set_fan_mode",
                blocking=True,
                entity_id=TEST_CLIMATE_ENTITY,
                fan_mode="Level 5",
            ),
            service_call.call_args_list,
        )
        self.assertEqual(temptamer_main.RUNTIME_STATE["last_fan_speed_decrease_at"], now)

    def test_system_now_uses_home_assistant_local_time(self):
        expected_now = datetime(2026, 8, 12, 18, 11, 17, tzinfo=timezone(timedelta(hours=12)))
        homeassistant_module = ModuleType("homeassistant")
        util_module = ModuleType("homeassistant.util")
        dt_module = ModuleType("homeassistant.util.dt")
        dt_module.now = Mock(return_value=expected_now)
        util_module.dt = dt_module
        homeassistant_module.util = util_module

        with patch.dict(
            sys.modules,
            {
                "homeassistant": homeassistant_module,
                "homeassistant.util": util_module,
                "homeassistant.util.dt": dt_module,
            },
        ):
            self.assertEqual(temptamer_main._system_now(), expected_now)

        dt_module.now.assert_called_once_with()

    def test_system_now_uses_hass_config_timezone_when_dt_util_is_unavailable(self):
        real_hass = getattr(temptamer_main, "hass", None)
        had_hass = hasattr(temptamer_main, "hass")
        temptamer_main.hass = SimpleNamespace(config=SimpleNamespace(time_zone="Pacific/Auckland"))

        try:
            with patch.dict(sys.modules, {"homeassistant": None}):
                now = temptamer_main._system_now()
        finally:
            if had_hass:
                temptamer_main.hass = real_hass
            else:
                delattr(temptamer_main, "hass")

        self.assertEqual(str(now.tzinfo), "Pacific/Auckland")

    def test_run_control_pass_uses_system_time_for_downstairs_day_schedule(self):
        fake_now = datetime(2026, 7, 25, 23, 29, 0, tzinfo=timezone(timedelta(hours=10)))
        captured_snapshots = []
        real_system_now = temptamer_main._system_now
        real_build_snapshot = temptamer_main.build_snapshot

        def capture_build_snapshot(*args, **kwargs):
            snapshot = real_build_snapshot(*args, **kwargs)
            captured_snapshots.append(snapshot)
            return snapshot

        temptamer_main.state._values.clear()
        temptamer_main.state._attrs.clear()
        temptamer_main.RUNTIME_STATE.clear()
        temptamer_main.RUNTIME_STATE.update(deepcopy(self.original_runtime_state))
        temptamer_main.state._values.update(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": "Day",
                    "input_select.temptamer_hvac_mode": "Heat",
                    "input_select.temptamer_comfort_mode_downstairs": "Auto",
                    "sensor.office_average_temperature": "22.0",
                    "sensor.average_dining_zone_temp": "22.0",
                    "sensor.downstairs_zone_average_temperature": "17.5",
                    "sensor.average_bed1_2_zone_temp": "18.0",
                    "sensor.average_bed3_4_zone_temp": "18.0",
                    "switch.roof_wt32_hpctrl_e8dbd0_downstairs": "on",
                    TEST_CLIMATE_ENTITY: "heat",
                }
            )
        )
        temptamer_main.state._attrs[TEST_CLIMATE_ENTITY] = {
            "fan_mode": "Level 1",
            "fan_modes": ["Level 1", "Level 2", "Level 3"],
            "temperature": 18,
            "current_temperature": 18,
        }
        service_call = Mock()
        temptamer_main.service.call = service_call
        temptamer_main._system_now = lambda: fake_now
        temptamer_main.build_snapshot = capture_build_snapshot

        try:
            temptamer_main.run_control_pass(reason="local time schedule test")
        finally:
            temptamer_main._system_now = real_system_now
            temptamer_main.build_snapshot = real_build_snapshot

        self.assertEqual(captured_snapshots[0].zones["downstairs"].scheme.name, SCHEME_DOWNSTAIRS)
        self.assertEqual(captured_snapshots[0].zones["downstairs"].scheme.continue_until, 20.6)
        self.assertNotIn(
            call(
                "switch",
                "turn_off",
                blocking=True,
                entity_id="switch.roof_wt32_hpctrl_e8dbd0_downstairs",
            ),
            service_call.call_args_list,
        )
        self.assertEqual(temptamer_main.RUNTIME_STATE["last_successful_control_pass"], fake_now)

    def test_run_control_pass_startup_reconcile_opens_continue_heating_zone_and_logs_request(self):
        temptamer_main.state._values.clear()
        temptamer_main.state._attrs.clear()
        temptamer_main.RUNTIME_STATE.clear()
        temptamer_main.RUNTIME_STATE.update(
            {
                "last_successful_control_pass": None,
                "last_zone_change": {},
                "pending_zone_state": {},
                "last_error": None,
                "last_heatcool_transition": None,
                "last_active_hvac_mode": None,
                "idle_started_at": None,
                "idle_heat_step": None,
                "idle_heat_step_changed_at": None,
                "idle_heat_zone_key": None,
                "idle_shutdown_at": None,
                "idle_shutdown_heat_step": None,
                "idle_shutdown_zone_key": None,
                "last_trigger": None,
            }
        )
        temptamer_main.state._values.update(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": "Night",
                    TEST_CLIMATE_ENTITY: "heat",
                    "sensor.home_temperature": "18.0",
                    "sensor.office_average_temperature": "16.95",
                    "sensor.average_dining_zone_temp": "18.0",
                    "sensor.average_bed1_2_zone_temp": "16.9",
                    "sensor.average_bed3_4_zone_temp": "18.0",
                    "switch.wt32_hpctrl_e8dbd0_office": "on",
                }
            )
        )
        temptamer_main.state._attrs[TEST_CLIMATE_ENTITY] = {
            "fan_mode": "low",
            "temperature": 23,
            "current_temperature": 24.5,
        }
        service_call = Mock()
        temptamer_main.service.call = service_call

        with self.assertLogs("pyscript.temptamer", level="INFO") as captured:
            temptamer_main.run_control_pass(reason="startup")

        self.assertEqual(
            service_call.call_args_list,
            [
                call(
                    "switch",
                    "turn_on",
                    blocking=True,
                    entity_id="switch.wt32_hpctrl_e8dbd0_office",
                ),
                call(
                    "switch",
                    "turn_off",
                    blocking=True,
                    entity_id="switch.wt32_hpctrl_e8dbd0_dining",
                ),
                call(
                    "switch",
                    "turn_off",
                    blocking=True,
                    entity_id="switch.roof_wt32_hpctrl_e8dbd0_downstairs",
                ),
                call(
                    "switch",
                    "turn_on",
                    blocking=True,
                    entity_id="switch.wt32_hpctrl_e8dbd0_bed_12",
                ),
                call(
                    "switch",
                    "turn_off",
                    blocking=True,
                    entity_id="switch.wt32_hpctrl_e8dbd0_bed_34",
                )
            ],
        )
        self.assertEqual(
            temptamer_main.RUNTIME_STATE["pending_zone_state"],
            {
                "office": True,
                "dining": False,
                "downstairs": False,
                "bedroom_1_2": True,
                "bedroom_3_4": False,
            },
        )
        self.assertTrue(
            any(
                "ZONES: requesting open for Bedroom 1&2 via switch.turn_on entity=switch.wt32_hpctrl_e8dbd0_bed_12 because 16.9 is below continue-until threshold 17.0"
                in entry
                for entry in captured.output
            )
        )
        self.assertTrue(
            any(
                "ZONES: startup_reconcile reported_open=Office desired_open=Bedroom 1&2, Office" in entry
                or "ZONES: startup_reconcile reported_open=Office desired_open=Office, Bedroom 1&2" in entry
                for entry in captured.output
            )
        )

    def test_run_control_pass_startup_reconcile_reasserts_desired_zone_state_when_reported_open_set_is_stale(self):
        temptamer_main.state._values.clear()
        temptamer_main.state._attrs.clear()
        temptamer_main.RUNTIME_STATE.clear()
        temptamer_main.RUNTIME_STATE.update(
            {
                "last_successful_control_pass": None,
                "last_zone_change": {},
                "pending_zone_state": {},
                "last_error": None,
                "last_heatcool_transition": None,
                "last_active_hvac_mode": None,
                "idle_started_at": None,
                "idle_heat_step": None,
                "idle_heat_step_changed_at": None,
                "idle_heat_zone_key": None,
                "idle_shutdown_at": None,
                "idle_shutdown_heat_step": None,
                "idle_shutdown_zone_key": None,
                "last_trigger": None,
            }
        )
        temptamer_main.state._values.update(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": "Night",
                    TEST_CLIMATE_ENTITY: "heat",
                    "sensor.home_temperature": "18.0",
                    "sensor.office_average_temperature": "18.2",
                    "sensor.average_dining_zone_temp": "18.2",
                    "sensor.average_bed1_2_zone_temp": "16.9",
                    "sensor.average_bed3_4_zone_temp": "18.0",
                    "switch.wt32_hpctrl_e8dbd0_office": "off",
                    "switch.wt32_hpctrl_e8dbd0_dining": "on",
                    "switch.wt32_hpctrl_e8dbd0_bed_12": "on",
                }
            )
        )
        temptamer_main.state._attrs[TEST_CLIMATE_ENTITY] = {
            "fan_mode": "low",
            "temperature": 23,
            "current_temperature": 24.5,
        }
        service_call = Mock()
        temptamer_main.service.call = service_call

        with self.assertLogs("pyscript.temptamer", level="INFO") as captured:
            temptamer_main.run_control_pass(reason="startup")

        self.assertEqual(
            service_call.call_args_list,
            [
                call(
                    "switch",
                    "turn_off",
                    blocking=True,
                    entity_id="switch.wt32_hpctrl_e8dbd0_office",
                ),
                call(
                    "switch",
                    "turn_off",
                    blocking=True,
                    entity_id="switch.wt32_hpctrl_e8dbd0_dining",
                ),
                call(
                    "switch",
                    "turn_off",
                    blocking=True,
                    entity_id="switch.roof_wt32_hpctrl_e8dbd0_downstairs",
                ),
                call(
                    "switch",
                    "turn_on",
                    blocking=True,
                    entity_id="switch.wt32_hpctrl_e8dbd0_bed_12",
                ),
                call(
                    "switch",
                    "turn_off",
                    blocking=True,
                    entity_id="switch.wt32_hpctrl_e8dbd0_bed_34",
                ),
            ],
        )
        self.assertTrue(
            any(
                "ZONES: startup_reconcile reported_open=Bedroom 1&2, Dining desired_open=Bedroom 1&2" in entry
                for entry in captured.output
            )
        )

    def test_run_control_pass_comfort_mode_change_turns_off_before_closing_zones_for_fan_rundown(self):
        now = datetime(2026, 8, 30, 7, 46, tzinfo=timezone.utc)
        real_system_now = temptamer_main._system_now
        temptamer_main.state._values.clear()
        temptamer_main.state._attrs.clear()
        temptamer_main.RUNTIME_STATE.clear()
        temptamer_main.RUNTIME_STATE.update(
            {
                "last_successful_control_pass": None,
                "last_zone_change": {},
                "pending_zone_state": {},
                "last_error": None,
                "last_heatcool_transition": None,
                "last_active_hvac_mode": None,
                "idle_started_at": None,
                "idle_heat_step": None,
                "idle_heat_step_changed_at": None,
                "idle_heat_zone_key": None,
                "idle_shutdown_at": None,
                "idle_shutdown_heat_step": None,
                "idle_shutdown_zone_key": None,
                "last_trigger": None,
            }
        )
        temptamer_main.state._values.update(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": "Night",
                    "input_select.temptamer_comfort_mode_office": "Auto",
                    TEST_CLIMATE_ENTITY: "heat",
                    "sensor.home_temperature": "18.0",
                    "sensor.office_average_temperature": "18.0",
                    "sensor.average_dining_zone_temp": "18.5",
                    "sensor.average_bed1_2_zone_temp": "18.5",
                    "sensor.average_bed3_4_zone_temp": "18.5",
                    "switch.wt32_hpctrl_e8dbd0_office": "on",
                    "switch.wt32_hpctrl_e8dbd0_dining": "on",
                }
            )
        )
        temptamer_main.state._attrs[TEST_CLIMATE_ENTITY] = {
            "fan_mode": "low",
            "temperature": 22,
            "current_temperature": 22,
        }
        service_call = Mock()
        temptamer_main.service.call = service_call

        try:
            temptamer_main._system_now = lambda: now
            temptamer_main.run_control_pass(reason="mode selection changed", comfort_mode_changed=True)

            self.assertCountEqual(
                service_call.call_args_list,
                [call("climate", "turn_off", blocking=True, entity_id=TEST_CLIMATE_ENTITY)],
            )
            self.assertEqual(
                temptamer_main.RUNTIME_STATE["immediate_shutdown_zone_close_not_before"],
                now + timedelta(minutes=2),
            )

            temptamer_main.state._values[TEST_CLIMATE_ENTITY] = "off"
            service_call.reset_mock()
            temptamer_main._system_now = lambda: now + timedelta(seconds=119)
            temptamer_main.run_control_pass(reason="fan rundown hold")
            self.assertEqual(service_call.call_args_list, [])

            temptamer_main._system_now = lambda: now + timedelta(minutes=2)
            temptamer_main.run_control_pass(reason="fan rundown complete")
            self.assertCountEqual(
                service_call.call_args_list,
                [
                    call(
                        "switch",
                        "turn_off",
                        blocking=True,
                        entity_id="switch.wt32_hpctrl_e8dbd0_office",
                    ),
                    call(
                        "switch",
                        "turn_off",
                        blocking=True,
                        entity_id="switch.wt32_hpctrl_e8dbd0_dining",
                    ),
                ],
            )
            self.assertIsNone(temptamer_main.RUNTIME_STATE["immediate_shutdown_zone_close_not_before"])
        finally:
            temptamer_main._system_now = real_system_now

    def test_run_control_pass_uses_climate_target_temp_step_for_set_temperature(self):
        temptamer_main.state._values.clear()
        temptamer_main.state._attrs.clear()
        temptamer_main.RUNTIME_STATE.clear()
        temptamer_main.RUNTIME_STATE.update(
            {
                "last_successful_control_pass": None,
                "last_zone_change": {},
                "pending_zone_state": {},
                "last_error": None,
                "last_heatcool_transition": None,
                "last_active_hvac_mode": None,
                "idle_started_at": None,
                "idle_heat_step": None,
                "idle_heat_step_changed_at": None,
                "idle_heat_zone_key": None,
                "idle_shutdown_at": None,
                "idle_shutdown_heat_step": None,
                "idle_shutdown_zone_key": None,
                "last_trigger": None,
            }
        )
        temptamer_main.state._values.update(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": "Night",
                    TEST_CLIMATE_ENTITY: "heat",
                    "sensor.home_temperature": "19.0",
                    "sensor.office_average_temperature": "14.5",
                    "sensor.average_dining_zone_temp": "19.5",
                    "sensor.average_bed1_2_zone_temp": "19.5",
                    "sensor.average_bed3_4_zone_temp": "19.5",
                    "switch.wt32_hpctrl_e8dbd0_office": "on",
                }
            )
        )
        temptamer_main.state._attrs[TEST_CLIMATE_ENTITY] = {
            "fan_mode": "low",
            "temperature": 22,
            "current_temperature": 22,
            "target_temp_step": 0.5,
        }
        service_call = Mock()
        temptamer_main.service.call = service_call

        temptamer_main.run_control_pass(reason="mode selection changed", comfort_mode_changed=True)

        self.assertEqual(
            service_call.call_args_list,
            [
                call(
                    "climate",
                    "set_temperature",
                    blocking=True,
                    entity_id=TEST_CLIMATE_ENTITY,
                    temperature=22.5,
                )
            ],
        )
        self.assertIsNone(temptamer_main.RUNTIME_STATE["idle_heat_step"])

    def test_invalid_target_temp_step_falls_back_to_integer_setpoints(self):
        self.assertEqual(normalize_heat_setpoint(17.6, None), 17)
        self.assertEqual(normalize_heat_setpoint(17.6, "unknown"), 17)
        self.assertEqual(normalize_cool_setpoint(23.2, 0), 24)

    def test_fan_hysteresis_uses_medium_thresholds(self):
        self.assertEqual(
            resolve_fan_mode(
                "low",
                "heat",
                EquipmentDemand(heat_requested=True, max_temperature_deficit=4.1),
            ),
            "medium",
        )
        self.assertEqual(
            resolve_fan_mode(
                "medium",
                "heat",
                EquipmentDemand(heat_requested=True, max_temperature_deficit=3.0),
            ),
            "medium",
        )
        self.assertEqual(
            resolve_fan_mode(
                "medium",
                "heat",
                EquipmentDemand(heat_requested=True, max_temperature_deficit=1.2),
            ),
            "low",
        )
        self.assertEqual(normalize_setpoint(25.1), 25)

    def test_fan_uses_start_threshold_when_comfort_mode_changes(self):
        demand = EquipmentDemand(heat_requested=True, max_temperature_deficit=2.6)

        self.assertEqual(
            resolve_fan_mode(
                "low",
                "heat",
                demand,
            ),
            "low",
        )
        self.assertEqual(
            resolve_fan_mode(
                "low",
                "heat",
                demand,
                comfort_mode_changed=True,
            ),
            "medium",
        )

    def test_fan_hysteresis_prefers_supported_level_modes(self):
        supported_fan_modes = ("Level 1", "Level 2", "Level 3")

        self.assertEqual(
            resolve_fan_mode(
                "Level 1",
                "heat",
                EquipmentDemand(heat_requested=True, max_temperature_deficit=4.1),
                supported_fan_modes=supported_fan_modes,
            ),
            "Level 2",
        )
        self.assertEqual(
            resolve_fan_mode(
                "Level 2",
                "heat",
                EquipmentDemand(heat_requested=True, max_temperature_deficit=1.2),
                supported_fan_modes=supported_fan_modes,
            ),
            "Level 1",
        )
        self.assertEqual(
            resolve_fan_mode(
                "Level 2",
                "fan_only",
                EquipmentDemand(fan_only_requested=True),
                supported_fan_modes=supported_fan_modes,
            ),
            "Level 1",
        )

    def test_fan_speed_decreases_by_one_level_every_three_minutes(self):
        supported_fan_modes = tuple(f"Level {level}" for level in range(1, 7))
        demand = EquipmentDemand(heat_requested=True, max_temperature_deficit=1.0)
        now = datetime(2026, 8, 17, 12, 16, 30, tzinfo=timezone.utc)

        self.assertEqual(
            resolve_fan_mode(
                "Level 6",
                "heat",
                demand,
                open_zone_count=2,
                supported_fan_modes=supported_fan_modes,
                now=now,
            ),
            "Level 5",
        )
        self.assertEqual(
            resolve_fan_mode(
                "Level 5",
                "heat",
                demand,
                open_zone_count=2,
                supported_fan_modes=supported_fan_modes,
                fan_speed_decrease_at=now - timedelta(minutes=2, seconds=59),
                now=now,
            ),
            "Level 5",
        )
        self.assertEqual(
            resolve_fan_mode(
                "Level 5",
                "heat",
                demand,
                open_zone_count=2,
                supported_fan_modes=supported_fan_modes,
                fan_speed_decrease_at=now - timedelta(minutes=3),
                now=now,
            ),
            "Level 4",
        )

    def test_hvac_start_fan_ramp_increases_from_level_one_to_the_requested_level_over_fifteen_minutes(self):
        supported_fan_modes = tuple(f"Level {level}" for level in range(1, 7))
        started_at = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)

        self.assertEqual(
            resolve_hvac_start_fan_ramp_mode(
                "Level 6",
                started_at,
                supported_fan_modes=supported_fan_modes,
                now=started_at,
            ),
            "Level 1",
        )
        self.assertEqual(
            resolve_hvac_start_fan_ramp_mode(
                "Level 6",
                started_at,
                supported_fan_modes=supported_fan_modes,
                now=started_at + timedelta(minutes=3),
            ),
            "Level 2",
        )
        self.assertEqual(
            resolve_hvac_start_fan_ramp_mode(
                "Level 6",
                started_at,
                supported_fan_modes=supported_fan_modes,
                now=started_at + timedelta(minutes=12),
            ),
            "Level 5",
        )
        self.assertEqual(
            resolve_hvac_start_fan_ramp_mode(
                "Level 6",
                started_at,
                supported_fan_modes=supported_fan_modes,
                now=started_at + timedelta(seconds=HVAC_START_FAN_RAMP_DURATION_SECONDS),
            ),
            "Level 6",
        )

    def test_heat_demand_fan_boost_requires_low_power_and_open_zone_continue_gap(self):
        now = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.office_average_temperature": "18.1",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                    }
                ),
                base_attr_map("20.0"),
            )
        )
        demand = EquipmentDemand(heat_requested=True, max_temperature_deficit=0.5)

        boost_level, boosted_at, reason = resolve_heat_demand_fan_boost(
            snapshot,
            demand,
            ("office",),
            13.99,
            now=now,
        )

        self.assertEqual(boost_level, 1)
        self.assertEqual(boosted_at, now)
        self.assertIn("eligible", reason)

        self.assertEqual(
            resolve_heat_demand_fan_boost(snapshot, demand, ("office",), 14.0, now=now)[:2],
            (0, None),
        )
        self.assertEqual(
            resolve_heat_demand_fan_boost(snapshot, demand, ("dining",), 13.99, now=now)[:2],
            (0, None),
        )
        self.assertEqual(
            resolve_heat_demand_fan_boost(
                snapshot,
                EquipmentDemand(cool_requested=True, max_temperature_deficit=3.0),
                ("office",),
                13.99,
                now=now,
            )[:2],
            (0, None),
        )
        self.assertEqual(
            resolve_heat_demand_fan_boost(snapshot, demand, ("office",), None, now=now)[:2],
            (0, None),
        )
        self.assertEqual(
            resolve_heat_demand_fan_boost(snapshot, demand, ("office",), float("nan"), now=now)[:2],
            (0, None),
        )

    def test_heat_demand_fan_boost_ramps_once_every_fifteen_minutes_and_caps_at_configured_maximum(self):
        now = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.office_average_temperature": "18.1",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                    }
                ),
                base_attr_map("20.0"),
            )
        )
        demand = EquipmentDemand(heat_requested=True, max_temperature_deficit=0.5)

        level, boosted_at, _ = resolve_heat_demand_fan_boost(snapshot, demand, ("office",), 13.5, now=now)
        self.assertEqual((level, boosted_at), (1, now))

        level, next_boosted_at, _ = resolve_heat_demand_fan_boost(
            snapshot,
            demand,
            ("office",),
            13.5,
            previous_boost_level=level,
            last_boost_at=boosted_at,
            now=now + timedelta(minutes=14, seconds=59),
        )
        self.assertEqual((level, next_boosted_at), (1, now))

        level, boosted_at, _ = resolve_heat_demand_fan_boost(
            snapshot,
            demand,
            ("office",),
            13.5,
            previous_boost_level=level,
            last_boost_at=next_boosted_at,
            now=now + timedelta(minutes=15),
        )
        self.assertEqual((level, boosted_at), (2, now + timedelta(minutes=15)))

        level, boosted_at, _ = resolve_heat_demand_fan_boost(
            snapshot,
            demand,
            ("office",),
            13.5,
            previous_boost_level=level,
            last_boost_at=boosted_at,
            now=now + timedelta(minutes=30),
        )
        self.assertEqual((level, boosted_at), (3, now + timedelta(minutes=30)))

        level, boosted_at, _ = resolve_heat_demand_fan_boost(
            snapshot,
            demand,
            ("office",),
            13.5,
            previous_boost_level=HEAT_DEMAND_FAN_BOOST_MAX_LEVEL,
            last_boost_at=now + timedelta(minutes=30),
            now=now + timedelta(minutes=45),
        )
        self.assertEqual((level, boosted_at), (HEAT_DEMAND_FAN_BOOST_MAX_LEVEL, now + timedelta(minutes=30)))

    def test_heat_demand_fan_boost_is_added_before_zone_multiplier_and_never_applies_to_cooling(self):
        supported_fan_modes = tuple(f"Level {level}" for level in range(1, 7))

        self.assertEqual(
            resolve_fan_mode(
                "Level 1",
                "heat",
                EquipmentDemand(heat_requested=True, max_temperature_deficit=1.0),
                open_zone_count=3,
                supported_fan_modes=supported_fan_modes,
                base_fan_boost=1,
            ),
            "Level 4",
        )
        self.assertEqual(
            resolve_fan_mode(
                "Level 1",
                "heat",
                EquipmentDemand(heat_requested=True, max_temperature_deficit=1.0),
                open_zone_count=4,
                supported_fan_modes=supported_fan_modes,
                base_fan_boost=3,
            ),
            "Level 6",
        )
        self.assertEqual(
            resolve_fan_mode(
                "Level 1",
                "cool",
                EquipmentDemand(cool_requested=True, max_temperature_deficit=1.0),
                supported_fan_modes=supported_fan_modes,
                base_fan_boost=3,
            ),
            "Level 1",
        )

    def test_run_control_pass_starts_long_off_heat_cycle_at_fan_level_one(self):
        now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)
        real_system_now = temptamer_main._system_now
        temptamer_main.state._values.clear()
        temptamer_main.state._attrs.clear()
        temptamer_main.RUNTIME_STATE.clear()
        temptamer_main.RUNTIME_STATE.update(deepcopy(self.original_runtime_state))
        temptamer_main.RUNTIME_STATE["last_successful_control_pass"] = now - timedelta(minutes=1)
        temptamer_main.RUNTIME_STATE["hvac_off_started_at"] = now - timedelta(
            seconds=HVAC_START_FAN_RAMP_MIN_OFF_SECONDS
        )
        temptamer_main.state._values.update(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": "Office",
                    "input_select.temptamer_hvac_mode": "Heat",
                    "sensor.office_average_temperature": "17.0",
                    "switch.wt32_hpctrl_e8dbd0_office": "on",
                    TEST_CLIMATE_ENTITY: "off",
                }
            )
        )
        temptamer_main.state._attrs[TEST_CLIMATE_ENTITY] = {
            "fan_mode": "Level 6",
            "fan_modes": [f"Level {level}" for level in range(1, 7)],
            "temperature": 20,
            "current_temperature": 20,
        }
        service_call = Mock()
        temptamer_main.service.call = service_call
        temptamer_main._system_now = lambda: now

        try:
            temptamer_main.run_control_pass(reason="long-off heat fan ramp test")
        finally:
            temptamer_main._system_now = real_system_now

        self.assertIn(
            call(
                "climate",
                "set_hvac_mode",
                blocking=True,
                entity_id=TEST_CLIMATE_ENTITY,
                hvac_mode="heat",
            ),
            service_call.call_args_list,
        )
        self.assertIn(
            call(
                "climate",
                "set_fan_mode",
                blocking=True,
                entity_id=TEST_CLIMATE_ENTITY,
                fan_mode="Level 1",
            ),
            service_call.call_args_list,
        )
        self.assertEqual(temptamer_main.RUNTIME_STATE["hvac_start_fan_ramp_started_at"], now)

    def test_run_control_pass_starts_long_off_cool_cycle_at_fan_level_one(self):
        now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)
        real_system_now = temptamer_main._system_now
        temptamer_main.state._values.clear()
        temptamer_main.state._attrs.clear()
        temptamer_main.RUNTIME_STATE.clear()
        temptamer_main.RUNTIME_STATE.update(deepcopy(self.original_runtime_state))
        temptamer_main.RUNTIME_STATE["last_successful_control_pass"] = now - timedelta(minutes=1)
        temptamer_main.RUNTIME_STATE["hvac_off_started_at"] = now - timedelta(
            seconds=HVAC_START_FAN_RAMP_MIN_OFF_SECONDS
        )
        temptamer_main.state._values.update(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": "Office",
                    "input_select.temptamer_hvac_mode": "Cool",
                    "sensor.office_average_temperature": "25.0",
                    "switch.wt32_hpctrl_e8dbd0_office": "on",
                    TEST_CLIMATE_ENTITY: "off",
                }
            )
        )
        temptamer_main.state._attrs[TEST_CLIMATE_ENTITY] = {
            "fan_mode": "Level 6",
            "fan_modes": [f"Level {level}" for level in range(1, 7)],
            "temperature": 20,
            "current_temperature": 20,
        }
        service_call = Mock()
        temptamer_main.service.call = service_call
        temptamer_main._system_now = lambda: now

        try:
            temptamer_main.run_control_pass(reason="long-off cool fan ramp test")
        finally:
            temptamer_main._system_now = real_system_now

        self.assertIn(
            call(
                "climate",
                "set_hvac_mode",
                blocking=True,
                entity_id=TEST_CLIMATE_ENTITY,
                hvac_mode="cool",
            ),
            service_call.call_args_list,
        )
        self.assertIn(
            call(
                "climate",
                "set_fan_mode",
                blocking=True,
                entity_id=TEST_CLIMATE_ENTITY,
                fan_mode="Level 1",
            ),
            service_call.call_args_list,
        )
        self.assertEqual(temptamer_main.RUNTIME_STATE["hvac_start_fan_ramp_started_at"], now)

    def test_run_control_pass_records_heat_demand_fan_boost_runtime_state(self):
        now = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
        real_system_now = temptamer_main._system_now
        temptamer_main.state._values.clear()
        temptamer_main.state._attrs.clear()
        temptamer_main.RUNTIME_STATE.clear()
        temptamer_main.RUNTIME_STATE.update(deepcopy(self.original_runtime_state))
        temptamer_main.RUNTIME_STATE["last_successful_control_pass"] = now - timedelta(minutes=1)
        temptamer_main.state._values.update(
            base_state_map(
                **{
                    "input_select.temptamer_comfort_mode": "Office",
                    "input_select.temptamer_hvac_mode": "Heat",
                    "sensor.office_average_temperature": "18.1",
                    "switch.wt32_hpctrl_e8dbd0_office": "on",
                    EAGLE_200_MAX_POWER_DEMAND_5M_SENSOR: "13.9",
                    TEST_CLIMATE_ENTITY: "heat",
                }
            )
        )
        temptamer_main.state._attrs[TEST_CLIMATE_ENTITY] = {
            "fan_mode": "Level 1",
            "fan_modes": [f"Level {level}" for level in range(1, 7)],
            "temperature": 20,
            "current_temperature": 20,
        }
        service_call = Mock()
        temptamer_main.service.call = service_call
        temptamer_main._system_now = lambda: now

        try:
            temptamer_main.run_control_pass(reason="heat demand fan boost test")
        finally:
            temptamer_main._system_now = real_system_now

        self.assertIn(
            call(
                "climate",
                "set_fan_mode",
                blocking=True,
                entity_id=TEST_CLIMATE_ENTITY,
                fan_mode="Level 2",
            ),
            service_call.call_args_list,
        )
        self.assertEqual(temptamer_main.RUNTIME_STATE["heat_demand_fan_boost_level"], 1)
        self.assertEqual(temptamer_main.RUNTIME_STATE["last_heat_demand_fan_boost_at"], now)
        self.assertEqual(temptamer_main.RUNTIME_STATE["max_power_demand_5m_kw"], 13.9)
        self.assertIn("eligible", temptamer_main.RUNTIME_STATE["heat_demand_fan_boost_reason"])
        status_attributes = temptamer_main.state.getattr(temptamer_main.STATUS_ENTITY_ID)
        self.assertEqual(status_attributes["heat_demand_fan_boost_level"], 1)
        self.assertEqual(Path(temptamer_main.HEAT_DEMAND_FAN_BOOST_STATE_FILE).read_text(encoding="utf-8"), "1\n")

    def test_heat_demand_fan_boost_restores_only_from_state_file_newer_than_fifteen_minutes(self):
        now = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as temporary_directory:
            state_file = Path(temporary_directory) / "fan_boost.state"
            temptamer_main.HEAT_DEMAND_FAN_BOOST_STATE_FILE = str(state_file)
            temptamer_main._persist_heat_demand_fan_boost_state(3)
            self.assertEqual(state_file.read_text(encoding="utf-8"), "3\n")

            fresh_modified_at = now - timedelta(minutes=14, seconds=59)
            fresh_timestamp = fresh_modified_at.timestamp()
            os.utime(state_file, (fresh_timestamp, fresh_timestamp))
            temptamer_main.RUNTIME_STATE.clear()
            temptamer_main.RUNTIME_STATE.update(deepcopy(self.original_runtime_state))

            temptamer_main._restore_heat_demand_fan_boost_state(now)

            self.assertEqual(temptamer_main.RUNTIME_STATE["heat_demand_fan_boost_level"], 3)
            self.assertEqual(temptamer_main.RUNTIME_STATE["last_heat_demand_fan_boost_at"], fresh_modified_at)

            stale_modified_at = now - timedelta(minutes=15)
            stale_timestamp = stale_modified_at.timestamp()
            os.utime(state_file, (stale_timestamp, stale_timestamp))
            temptamer_main.RUNTIME_STATE.clear()
            temptamer_main.RUNTIME_STATE.update(deepcopy(self.original_runtime_state))

            temptamer_main._restore_heat_demand_fan_boost_state(now)

            self.assertEqual(temptamer_main.RUNTIME_STATE["heat_demand_fan_boost_level"], 0)
            self.assertIsNone(temptamer_main.RUNTIME_STATE["last_heat_demand_fan_boost_at"])

    def test_fan_speed_level_scales_with_effective_open_zone_count(self):
        supported_fan_modes = ("Level 1", "Level 2", "Level 3", "Level 4", "Level 5", "Level 6")

        self.assertEqual(
            DEFAULT_SYSTEM_CONFIG.comfort_modes["Day"].fan_speed_level(
                1.0,
                3,
                current_speed_level=1,
            ),
            2,
        )
        self.assertEqual(
            DEFAULT_SYSTEM_CONFIG.comfort_modes["Day"].fan_speed_level(
                1.0,
                4,
                current_speed_level=1,
            ),
            3,
        )
        self.assertEqual(
            resolve_fan_mode(
                "Level 1",
                "heat",
                EquipmentDemand(heat_requested=True, max_temperature_deficit=1.0),
                open_zone_count=3,
                supported_fan_modes=supported_fan_modes,
            ),
            "Level 2",
        )
        self.assertEqual(
            resolve_fan_mode(
                "Level 1",
                "heat",
                EquipmentDemand(heat_requested=True, max_temperature_deficit=4.1),
                open_zone_count=3,
                supported_fan_modes=supported_fan_modes,
            ),
            "Level 4",
        )
        self.assertEqual(
            resolve_fan_mode(
                "Level 1",
                "heat",
                EquipmentDemand(heat_requested=True, max_temperature_deficit=1.0),
                open_zone_count=4,
                supported_fan_modes=supported_fan_modes,
            ),
            "Level 3",
        )
        self.assertEqual(
            resolve_fan_mode(
                "Level 1",
                "heat",
                EquipmentDemand(heat_requested=True, max_temperature_deficit=4.1),
                open_zone_count=4,
                supported_fan_modes=supported_fan_modes,
            ),
            "Level 6",
        )

    def test_fan_hysteresis_unscales_current_level_for_multiple_open_zones(self):
        supported_fan_modes = ("Level 1", "Level 2", "Level 3", "Level 4")
        demand = EquipmentDemand(heat_requested=True, max_temperature_deficit=1.3)

        self.assertEqual(
            resolve_fan_mode(
                "Level 2",
                "heat",
                demand,
                open_zone_count=3,
                supported_fan_modes=supported_fan_modes,
            ),
            "Level 2",
        )
        self.assertEqual(
            resolve_fan_mode(
                "Level 4",
                "heat",
                demand,
                open_zone_count=3,
                supported_fan_modes=supported_fan_modes,
            ),
            "Level 4",
        )

    def test_apply_dispatch_plan_does_not_reapply_an_unchanged_level_fan_mode(self):
        controller = Mock()

        apply_dispatch_plan(
            controller,
            DispatchPlan(turn_off=False, hvac_mode="heat", fan_mode="Level 2"),
            current_hvac_mode="heat",
            current_fan_mode="Level 2",
            current_setpoint=None,
        )

        controller.call_service.assert_not_called()

    def test_dispatch_plan_counts_open_downstairs_as_two_zones_for_fan_multiplier(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "sensor.office_average_temperature": "17.0",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                        "switch.roof_wt32_hpctrl_e8dbd0_downstairs": "on",
                    }
                ),
                base_attr_map("18.0"),
            )
        )
        demand = EquipmentDemand(
            heat_requested=True,
            requested_by_zones=("office",),
            max_temperature_deficit=1.0,
        )

        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office", "downstairs"),
            current_hvac_mode="heat",
            current_fan_mode="Level 1",
            supported_fan_modes=("Level 1", "Level 2", "Level 3"),
        )

        self.assertEqual(plan.fan_mode, "Level 2")

    def test_dispatch_plan_triples_fan_when_effective_open_zones_reach_four(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "sensor.office_average_temperature": "17.0",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                        "switch.wt32_hpctrl_e8dbd0_dining": "on",
                        "switch.roof_wt32_hpctrl_e8dbd0_downstairs": "on",
                    }
                ),
                base_attr_map("18.0"),
            )
        )
        demand = EquipmentDemand(
            heat_requested=True,
            requested_by_zones=("office",),
            max_temperature_deficit=1.0,
        )

        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office", "dining", "downstairs"),
            current_hvac_mode="heat",
            current_fan_mode="Level 1",
            supported_fan_modes=("Level 1", "Level 2", "Level 3"),
        )

        self.assertEqual(plan.fan_mode, "Level 3")

    def test_dispatch_plan_fan_multiplier_uses_reported_open_zones_not_new_predictions(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_hvac_mode": "Cool",
                        "sensor.office_average_temperature": "21.2",
                        "sensor.average_dining_zone_temp": "19.0",
                        "sensor.average_bed1_2_zone_temp": "18.9",
                        "sensor.average_bed3_4_zone_temp": "18.2",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                        "switch.wt32_hpctrl_e8dbd0_dining": "on",
                    }
                ),
                base_attr_map("23.0"),
            )
        )
        demand = EquipmentDemand(
            cool_requested=True,
            requested_by_zones=("bedroom_1_2",),
            max_temperature_deficit=2.9,
        )

        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("bedroom_1_2", "bedroom_3_4", "dining", "office"),
            current_hvac_mode="off",
            current_fan_mode="Level 1",
            supported_fan_modes=("Level 1", "Level 2", "Level 3", "Level 4", "Level 5"),
        )

        self.assertEqual(plan.open_zones, ("bedroom_1_2", "bedroom_3_4", "dining", "office"))
        self.assertEqual(plan.fan_mode, "Level 2")

    def test_night_comfort_mode_uses_quieter_fan_thresholds(self):
        night_mode = DEFAULT_SYSTEM_CONFIG.comfort_modes[COMFORT_MODE_NIGHT]

        self.assertEqual(
            resolve_fan_mode(
                "low",
                "heat",
                EquipmentDemand(heat_requested=True, max_temperature_deficit=4.1),
                comfort_mode=night_mode,
            ),
            "low",
        )
        self.assertEqual(
            resolve_fan_mode(
                "low",
                "heat",
                EquipmentDemand(heat_requested=True, max_temperature_deficit=6.1),
                comfort_mode=night_mode,
            ),
            "medium",
        )
        self.assertEqual(
            resolve_fan_mode(
                "medium",
                "heat",
                EquipmentDemand(heat_requested=True, max_temperature_deficit=2.9),
                comfort_mode=night_mode,
            ),
            "low",
        )

    def test_dispatch_plan_uses_selected_comfort_mode_fan_thresholds(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": COMFORT_MODE_NIGHT,
                        "sensor.office_average_temperature": "12.0",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                    }
                ),
                base_attr_map("16.0"),
            )
        )
        demand = EquipmentDemand(
            heat_requested=True,
            requested_by_zones=("office",),
            max_temperature_deficit=4.1,
        )

        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
        )

        self.assertEqual(plan.fan_mode, "low")

    def test_dispatch_plan_uses_start_fan_threshold_when_comfort_mode_changes(self):
        snapshot = build_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                        GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "0",
                        "sensor.office_average_temperature": "17.0",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                    }
                ),
                base_attr_map("18.0"),
            )
        )
        demand = EquipmentDemand(
            heat_requested=True,
            requested_by_zones=("office",),
            max_temperature_deficit=1.6,
        )

        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            comfort_mode_changed=True,
        )

        self.assertEqual(plan.fan_mode, "medium")

    def test_dispatch_plan_powerday_without_free_power_uses_default_fan_thresholds(self):
        snapshot = build_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                        GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "1",
                        "sensor.office_average_temperature": "17.0",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                    }
                ),
                base_attr_map("18.0"),
            )
        )
        demand = EquipmentDemand(
            heat_requested=True,
            requested_by_zones=("office",),
            max_temperature_deficit=2.6,
        )

        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
        )

        self.assertEqual(plan.fan_mode, "low")

    def test_dispatch_plan_powerday_heat_sink_uses_aggressive_fan_thresholds(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                        GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "1",
                        "sensor.office_average_temperature": "17.0",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                    }
                ),
                base_attr_map("18.0"),
            ),
            heat_sink_available=True,
        )
        demand = EquipmentDemand(
            heat_requested=True,
            requested_by_zones=("office",),
            max_temperature_deficit=2.6,
        )

        plan = build_dispatch_plan(
            snapshot,
            demand,
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
        )

        self.assertFalse(snapshot.free_power_available)
        self.assertTrue(snapshot.heat_sink_available)
        self.assertEqual(plan.fan_mode, "medium")

    def test_power_comfort_mode_uses_more_aggressive_fan_levels_when_power_is_free(self):
        power_mode = DEFAULT_SYSTEM_CONFIG.comfort_modes[COMFORT_MODE_POWER_DAY]

        self.assertEqual(
            resolve_fan_mode(
                "low",
                "heat",
                EquipmentDemand(heat_requested=True, max_temperature_deficit=2.6),
                comfort_mode=power_mode,
                free_power_available=True,
            ),
            "medium",
        )
        self.assertEqual(
            resolve_fan_mode(
                "low",
                "heat",
                EquipmentDemand(heat_requested=True, max_temperature_deficit=2.6),
                comfort_mode=power_mode,
                free_power_available=False,
            ),
            "low",
        )
        self.assertEqual(
            resolve_fan_mode(
                "low",
                "off",
                EquipmentDemand(heat_requested=True, max_temperature_deficit=1.6),
                comfort_mode=power_mode,
                free_power_available=True,
            ),
            "medium",
        )
        self.assertEqual(
            resolve_fan_mode(
                "low",
                "off",
                EquipmentDemand(heat_requested=True, max_temperature_deficit=1.6),
            ),
            "low",
        )


class PowerDayForecastTests(unittest.TestCase):
    def setUp(self):
        self.original_state_values = dict(temptamer_main.state._values)
        self.original_state_attrs = deepcopy(temptamer_main.state._attrs)
        self.original_service_call = temptamer_main.service.call
        self.original_runtime_state = deepcopy(temptamer_main.RUNTIME_STATE)
        temptamer_main.RUNTIME_STATE.clear()
        temptamer_main.RUNTIME_STATE.update(
            {
                "powerday_dry_active": False,
                "powerday_dry_started_at": None,
                "powerday_dry_completed": False,
                "powerday_dry_elapsed_seconds": 0.0,
                "powerday_dry_heat_transition_started_at": None,
                "powerday_dry_heat_transition_pending_off": False,
                "powerday_dry_eligible": False,
            }
        )

    def tearDown(self):
        temptamer_main.state._values = dict(self.original_state_values)
        temptamer_main.state._attrs = dict(self.original_state_attrs)
        temptamer_main.service.call = self.original_service_call
        temptamer_main.RUNTIME_STATE.clear()
        temptamer_main.RUNTIME_STATE.update(self.original_runtime_state)

    @staticmethod
    def _assessment(*, humidity=70.0, level=POWERDAY_HEATSOAK_SUPPRESSED):
        now = datetime(2026, 9, 12, 11, 0, tzinfo=timezone.utc)
        return PowerDayForecastAssessment(
            generated_at=now,
            level=level,
            daytime_peak_celsius=22.0,
            evening_minimum_celsius=18.0,
            evening_maximum_humidity=humidity,
            source="forecast",
            reason="test assessment",
        )

    @staticmethod
    def _snapshot(*, humidity=None, level=POWERDAY_HEATSOAK_SUPPRESSED, hvac_mode="Heat", temperature=21.0):
        overrides = {
            "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
            "input_select.temptamer_hvac_mode": hvac_mode,
            "input_select.temptamer_comfort_mode_downstairs": "Auto",
            GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "0",
            "sensor.office_average_temperature": str(temperature),
            "sensor.average_dining_zone_temp": str(temperature),
            "sensor.downstairs_zone_average_temperature": str(temperature),
            "sensor.average_bed1_2_zone_temp": str(temperature),
            "sensor.average_bed3_4_zone_temp": str(temperature),
        }
        if humidity is not None:
            overrides["sensor.climate_indoor_humidity"] = str(humidity)
        return build_behavior_snapshot(
            FakeReader(base_state_map(**overrides), base_attr_map(str(temperature))),
            free_power_heat_soak_level=level,
            now=datetime(2026, 9, 12, 11, 0, tzinfo=timezone.utc),
        )

    def test_forecast_assessment_classifies_full_reduced_and_suppressed(self):
        now = datetime(2026, 9, 12, 11, 0, tzinfo=timezone.utc)

        def assess(day_peak, evening_minimum):
            return assess_powerday_forecast(
                (
                    WeatherForecastPoint(now + timedelta(hours=2), day_peak, "sunny", 50.0),
                    WeatherForecastPoint(now + timedelta(hours=7), evening_minimum, "cloudy", 70.0),
                ),
                now=now,
            )

        self.assertEqual(assess(17.9, 18.0).level, POWERDAY_HEATSOAK_FULL)
        self.assertEqual(assess(22.0, 13.9).level, POWERDAY_HEATSOAK_FULL)
        self.assertEqual(assess(20.0, 16.0).level, POWERDAY_HEATSOAK_REDUCED)
        warm = assess(22.0, 17.0)
        self.assertEqual(warm.level, POWERDAY_HEATSOAK_SUPPRESSED)
        self.assertEqual(warm.evening_maximum_humidity, 70.0)

    def test_forecast_humidity_window_starts_at_fifteen_hundred(self):
        now = datetime(2026, 9, 12, 11, 0, tzinfo=timezone.utc)
        assessment = assess_powerday_forecast(
            (
                WeatherForecastPoint(now + timedelta(hours=2), 22.0, "sunny", 40.0),
                WeatherForecastPoint(now + timedelta(hours=5), 20.0, "cloudy", 75.0),
                WeatherForecastPoint(now + timedelta(hours=7), 18.0, "cloudy", 65.0),
            ),
            now=now,
        )

        self.assertEqual(assessment.level, POWERDAY_HEATSOAK_SUPPRESSED)
        self.assertEqual(assessment.evening_maximum_humidity, 75.0)

    def test_forecast_assessment_uses_local_time_windows(self):
        melbourne_time = timezone(timedelta(hours=10))
        now = datetime(2026, 9, 12, 10, 0, tzinfo=melbourne_time)
        assessment = assess_powerday_forecast(
            (
                WeatherForecastPoint(datetime(2026, 9, 12, 3, 0, tzinfo=timezone.utc), 22.0),
                WeatherForecastPoint(datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc), 18.0, humidity=70.0),
            ),
            now=now,
        )

        self.assertEqual(assessment.daytime_peak_celsius, 22.0)
        self.assertEqual(assessment.evening_minimum_celsius, 18.0)
        self.assertEqual(assessment.level, POWERDAY_HEATSOAK_SUPPRESSED)

    def test_forecast_assessment_requires_both_temperature_windows(self):
        now = datetime(2026, 9, 12, 11, 0, tzinfo=timezone.utc)
        assessment = assess_powerday_forecast(
            (WeatherForecastPoint(now + timedelta(hours=2), 22.0, "sunny", 70.0),),
            now=now,
        )

        self.assertEqual(assessment.level, POWERDAY_HEATSOAK_FULL)
        self.assertEqual(assessment.source, "unavailable")

    def test_reduced_heatsoak_keeps_base_schemes_and_applies_half_initial_boost(self):
        snapshot = self._snapshot(level=POWERDAY_HEATSOAK_REDUCED)
        expected_adjustments = {
            "office": 0.25,
            "dining": 0.5,
            "downstairs": 0.75,
            "bedroom_1_2": 0.125,
            "bedroom_3_4": 0.5,
        }
        expected_schemes = {
            "office": SCHEME_DAY_LIVING,
            "dining": SCHEME_DINING_BASIC,
            "downstairs": SCHEME_DOWNSTAIRS,
            "bedroom_1_2": SCHEME_BEDROOM,
            "bedroom_3_4": SCHEME_BEDROOM,
        }
        for zone_key, adjustment in expected_adjustments.items():
            base = TEST_SYSTEM_CONFIG.heat_control_schemes[expected_schemes[zone_key]]
            adjusted = snapshot.zones[zone_key].scheme
            self.assertEqual(adjusted.name, base.name)
            self.assertAlmostEqual(adjusted.enable_outside, base.enable_outside + adjustment)
            self.assertAlmostEqual(adjusted.continue_until, base.continue_until + adjustment)
            self.assertAlmostEqual(adjusted.ideal_target, base.ideal_target + adjustment)

    def test_reduced_heatsoak_boost_is_not_held_by_the_full_heatsoak_downstairs_gate(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(**{
                    "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                    "input_select.temptamer_comfort_mode_downstairs": "Auto",
                    GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "0",
                    "sensor.downstairs_zone_average_temperature": "19.0",
                })
            ),
            free_power_heat_soak_level=POWERDAY_HEATSOAK_REDUCED,
            now=datetime(2026, 9, 12, 13, 30, tzinfo=timezone.utc),
        )

        self.assertEqual(
            snapshot.zones["office"].scheme.continue_until,
            TEST_SYSTEM_CONFIG.heat_control_schemes[SCHEME_DAY_LIVING].continue_until + 0.25,
        )
        self.assertEqual(
            snapshot.zones["dining"].scheme.continue_until,
            TEST_SYSTEM_CONFIG.heat_control_schemes[SCHEME_DINING_BASIC].continue_until + 0.5,
        )

    def test_suppressed_heatsoak_removes_only_zero_price_adjustments(self):
        suppressed = self._snapshot(level=POWERDAY_HEATSOAK_SUPPRESSED)
        battery = build_behavior_snapshot(
            FakeReader(
                base_state_map(**{
                    "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                    "input_select.temptamer_comfort_mode_downstairs": "Auto",
                    GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "0",
                    "sensor.downstairs_zone_average_temperature": "21.0",
                }),
                base_attr_map("21.0"),
            ),
            battery_free_power_boost_available=True,
            free_power_heat_soak_level=POWERDAY_HEATSOAK_SUPPRESSED,
            now=datetime(2026, 9, 12, 11, 0, tzinfo=timezone.utc),
        )
        export = build_behavior_snapshot(
            FakeReader(
                base_state_map(**{
                    "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                    "input_select.temptamer_comfort_mode_downstairs": "Auto",
                    GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "1",
                    "sensor.downstairs_zone_average_temperature": "21.0",
                }),
                base_attr_map("21.0"),
            ),
            heat_sink_available=True,
            free_power_heat_soak_level=POWERDAY_HEATSOAK_SUPPRESSED,
            now=datetime(2026, 9, 12, 11, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(suppressed.zones["dining"].scheme.name, SCHEME_DINING_BASIC)
        self.assertEqual(
            suppressed.zones["downstairs"].scheme,
            TEST_SYSTEM_CONFIG.heat_control_schemes[SCHEME_DOWNSTAIRS],
        )
        self.assertEqual(
            battery.zones["downstairs"].scheme.continue_until,
            TEST_SYSTEM_CONFIG.heat_control_schemes[SCHEME_DOWNSTAIRS].continue_until + 1.5,
        )
        self.assertEqual(export.zones["dining"].scheme.name, SCHEME_DAY_LIVING)
        self.assertEqual(
            export.zones["downstairs"].scheme.continue_until,
            TEST_SYSTEM_CONFIG.heat_control_schemes[SCHEME_DOWNSTAIRS].continue_until + 1.5,
        )

    def test_reduced_and_suppressed_heatsoak_disable_full_only_supplements(self):
        demand = EquipmentDemand(
            heat_requested=True,
            requested_by_zones=("office",),
            max_temperature_deficit=2.6,
        )
        for level in (POWERDAY_HEATSOAK_REDUCED, POWERDAY_HEATSOAK_SUPPRESSED):
            with self.subTest(level=level):
                snapshot = self._snapshot(level=level, temperature=20.0)
                plan = build_dispatch_plan(
                    snapshot,
                    demand,
                    ("office", "downstairs"),
                    current_hvac_mode=HVAC_HEAT,
                    current_fan_mode="low",
                    target_temp_step=0.5,
                )
                plan_without_free_price = build_dispatch_plan(
                    replace(snapshot, free_power_available=False),
                    demand,
                    ("office", "downstairs"),
                    current_hvac_mode=HVAC_HEAT,
                    current_fan_mode="low",
                    target_temp_step=0.5,
                )

                self.assertEqual(plan.setpoint, plan_without_free_price.setpoint)
                self.assertEqual(plan.fan_mode, "low")
                self.assertEqual(
                    temptamer_main._powerday_downstairs_free_power_fan_boost(
                        snapshot,
                        demand,
                        ("office", "downstairs"),
                        HVAC_HEAT,
                    ),
                    0,
                )
                eligible, reason = temptamer_main._powerday_downstairs_priority_base_eligibility(
                    snapshot,
                    HVAC_HEAT,
                )
                self.assertFalse(eligible)
                self.assertIn(level, reason)

    def test_dehumidification_uses_strict_indoor_and_forecast_humidity_thresholds(self):
        snapshot = self._snapshot()
        cases = (
            (45.0, 60.1, False),
            (45.1, 60.0, False),
            (45.1, 60.1, True),
            (50.0, None, False),
            (50.1, None, True),
            (-0.1, 70.0, False),
            (100.1, 70.0, False),
        )
        for indoor, forecast, expected in cases:
            with self.subTest(indoor=indoor, forecast=forecast):
                decision = resolve_powerday_dehumidification(
                    snapshot,
                    self._assessment(humidity=forecast),
                    indoor_humidity=indoor,
                    supported_hvac_modes=("off", "heat", "dry"),
                    has_thermal_demand=False,
                    heat_forecast_safe=True,
                    cool_forecast_safe=True,
                    cycle_completed=False,
                    currently_active=False,
                )
                self.assertEqual(decision.eligible, expected)

    def test_dehumidification_respects_cool_forecast_and_temperature_guards(self):
        cool_snapshot = self._snapshot(hvac_mode="Cool")
        blocked_forecast = resolve_powerday_dehumidification(
            cool_snapshot,
            self._assessment(),
            indoor_humidity=51.0,
            supported_hvac_modes=("cool", "dry"),
            has_thermal_demand=False,
            heat_forecast_safe=True,
            cool_forecast_safe=False,
            cycle_completed=False,
            currently_active=False,
        )
        cold_snapshot = self._snapshot(temperature=19.9)
        blocked_temperature = resolve_powerday_dehumidification(
            cold_snapshot,
            self._assessment(),
            indoor_humidity=51.0,
            supported_hvac_modes=("heat", "dry"),
            has_thermal_demand=False,
            heat_forecast_safe=True,
            cool_forecast_safe=True,
            cycle_completed=False,
            currently_active=False,
        )

        self.assertFalse(blocked_forecast.eligible)
        self.assertIn("cooling demand", blocked_forecast.reason)
        self.assertFalse(blocked_temperature.eligible)
        self.assertIn("start floor", blocked_temperature.reason)

    def test_dehumidification_heatcool_requires_both_forecasts_to_be_safe(self):
        snapshot = self._snapshot(hvac_mode="HeatCool")
        for heat_safe, cool_safe, expected in ((True, True, True), (False, True, False), (True, False, False)):
            with self.subTest(heat_safe=heat_safe, cool_safe=cool_safe):
                decision = resolve_powerday_dehumidification(
                    snapshot,
                    self._assessment(),
                    indoor_humidity=51.0,
                    supported_hvac_modes=("heat_cool", "dry"),
                    has_thermal_demand=False,
                    heat_forecast_safe=heat_safe,
                    cool_forecast_safe=cool_safe,
                    cycle_completed=False,
                    currently_active=False,
                )
                self.assertEqual(decision.eligible, expected)

    def test_dehumidification_requires_all_non_humidity_eligibility_inputs(self):
        snapshot = self._snapshot()
        cases = (
            (replace(snapshot, comfort_mode="Day"), ("heat", "dry"), False, False, "not PowerDay"),
            (replace(snapshot, free_power_available=False), ("heat", "dry"), False, False, "not available"),
            (replace(snapshot, free_power_heat_soak_level=POWERDAY_HEATSOAK_REDUCED), ("heat", "dry"), False, False, "reduced"),
            (snapshot, ("heat",), False, False, "does not advertise"),
            (snapshot, ("heat", "dry"), True, False, "takes priority"),
            (snapshot, ("heat", "dry"), False, True, "already complete"),
            (replace(snapshot, selected_hvac_mode="Off"), ("heat", "dry"), False, False, "does not allow"),
            (replace(snapshot, selected_hvac_mode="Manual"), ("heat", "dry"), False, False, "does not allow"),
        )
        for candidate, supported_modes, thermal_demand, completed, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                decision = resolve_powerday_dehumidification(
                    candidate,
                    self._assessment(),
                    indoor_humidity=51.0,
                    supported_hvac_modes=supported_modes,
                    has_thermal_demand=thermal_demand,
                    heat_forecast_safe=True,
                    cool_forecast_safe=True,
                    cycle_completed=completed,
                    currently_active=False,
                )
                self.assertFalse(decision.eligible)
                self.assertIn(expected_reason, decision.reason)

    def test_active_dry_rechecks_humidity_and_uses_nineteen_degree_abort_floor(self):
        warm_snapshot = self._snapshot(temperature=19.1)
        cold_snapshot = self._snapshot(temperature=19.0)
        lost_humidity = resolve_powerday_dehumidification(
            warm_snapshot,
            self._assessment(),
            indoor_humidity=45.0,
            supported_hvac_modes=("heat", "dry"),
            has_thermal_demand=False,
            heat_forecast_safe=True,
            cool_forecast_safe=True,
            cycle_completed=False,
            currently_active=True,
        )
        cold_abort = resolve_powerday_dehumidification(
            cold_snapshot,
            self._assessment(),
            indoor_humidity=51.0,
            supported_hvac_modes=("heat", "dry"),
            has_thermal_demand=False,
            heat_forecast_safe=True,
            cool_forecast_safe=True,
            cycle_completed=False,
            currently_active=True,
        )

        self.assertFalse(lost_humidity.eligible)
        self.assertIn("humidity gate", lost_humidity.reason)
        self.assertFalse(cold_abort.eligible)
        self.assertIn("abort floor", cold_abort.reason)

    def test_dry_zone_actions_open_every_enabled_zone_and_close_explicit_off(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(**{
                    "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                    "input_select.temptamer_comfort_mode_office": "Off",
                    "input_select.temptamer_comfort_mode_downstairs": "Auto",
                    GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "0",
                    "switch.wt32_hpctrl_e8dbd0_office": "on",
                })
            ),
            free_power_heat_soak_level=POWERDAY_HEATSOAK_SUPPRESSED,
        )

        actions, predicted = resolve_dry_zone_actions(snapshot)

        self.assertNotIn("office", predicted)
        self.assertEqual(set(predicted), {"dining", "downstairs", "bedroom_1_2", "bedroom_3_4"})
        self.assertTrue(any(action.zone_key == "office" and not action.turn_on for action in actions))
        self.assertTrue(all(action.safety_required for action in actions if action.turn_on))
        first_closure = next(index for index, action in enumerate(actions) if not action.turn_on)
        self.assertTrue(all(action.turn_on for action in actions[:first_closure]))

    def test_dry_dispatch_changes_only_hvac_mode(self):
        snapshot = self._snapshot()
        plan = build_dispatch_plan(
            snapshot,
            EquipmentDemand(dry_requested=True, reason="humidity gate"),
            tuple(snapshot.zones),
            current_hvac_mode="off",
            current_fan_mode="Level 2",
            current_setpoint=22.0,
        )
        controller = Mock()

        apply_dispatch_plan(
            controller,
            plan,
            config=TEST_SYSTEM_CONFIG,
            current_hvac_mode="off",
            current_fan_mode="Level 2",
            current_setpoint=22.0,
        )

        self.assertEqual(plan.hvac_mode, HVAC_DRY)
        self.assertIsNone(plan.fan_mode)
        self.assertIsNone(plan.setpoint)
        controller.call_service.assert_called_once_with(
            "climate", "set_hvac_mode", entity_id=TEST_CLIMATE_ENTITY, hvac_mode=HVAC_DRY
        )

    def test_control_pass_suppresses_warm_day_heatsoak_and_dispatches_dry(self):
        now = datetime(2026, 9, 12, 11, 0, tzinfo=timezone.utc)
        forecast_response = {
            IDLE_DEMAND_FORECAST_WEATHER_ENTITY: {
                "forecast": [
                    {"datetime": (now + timedelta(hours=2)).isoformat(), "temperature": 22.0},
                    {
                        "datetime": (now + timedelta(hours=7)).isoformat(),
                        "temperature": 18.0,
                        "humidity": 70.0,
                    },
                ]
            }
        }

        def service_call(domain, service_name, **kwargs):
            if domain == "weather" and service_name == "get_forecasts":
                return forecast_response
            return None

        temptamer_main.state._values.clear()
        temptamer_main.state._attrs.clear()
        temptamer_main.state._values.update(
            base_state_map(**{
                "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                "input_select.temptamer_hvac_mode": "Heat",
                "input_select.temptamer_comfort_mode_downstairs": "Auto",
                GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "0",
                "sensor.climate_indoor_humidity": "51.0",
                "sensor.home_temperature": "23.0",
                "sensor.office_average_temperature": "23.0",
                "sensor.average_dining_zone_temp": "23.0",
                "sensor.downstairs_zone_average_temperature": "23.0",
                "sensor.average_bed1_2_zone_temp": "23.0",
                "sensor.average_bed3_4_zone_temp": "23.0",
                TEST_CLIMATE_ENTITY: "off",
            })
        )
        temptamer_main.state._attrs[TEST_CLIMATE_ENTITY] = {
            "current_temperature": 23.0,
            "temperature": 22.0,
            "fan_mode": "Level 2",
            "fan_modes": ["Level 1", "Level 2", "Level 3"],
            "hvac_modes": ["off", "heat", "cool", "dry"],
        }
        temptamer_main.RUNTIME_STATE.clear()
        temptamer_main.RUNTIME_STATE.update(deepcopy(self.original_runtime_state))
        temptamer_main.RUNTIME_STATE["last_successful_control_pass"] = now - timedelta(minutes=1)
        temptamer_main.RUNTIME_STATE["powerday_dry_restore_checked"] = True
        service_mock = Mock(side_effect=service_call)
        temptamer_main.service.call = service_mock

        with (
            patch.object(temptamer_main, "_system_now", return_value=now),
            patch.object(temptamer_main, "_persist_powerday_dry_cycle_state"),
            patch.object(temptamer_main, "_restore_heat_demand_fan_boost_state"),
            patch.object(temptamer_main, "_persist_heat_demand_fan_boost_state"),
        ):
            temptamer_main.run_control_pass(reason="forecast-aware dry integration test")

        self.assertEqual(
            temptamer_main.RUNTIME_STATE["powerday_forecast_assessment"].level,
            POWERDAY_HEATSOAK_SUPPRESSED,
        )
        self.assertTrue(temptamer_main.RUNTIME_STATE["powerday_dry_active"])
        self.assertIn(
            call(
                "climate",
                "set_hvac_mode",
                blocking=True,
                entity_id=TEST_CLIMATE_ENTITY,
                hvac_mode=HVAC_DRY,
            ),
            service_mock.call_args_list,
        )
        climate_commands = [
            command
            for command in service_mock.call_args_list
            if command.args and command.args[0] == "climate"
        ]
        self.assertFalse(any(command.args[1] == "set_fan_mode" for command in climate_commands))
        self.assertFalse(any(command.args[1] == "set_temperature" for command in climate_commands))

    def test_heat_to_dry_waits_five_minutes_and_dry_stops_at_maximum_runtime(self):
        now = datetime(2026, 9, 12, 11, 0, tzinfo=timezone.utc)
        snapshot = self._snapshot(humidity=51.0, temperature=23.0)
        controller = FakeReader(
            base_state_map(**{"sensor.climate_indoor_humidity": "51.0"}),
            base_attr_map("21.0"),
        )
        with (
            patch.object(temptamer_main, "_forecast_safe_for_operation", return_value=True),
            patch.object(temptamer_main, "_persist_powerday_dry_cycle_state"),
        ):
            requested, turn_off, _decision = temptamer_main._resolve_powerday_dry_request(
                controller,
                snapshot,
                EquipmentDemand(reason="idle"),
                self._assessment(),
                weather_points=(),
                supported_hvac_modes=("heat", "dry"),
                current_hvac_mode="heat",
                now=now,
            )
            self.assertFalse(requested)
            self.assertTrue(turn_off)

            requested, turn_off, _decision = temptamer_main._resolve_powerday_dry_request(
                controller,
                snapshot,
                EquipmentDemand(reason="idle"),
                self._assessment(),
                weather_points=(),
                supported_hvac_modes=("heat", "dry"),
                current_hvac_mode="off",
                now=now + timedelta(minutes=1),
            )
            self.assertFalse(requested)
            self.assertTrue(turn_off)

            dry_started_at = now + timedelta(minutes=1, seconds=POWERDAY_DRY_HEAT_TRANSITION_SECONDS)
            requested, turn_off, _decision = temptamer_main._resolve_powerday_dry_request(
                controller,
                snapshot,
                EquipmentDemand(reason="idle"),
                self._assessment(),
                weather_points=(),
                supported_hvac_modes=("heat", "dry"),
                current_hvac_mode="off",
                now=dry_started_at,
            )
            self.assertTrue(requested)
            self.assertFalse(turn_off)

            requested, _turn_off, decision = temptamer_main._resolve_powerday_dry_request(
                controller,
                snapshot,
                EquipmentDemand(reason="idle"),
                self._assessment(),
                weather_points=(),
                supported_hvac_modes=("heat", "dry"),
                current_hvac_mode="dry",
                now=dry_started_at + timedelta(seconds=POWERDAY_DRY_MAX_SECONDS),
            )
            self.assertFalse(requested)
            self.assertTrue(temptamer_main.RUNTIME_STATE["powerday_dry_completed"])
            self.assertEqual(
                temptamer_main.RUNTIME_STATE["powerday_dry_started_at"],
                dry_started_at,
            )
            self.assertEqual(
                temptamer_main.RUNTIME_STATE["powerday_dry_completed_at"],
                dry_started_at + timedelta(seconds=POWERDAY_DRY_MAX_SECONDS),
            )
            self.assertIn("maximum dry runtime", decision.reason)

    def test_restored_dry_cycle_fails_safe_for_missing_start_or_observed_heat(self):
        now = datetime(2026, 9, 12, 11, 0, tzinfo=timezone.utc)
        snapshot = self._snapshot(humidity=51.0, temperature=23.0)
        controller = FakeReader(
            base_state_map(**{"sensor.climate_indoor_humidity": "51.0"}),
            base_attr_map("21.0"),
        )
        with (
            patch.object(temptamer_main, "_forecast_safe_for_operation", return_value=True),
            patch.object(temptamer_main, "_persist_powerday_dry_cycle_state"),
        ):
            temptamer_main.RUNTIME_STATE["powerday_dry_active"] = True
            requested, _turn_off, missing_start = temptamer_main._resolve_powerday_dry_request(
                controller,
                snapshot,
                EquipmentDemand(reason="idle"),
                self._assessment(),
                weather_points=(),
                supported_hvac_modes=("heat", "dry"),
                current_hvac_mode=HVAC_DRY,
                now=now,
            )
            self.assertFalse(requested)
            self.assertIn("no valid start time", missing_start.reason)
            self.assertTrue(temptamer_main.RUNTIME_STATE["powerday_dry_completed"])

            temptamer_main.RUNTIME_STATE["powerday_dry_active"] = True
            temptamer_main.RUNTIME_STATE["powerday_dry_started_at"] = now
            temptamer_main.RUNTIME_STATE["powerday_dry_completed"] = False
            requested, _turn_off, observed_heat = temptamer_main._resolve_powerday_dry_request(
                controller,
                snapshot,
                EquipmentDemand(reason="idle"),
                self._assessment(),
                weather_points=(),
                supported_hvac_modes=("heat", "dry"),
                current_hvac_mode=HVAC_HEAT,
                now=now + timedelta(minutes=1),
            )

        self.assertFalse(requested)
        self.assertIn("entered heat", observed_heat.reason)
        self.assertTrue(temptamer_main.RUNTIME_STATE["powerday_dry_completed"])
        self.assertTrue(temptamer_main.RUNTIME_STATE["powerday_dry_heat_transition_pending_off"])

    def test_dry_to_heat_waits_five_minutes_but_dry_to_cool_is_direct(self):
        now = datetime(2026, 9, 12, 11, 0, tzinfo=timezone.utc)
        heat_plan = DispatchPlan(turn_off=False, hvac_mode=HVAC_HEAT, open_zones=("office",))
        cool_plan = DispatchPlan(turn_off=False, hvac_mode=HVAC_COOL, open_zones=("office",))
        with patch.object(temptamer_main, "_persist_powerday_dry_cycle_state"):
            initial = temptamer_main._apply_dry_to_heat_transition_hold(
                heat_plan,
                current_hvac_mode=HVAC_DRY,
                now=now,
            )
            waiting = temptamer_main._apply_dry_to_heat_transition_hold(
                heat_plan,
                current_hvac_mode="off",
                now=now + timedelta(minutes=1),
            )
            still_waiting = temptamer_main._apply_dry_to_heat_transition_hold(
                heat_plan,
                current_hvac_mode="off",
                now=now + timedelta(minutes=1, seconds=POWERDAY_DRY_HEAT_TRANSITION_SECONDS - 1),
            )
            released = temptamer_main._apply_dry_to_heat_transition_hold(
                heat_plan,
                current_hvac_mode="off",
                now=now + timedelta(minutes=1, seconds=POWERDAY_DRY_HEAT_TRANSITION_SECONDS),
            )
            direct = temptamer_main._apply_dry_to_heat_transition_hold(
                cool_plan,
                current_hvac_mode=HVAC_DRY,
                now=now,
            )

        self.assertTrue(initial.turn_off)
        self.assertTrue(waiting.turn_off)
        self.assertTrue(still_waiting.turn_off)
        self.assertIs(released, heat_plan)
        self.assertIs(direct, cool_plan)

    def test_recent_ordinary_heat_shutdown_also_holds_before_dry(self):
        now = datetime(2026, 9, 12, 11, 0, tzinfo=timezone.utc)
        snapshot = self._snapshot(humidity=51.0, temperature=23.0)
        controller = FakeReader(
            base_state_map(**{"sensor.climate_indoor_humidity": "51.0"}),
            base_attr_map("21.0"),
        )
        temptamer_main.RUNTIME_STATE["last_active_hvac_mode"] = HVAC_HEAT
        temptamer_main.RUNTIME_STATE["hvac_off_started_at"] = now - timedelta(
            seconds=POWERDAY_DRY_HEAT_TRANSITION_SECONDS - 1
        )
        with (
            patch.object(temptamer_main, "_forecast_safe_for_operation", return_value=True),
            patch.object(temptamer_main, "_persist_powerday_dry_cycle_state"),
        ):
            requested, turn_off, decision = temptamer_main._resolve_powerday_dry_request(
                controller,
                snapshot,
                EquipmentDemand(reason="idle"),
                self._assessment(),
                weather_points=(),
                supported_hvac_modes=("heat", "dry"),
                current_hvac_mode="off",
                now=now,
            )

        self.assertFalse(requested)
        self.assertTrue(turn_off)
        self.assertIn("reversing hold", temptamer_main.RUNTIME_STATE["powerday_dry_reason"])
        self.assertTrue(decision.eligible)

    def test_dry_cycle_state_restores_and_price_transition_resets_it(self):
        started_at = datetime(2026, 9, 12, 11, 5, tzinfo=timezone.utc)
        payload = {
            "free_power_period_active": True,
            "dry_active": False,
            "dry_started_at": started_at.isoformat(),
            "dry_completed": True,
            "dry_completed_at": (started_at + timedelta(minutes=30)).isoformat(),
            "heat_transition_started_at": None,
            "heat_transition_pending_off": False,
        }
        temptamer_main.RUNTIME_STATE["powerday_dry_restore_checked"] = False
        with (
            patch.object(temptamer_main, "_read_powerday_dry_cycle_state", return_value=(payload, None)),
            patch.object(temptamer_main, "_persist_powerday_dry_cycle_state") as persist,
        ):
            temptamer_main._restore_powerday_dry_cycle_state()
            self.assertTrue(temptamer_main.RUNTIME_STATE["powerday_dry_completed"])
            self.assertEqual(temptamer_main.RUNTIME_STATE["powerday_dry_started_at"], started_at)
            self.assertEqual(
                temptamer_main.RUNTIME_STATE["powerday_dry_completed_at"],
                started_at + timedelta(minutes=30),
            )
            temptamer_main._sync_powerday_free_power_period(False)

        self.assertFalse(temptamer_main.RUNTIME_STATE["powerday_dry_completed"])
        self.assertFalse(temptamer_main.RUNTIME_STATE["powerday_free_power_period_active"])
        persist.assert_called_once()


class IdleDemandForecastTests(unittest.TestCase):
    def setUp(self):
        self.original_runtime_state = deepcopy(temptamer_main.RUNTIME_STATE)
        temptamer_main.RUNTIME_STATE.clear()

    def tearDown(self):
        temptamer_main.RUNTIME_STATE.clear()
        temptamer_main.RUNTIME_STATE.update(self.original_runtime_state)

    @staticmethod
    def _snapshot(*, operation_mode, office_temperature, office_minimum=None):
        overrides = {
            "input_select.temptamer_comfort_mode": "Office",
            "input_select.temptamer_hvac_mode": "Heat" if operation_mode == HVAC_HEAT else "Cool",
            "input_select.temptamer_comfort_mode_dining": "Off",
            "input_select.temptamer_comfort_mode_downstairs": "Off",
            "input_select.temptamer_comfort_mode_bed12": "Off",
            "input_select.temptamer_comfort_mode_bed34": "Off",
            "sensor.office_average_temperature": str(office_temperature),
            "switch.wt32_hpctrl_e8dbd0_office": "on",
        }
        if office_minimum is not None:
            overrides["sensor.office_minimum_temperature"] = str(office_minimum)
        return build_behavior_snapshot(FakeReader(base_state_map(**overrides), base_attr_map("22.0")))

    def test_hourly_weather_forecast_parses_valid_entries_and_interpolates_from_current_temperature(self):
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        response = {
            IDLE_DEMAND_FORECAST_WEATHER_ENTITY: {
                "forecast": [
                    {
                        "datetime": "2026-08-24T13:00:00Z",
                        "temperature": 20.0,
                        "condition": "sunny",
                        "humidity": "67",
                    },
                    {"datetime": "invalid", "temperature": 30.0},
                    {"datetime": "2026-08-24T14:00:00Z", "temperature": "unknown", "condition": "cloudy"},
                ]
            }
        }

        points = parse_hourly_weather_forecast(response, IDLE_DEMAND_FORECAST_WEATHER_ENTITY)

        self.assertEqual(len(points), 2)
        self.assertEqual(points[0].condition, "sunny")
        self.assertEqual(points[0].humidity, 67.0)
        self.assertIsNone(points[1].temperature)
        self.assertEqual(
            forecast_temperature_at(
                points,
                now=now,
                at=now + timedelta(minutes=30),
                current_outdoor_temperature=10.0,
            ),
            15.0,
        )

    def test_weather_forecast_refresh_uses_cache_for_fifteen_minutes_then_refreshes(self):
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        response = {
            IDLE_DEMAND_FORECAST_WEATHER_ENTITY: {
                "forecast": [{"datetime": "2026-08-24T13:00:00Z", "temperature": 20.0, "condition": "sunny"}]
            }
        }
        controller = Mock()
        controller.call_service.return_value = response

        first = temptamer_main._refresh_idle_demand_weather_forecast(controller, now)
        cached = temptamer_main._refresh_idle_demand_weather_forecast(controller, now + timedelta(minutes=14, seconds=59))
        refreshed = temptamer_main._refresh_idle_demand_weather_forecast(controller, now + timedelta(minutes=15))

        self.assertEqual(first, cached)
        self.assertEqual(first, refreshed)
        self.assertEqual(controller.call_service.call_count, 2)
        self.assertEqual(
            controller.call_service.call_args_list[0],
            call(
                "weather",
                "get_forecasts",
                entity_id=IDLE_DEMAND_FORECAST_WEATHER_ENTITY,
                type="hourly",
                return_response=True,
            ),
        )

    def test_weather_forecast_refresh_retains_last_good_for_one_hour(self):
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        response = {
            IDLE_DEMAND_FORECAST_WEATHER_ENTITY: {
                "forecast": [{"datetime": "2026-08-24T13:00:00Z", "temperature": 20.0}]
            }
        }
        controller = Mock()
        controller.call_service.side_effect = (response, RuntimeError("temporary failure"), RuntimeError("still down"))

        fresh = temptamer_main._refresh_idle_demand_weather_forecast(controller, now)
        retained = temptamer_main._refresh_idle_demand_weather_forecast(controller, now + timedelta(minutes=15))
        expired = temptamer_main._refresh_idle_demand_weather_forecast(
            controller,
            now + timedelta(seconds=WEATHER_FORECAST_MAX_AGE_SECONDS + 1),
        )

        self.assertEqual(retained, fresh)
        self.assertEqual(expired, ())
        self.assertEqual(controller.call_service.call_count, 3)

    def test_runtime_idle_forecast_does_not_pass_a_pyscript_callback_to_the_forecast_module(self):
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        snapshot = self._snapshot(operation_mode=HVAC_HEAT, office_temperature=22.5)
        weather_points = (WeatherForecastPoint(now + timedelta(hours=1), 10.0, "sunny"),)
        expected = IdleDemandForecast(
            generated_at=now,
            horizon_seconds=30 * 60,
            earliest_demand_at=now + timedelta(minutes=30),
            earliest_zone_key="office",
            operation_mode=HVAC_HEAT,
            safe_to_turn_off=True,
            source="forecast",
            reason="office heat demand predicted in 30 minutes",
        )

        with (
            patch.object(temptamer_main, "_refresh_idle_demand_weather_forecast", return_value=weather_points),
            patch.object(temptamer_main, "resolve_outdoor_temperature", return_value=10.0),
            patch.object(temptamer_main, "forecast_idle_demand", return_value=expected) as forecast,
        ):
            result = temptamer_main._resolve_idle_demand_forecast(
                Mock(),
                snapshot,
                EquipmentDemand(reason="all zones satisfied"),
                current_hvac_mode=HVAC_HEAT,
                operation_mode=HVAC_HEAT,
                now=now,
            )

        self.assertIs(result, expected)
        self.assertNotIn("adjustment_provider", forecast.call_args.kwargs)
        self.assertEqual(forecast.call_args.kwargs["current_outdoor_temperature"], 10.0)

    def test_heating_forecast_is_safe_when_demand_is_first_predicted_at_thirty_minutes(self):
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        snapshot = self._snapshot(operation_mode=HVAC_HEAT, office_temperature=20.195)

        result = forecast_idle_demand(snapshot, operation_mode=HVAC_HEAT, now=now)

        self.assertTrue(result.safe_to_turn_off)
        self.assertEqual(result.earliest_zone_key, "office")
        self.assertEqual(result.earliest_demand_at, now + timedelta(minutes=30))
        self.assertEqual(result.source, "base_rate")

    def test_heating_forecast_retains_idle_when_demand_is_predicted_in_twenty_nine_minutes(self):
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        snapshot = self._snapshot(operation_mode=HVAC_HEAT, office_temperature=20.19)

        result = forecast_idle_demand(snapshot, operation_mode=HVAC_HEAT, now=now)

        self.assertFalse(result.safe_to_turn_off)
        self.assertEqual(result.earliest_zone_key, "office")
        self.assertEqual(result.earliest_demand_at, now + timedelta(minutes=29))

    def test_cooling_forecast_is_safe_when_demand_is_first_predicted_at_thirty_minutes(self):
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        snapshot = self._snapshot(operation_mode=HVAC_COOL, office_temperature=21.805)

        result = forecast_idle_demand(snapshot, operation_mode=HVAC_COOL, now=now)

        self.assertTrue(result.safe_to_turn_off)
        self.assertEqual(result.earliest_zone_key, "office")
        self.assertEqual(result.earliest_demand_at, now + timedelta(minutes=30))

    def test_cooling_forecast_retains_idle_when_demand_is_predicted_in_twenty_nine_minutes(self):
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        snapshot = self._snapshot(operation_mode=HVAC_COOL, office_temperature=21.81)

        result = forecast_idle_demand(snapshot, operation_mode=HVAC_COOL, now=now)

        self.assertFalse(result.safe_to_turn_off)
        self.assertEqual(result.earliest_zone_key, "office")
        self.assertEqual(result.earliest_demand_at, now + timedelta(minutes=29))

    def test_forecast_uses_secondary_minimum_temperature_trigger(self):
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        snapshot = self._snapshot(operation_mode=HVAC_HEAT, office_temperature=20.5, office_minimum=19.0)

        result = forecast_idle_demand(snapshot, operation_mode=HVAC_HEAT, now=now)

        self.assertFalse(result.safe_to_turn_off)
        self.assertEqual(result.earliest_zone_key, "office")
        self.assertEqual(result.earliest_demand_at, now + timedelta(minutes=1))

    def test_forecast_comfort_score_uses_hourly_condition_and_can_block_shutdown(self):
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        snapshot = self._snapshot(operation_mode=HVAC_HEAT, office_temperature=20.4)
        conditions: list[str | None] = []

        def adjustment_provider(_at, _outdoor_temperature, condition):
            conditions.append(condition)
            return {"office": -0.5}

        result = forecast_idle_demand(
            snapshot,
            operation_mode=HVAC_HEAT,
            now=now,
            weather_points=(WeatherForecastPoint(now + timedelta(hours=1), 10.0, "sunny"),),
            current_outdoor_temperature=10.0,
            adjustment_provider=adjustment_provider,
        )

        self.assertFalse(result.safe_to_turn_off)
        self.assertEqual(result.earliest_demand_at, now + timedelta(minutes=1))
        self.assertEqual(conditions, ["sunny"])

    def test_safe_forecast_keeps_the_unit_idle_until_the_minimum_duration(self):
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        snapshot = self._snapshot(operation_mode=HVAC_HEAT, office_temperature=22.5)
        forecast = IdleDemandForecast(
            generated_at=now,
            horizon_seconds=30 * 60,
            earliest_demand_at=now + timedelta(minutes=30),
            earliest_zone_key="office",
            operation_mode=HVAC_HEAT,
            safe_to_turn_off=True,
            source="forecast",
            reason="office heat demand predicted in 30 minutes",
        )

        plan = build_dispatch_plan(
            snapshot,
            EquipmentDemand(reason="all zones satisfied"),
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint=22.0,
            idle_started_at=now,
            idle_demand_forecast=forecast,
            now=now,
        )

        self.assertFalse(plan.turn_off)
        self.assertTrue(plan.idle)

    def test_safe_forecast_turns_off_after_minimum_idle_without_creating_restart_memory(self):
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        snapshot = self._snapshot(operation_mode=HVAC_HEAT, office_temperature=22.5)
        forecast = IdleDemandForecast(
            generated_at=now,
            horizon_seconds=30 * 60,
            earliest_demand_at=now + timedelta(minutes=30),
            earliest_zone_key="office",
            operation_mode=HVAC_HEAT,
            safe_to_turn_off=True,
            source="forecast",
            reason="office heat demand predicted in 30 minutes",
        )

        plan = build_dispatch_plan(
            snapshot,
            EquipmentDemand(reason="all zones satisfied"),
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            idle_started_at=now - timedelta(seconds=MIN_IDLE_SECONDS),
            idle_demand_forecast=forecast,
            now=now,
        )

        self.assertTrue(plan.turn_off)
        self.assertFalse(plan.idle)
        self.assertFalse(plan.idle_shutdown)
        self.assertIn("forecast idle shutdown", plan.reason)
        temptamer_main.RUNTIME_STATE["idle_heat_step"] = -4
        temptamer_main.RUNTIME_STATE["idle_heat_zone_key"] = "office"
        temptamer_main._update_idle_shutdown_runtime_state(plan, now, current_hvac_mode="heat")
        self.assertIsNone(temptamer_main.RUNTIME_STATE["idle_shutdown_at"])
        self.assertIsNone(temptamer_main.RUNTIME_STATE["idle_shutdown_heat_step"])
        self.assertIsNone(temptamer_main.RUNTIME_STATE["idle_shutdown_zone_key"])

    def test_unsafe_forecast_preserves_existing_idle_dispatch(self):
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        snapshot = self._snapshot(operation_mode=HVAC_HEAT, office_temperature=22.5)
        forecast = IdleDemandForecast(
            generated_at=now,
            horizon_seconds=30 * 60,
            earliest_demand_at=now + timedelta(minutes=29),
            earliest_zone_key="office",
            operation_mode=HVAC_HEAT,
            safe_to_turn_off=False,
            source="forecast",
            reason="office heat demand predicted in 29 minutes",
        )

        plan = build_dispatch_plan(
            snapshot,
            EquipmentDemand(reason="all zones satisfied"),
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint=22.0,
            idle_demand_forecast=forecast,
            now=now,
        )

        self.assertFalse(plan.turn_off)
        self.assertTrue(plan.idle)

    def test_active_demand_overrides_a_safe_idle_forecast(self):
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        snapshot = self._snapshot(operation_mode=HVAC_HEAT, office_temperature=18.0)
        forecast = IdleDemandForecast(
            generated_at=now,
            horizon_seconds=30 * 60,
            earliest_demand_at=None,
            earliest_zone_key=None,
            operation_mode=HVAC_HEAT,
            safe_to_turn_off=True,
            source="forecast",
            reason="no heat demand predicted within 30 minutes",
        )

        plan = build_dispatch_plan(
            snapshot,
            EquipmentDemand(heat_requested=True, requested_by_zones=("office",), reason="office is below enable threshold"),
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            idle_demand_forecast=forecast,
            now=now,
        )

        self.assertFalse(plan.turn_off)
        self.assertEqual(plan.hvac_mode, HVAC_HEAT)

    def test_powerday_boosted_heat_soak_preserves_existing_idle_dispatch(self):
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                        "input_select.temptamer_comfort_mode_dining": "Off",
                        "input_select.temptamer_comfort_mode_downstairs": "Off",
                        "input_select.temptamer_comfort_mode_bed12": "Off",
                        "input_select.temptamer_comfort_mode_bed34": "Off",
                        "sensor.office_average_temperature": "23.0",
                        "switch.wt32_hpctrl_e8dbd0_office": "on",
                    }
                ),
                base_attr_map("22.0"),
            ),
            heat_sink_available=True,
        )
        forecast = IdleDemandForecast(
            generated_at=now,
            horizon_seconds=30 * 60,
            earliest_demand_at=None,
            earliest_zone_key=None,
            operation_mode=HVAC_HEAT,
            safe_to_turn_off=True,
            source="forecast",
            reason="no heat demand predicted within 30 minutes",
        )

        plan = build_dispatch_plan(
            snapshot,
            EquipmentDemand(reason="all zones satisfied"),
            ("office",),
            current_hvac_mode="heat",
            current_fan_mode="low",
            current_setpoint=22.0,
            idle_demand_forecast=forecast,
            now=now,
        )

        self.assertFalse(plan.turn_off)
        self.assertTrue(plan.idle)

    def test_reduced_powerday_heatsoak_allows_safe_forecast_shutdown(self):
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(**{
                    "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                    GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "0",
                    "sensor.office_average_temperature": "23.0",
                    "switch.wt32_hpctrl_e8dbd0_office": "on",
                }),
                base_attr_map("22.0"),
            ),
            free_power_heat_soak_level=POWERDAY_HEATSOAK_REDUCED,
            now=now,
        )
        forecast = IdleDemandForecast(
            generated_at=now,
            horizon_seconds=30 * 60,
            earliest_demand_at=None,
            earliest_zone_key=None,
            operation_mode=HVAC_HEAT,
            safe_to_turn_off=True,
            source="forecast",
            reason="no heat demand predicted within 30 minutes",
        )

        plan = build_dispatch_plan(
            snapshot,
            EquipmentDemand(reason="all zones satisfied"),
            ("office",),
            current_hvac_mode=HVAC_HEAT,
            current_fan_mode="low",
            current_setpoint=22.0,
            idle_started_at=now - timedelta(seconds=MIN_IDLE_SECONDS),
            idle_demand_forecast=forecast,
            now=now,
        )

        self.assertTrue(plan.turn_off)
        self.assertIn("forecast idle shutdown", plan.reason)


if __name__ == "__main__":
    unittest.main()
