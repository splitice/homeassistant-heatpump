# homeassistant-heatpump

PyScript heatpump control loop for Home Assistant.

## TempTamer

This repository now includes a PyScript app in `apps/temptamer` that implements the TempTamer control loop described in the `ARCHITECTURE` documents:

- configuration and constants
- comfort mode and zone state resolution
- zone actuation with anti-flap protection
- abstract equipment demand resolution
- heatpump dispatch for the configured climate entity

## Files

- `apps/temptamer/config.py` – entity IDs, zone definitions, comfort-mode mapping, and default thresholds
- `apps/temptamer/state_reader.py` – Home Assistant state normalization with fallback to `sensor.home_temperature`
- `apps/temptamer/zone_control.py` – zone opening/closing decisions
- `apps/temptamer/demand_resolver.py` – abstract heating demand resolution
- `apps/temptamer/heatpump_dispatcher.py` – HVAC mode, fan mode, and setpoint planning
- `apps/temptamer/main.py` – the PyScript entrypoint with a 60-second loop and immediate comfort-mode reconciliation

## Home Assistant setup

1. Install and enable [PyScript](https://hacs-pyscript.readthedocs.io/) in Home Assistant.
2. Copy the `apps/temptamer` package into your Home Assistant PyScript apps directory.
3. Update `apps/temptamer/config.py` so the zone sensor and switch entity IDs match your Home Assistant entities.
4. Ensure `input_select.temptamer_comfort_mode`, `sensor.home_temperature`, and `climate.wt32_hpctrl_e8dbd0_heatpump` exist or are adjusted in the config.
5. Reload PyScript.

## Validation

The runtime decision logic is covered by `unittest` tests in `tests/test_temptamer.py`.
