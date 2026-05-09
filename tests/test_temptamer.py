from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from pyscript.apps.temptamer.config import DEFAULT_SYSTEM_CONFIG
from pyscript.apps.temptamer.models import EquipmentDemand
from pyscript.apps.temptamer.demand_resolver import resolve_equipment_demand
from pyscript.apps.temptamer.heatpump_dispatcher import build_dispatch_plan, normalize_setpoint, resolve_fan_mode
from pyscript.apps.temptamer.state_reader import build_snapshot
from pyscript.apps.temptamer.zone_control import describe_zone_predictions, resolve_zone_actions


ENTITY_ID_ALIASES = {
    DEFAULT_SYSTEM_CONFIG.zones["office"].sensor_entity_id: "sensor.office_temperature",
    DEFAULT_SYSTEM_CONFIG.zones["dining"].sensor_entity_id: "sensor.dining_temperature",
    DEFAULT_SYSTEM_CONFIG.zones["bedroom_1_2"].sensor_entity_id: "sensor.bedroom_1_2_temperature",
    DEFAULT_SYSTEM_CONFIG.zones["bedroom_3_4"].sensor_entity_id: "sensor.bedroom_3_4_temperature",
    DEFAULT_SYSTEM_CONFIG.zones["office"].switch_entity_id: "switch.office_zone",
    DEFAULT_SYSTEM_CONFIG.zones["dining"].switch_entity_id: "switch.dining_zone",
    DEFAULT_SYSTEM_CONFIG.zones["bedroom_1_2"].switch_entity_id: "switch.bedroom_1_2_zone",
    DEFAULT_SYSTEM_CONFIG.zones["bedroom_3_4"].switch_entity_id: "switch.bedroom_3_4_zone",
}


class FakeReader:
    def __init__(self, state_map, attr_map=None):
        self.state_map = state_map
        self.attr_map = attr_map or {}

    def get_state(self, entity_id):
        if entity_id in self.state_map:
            return self.state_map.get(entity_id)
        return self.state_map.get(ENTITY_ID_ALIASES.get(entity_id))

    def get_attr(self, entity_id, attr_name):
        return self.attr_map.get(entity_id, {}).get(attr_name)


def make_demand(**overrides):
    return EquipmentDemand(**overrides)


