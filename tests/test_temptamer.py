from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock, call

from pyscript.apps.temptamer.comfort_modes import DefaultComfortMode, NightComfortMode, PowerComfortMode
from pyscript.apps.temptamer.config import (
    DEFAULT_SYSTEM_CONFIG,
    GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR,
    MODE_TRIGGER_ENTITIES,
)
from pyscript.apps.temptamer.constants import (
    COMFORT_MODE_NIGHT,
    COMFORT_MODE_POWER_DAY,
    FAN_LOW,
    HVAC_COOL,
    HVAC_FAN_ONLY,
    HVAC_HEAT,
    IDLE_HEAT_UNWIND_SECONDS,
    SCHEME_BATHROOM,
    SCHEME_BEDROOM,
    SCHEME_DAY_LIVING,
    SCHEME_DINING_BASIC,
    SCHEME_NIGHT,
    SCHEME_OFF,
)
from pyscript.apps.temptamer.demand_resolver import resolve_equipment_demand, resolve_operating_mode
from pyscript.apps.temptamer.heatpump_dispatcher import (
    build_dispatch_plan,
    normalize_cool_setpoint,
    normalize_heat_setpoint,
    normalize_setpoint,
    resolve_fan_mode,
    resolve_idle_started_at,
)
from pyscript.apps.temptamer.models import ControlScheme, EquipmentDemand, SystemConfig
import pyscript.apps.temptamer.main as temptamer_main
from pyscript.apps.temptamer.state_reader import build_snapshot
from pyscript.apps.temptamer.zone_control import describe_zone_predictions, resolve_zone_actions


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
        "input_select.temptamer_comfort_mode_bed12": "Auto",
        "input_select.temptamer_comfort_mode_bed34": "Auto",
        "sensor.home_temperature": "18.0",
        TEST_CLIMATE_ENTITY: "off",
        "sensor.office_average_temperature": "18.0",
        "sensor.average_dining_zone_temp": "18.0",
        "sensor.average_bed1_2_zone_temp": "18.0",
        "sensor.average_bed3_4_zone_temp": "18.0",
        "sensor.bathroom_motion_temperature": "18.0",
        GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "1",
        "switch.wt32_hpctrl_e8dbd0_office": "off",
        "switch.wt32_hpctrl_e8dbd0_dining": "off",
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


TEST_HEAT_CONTROL_SCHEMES = {
    SCHEME_OFF: ControlScheme(name=SCHEME_OFF, enable_outside=0.0, continue_until=0.0, ideal_target=0.0),
    SCHEME_NIGHT: DEFAULT_SYSTEM_CONFIG.heat_control_schemes[SCHEME_NIGHT],
    SCHEME_DAY_LIVING: ControlScheme(name=SCHEME_DAY_LIVING, enable_outside=20.0, continue_until=22.0, ideal_target=21.0),
    SCHEME_DINING_BASIC: DEFAULT_SYSTEM_CONFIG.heat_control_schemes[SCHEME_DINING_BASIC],
    SCHEME_BEDROOM: ControlScheme(name=SCHEME_BEDROOM, enable_outside=14.0, continue_until=16.0, ideal_target=14.0),
    SCHEME_BATHROOM: ControlScheme(name=SCHEME_BATHROOM, enable_outside=14.0, continue_until=16.0, ideal_target=14.0),
}

TEST_COOL_CONTROL_SCHEMES = {
    SCHEME_OFF: ControlScheme(name=SCHEME_OFF, enable_outside=0.0, continue_until=0.0, ideal_target=0.0),
    SCHEME_NIGHT: ControlScheme(name=SCHEME_NIGHT, enable_outside=17.0, continue_until=15.0, ideal_target=16.0),
    SCHEME_DAY_LIVING: ControlScheme(name=SCHEME_DAY_LIVING, enable_outside=22.0, continue_until=20.0, ideal_target=21.0),
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
)


def build_behavior_snapshot(reader, *, last_switch_changes=None, pending_switch_states=None, now=None):
    return build_snapshot(
        reader,
        config=TEST_SYSTEM_CONFIG,
        last_switch_changes=last_switch_changes,
        pending_switch_states=pending_switch_states,
        now=now,
    )


