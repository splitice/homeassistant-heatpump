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
   `TEMPTAMER_LOGGING_CATEGORIES` in that file independently enables or disables lifecycle, comfort-adjustment, fan-boost, PowerDay, PowerOff, zone, dispatch, and setpoint logs. Set `comfort_adjustment_diagnostics` to `True` to emit the per-zone operative-model breakdowns.
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
   - `input_number.temptamer_setpoint_adjustment`, with minimum `-1.5`, maximum `1.5`, and step `0.1`, to offset every zone together
   - `sensor.gw3000c_outdoor_temperature` and `sensor.gw3000c_solar_radiation`, or the `weather.epping` temperature/condition fallbacks
   - `weather.epping_hourly`, which supports Home Assistant's hourly `weather.get_forecasts` action for predictive idle shutdown
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
- A separate comfort-score publisher runs at startup, on source changes, and every minute regardless of whether TempTamer control is enabled. It calculates each room independently with a configurable operative-temperature envelope model, then publishes the comfort-weighted zone score to the five `input_number.comfort_adjustment_*` helpers. Positive scores represent radiant/solar warmth and delay heating; negative scores represent a cold envelope and request heating sooner. It uses the active zone's unadjusted heat or cool `ideal_target` (or `20.0C` when control is disabled or that target is unavailable); room temperature selects the shared operating mode but never feeds back into the score magnitude. The publisher records its score-semantics version; a changed version forces one unthrottled reseed of all helpers before the control loop can consume them.
- Windows use a 15-minute elapsed-time-aware outdoor filter. Walls use their construction profile’s filter: initially three hours for upstairs brick veneer and eight hours for downstairs double brick. After a restart, each filter remains in warm-up for half its time constant. Output movement is limited to `0.2C` per 15 minutes using an unrounded accumulator, so one-minute passes still make progress before the helper value is rounded to `0.1C`.
- Each shutter changes only its own window’s physical U-value and direct/diffuse SHGC. Façade solar uses vertical-plane direct incidence (`cos(elevation) × cos(azimuth difference)`) inside the configured awning exposure mask, plus diffuse and ground-reflected components. Direct-normal irradiance is capped rather than discarded at low sun angles, and all solar components are zero below the horizon. The solar term is independently capped at `0.4C`. Every zone has a separately calibrated filtered-irradiance fabric score: Downstairs uses 120 minutes, threshold `30`, coefficient `0.0015`, cap `0.25`; Bed 1/2 uses 45 minutes, threshold `30`, coefficient `0.0012`, cap `0.20`; Bed 3/4 uses 45 minutes, threshold `25`, coefficient `0.0020`, cap `0.30`; Office uses 30 minutes, threshold `20`, coefficient `0.0045`, cap `0.40`; and Dining uses 60 minutes, threshold `25`, coefficient `0.0025`, cap `0.40`. Fabric-filter memory is persisted across reloads, discarded when irradiance is stale, and forcibly zeroed below the horizon. Rumpus direct solar is permanently shaded to zero. `DEFAULT_COMFORT_ADJUSTMENT_CONFIG.calculation_model` can be set to `legacy` for an immediate rollback, but production defaults to `operative`.
- A cached operating mode is retained only as a five-minute transient hold when the user selection, heat-pump action, and configured Heat/Cool mode are all unavailable; normal outdoor/indoor inference resumes when that hold expires.
- Set `TEMPTAMER_LOGGING_CATEGORIES["comfort_adjustment_diagnostics"]` to `True` while calibrating. It logs each zone's envelope, window-solar, fabric-solar, raw, rounded, and filtered comfort scores; target; separate window and wall outdoor temperatures; operative terms (`window_k`, `wall_k`, total k, denominator); per-room weights, shutter U/SHGC, solar terms, min/max/spread; and rejected, missing, or stale inputs. Calibration configuration is logged whenever it changes. The diagnostic stream is disabled by default and creates no Home Assistant entities.
- Each comfort-score helper shifts its zone's heat and cool `enable_outside`, `continue_until`, and `ideal_target` thresholds by **subtracting** the helper value. Positive solar warmth therefore lowers thresholds and delays heat, while a negative cold-envelope score raises thresholds and requests heat sooner. `input_number.temptamer_setpoint_adjustment` remains a conventional positive-is-warmer offset for every zone. The offsets compose, keep threshold spacing intact, and apply to zone opening, equipment demand, fan logic, dispatch planning, and idle-demand forecasts.
- HVAC selection supports `Heat`, `Cool`, `HeatCool`, `Off`, and `Manual`. `Manual` leaves both the zone switches and heatpump untouched, while `HeatCool` enforces a one-hour anti-flap delay before changing between heating and cooling.
- `PowerDay` follows the `Office` comfort mapping except downstairs, which uses the dedicated `Downstairs` scheme. The Downstairs thresholds are each `0.5C` above `DayLiving`. When `sensor.entry_goodwe_inverter_current_electricity_price` reports `0`, dining and bedroom zones using `DiningBasic` or `Bedroom` are upgraded to `DayLiving` so the house can heat soak during free power; the normal free-power offsets also apply to `Downstairs`.
- The PowerDay free-power setpoint boost is defined directly on `PowerComfortMode`: zones default to `1C` initially and `2C` from 13:00. Office uses `0.5C` then `1C`; Bed 1&2 uses `1C` then `1.25C`; and Downstairs uses `1.25C` then `2.25C`. A free-power PowerDay can promote to the later value before 13:00 when the 15-minute PV average exceeds `6 kW`; it remains promoted until free power ends. Office and dining boosts are still held until downstairs is strictly above `19C`. Cooling thresholds are unaffected.
- Outside a free-price period, PowerDay also applies each zone's initial `FreePowerSetpointBoost` when the battery reaches `95%`. This battery-triggered boost remains latched while the battery is at least `90%`; it affects the zone setpoint targets only, not the free-power fan, direct-target, or zone-priority supplements.
- During that free-price PowerDay heat soak, downstairs raises its heat `enable_outside` threshold by an additional `1C`, adds one physical fan level, and applies a configurable direct `+1C` heat-pump target boost whenever it is planned open with active heat demand. The direct target boost is `-1C` for active cooling demand. These adjustments do not apply to maintain/idle operation or export-only heat soaking.
- During a free-power `PowerDay` heat soak, if downstairs is at least `3C` colder than every open upstairs zone and still needs heat, TempTamer temporarily closes the upstairs vents, heats downstairs, and adds two fan levels. The priority lasts at least ten minutes and only releases once the gap falls below `2C`, free power ends, or downstairs reaches its target.
- `PowerOff` keeps the heatpump off until battery remaining is above `95%`, PV power is above `1 kW`, and HVAC mode is not `Off`. It then runs for at least 15 minutes using the `PowerDay` configuration from 08:00 (inclusive) to 22:00 (exclusive), or `Night` outside those hours. Once the 15-minute minimum has elapsed, it turns the heatpump off when battery remaining falls below `90%`. Selecting any other comfort mode cancels PowerOff immediately and performs the usual immediate mode-change reconciliation.
- In `Heat` mode, any enabled zone below `enable_outside` starts heating and heating continues until every enabled zone reaches `continue_until`. In `Cool` mode, any enabled zone above `enable_outside` starts cooling and cooling continues until every enabled zone drops to `ideal_target` or lower.
- After more than one hour without an active heat or cool request, a new request starts with Downstairs alone for at least three minutes when every upstairs vent is closed and Downstairs is enabled and below its continuation target. The normal zone-selection order resumes after that hold; cooling uses the equivalent lower-bound continuation check.
- When the heat pump has been off for at least 15 minutes, a new heating or cooling cycle starts at physical fan Level 1 and ramps to its calculated fan speed over the following 15 minutes.
- When a planned-open heating zone is at least `2.0C` below its `continue_until` target and the rolling five-minute maximum demand is below `14 kW`, TempTamer adds one pre-multiplier fan level immediately, then one more every 15 minutes to the configured maximum. The boost is disabled for cooling, unavailable demand readings, or demand at/above `14 kW`.
- When there is no remaining active demand but the heatpump is still running, TempTamer forecasts heat and cool calls for the next 30 minutes before entering idle. It refreshes the `weather.epping_hourly` hourly forecast at most every 15 minutes, linearly interpolates outdoor temperature, and uses forecast condition with the current sun and shutter geometry to recalculate future comfort thresholds. It uses a `0.4C`/hour heat-loss reference at `20C` indoors and `9.5C` outdoors, and a `0.4C`/hour cooling-gain reference at `20C` indoors and `30C` outdoors. If every enabled zone remains at least 30 minutes from demand, it turns the heatpump off immediately; a predicted call sooner than 30 minutes retains the existing idle ladder. Missing forecast, outdoor, or solar data falls back to that base drift rate and current adjusted thresholds. PowerDay heat-soak targets retain the existing idle behavior. Forecast shutdowns do not retain idle-backoff restart memory, while the normal 15-minute off-time fan ramp still applies.
- Each zone can override the global comfort mode with its own `input_select.temptamer_comfort_mode_*` entity. Use `Auto` to follow the global comfort mode, or choose one of the configured control scheme names (`Off`, `Night`, `DayLiving`, `Downstairs`, `DiningBasic`, or `Bedroom`) to apply that scheme directly. Downstairs uses the dedicated `Downstairs` scheme throughout Day and PowerDay modes. Bedroom 3&4 also supports a `Bathroom` override that uses `sensor.bathroom_motion_temperature` for that zone's temperature decisions while keeping the same thresholds as the bedroom scheme. Omitting the override entity or providing an unrecognized value keeps the global comfort mode in effect for that zone.

## Validation

The runtime decision logic is covered by `unittest` tests in `tests/test_temptamer.py`.
