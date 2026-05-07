# TempTamer Step 2: Comfort Mode and Zone State Resolution

## Goal
Convert Home Assistant entity state into a normalized runtime model that can answer:
- Which comfort mode is active
- Which scheme applies to each zone
- Which zones are calling for heat
- Which zones are at or above ideal target
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
    at_ideal_zones: list[str],
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
   - `at_ideal_zones`: zones with `current_temp >= ideal_target`
5. Track the current zone switch state and the timestamp of the last change for anti-flap enforcement.

## Zone state policy
- A zone may be opened when:
  - Its scheme is not `Off`, and
  - It is below `continue_until`, or it is needed as the safety-open zone
- A zone may be closed when:
  - It is in `at_ideal_zones`, and
  - It has been on for at least 5 minutes, and
  - Closing it still leaves at least one zone open
- A zone must not be toggled more than once in a 5-minute period.
- A comfort mode change is the single exception to the anti-flap rule; the resulting zone-state changes should be applied immediately.

## Safety rules
- Never issue a heat or cool request unless at least one zone is open first.
- Never allow all zone switches to become off.
- If the active comfort mode would otherwise close every zone, keep one preferred zone open:
  1. A zone still below `continue_until`
  2. Otherwise the enabled zone with the lowest temperature
  3. Otherwise the currently open zone with the oldest last-change timestamp

## Pyscript responsibilities
- Use state-triggered handlers for comfort mode changes to request an immediate control pass.
- Use a time-triggered loop every minute to recompute the snapshot because inlet temperature affects the required setpoint over time.
- On startup, read the current zone switch and climate state first, then calculate the desired state from there. Anti-flap timers reset on startup.
- Manual user changes to zone switches or climate settings are not treated as supported overrides; the next control pass should reconcile them back to the desired TempTamer state within the anti-flap limits.
