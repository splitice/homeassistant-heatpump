from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock

from pyscript.apps.temptamer.constants import HVAC_COOL, HVAC_HEAT
from pyscript.apps.temptamer.demand_resolver import resolve_equipment_demand, resolve_operating_mode
from pyscript.apps.temptamer.heatpump_dispatcher import build_dispatch_plan, normalize_setpoint, resolve_fan_mode
from pyscript.apps.temptamer.models import EquipmentDemand
import pyscript.apps.temptamer.main as temptamer_main
from pyscript.apps.temptamer.state_reader import build_snapshot
from pyscript.apps.temptamer.zone_control import describe_zone_predictions, resolve_zone_actions


INLET_SENSOR = "sensor.wt32_hpctrl_e8dbd0_inside_coil_inlet_temp"


class FakeReader:
    def __init__(self, state_map):
        self.state_map = state_map

    def get_state(self, entity_id):
        return self.state_map.get(entity_id)


def base_state_map(**overrides):
    state_map = {
        "input_select.temptamer_comfort_mode": "Day",
        "input_select.temptamer_hvac_mode": "Heat",
        "sensor.home_temperature": "18.0",
        INLET_SENSOR: "19.0",
        "sensor.office_temperature": "18.0",
        "sensor.dining_temperature": "18.0",
        "sensor.bedroom_1_2_temperature": "18.0",
        "sensor.bedroom_3_4_temperature": "18.0",
        "switch.office_zone": "off",
        "switch.dining_zone": "off",
        "switch.bedroom_1_2_zone": "off",
        "switch.bedroom_3_4_zone": "off",
    }
    state_map.update(overrides)
    return state_map


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
                    INLET_SENSOR: "23.4",
                    "sensor.office_temperature": "unavailable",
                    "sensor.dining_temperature": "17.0",
                    "sensor.bedroom_1_2_temperature": "16.5",
                    "sensor.bedroom_3_4_temperature": "unknown",
                    "switch.dining_zone": "on",
                    "switch.bedroom_3_4_zone": "on",
                }
            )
        )

        snapshot = build_snapshot(reader)

        self.assertEqual(snapshot.selected_hvac_mode, "Cool")
        self.assertEqual(snapshot.zones["office"].current_temp, 18.5)
        self.assertEqual(snapshot.zones["bedroom_3_4"].current_temp, 18.5)
        self.assertEqual(snapshot.inlet_temp, 23.4)
        self.assertEqual(snapshot.zones["office"].applied_comfort_mode, "Day")
        self.assertEqual(snapshot.zones["office"].scheme.name, "DayLiving")
        self.assertEqual(snapshot.zones["dining"].scheme.name, "Night")
        self.assertEqual(snapshot.zones["bedroom_1_2"].scheme.name, "Bedroom")

    def test_zone_actions_respect_antiflap_but_allow_mode_change(self):
        snapshot = build_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "sensor.home_temperature": "18.0",
                        INLET_SENSOR: "19.0",
                        "sensor.office_temperature": "17.0",
                        "sensor.dining_temperature": "22.0",
                        "sensor.bedroom_1_2_temperature": "20.0",
                        "sensor.bedroom_3_4_temperature": "20.0",
                        "switch.dining_zone": "on",
                    }
                )
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
        snapshot = build_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "sensor.home_temperature": "18.0",
                        INLET_SENSOR: "18.5",
                        "sensor.office_temperature": "17.5",
                        "sensor.dining_temperature": "17.8",
                    }
                )
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

    def test_zone_prediction_diagnostics_explain_anti_flap_decisions(self):
        now = datetime(2026, 5, 7, 12, 1, 0, tzinfo=timezone.utc)
        snapshot = build_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "sensor.home_temperature": "18.0",
                        INLET_SENSOR: "18.5",
                        "sensor.office_temperature": "17.5",
                        "sensor.dining_temperature": "17.8",
                    }
                )
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
        snapshot = build_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_comfort_mode": "Office",
                        "sensor.home_temperature": "18.0",
                        INLET_SENSOR: "16.4",
                        "sensor.office_temperature": "17.0",
                        "sensor.dining_temperature": "21.0",
                        "sensor.bedroom_1_2_temperature": "19.5",
                        "sensor.bedroom_3_4_temperature": "19.5",
                        "switch.office_zone": "on",
                    }
                )
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
        self.assertEqual(demand.requested_by_zone, "office")
        self.assertEqual(plan.hvac_mode, "heat")
        self.assertEqual(plan.setpoint, 19)
        self.assertEqual(plan.fan_mode, "low")

    def test_cooling_mode_dispatches_cool_plan(self):
        now = datetime(2026, 5, 7, 12, 1, 0, tzinfo=timezone.utc)
        snapshot = build_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_hvac_mode": "Cool",
                        "sensor.home_temperature": "22.0",
                        INLET_SENSOR: "25.0",
                        "sensor.office_temperature": "24.5",
                        "sensor.dining_temperature": "20.0",
                        "sensor.bedroom_1_2_temperature": "20.0",
                        "sensor.bedroom_3_4_temperature": "20.0",
                    }
                )
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
        self.assertEqual(demand.requested_by_zone, "office")
        self.assertEqual(plan.hvac_mode, "cool")
        self.assertEqual(plan.setpoint, 23)

    def test_heatcool_mode_holds_current_mode_during_antiflap_window(self):
        now = datetime(2026, 5, 7, 12, 1, 0, tzinfo=timezone.utc)
        snapshot = build_snapshot(
            FakeReader(
                base_state_map(
                    **{
                        "input_select.temptamer_hvac_mode": "HeatCool",
                        "sensor.home_temperature": "22.0",
                        INLET_SENSOR: "24.0",
                        "sensor.office_temperature": "24.5",
                        "sensor.dining_temperature": "20.0",
                        "sensor.bedroom_1_2_temperature": "20.0",
                        "sensor.bedroom_3_4_temperature": "20.0",
                    }
                )
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
                    "switch.office_zone": "on",
                    "climate.wt32_hpctrl_e8dbd0_heatpump": "heat",
                }
            )
        )
        temptamer_main.state._attrs["climate.wt32_hpctrl_e8dbd0_heatpump"] = {
            "fan_mode": "low",
            "temperature": 19,
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
                EquipmentDemand(heat_requested=True, max_temperature_deficit=5.1),
            ),
            "medium",
        )
        self.assertEqual(
            resolve_fan_mode(
                "medium",
                "heat",
                EquipmentDemand(heat_requested=True, max_temperature_deficit=2.9),
            ),
            "low",
        )
        self.assertEqual(normalize_setpoint(25.1), 25)


if __name__ == "__main__":
    unittest.main()
