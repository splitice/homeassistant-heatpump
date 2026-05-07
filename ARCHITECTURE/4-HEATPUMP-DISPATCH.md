# TempTamer Step 4: Heatpump Dispatch

## Goal
Translate abstract equipment demand into concrete climate commands for `climate.wt32_hpctrl_e8dbd0_heatpump`.

## Dispatch inputs
- `EquipmentDemand`
- Current inlet temperature
- Current heatpump HVAC mode
- Current fan mode
- Current setpoint
- Predicted open zones after zone actions complete

## HVAC mode rules
- If comfort mode is `Off`:
  - Turn the heatpump off
- Else if `fan_only_requested`:
  - Set HVAC mode to `fan_only`
- Else if `heat_requested`:
  - Set HVAC mode to `heat`
- Else:
  - Turn the heatpump off

## Setpoint logic
When `heat_requested` is true:
1. Choose the zone with the maximum deficit from its `enable_below` threshold.
2. Calculate:
   - `minimum_room_target = zone.scheme.enable_below`
   - `inlet_cap_target = inlet_temperature + 2`
3. Set requested heatpump temperature to:
   - `max(minimum_room_target, inlet_cap_target)`

When continuing in heating mode without requesting additional heat:
- Set the requested temperature to the current inlet temperature.

When in fan-only mode:
- Do not request extra heating; maintain the setpoint at the current inlet temperature unless the climate integration requires a retained value.

## Fan-speed logic
At heat start:
- Use `medium` fan when the selected requesting zone differential is greater than 4°C
- Otherwise use `low`

While already heating:
- Change `low -> medium` only when differential becomes greater than 5°C
- Change `medium -> low` only when differential becomes less than 3°C
- Do not oscillate fan speed between loop runs unless a hysteresis boundary is crossed

## Dispatch ordering
1. Ensure at least one zone is open.
2. Apply pending zone switch actions.
3. Re-check that at least one zone is open or predicted open.
4. Only then call climate services.
5. Log every dispatch decision with:
   - HVAC mode
   - Setpoint
   - Fan speed
   - Requesting zone
   - Open zones
   - Reason for action

## Example log messages
- `HEATING: Setting setpoint to 20.0 due to request for heat from Office. Zones open: Office, Dining`
- `ZONES: Opening Dining because 12.8 is below continue-until target 18.0`
- `FAN: Switching from low to medium because Office deficit is 5.4`
- `SAFETY: Prevented all zones from closing; keeping Office open`