class TempTamerTests(unittest.TestCase):
    def test_build_snapshot_uses_house_sensor_fallback(self):
        reader = FakeReader(
            {
                "input_select.temptamer_comfort_mode": "Night",
                "sensor.home_temperature": "18.5",
                "climate.wt32_hpctrl_e8dbd0_heatpump": "off",
                "sensor.office_temperature": "unavailable",
                "sensor.dining_temperature": "17.0",
                "sensor.bedroom_1_2_temperature": "16.5",
                "sensor.bedroom_3_4_temperature": "unknown",
                "switch.office_zone": "off",
                "switch.dining_zone": "on",
                "switch.bedroom_1_2_zone": "off",
                "switch.bedroom_3_4_zone": "on",
            },
            {
                "climate.wt32_hpctrl_e8dbd0_heatpump": {"current_temperature": "23.4"},
            },
        )

        snapshot = build_snapshot(reader)

        self.assertEqual(snapshot.zones["office"].current_temp, 18.5)
        self.assertEqual(snapshot.zones["bedroom_3_4"].current_temp, 18.5)
        self.assertEqual(snapshot.inlet_temp, 23.4)
        self.assertEqual(snapshot.zones["bedroom_1_2"].scheme.name, "Night")

    def test_build_snapshot_uses_climate_current_temperature_when_house_sensor_missing(self):
        snapshot = build_snapshot(
            FakeReader(
                {
                    "input_select.temptamer_comfort_mode": "Night",
                    "sensor.home_temperature": "unknown",
                    "climate.wt32_hpctrl_e8dbd0_heatpump": "off",
                    "sensor.office_temperature": "unavailable",
                    "sensor.dining_temperature": "17.0",
                    "sensor.bedroom_1_2_temperature": "16.5",
                    "sensor.bedroom_3_4_temperature": "unknown",
                    "switch.office_zone": "off",
                    "switch.dining_zone": "on",
                    "switch.bedroom_1_2_zone": "off",
                    "switch.bedroom_3_4_zone": "on",
                },
                {
                    "climate.wt32_hpctrl_e8dbd0_heatpump": {"current_temperature": "22.1"},
                },
            )
        )

        self.assertEqual(snapshot.inlet_temp, 22.1)
        self.assertEqual(snapshot.zones["office"].current_temp, 22.1)
        self.assertEqual(snapshot.zones["bedroom_3_4"].current_temp, 22.1)

    def test_zone_actions_respect_antiflap_but_allow_mode_change(self):
        snapshot = build_snapshot(
            FakeReader(
                {
                    "input_select.temptamer_comfort_mode": "Day",
                    "sensor.home_temperature": "18.0",
                    "climate.wt32_hpctrl_e8dbd0_heatpump": "off",
                    "sensor.office_temperature": "17.0",
                    "sensor.dining_temperature": "22.0",
                    "sensor.bedroom_1_2_temperature": "20.0",
                    "sensor.bedroom_3_4_temperature": "20.0",
                    "switch.office_zone": "off",
                    "switch.dining_zone": "on",
                    "switch.bedroom_1_2_zone": "off",
                    "switch.bedroom_3_4_zone": "off",
                },
                {
                    "climate.wt32_hpctrl_e8dbd0_heatpump": {"current_temperature": "19.0"},
                },
            ),
            last_switch_changes={
                "office": datetime(2026, 1, 1, 12, 0, 0),
                "dining": datetime(2026, 1, 1, 12, 0, 0),
            },
        )
        now = datetime(2026, 1, 1, 12, 2, 0)

        actions, predicted_open = resolve_zone_actions(snapshot, now, comfort_mode_changed=False)

        self.assertEqual(actions, [])
        self.assertEqual(predicted_open, ("dining",))

        actions, predicted_open = resolve_zone_actions(snapshot, now, comfort_mode_changed=True)

        self.assertCountEqual(
            [(action.zone_key, action.turn_on) for action in actions],
            [("dining", False), ("office", True)],
        )
        self.assertEqual(predicted_open, ("office",))

    def test_zone_actions_force_safety_open_with_realistic_temperatures(self):
        now = datetime(2026, 5, 7, 12, 1, 0, tzinfo=timezone.utc)
        snapshot = build_snapshot(
            FakeReader(
                {
                    "input_select.temptamer_comfort_mode": "Day",
                    "sensor.home_temperature": "18.0",
                    "climate.wt32_hpctrl_e8dbd0_heatpump": "off",
                    "sensor.office_temperature": "17.5",
                    "sensor.dining_temperature": "17.8",
                    "sensor.bedroom_1_2_temperature": "18.0",
                    "sensor.bedroom_3_4_temperature": "18.0",
                    "switch.office_zone": "off",
                    "switch.dining_zone": "off",
                    "switch.bedroom_1_2_zone": "off",
                    "switch.bedroom_3_4_zone": "off",
                },
                {
                    "climate.wt32_hpctrl_e8dbd0_heatpump": {"current_temperature": "18.5"},
                },
            ),
            last_switch_changes={
                "office": datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc),
                "dining": datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc),
            },
            now=now,
        )

        actions, predicted_open = resolve_zone_actions(snapshot, now, comfort_mode_changed=False)

        self.assertEqual(predicted_open, ("office",))
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].zone_key, "office")
        self.assertTrue(actions[0].turn_on)
        self.assertTrue(actions[0].safety_required)
        self.assertFalse(actions[0].discretionary)
        self.assertIn("overriding anti-flap delay", actions[0].reason)

    def test_zone_actions_keep_same_safety_zone_when_multiple_zones_need_heat(self):
        first_now = datetime(2026, 5, 7, 12, 3, 0, tzinfo=timezone.utc)
        first_snapshot = build_snapshot(
            FakeReader(
                {
                    "input_select.temptamer_comfort_mode": "Office",
                    "sensor.home_temperature": "18.0",
                    "climate.wt32_hpctrl_e8dbd0_heatpump": "off",
                    "sensor.office_temperature": "18.5",
                    "sensor.dining_temperature": "18.5",
                    "sensor.bedroom_1_2_temperature": "18.0",
                    "sensor.bedroom_3_4_temperature": "18.0",
                    "switch.office_zone": "off",
                    "switch.dining_zone": "off",
                    "switch.bedroom_1_2_zone": "off",
                    "switch.bedroom_3_4_zone": "off",
                },
                {
                    "climate.wt32_hpctrl_e8dbd0_heatpump": {"current_temperature": "18.5"},
                },
            ),
            last_switch_changes={
                "office": datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc),
                "dining": datetime(2026, 5, 7, 11, 59, 0, tzinfo=timezone.utc),
            },
            now=first_now,
        )

        first_actions, first_predicted_open = resolve_zone_actions(first_snapshot, first_now, comfort_mode_changed=False)

        self.assertEqual(first_predicted_open, ("office",))
        self.assertEqual(len(first_actions), 1)
        self.assertEqual(first_actions[0].zone_key, "office")
        self.assertTrue(first_actions[0].safety_required)

        second_now = datetime(2026, 5, 7, 12, 3, 30, tzinfo=timezone.utc)
        second_snapshot = build_snapshot(
            FakeReader(
                {
                    "input_select.temptamer_comfort_mode": "Office",
                    "sensor.home_temperature": "18.0",
                    "climate.wt32_hpctrl_e8dbd0_heatpump": "off",
                    "sensor.office_temperature": "18.5",
                    "sensor.dining_temperature": "18.5",
                    "sensor.bedroom_1_2_temperature": "18.0",
                    "sensor.bedroom_3_4_temperature": "18.0",
                    "switch.office_zone": "off",
                    "switch.dining_zone": "off",
                    "switch.bedroom_1_2_zone": "off",
                    "switch.bedroom_3_4_zone": "off",
                },
                {
                    "climate.wt32_hpctrl_e8dbd0_heatpump": {"current_temperature": "18.5"},
                },
            ),
            last_switch_changes={
                "office": first_now,
                "dining": datetime(2026, 5, 7, 11, 59, 0, tzinfo=timezone.utc),
            },
            now=second_now,
        )

        second_actions, second_predicted_open = resolve_zone_actions(second_snapshot, second_now, comfort_mode_changed=False)

        self.assertEqual(second_predicted_open, ("office",))
        self.assertEqual(len(second_actions), 1)
        self.assertEqual(second_actions[0].zone_key, "office")
        self.assertTrue(second_actions[0].safety_required)

    def test_zone_prediction_diagnostics_explain_anti_flap_decisions(self):
        now = datetime(2026, 5, 7, 12, 1, 0, tzinfo=timezone.utc)
        snapshot = build_snapshot(
            FakeReader(
                {
                    "input_select.temptamer_comfort_mode": "Day",
                    "sensor.home_temperature": "18.0",
                    "climate.wt32_hpctrl_e8dbd0_heatpump": "off",
                    "sensor.office_temperature": "17.5",
                    "sensor.dining_temperature": "17.8",
                    "sensor.bedroom_1_2_temperature": "18.0",
                    "sensor.bedroom_3_4_temperature": "18.0",
                    "switch.office_zone": "off",
                    "switch.dining_zone": "off",
                    "switch.bedroom_1_2_zone": "off",
                    "switch.bedroom_3_4_zone": "off",
                },
                {
                    "climate.wt32_hpctrl_e8dbd0_heatpump": {"current_temperature": "18.5"},
                },
            ),
            last_switch_changes={
                "office": datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc),
                "dining": datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc),
            },
            now=now,
        )

        _actions, predicted_open = resolve_zone_actions(snapshot, now, comfort_mode_changed=False)
        diagnostics = describe_zone_predictions(snapshot, now, predicted_open, comfort_mode_changed=False)

        self.assertEqual(len(diagnostics), 4)
        self.assertTrue(any("office:" in entry and "predicted=open" in entry for entry in diagnostics))
        self.assertTrue(any("dining:" in entry and "held closed by anti-flap delay" in entry for entry in diagnostics))

    def test_equipment_demand_and_dispatch_plan_choose_heat_setpoint(self):
        snapshot = build_snapshot(
            FakeReader(
                {
                    "input_select.temptamer_comfort_mode": "Office",
                    "sensor.home_temperature": "18.0",
                    "climate.wt32_hpctrl_e8dbd0_heatpump": "off",
                    "sensor.office_temperature": "17.0",
                    "sensor.dining_temperature": "21.0",
                    "sensor.bedroom_1_2_temperature": "19.5",
                    "sensor.bedroom_3_4_temperature": "19.5",
                    "switch.office_zone": "on",
                    "switch.dining_zone": "off",
                    "switch.bedroom_1_2_zone": "off",
                    "switch.bedroom_3_4_zone": "off",
                },
                {
                    "climate.wt32_hpctrl_e8dbd0_heatpump": {"current_temperature": "16.4"},
                },
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",))
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
        self.assertEqual(plan.fan_mode, "low")

    def test_dispatch_plan_logs_setpoint_calculation(self):
        snapshot = build_snapshot(
            FakeReader(
                {
                    "input_select.temptamer_comfort_mode": "Office",
                    "sensor.home_temperature": "18.0",
                    "climate.wt32_hpctrl_e8dbd0_heatpump": "off",
                    "sensor.office_temperature": "17.0",
                    "sensor.dining_temperature": "21.0",
                    "sensor.bedroom_1_2_temperature": "19.5",
                    "sensor.bedroom_3_4_temperature": "19.5",
                    "switch.office_zone": "on",
                    "switch.dining_zone": "off",
                    "switch.bedroom_1_2_zone": "off",
                    "switch.bedroom_3_4_zone": "off",
                },
                {
                    "climate.wt32_hpctrl_e8dbd0_heatpump": {"current_temperature": "14.0"},
                },
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office",))

        with self.assertLogs("pyscript.temptamer", level="INFO") as captured:
            plan = build_dispatch_plan(
                snapshot,
                demand,
                ("office",),
                current_hvac_mode="off",
                current_fan_mode="low",
            )

        self.assertEqual(plan.setpoint, 19)
        self.assertTrue(
            any(
                "SETPOINT: inlet_temp=14.0 zone=office enable_below=19.0" in message
                and "normalized=19" in message
                for message in captured.output
            )
        )

    def test_equipment_demand_lists_all_requesting_zones(self):
        snapshot = build_snapshot(
            FakeReader(
                {
                    "input_select.temptamer_comfort_mode": "Day",
                    "sensor.home_temperature": "18.0",
                    "climate.wt32_hpctrl_e8dbd0_heatpump": "off",
                    "sensor.office_temperature": "17.0",
                    "sensor.dining_temperature": "18.0",
                    "sensor.bedroom_1_2_temperature": "19.5",
                    "sensor.bedroom_3_4_temperature": "19.5",
                    "switch.office_zone": "on",
                    "switch.dining_zone": "on",
                    "switch.bedroom_1_2_zone": "off",
                    "switch.bedroom_3_4_zone": "off",
                },
                {
                    "climate.wt32_hpctrl_e8dbd0_heatpump": {"current_temperature": "16.4"},
                },
            )
        )

        demand = resolve_equipment_demand(snapshot, ("office", "dining"))
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

    def test_fan_hysteresis_uses_medium_thresholds(self):
        self.assertEqual(
            resolve_fan_mode(
                "low",
                "heat",
                make_demand(heat_requested=True, max_temperature_deficit=5.1),
            ),
            "medium",
        )
        self.assertEqual(
            resolve_fan_mode(
                "medium",
                "heat",
                make_demand(heat_requested=True, max_temperature_deficit=2.9),
            ),
            "medium",
        )
        self.assertEqual(
            resolve_fan_mode(
                "medium",
                "heat",
                make_demand(heat_requested=True, max_temperature_deficit=1.9),
            ),
            "low",
        )
        self.assertEqual(normalize_setpoint(25.1), 25)


if __name__ == "__main__":
    unittest.main()