class TempTamerTests(unittest.TestCase):
    def setUp(self):
        self.original_state_values = dict(temptamer_main.state._values)
        self.original_state_attrs = dict(temptamer_main.state._attrs)
        self.original_runtime_state = dict(temptamer_main.RUNTIME_STATE)
        self.original_service_call = temptamer_main.service.call

    def tearDown(self):
        temptamer_main.state._values = dict(self.original_state_values)
        temptamer_main.state._attrs = dict(self.original_state_attrs)
        temptamer_main.RUNTIME_STATE.clear()
        temptamer_main.RUNTIME_STATE.update(self.original_runtime_state)
        temptamer_main.service.call = self.original_service_call

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
        self.assertEqual(snapshot.zones["bedroom_1_2"].scheme.name, "Night")

    def test_build_snapshot_auto_zone_override_falls_back_to_global_mode(self):
        snapshot = build_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "input_select.temptamer_comfort_mode_office": "Auto",
                    }
                ),
                base_attr_map("21.0", temperature="18.0"),
            )
        )

        self.assertEqual(snapshot.zones["office"].applied_comfort_mode, "Office")
        self.assertEqual(snapshot.zones["office"].scheme.name, "DayLiving")
        self.assertEqual(snapshot.zones["dining"].scheme.name, "DiningBasic")

    def test_default_comfort_modes_are_mode_objects(self):
        day_mode = DEFAULT_SYSTEM_CONFIG.comfort_modes["Day"]
        night_mode = DEFAULT_SYSTEM_CONFIG.comfort_modes[COMFORT_MODE_NIGHT]
        power_mode = DEFAULT_SYSTEM_CONFIG.comfort_modes[COMFORT_MODE_POWER_DAY]

        self.assertIsInstance(day_mode, DefaultComfortMode)
        self.assertIsInstance(night_mode, NightComfortMode)
        self.assertIsInstance(power_mode, PowerComfortMode)
        self.assertEqual(day_mode.fan_speed_level(2.6, 1, current_speed_level=1), 1)
        self.assertEqual(day_mode.fan_speed_level(2.6, 1, current_speed_level=1, starting=True), 2)
        self.assertEqual(night_mode.fan_speed_level(4.1, 1, current_speed_level=1), 1)
        self.assertEqual(night_mode.fan_speed_level(6.1, 1, current_speed_level=1), 2)
        self.assertEqual(power_mode.fan_speed_level(2.6, 1, current_speed_level=1), 1)
        self.assertEqual(power_mode.fan_speed_level(2.6, 1, current_speed_level=1, free_power_available=True), 2)
        self.assertIn(GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR, MODE_TRIGGER_ENTITIES)

    def test_powerday_uses_office_mapping_when_power_is_not_free(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                        GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "1",
                    }
                ),
                base_attr_map("21.0"),
            )
        )

        self.assertEqual(snapshot.comfort_mode, COMFORT_MODE_POWER_DAY)
        self.assertEqual(snapshot.zones["office"].scheme.name, SCHEME_DAY_LIVING)
        self.assertEqual(snapshot.zones["dining"].scheme.name, SCHEME_DINING_BASIC)
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
        self.assertEqual(
            snapshot.comfort_mode_behavior.fan_speed_level(
                2.6,
                1,
                current_speed_level=1,
                free_power_available=snapshot.free_power_available,
            ),
            1,
        )

    def test_powerday_heat_soaks_dining_and_bedrooms_when_power_is_free(self):
        power_mode = TEST_SYSTEM_CONFIG.comfort_modes[COMFORT_MODE_POWER_DAY]
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                        GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "0",
                    }
                ),
                base_attr_map("21.0"),
            ),
            now=datetime(2026, 7, 25, 12, 59, tzinfo=timezone.utc),
        )

        self.assertEqual(snapshot.comfort_mode, COMFORT_MODE_POWER_DAY)
        self.assertEqual(snapshot.zones["office"].scheme.name, SCHEME_DAY_LIVING)
        self.assertEqual(snapshot.zones["dining"].scheme.name, SCHEME_DAY_LIVING)
        self.assertEqual(snapshot.zones["bedroom_1_2"].scheme.name, SCHEME_DAY_LIVING)
        self.assertEqual(snapshot.zones["bedroom_3_4"].scheme.name, SCHEME_DAY_LIVING)
        base_day_living_scheme = TEST_SYSTEM_CONFIG.heat_control_schemes[SCHEME_DAY_LIVING]
        adjusted_continue_until = base_day_living_scheme.continue_until + power_mode.free_power_initial_suppliment
        for zone_key in ("office", "dining", "bedroom_1_2", "bedroom_3_4"):
            self.assertEqual(snapshot.zones[zone_key].scheme.continue_until, adjusted_continue_until)
            self.assertEqual(snapshot.zones[zone_key].scheme.enable_outside, adjusted_continue_until - 0.75)
            self.assertEqual(snapshot.zones[zone_key].scheme.ideal_target, adjusted_continue_until - 0.5)
        self.assertTrue(snapshot.free_power_available)
        self.assertEqual(
            snapshot.comfort_mode_behavior.fan_speed_level(
                2.6,
                1,
                current_speed_level=1,
                free_power_available=snapshot.free_power_available,
            ),
            2,
        )

    def test_powerday_uses_later_heat_supplement_after_1pm_when_power_is_free(self):
        power_mode = TEST_SYSTEM_CONFIG.comfort_modes[COMFORT_MODE_POWER_DAY]
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": COMFORT_MODE_POWER_DAY,
                        GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR: "0",
                    }
                ),
                base_attr_map("21.0"),
            ),
            now=datetime(2026, 7, 25, 13, 1, tzinfo=timezone.utc),
        )

        base_day_living_scheme = TEST_SYSTEM_CONFIG.heat_control_schemes[SCHEME_DAY_LIVING]
        adjusted_continue_until = base_day_living_scheme.continue_until + power_mode.free_power_later
        self.assertEqual(snapshot.zones["office"].scheme.continue_until, adjusted_continue_until)
        self.assertEqual(snapshot.zones["office"].scheme.enable_outside, adjusted_continue_until - 0.75)
        self.assertEqual(snapshot.zones["office"].scheme.ideal_target, adjusted_continue_until - 0.5)

    def test_unrecognized_zone_override_falls_back_to_global_mode(self):
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

        self.assertEqual(snapshot.zones["bedroom_3_4"].applied_comfort_mode, "Office")
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

        self.assertEqual(len(diagnostics), 4)
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
        self.assertEqual(plan.setpoint, 22)

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

    def test_comfort_mode_drop_enters_idle_with_midpoint_clamp(self):
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
        )

        self.assertTrue(plan.idle)
        self.assertEqual(plan.setpoint, 20)
        self.assertEqual(plan.idle_heat_step, 0)

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

    def test_heating_idle_stage_3_respects_minimum_heat_setpoint(self):
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

        self.assertTrue(plan.turn_off)
        self.assertFalse(plan.idle)
        self.assertTrue(plan.idle_shutdown)
        self.assertIsNone(plan.setpoint)
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

    def test_heating_idle_stage_6_turns_off_when_backoff_reaches_minimum_heat_setpoint(self):
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

        self.assertTrue(plan.turn_off)
        self.assertFalse(plan.idle)
        self.assertTrue(plan.idle_shutdown)
        self.assertEqual(plan.idle_heat_step, -11)

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
        self.assertEqual(plan.setpoint, 18)

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

    def test_run_control_pass_comfort_mode_change_clamps_initial_idle_setpoint(self):
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
                    "sensor.office_average_temperature": "18.0",
                    "sensor.average_dining_zone_temp": "18.5",
                    "sensor.average_bed1_2_zone_temp": "18.5",
                    "sensor.average_bed3_4_zone_temp": "18.5",
                    "switch.wt32_hpctrl_e8dbd0_office": "on",
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

        temptamer_main.run_control_pass(reason="mode selection changed", comfort_mode_changed=True)

        self.assertEqual(
            service_call.call_args_list,
            [
                call(
                    "climate",
                    "set_temperature",
                    blocking=True,
                    entity_id=TEST_CLIMATE_ENTITY,
                    temperature=20,
                )
            ],
        )
        self.assertEqual(temptamer_main.RUNTIME_STATE["idle_heat_step"], 0)

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
                    "sensor.office_average_temperature": "19.0",
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
                    temperature=20.5,
                )
            ],
        )
        self.assertEqual(temptamer_main.RUNTIME_STATE["idle_heat_step"], 0)

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

    def test_fan_speed_level_doubles_when_three_or_more_zones_are_open(self):
        supported_fan_modes = ("Level 1", "Level 2", "Level 3")

        self.assertEqual(
            DEFAULT_SYSTEM_CONFIG.comfort_modes["Day"].fan_speed_level(
                1.0,
                3,
                current_speed_level=1,
            ),
            2,
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
            "Level 3",
        )

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


if __name__ == "__main__":
    unittest.main()
