from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from pyscript.apps.temptamer.models import EquipmentDemand
from pyscript.apps.temptamer.demand_resolver import resolve_equipment_demand
from pyscript.apps.temptamer.heatpump_dispatcher import build_dispatch_plan, normalize_setpoint, resolve_fan_mode
from pyscript.apps.temptamer.main import PyscriptController
from pyscript.apps.temptamer.state_reader import build_snapshot, parse_float
from pyscript.apps.temptamer.zone_control import resolve_zone_actions
import pyscript.apps.temptamer.main as temptamer_main


class FakeReader:
    def __init__(self, state_map):
        self.state_map = state_map

    def get_state(self, entity_id):
        return self.state_map.get(entity_id)


class FakeStateVal:
    def __init__(self, numeric_value):
        self.numeric_value = numeric_value

    def as_float(self, default=None):
        if self.numeric_value is None:
            return default
        return float(self.numeric_value)

    def __float__(self):
        raise ValueError("plain float conversion should not be used")


def make_demand(**overrides):
    return EquipmentDemand(**overrides)


class TempTamerTests(unittest.TestCase):
    def test_parse_float_uses_stateval_helper(self):
        self.assertEqual(parse_float(FakeStateVal(20.25)), 20.25)

    def test_build_snapshot_uses_house_sensor_fallback(self):
        reader = FakeReader(
            {
                "input_select.temptamer_comfort_mode": "Night",
                "sensor.home_temperature": "18.5",
                "sensor.wt32_hpctrl_e8dbd0_inside_coil_inlet_temp": "23.4",
                "sensor.office_temperature": "unavailable",
                "sensor.dining_temperature": "17.0",
                "sensor.bedroom_1_2_temperature": "16.5",
                "sensor.bedroom_3_4_temperature": "unknown",
                "switch.office_zone": "off",
                "switch.dining_zone": "on",
                "switch.bedroom_1_2_zone": "off",
                "switch.bedroom_3_4_zone": "on",
            }
        )

        snapshot = build_snapshot(reader)

        self.assertEqual(snapshot.zones["office"].current_temp, 18.5)
        self.assertEqual(snapshot.zones["bedroom_3_4"].current_temp, 18.5)
        self.assertEqual(snapshot.inlet_temp, 23.4)
        self.assertEqual(snapshot.zones["bedroom_1_2"].scheme.name, "Bedroom")

    def test_build_snapshot_falls_back_when_house_sensor_unavailable(self):
        reader = FakeReader(
            {
                "input_select.temptamer_comfort_mode": "Night",
                "sensor.home_temperature": "unavailable",
                "sensor.wt32_hpctrl_e8dbd0_inside_coil_inlet_temp": "19.8",
                "sensor.office_temperature": "18.1",
                "sensor.dining_temperature": "17.0",
                "sensor.bedroom_1_2_temperature": "16.5",
                "sensor.bedroom_3_4_temperature": "unknown",
                "switch.office_zone": "off",
                "switch.dining_zone": "on",
                "switch.bedroom_1_2_zone": "off",
                "switch.bedroom_3_4_zone": "on",
            }
        )

        snapshot = build_snapshot(reader)

        self.assertEqual(snapshot.inlet_temp, 19.8)
        self.assertEqual(snapshot.zones["bedroom_3_4"].current_temp, 19.8)

    def test_build_snapshot_accepts_pyscript_state_values(self):
        reader = FakeReader(
            {
                "input_select.temptamer_comfort_mode": "Night",
                "sensor.home_temperature": FakeStateVal(18.4),
                "sensor.wt32_hpctrl_e8dbd0_inside_coil_inlet_temp": FakeStateVal(20.1),
                "sensor.office_temperature": FakeStateVal(17.6),
                "sensor.dining_temperature": FakeStateVal(17.0),
                "sensor.bedroom_1_2_temperature": FakeStateVal(16.5),
                "sensor.bedroom_3_4_temperature": FakeStateVal(18.0),
                "switch.office_zone": "off",
                "switch.dining_zone": "on",
                "switch.bedroom_1_2_zone": "off",
                "switch.bedroom_3_4_zone": "on",
            }
        )

        snapshot = build_snapshot(reader)

        self.assertEqual(snapshot.zones["office"].current_temp, 17.6)
        self.assertEqual(snapshot.inlet_temp, 20.1)

    def test_zone_actions_respect_antiflap_but_allow_mode_change(self):
        snapshot = build_snapshot(
            FakeReader(
                {
                    "input_select.temptamer_comfort_mode": "Day",
                    "sensor.home_temperature": "18.0",
                    "sensor.wt32_hpctrl_e8dbd0_inside_coil_inlet_temp": "19.0",
                    "sensor.office_temperature": "17.0",
                    "sensor.dining_temperature": "22.0",
                    "sensor.bedroom_1_2_temperature": "20.0",
                    "sensor.bedroom_3_4_temperature": "20.0",
                    "switch.office_zone": "off",
                    "switch.dining_zone": "on",
                    "switch.bedroom_1_2_zone": "off",
                    "switch.bedroom_3_4_zone": "off",
                }
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

    def test_zone_actions_handle_equal_ranks_without_comparing_zone_objects(self):
        snapshot = build_snapshot(
            FakeReader(
                {
                    "input_select.temptamer_comfort_mode": "Night",
                    "sensor.home_temperature": "18.0",
                    "sensor.wt32_hpctrl_e8dbd0_inside_coil_inlet_temp": "19.0",
                    "sensor.office_temperature": "17.0",
                    "sensor.dining_temperature": "17.0",
                    "sensor.bedroom_1_2_temperature": "21.0",
                    "sensor.bedroom_3_4_temperature": "21.0",
                    "switch.office_zone": "off",
                    "switch.dining_zone": "off",
                    "switch.bedroom_1_2_zone": "off",
                    "switch.bedroom_3_4_zone": "off",
                }
            ),
            last_switch_changes={
                "office": datetime(2026, 1, 1, 12, 0, 0),
                "dining": datetime(2026, 1, 1, 12, 0, 0),
            },
        )

        actions, predicted_open = resolve_zone_actions(
            snapshot,
            datetime(2026, 1, 1, 12, 20, 0),
            comfort_mode_changed=False,
        )

        self.assertEqual(len(actions), 1)
        self.assertIn(actions[0].zone_key, {"office", "dining"})
        self.assertEqual(predicted_open, (actions[0].zone_key,))

    def test_zone_actions_handle_naive_last_change_with_aware_now(self):
        snapshot = build_snapshot(
            FakeReader(
                {
                    "input_select.temptamer_comfort_mode": "Day",
                    "sensor.home_temperature": "18.0",
                    "sensor.wt32_hpctrl_e8dbd0_inside_coil_inlet_temp": "19.0",
                    "sensor.office_temperature": "17.0",
                    "sensor.dining_temperature": "22.0",
                    "sensor.bedroom_1_2_temperature": "20.0",
                    "sensor.bedroom_3_4_temperature": "20.0",
                    "switch.office_zone": "off",
                    "switch.dining_zone": "on",
                    "switch.bedroom_1_2_zone": "off",
                    "switch.bedroom_3_4_zone": "off",
                }
            ),
            last_switch_changes={
                "office": datetime(2026, 1, 1, 12, 0, 0),
                "dining": datetime(2026, 1, 1, 12, 0, 0),
            },
        )

        actions, predicted_open = resolve_zone_actions(
            snapshot,
            datetime(2026, 1, 1, 12, 2, 0, tzinfo=timezone.utc),
            comfort_mode_changed=False,
        )

        self.assertEqual(actions, [])
        self.assertEqual(predicted_open, ("dining",))

    def test_build_snapshot_normalizes_naive_last_switch_change(self):
        snapshot = build_snapshot(
            FakeReader(
                {
                    "input_select.temptamer_comfort_mode": "Night",
                    "sensor.home_temperature": "18.5",
                    "sensor.wt32_hpctrl_e8dbd0_inside_coil_inlet_temp": "23.4",
                    "sensor.office_temperature": "18.1",
                    "sensor.dining_temperature": "17.0",
                    "sensor.bedroom_1_2_temperature": "16.5",
                    "sensor.bedroom_3_4_temperature": "18.0",
                    "switch.office_zone": "off",
                    "switch.dining_zone": "on",
                    "switch.bedroom_1_2_zone": "off",
                    "switch.bedroom_3_4_zone": "on",
                }
            ),
            last_switch_changes={"office": datetime(2026, 1, 1, 12, 0, 0)},
        )

        self.assertEqual(snapshot.zones["office"].last_switch_change, datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))

    def test_equipment_demand_and_dispatch_plan_choose_heat_setpoint(self):
        snapshot = build_snapshot(
            FakeReader(
                {
                    "input_select.temptamer_comfort_mode": "Office",
                    "sensor.home_temperature": "18.0",
                    "sensor.wt32_hpctrl_e8dbd0_inside_coil_inlet_temp": "16.4",
                    "sensor.office_temperature": "17.0",
                    "sensor.dining_temperature": "21.0",
                    "sensor.bedroom_1_2_temperature": "19.5",
                    "sensor.bedroom_3_4_temperature": "19.5",
                    "switch.office_zone": "on",
                    "switch.dining_zone": "off",
                    "switch.bedroom_1_2_zone": "off",
                    "switch.bedroom_3_4_zone": "off",
                }
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
        self.assertEqual(demand.requested_by_zone, "office")
        self.assertEqual(plan.hvac_mode, "heat")
        self.assertEqual(plan.setpoint, 19)
        self.assertEqual(plan.fan_mode, "low")

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
            "low",
        )
        self.assertEqual(normalize_setpoint(25.1), 25)

    def test_pyscript_controller_uses_entity_bound_service_when_available(self):
        entity_service = Mock()
        temptamer_main.state = SimpleNamespace(
            get=lambda name: entity_service if name == "climate.hp.set_hvac_mode" else None,
            getattr=lambda _name: {},
            set=lambda *_args, **_kwargs: None,
        )
        temptamer_main.service = SimpleNamespace(call=Mock())

        controller = PyscriptController()
        controller.call_service(
            "climate",
            "set_hvac_mode",
            entity_id="climate.hp",
            hvac_mode="heat",
        )

        entity_service.assert_called_once_with(hvac_mode="heat")
        temptamer_main.service.call.assert_not_called()

    def test_pyscript_controller_falls_back_to_service_call(self):
        temptamer_main.state = SimpleNamespace(
            get=lambda _name: None,
            getattr=lambda _name: {},
            set=lambda *_args, **_kwargs: None,
        )
        temptamer_main.service = SimpleNamespace(call=Mock())

        controller = PyscriptController()
        controller.call_service(
            "switch",
            "turn_on",
            entity_id="switch.office_zone",
        )

        temptamer_main.service.call.assert_called_once_with(
            "switch",
            "turn_on",
            entity_id="switch.office_zone",
        )

    def test_pyscript_controller_missing_entity_returns_none(self):
        def raise_name_error(_name):
            raise NameError("entity missing")

        temptamer_main.state = SimpleNamespace(
            get=raise_name_error,
            getattr=lambda _name: None,
            set=lambda *_args, **_kwargs: None,
        )

        controller = PyscriptController()

        self.assertIsNone(controller.get_state("sensor.missing_temperature"))


if __name__ == "__main__":
    unittest.main()
