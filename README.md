# homeassistant-heatpump

PyScript heatpump control loop for Home Assistant.

## TempTamer

This repository now includes a PyScript app in `pyscript/apps/temptamer` that implements the TempTamer control loop described in the `ARCHITECTURE` documents:

- configuration and constants
- comfort mode and zone state resolution
- zone actuation with anti-flap protection
- abstract equipment demand resolution
- heatpump dispatch for the configured climate entity

## Files

- `pyscript/apps/temptamer/__init__.py` – the Home Assistant app package entrypoint that is autoloaded by `pyscript`
- `pyscript/apps/temptamer/main.py` – the TempTamer runtime with trigger-driven periodic control, status entities, and immediate comfort-mode reconciliation
- `pyscript/apps/temptamer/config.py` – entity IDs, zone definitions, comfort-mode mapping, and default thresholds
- `pyscript/apps/temptamer/state_reader.py` – Home Assistant state normalization with fallback to `sensor.home_temperature`
- `pyscript/apps/temptamer/zone_control.py` – zone opening/closing decisions
- `pyscript/apps/temptamer/demand_resolver.py` – abstract heating demand resolution
- `pyscript/apps/temptamer/heatpump_dispatcher.py` – HVAC mode, fan mode, and setpoint planning

## Home Assistant setup

1. Install and enable [PyScript](https://hacs-pyscript.readthedocs.io/) in Home Assistant.
2. Copy the repository's `pyscript/apps/temptamer` directory into your Home Assistant config at `config/pyscript/apps/temptamer`.
3. Add a `pyscript` app configuration entry so Home Assistant will load the package-form app:

	 ```yaml
	 pyscript:
		 apps:
			 temptamer: {}
	 ```

	 If you already keep `pyscript` configuration in `config/pyscript/config.yaml`, add the same `apps.temptamer` entry there instead.
4. Update `pyscript/apps/temptamer/config.py` so the zone sensor and switch entity IDs match your Home Assistant entities.
5. Ensure `input_select.temptamer_comfort_mode`, `sensor.home_temperature`, and `climate.wt32_hpctrl_e8dbd0_heatpump` exist or are adjusted in the config.
6. Reload `pyscript`.

## Runtime model

- TempTamer now uses `pyscript`-native APIs for entity reads via `state.get` / `state.getattr`, entity-bound service calls such as `climate.<entity>.set_hvac_mode(...)`, and state writes via `state.set`.
- TempTamer uses the package-form app layout documented by `pyscript`: `config/pyscript/apps/temptamer/__init__.py` is the autoloaded entrypoint, with helper modules living alongside it inside the same app package.
- The app registers three Home Assistant services: `temptamer.start`, `temptamer.stop`, and `temptamer.run_once`.
- Instead of a hand-rolled forever-loop, TempTamer now follows the `pyscript` reference model: a `@time_trigger("startup")` initializer sets up state and a `@time_trigger("period(now, ...)")` function runs the periodic control pass.
- Runtime status is published to `pyscript.temptamer_status`, and the enabled/disabled switch is persisted in `pyscript.temptamer_enabled`.
- The runtime calls `task.unique(...)` for each control pass so overlapping periodic, startup, and comfort-mode triggers do not pile up across reloads or rapid state changes.
- Comfort-mode changes still trigger an immediate reconciliation pass whenever TempTamer is enabled.

## Validation

The runtime decision logic is covered by `unittest` tests in `tests/test_temptamer.py`.
