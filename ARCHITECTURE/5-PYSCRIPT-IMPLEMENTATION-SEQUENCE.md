# TempTamer Step 5: Pyscript Implementation Sequence

## Goal
Implement TempTamer incrementally so each stage can be manually verified in Home Assistant before moving to the next.

## Sequence
1. **Create configuration and constants**
   - Add all entity ids
   - Add named control thresholds
   - Add comfort-mode mappings
2. **Build runtime state readers**
   - Read comfort mode, inlet temperature, zone temperatures, switch state
   - Add house-sensor fallback handling
   - Normalize unavailable/unknown sensor values
3. **Implement demand resolution**
   - Produce `DemandSnapshot`
   - Calculate heat, continue-heating, and ideal-balance lists
4. **Implement zone action resolution**
   - Enforce minimum open zone rule
   - Enforce 5-minute anti-flap window
   - Store per-zone last-change timestamps in pyscript state
5. **Implement heatpump dispatcher**
   - Apply HVAC mode, fan mode, and setpoint logic
   - Separate abstract demand from heatpump-specific commands
6. **Add loop triggers**
   - Every-minute periodic pass
   - Immediate pass on comfort mode change
   - Optional immediate pass on zone temperature updates
7. **Add structured logging**
   - Log each decision path
   - Log safety interventions and suppressed actions
8. **Manual validation in Home Assistant**
   - Comfort mode `Off` turns the unit off
   - `Night`, `Day`, and `Office` open the expected zones
   - Heating starts when a zone drops below the scheme minimum
   - Setpoint updates every minute with inlet temperature drift
   - Fan hysteresis behaves as specified

## Recommended pyscript structure
```text
apps/temptamer/
  __init__.py
  constants.py
  config.py
  models.py
  state_reader.py
  zone_control.py
  demand_resolver.py
  heatpump_dispatcher.py
  main.py
```

## State to persist in pyscript
- Last successful control pass timestamp
- Last zone on/off change timestamp per zone
- Last requested HVAC mode
- Last requested fan mode
- Last requested setpoint

## Acceptance criteria
- No magic numbers remain in runtime logic
- Zone toggles are rate-limited to one change per 5 minutes per zone
- There is always at least one zone open before calling for heat/cool
- Missing zone sensors fall back to the house temperature sensor
- Logs explain why TempTamer changed zone or heatpump state
- Heatpump logic can be replaced later by another dispatcher that consumes the same abstract demand object
