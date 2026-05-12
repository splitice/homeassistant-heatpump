from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock

from pyscript.apps.temptamer.config import DEFAULT_SYSTEM_CONFIG
from pyscript.apps.temptamer.constants import (
    HVAC_COOL,
    HVAC_HEAT,
    LOW_TO_MEDIUM_FAN_DIFFERENTIAL,
    SCHEME_BEDROOM,
    SCHEME_DAY_LIVING,
    SCHEME_DINING_BASIC,
    SCHEME_NIGHT,
    SCHEME_OFF,
)
from pyscript.apps.temptamer.demand_resolver import resolve_equipment_demand, resolve_operating_mode
from pyscript.apps.temptamer.heatpump_dispatcher import build_dispatch_plan, normalize_setpoint, resolve_fan_mode
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
        "switch.wt32_hpctrl_e8dbd0_office": "off",
        "switch.wt32_hpctrl_e8dbd0_dining": "off",
        "switch.wt32_hpctrl_e8dbd0_bed_12": "off",
        "switch.wt32_hpctrl_e8dbd0_bed_34": "off",
    }
    state_map.update(overrides)
    return state_map


def base_attr_map(current_temperature="19.0"):
    return {
        TEST_CLIMATE_ENTITY: {
            "current_temperature": current_temperature,
        }
    }


TEST_HEAT_CONTROL_SCHEMES = {
    SCHEME_OFF: ControlScheme(name=SCHEME_OFF, enable_outside=0.0, continue_until=0.0, ideal_target=0.0),
    SCHEME_NIGHT: ControlScheme(name=SCHEME_NIGHT, enable_outside=15.0, continue_until=17.0, ideal_target=16.0),
    SCHEME_DAY_LIVING: ControlScheme(name=SCHEME_DAY_LIVING, enable_outside=20.0, continue_until=22.0, ideal_target=21.0),
    SCHEME_DINING_BASIC: ControlScheme(name=SCHEME_DINING_BASIC, enable_outside=14.0, continue_until=17.0, ideal_target=15.0),
    SCHEME_BEDROOM: ControlScheme(name=SCHEME_BEDROOM, enable_outside=14.0, continue_until=16.0, ideal_target=14.0),
}

TEST_COOL_CONTROL_SCHEMES = {
    SCHEME_OFF: ControlScheme(name=SCHEME_OFF, enable_outside=0.0, continue_until=0.0, ideal_target=0.0),
    SCHEME_NIGHT: ControlScheme(name=SCHEME_NIGHT, enable_outside=17.0, continue_until=15.0, ideal_target=16.0),
    SCHEME_DAY_LIVING: ControlScheme(name=SCHEME_DAY_LIVING, enable_outside=22.0, continue_until=20.0, ideal_target=21.0),
    SCHEME_DINING_BASIC: ControlScheme(name=SCHEME_DINING_BASIC, enable_outside=16.0, continue_until=13.0, ideal_target=15.0),
    SCHEME_BEDROOM: ControlScheme(name=SCHEME_BEDROOM, enable_outside=16.0, continue_until=12.0, ideal_target=14.0),
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
                    "input_select.temptamer_comfort_mode_office": "Day",
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
        self.assertEqual(snapshot.zones["office"].applied_comfort_mode, "Day")
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
                base_attr_map("21.0"),
            )
        )

        self.assertEqual(snapshot.zones["office"].applied_comfort_mode, "Office")
        self.assertEqual(snapshot.zones["office"].scheme.name, "DayLiving")
        self.assertEqual(snapshot.zones["dining"].scheme.name, "DiningBasic")

    def test_build_snapshot_uses_climate_current_temperature_when_house_sensor_missing(self):
        snapshot = build_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Night",
                        "sensor.home_temperature": "unknown",
                        "sensor.office_average_temperature": "unavailable",
                        "sensor.average_dining_zone_temp": "17.0",
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
        self.assertEqual(plan.setpoint, 20)
        self.assertEqual(plan.fan_mode, "low")

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

        self.assertEqual(plan.setpoint, 20)
        self.assertTrue(
            any(
                "SETPOINT: inlet_temp=14.0 zone=office enable_outside=20.0" in message
                and "raw=20.0" in message
                and "normalized=20" in message
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

    def test_maintain_cooling_uses_continue_threshold_setpoint(self):
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
        )

        self.assertFalse(demand.cool_requested)
        self.assertTrue(demand.maintain_cool_mode)
        self.assertEqual(demand.requested_by_zones, ("office",))
        self.assertEqual(plan.setpoint, 20)

    def test_no_heat_demand_turns_system_off_once_all_zones_exceed_continue_threshold(self):
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
        )

        self.assertEqual(demand.reason, "no active heating demand")
        self.assertTrue(plan.turn_off)

    def test_no_heat_demand_holds_status_quo_in_neutral_band(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "18.0",
                        "sensor.office_average_temperature": "21.5",
                        "sensor.average_dining_zone_temp": "16.0",
                        "sensor.average_bed1_2_zone_temp": "15.0",
                        "sensor.average_bed3_4_zone_temp": "15.0",
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

        self.assertEqual(demand.reason, "no active heating demand")
        self.assertFalse(plan.turn_off)
        self.assertIsNone(plan.hvac_mode)

    def test_no_cool_demand_turns_system_off_once_all_zones_drop_below_continue_threshold(self):
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
                base_attr_map("21.0"),
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

        self.assertEqual(demand.reason, "no active cooling demand")
        self.assertTrue(plan.turn_off)

    def test_no_cool_demand_holds_status_quo_in_neutral_band(self):
        snapshot = build_behavior_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_hvac_mode": "Cool",
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "20.5",
                        "sensor.office_average_temperature": "20.5",
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

        self.assertEqual(demand.reason, "no active cooling demand")
        self.assertFalse(plan.turn_off)
        self.assertIsNone(plan.hvac_mode)

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
        self.assertEqual(plan.setpoint, 20)

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
                EquipmentDemand(heat_requested=True, max_temperature_deficit=LOW_TO_MEDIUM_FAN_DIFFERENTIAL),
            ),
            "medium",
        )
        self.assertEqual(
            resolve_fan_mode(
                "medium",
                "heat",
                EquipmentDemand(heat_requested=True, max_temperature_deficit=1.9),
            ),
            "low",
        )
        self.assertEqual(normalize_setpoint(25.1), 25)


if __name__ == "__main__":
    unittest.main()
