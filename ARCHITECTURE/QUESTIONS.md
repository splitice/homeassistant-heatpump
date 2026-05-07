# TempTamer Open Questions

There are currently no unresolved architecture questions.

## Confirmed clarifications captured in the plan
- House temperature fallback sensor: `sensor.home_temperature`
- Bedroom zones use a dedicated `Bedroom` scheme in every non-`Off` comfort mode, currently matching `Night`
- Comfort mode changes bypass anti-flap limits so the new mode applies immediately
- Supported HVAC modes: `heat`, `cool`, `fan_only`, `dry`
- On restart, read live entity state and recalculate desired state; anti-flap timers reset
- Manual changes are unsupported for now and are reconciled back to TempTamer within anti-flap limits
