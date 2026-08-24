# homeassistant-heatpump

PyScript heatpump control loop for Home Assistant.

## TempTamer

This repository now includes a PyScript app in `pyscript/apps/temptamer` that implements the TempTamer control loop.

- configuration and constants
- comfort mode and zone state resolution
- zone actuation with anti-flap protection
- abstract equipment demand resolution
- heatpump dispatch for the configured climate entity

## Files

- `pyscript/apps/temptamer/__init__.py` – the Home Assistant app package entrypoint that is autoloaded by `pyscript`
- `pyscript/apps/temptamer/main.py` – the TempTamer runtime with trigger-driven periodic control, status entities, and immediate comfort-mode reconciliation
- `pyscript/apps/temptamer/config.py` – entity IDs, zone definitions, comfort-mode mapping, and default thresholds
- `pyscript/apps/temptamer/comfort_adjustments.py` – pure per-zone adjustment, solar, shutter, and operating-mode calculation
- `pyscript/apps/temptamer/state_reader.py` – Home Assistant state normalization with fallback to `sensor.home_temperature` and `climate.current_temperature`
- `pyscript/apps/temptamer/zone_control.py` – zone opening/closing decisions
- `pyscript/apps/temptamer/demand_resolver.py` – abstract heating demand resolution
- `pyscript/apps/temptamer/heatpump_dispatcher.py` – HVAC mode, fan mode, and setpoint planning

## Home Assistant setup

