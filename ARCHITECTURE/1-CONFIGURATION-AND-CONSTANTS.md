# TempTamer Step 1: Configuration and Constants

## Goal
Create a small configuration layer that defines all entities, zone metadata, comfort mode mappings, and control constants with no magic numbers in the control logic.

## Scope
- Define the TempTamer integration name and logger prefix.
- Represent each zone as a single configuration object containing:
  - Human readable name
  - Temperature sensor entity id
  - Zone switch entity id
  - Fallback sensor behavior
- Represent each comfort mode as a mapping from zone name to control scheme.
- Represent each control scheme as named temperature constants:
  - `enable_below`
  - `continue_until`
  - `ideal_target`
- Define loop and anti-flap constants:
  - Main control interval: 60 seconds
  - Minimum zone state hold time: 5 minutes
  - Minimum delay between zone state changes: 5 minutes
  - Minimum open zones while heating/cooling: 1
- Define fan-speed hysteresis constants:
  - Heat-start threshold for medium fan: differential greater than 4°C
  - Low-to-medium threshold: differential greater than 5°C
  - Medium-to-low threshold: differential less than 3°C
- Define setpoint constants:
  - Maximum requested setpoint increase above inlet temperature: 2°C
  - Minimum requested heat setpoint: 17°C
  - Maximum requested heat setpoint: 25°C
  - Requested heat setpoints must be whole integers rounded up to the nearest whole degree

## Proposed module layout
- `apps/temptamer/constants.py`
  - Entity ids
  - Control scheme constants
  - Fan and loop constants
- `apps/temptamer/config.py`
  - Zone definitions
  - Comfort mode to zone-scheme assignment

## Data model
```python
ControlScheme(
    name: str,
    enable_below: float,
    continue_until: float,
    ideal_target: float,
)

ZoneConfig(
    key: str,
    label: str,
    sensor_entity_id: str | None,
    switch_entity_id: str,
)

SystemConfig(
    house_temperature_sensor: str,
    inlet_temperature_sensor: str,
    comfort_mode_entity: str,
    climate_entity: str,
    zones: dict[str, ZoneConfig],
    comfort_modes: dict[str, dict[str, str]],
)
```

## Required comfort mode mapping
- `Off`
  - All zones => `Off`
- `Night`
  - `Office` => `Night`
  - `Dining` => `Night`
  - `Bedroom 1&2` => `Bedroom`
  - `Bedroom 3&4` => `Bedroom`
- `Day`
  - `Office` => `DayLiving`
  - `Dining` => `DayLiving`
  - `Bedroom 1&2` => `Bedroom`
  - `Bedroom 3&4` => `Bedroom`
- `Office`
  - `Office` => `DayLiving`
  - `Dining` => `DiningBasic`
  - `Bedroom 1&2` => `Bedroom`
  - `Bedroom 3&4` => `Bedroom`

## Notes
- `sensor.home_temperature` is the house temperature sensor used for any zone whose dedicated sensor is missing, unknown, or unavailable.
- `Bedroom` is a dedicated bedroom control scheme that is currently a direct copy of `Night`.
- The heatpump dispatcher should use the Home Assistant HVAC mode values `heat`, `cool`, `fan_only`, and `dry`.
