# TempTamer Step 3: Heat Demand and Zone Actuation

## Goal
Turn the normalized runtime state into two independent outputs:
- Zone switch actions
- Abstract equipment demand (`heat_requested`, later `cool_requested`)

This separation keeps the heatpump implementation modular for future alternate heating or cooling sources.

## Core decision model
The control loop should compute an abstract command object:

```python
EquipmentDemand(
    heat_requested: bool,
    cool_requested: bool,
    fan_only_requested: bool,
    requested_by_zone: str | None,
    max_temperature_deficit: float,
)
```

## Heat demand rules
- `heat_requested = True` when at least one enabled zone is below `enable_below`.
- Heating should continue while one or more enabled zones remain below `continue_until`.
- `requested_by_zone` should be the zone with the maximum deficit from `enable_below`.
- `max_temperature_deficit` is the largest `(enable_below - current_temp)` value across enabled zones.

## Ideal balance logic
When the loop is about to stop heating because no zone remains below `continue_until`, perform a balancing check:
- If at least one currently enabled/open zone is in `at_ideal_zones`
- And at least one currently enabled zone is still below `ideal_target`
- Then keep air moving instead of fully stopping heatpump airflow

Balancing outcome:
- If all open zones are in `at_ideal_zones`, but any zone is not yet above `continue_until`, switch equipment demand to `fan_only_requested = True`.
- Otherwise keep heating mode active but request a neutral setpoint equal to the inlet temperature. This allows circulation without calling for more heat.

## Zone actuation rules
1. Determine desired zone openness based on the active schemes and current temperatures.
2. Open zones that are below `continue_until` if anti-flap rules allow it.
3. Close zones in `at_ideal_zones` only if:
   - Their minimum-on time has elapsed, and
   - Another zone will remain open after the change.
4. If heat is required and no zone is currently open, open the highest-priority eligible zone before any heatpump command.
5. Execute at most one discretionary zone state change per loop pass to avoid rapid oscillation and to simplify logging/diagnostics.
6. A safety-required zone open is exempt from the discretionary limit when it is needed to satisfy the "at least one zone open" rule before equipment dispatch.
7. A comfort mode change is also exempt from the discretionary limit so the new mode takes effect immediately.

## Suggested priority order for zone actions
- Highest opening priority: lowest temperature relative to `enable_below`
- Highest closing priority: greatest amount at or above `ideal_target`
- Ties broken by oldest last state change timestamp

## Service boundaries
- `resolve_zone_actions(snapshot) -> list[ZoneAction]`
- `resolve_equipment_demand(snapshot, predicted_open_zones) -> EquipmentDemand`

The zone resolver should run first so equipment safety checks can use the predicted post-action zone state.