1. Install and enable [PyScript](https://hacs-pyscript.readthedocs.io/) in Home Assistant.
2. Copy the repository's `pyscript/apps/temptamer` directory into your Home Assistant config at `config/pyscript/apps/temptamer`.
3. Add a `pyscript` app configuration entry so Home Assistant will load the package-form app:

	 ```yaml
	 pyscript:
		 allow_all_imports: true
		 hass_is_global: true
		 apps:
			 temptamer: {}
	 ```

	 `allow_all_imports` and `hass_is_global` are required for TempTamer's cover-device label lookup. If you already keep `pyscript` configuration in `config/pyscript/config.yaml`, add the same settings and `apps.temptamer` entry there instead.
4. Update `pyscript/apps/temptamer/config.py` so the zone sensor and switch entity IDs match your Home Assistant entities.
5. Ensure these helper entities exist, or adjust `pyscript/apps/temptamer/config.py` to match your setup:
   - `input_select.temptamer_comfort_mode` with `Off`, `Night`, `Day`, `Office`, `PowerDay`, and `PowerOff`
   - `input_select.temptamer_hvac_mode` with `Heat`, `Cool`, `HeatCool`, `Off`, and `Manual`
   - `input_select.temptamer_comfort_mode_office` with `Auto`, `Off`, `Night`, `DayLiving`, `Downstairs`, `DiningBasic`, and `Bedroom`
   - `input_select.temptamer_comfort_mode_dining` with `Auto`, `Off`, `Night`, `DayLiving`, `Downstairs`, `DiningBasic`, and `Bedroom`
   - `input_select.temptamer_comfort_mode_downstairs` with `Auto`, `Off`, `Night`, `DayLiving`, `Downstairs`, `DiningBasic`, and `Bedroom`
   - `input_select.temptamer_comfort_mode_bed12` with `Auto`, `Off`, `Night`, `DayLiving`, `Downstairs`, `DiningBasic`, and `Bedroom`
   - `input_select.temptamer_comfort_mode_bed34` with `Auto`, `Off`, `Night`, `DayLiving`, `Downstairs`, `DiningBasic`, `Bedroom`, and `Bathroom`
   - `sensor.home_temperature`
   - `sensor.downstairs_zone_average_temperature`
   - `sensor.entry_goodwe_inverter_current_electricity_price`
   - a kW-valued, rolling five-minute maximum-demand sensor (configured as `sensor.eagle_200_max_power_demand_5m`)
   - `climate.wt32_hpctrl_e8dbd0_heatpump`
   - `switch.roof_wt32_hpctrl_e8dbd0_downstairs`
   - `input_select.heatpump_mode_user`, whose direct `Heat` or `Cool` selection takes precedence for comfort adjustments
   - `input_number.awning_min_sun_elevation` and `input_number.awning_exposure_half_band`
   - five manual number helpers with minimum `-1.5`, maximum `1.5`, and step `0.1`:
     - `input_number.comfort_adjustment_downstairs`
     - `input_number.comfort_adjustment_bed_1_2`
     - `input_number.comfort_adjustment_bed_3_4`
     - `input_number.comfort_adjustment_office`
     - `input_number.comfort_adjustment_dining`
   - `sensor.gw3000c_outdoor_temperature` and `sensor.gw3000c_solar_radiation`, or the `weather.epping` temperature/condition fallbacks
   - `sun.sun` with `elevation` and `azimuth` attributes
   - the room temperature sensors and shutter covers configured in `DEFAULT_COMFORT_ADJUSTMENT_CONFIG`
   - one `awning_n`, `awning_e`, `awning_s`, or `awning_w` label on each shutter cover's Home Assistant device
6. Reload `pyscript`.

## Runtime model

- TempTamer now uses `pyscript`-native APIs for entity reads via `state.get` / `state.getattr`, entity-bound service calls such as `climate.<entity>.set_hvac_mode(...)`, and state writes via `state.set`.
- TempTamer uses the package-form app layout documented by `pyscript`: `config/pyscript/apps/temptamer/__init__.py` is the autoloaded entrypoint, with helper modules living alongside it inside the same app package.
- The app registers three Home Assistant services: `temptamer.start`, `temptamer.stop`, and `temptamer.run_once`.
- Instead of a hand-rolled forever-loop, TempTamer now follows the `pyscript` reference model: a `@time_trigger("startup")` initializer sets up state and a `@time_trigger("period(now, ...)")` function runs the periodic control pass.
- Runtime status is published to `pyscript.temptamer_status`, and the enabled/disabled switch is persisted in `pyscript.temptamer_enabled`.
- Heat-demand fan boost is persisted in `/config/pyscript/temptamer_fan_boost.state`; it is restored after a PyScript reload only when the file was updated within the last 15 minutes.
- The runtime calls `task.unique(...)` for each control pass so overlapping periodic, startup, and comfort-mode triggers do not pile up across reloads or rapid state changes.
- Comfort-mode changes still trigger an immediate reconciliation pass whenever TempTamer is enabled.
- A separate comfort-adjustment publisher runs at startup, on source changes, and every minute regardless of whether TempTamer control is enabled. It calculates independent per-zone relative adjustments from room temperature, outdoor conditions, solar gain, façade exposure, and shutter openness, then writes the five `input_number.comfort_adjustment_*` helpers. Outdoor temperature is exponentially filtered with a 10-minute time constant upstairs and a 30-minute time constant downstairs to model the double-brick thermal delay; a 0.025°C rounding hysteresis prevents output chatter. Solar access retains a diffuse baseline while direct gain tapers from on-axis to zero at the configured façade-exposure band. Its envelope calculation uses the active zone's unadjusted heat or cool `ideal_target` (or `20.0C` when control is disabled or that target is unavailable), while room temperature still selects heat or cool operation.
- Each helper immediately shifts its zone's heat and cool `enable_outside`, `continue_until`, and `ideal_target` thresholds by the helper value. This keeps the threshold spacing intact and applies the adjustment to zone opening, equipment demand, fan logic, and dispatch planning without adding a separate global heat-pump setpoint offset.
- HVAC selection supports `Heat`, `Cool`, `HeatCool`, `Off`, and `Manual`. `Manual` leaves both the zone switches and heatpump untouched, while `HeatCool` enforces a one-hour anti-flap delay before changing between heating and cooling.
- `PowerDay` follows the `Office` comfort mapping except downstairs, which uses the dedicated `Downstairs` scheme. The Downstairs thresholds are each `0.5C` above `DayLiving`. When `sensor.entry_goodwe_inverter_current_electricity_price` reports `0`, dining and bedroom zones using `DiningBasic` or `Bedroom` are upgraded to `DayLiving` so the house can heat soak during free power; the normal free-power offsets also apply to `Downstairs`.
- The free-power heating supplements (`free_power_initial_suppliment` and `free_power_later`) for office and dining are held until downstairs is strictly above `19C`. The `Downstairs` scheme retains its `1.25C` initial and `3C` later supplements; every other scheme receives `0.75C` initially and `1.5C` later. Cooling thresholds are unaffected.
- During that free-price PowerDay heat soak, downstairs raises its heat `enable_outside` threshold by an additional `1C` and adds one physical fan level whenever it is planned open with active heat demand. Neither adjustment applies to cooling or export-only heat soaking.
- During a free-power `PowerDay` heat soak, if downstairs is at least `3C` colder than every open upstairs zone and still needs heat, TempTamer temporarily closes the upstairs vents, heats downstairs, and adds two fan levels. The priority lasts at least ten minutes and only releases once the gap falls below `2C`, free power ends, or downstairs reaches its target.
- `PowerOff` keeps the heatpump off until battery remaining is above `95%`, PV power is above `1 kW`, and HVAC mode is not `Off`. It then runs for at least 15 minutes using the `PowerDay` configuration from 08:00 (inclusive) to 22:00 (exclusive), or `Night` outside those hours. Once the 15-minute minimum has elapsed, it turns the heatpump off when battery remaining falls below `90%`. Selecting any other comfort mode cancels PowerOff immediately and performs the usual immediate mode-change reconciliation.
- In `Heat` mode, any enabled zone below `enable_outside` starts heating and heating continues until every enabled zone reaches `continue_until`. In `Cool` mode, any enabled zone above `enable_outside` starts cooling and cooling continues until every enabled zone drops to `ideal_target` or lower.
- When the heat pump has been off for at least 15 minutes, a new heating or cooling cycle starts at physical fan Level 1 and ramps to its calculated fan speed over the following 15 minutes.
- When a planned-open heating zone is at least `2.0C` below its `continue_until` target and the rolling five-minute maximum demand is below `14 kW`, TempTamer adds one pre-multiplier fan level immediately, then one more every 15 minutes to the configured maximum. The boost is disabled for cooling, unavailable demand readings, or demand at/above `14 kW`.
- When there is no remaining active demand but the heatpump is still running, TempTamer enters an idle dispatch state that keeps the current HVAC mode. On the first heating-idle pass, it preserves the current target unless that target is more than `2.0C` above the coldest predicted-open room; in that case it starts idle from the midpoint between the room temperature and the inherited target. After entry, the existing idle backoff ladder remains in effect before escalating to `turn_off` after one hour. A mode-change reconciliation that removes all thermal demand instead closes the final satisfied zone and turns the heatpump off immediately. If the heatpump still reports `heat` or `cool`, TempTamer keeps retrying the shutdown path instead of starting a fresh idle hour.
- Each zone can override the global comfort mode with its own `input_select.temptamer_comfort_mode_*` entity. Use `Auto` to follow the global comfort mode, or choose one of the configured control scheme names (`Off`, `Night`, `DayLiving`, `Downstairs`, `DiningBasic`, or `Bedroom`) to apply that scheme directly. Downstairs uses the dedicated `Downstairs` scheme throughout Day and PowerDay modes. Bedroom 3&4 also supports a `Bathroom` override that uses `sensor.bathroom_motion_temperature` for that zone's temperature decisions while keeping the same thresholds as the bedroom scheme. Omitting the override entity or providing an unrecognized value keeps the global comfort mode in effect for that zone.

## Validation

The runtime decision logic is covered by `unittest` tests in `tests/test_temptamer.py`.
