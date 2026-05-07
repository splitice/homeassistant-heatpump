# TempTamer Step 2: Comfort Mode and Zone State Resolution

## Goal
Convert Home Assistant entity state into a normalized runtime model that can answer:
- Which comfort mode is active
- Which scheme applies to each zone
- Which zones are calling for heat
- Which zones are above ideal target
- Which zones are allowed to change state right now

## Runtime state objects
```python
ZoneRuntimeState(
    key: str,
    current_temp: float,
    scheme: ControlScheme,
    is_enabled_by_mode: bool,
    switch_is_on: bool,
    last_switch_change: datetime | None,
)

DemandSnapshot(
    comfort_mode: str,
    inlet_temp: float,
    zones: dict[str, ZoneRuntimeState],
    heat_calling_zones: list[str],
    continue_heating_zones: list[str],
    below_ideal_zones: list[str],
    above_ideal_zones: list[str],
)
```

## Resolution rules
1. Read `input_select.temptamer_comfort_mode`.
2. If the comfort mode is `Off`, produce a snapshot with no heating demand.
3. For each configured zone:
   - Select the control scheme from the active comfort mode mapping.
   - Resolve the zone temperature from its configured sensor.
   - If the sensor is missing, unavailable, or unknown, use the house temperature sensor.
   - If the scheme is `Off`, mark the zone as not enabled by mode.
4. Build derived demand lists:
   - `heat_calling_zones`: zones with `current_temp < enable_below`
   - `continue_heating_zones`: zones with `current_temp < continue_until`
   - `below_ideal_zones`: zones with `current_temp < ideal_target`
   - `above_ideal_zones`: zones with `current_temp > ideal_target`
5. Track the current zone switch state and the timestamp of the last change for anti-flap enforcement.

## Zone state policy
- A zone may be opened when:
  - Its scheme is not `Off`, and
  - It is below `continue_until`, or it is needed as the safety-open zone
- A zone may be closed when:
  - It is above `ideal_target`, and
  - It has been on for at least 5 minutes, and
  - Closing it still leaves at least one zone open
- A zone must not be toggled more than once in a 5-minute period.

## Safety rules
- Never issue a heat or cool request unless at least one zone is open first.
- Never allow all zone switches to become off.
- If the active comfort mode would otherwise close every zone, keep one preferred zone open:
  1. A zone still below `continue_until`
  2. Otherwise the enabled zone with the lowest temperature
  3. Otherwise the currently open zone with the oldest last-change timestamp

## Pending design decision
- Comfort mode changes should trigger an immediate control pass, but whether that pass may override the normal 5-minute anti-flap restriction is intentionally left open until `ARCHITECTURE/QUESTIONS.md` question 3 is answered.

## Pyscript responsibilities
- Use state-triggered handlers for comfort mode changes to request an immediate control pass.
- Use a time-triggered loop every minute to recompute the snapshot because inlet temperature affects the required setpoint over time.
