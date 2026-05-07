# TempTamer Open Questions

1. What is the entity id for the house temperature sensor used when a zone sensor is missing or unavailable?
2. Should Bedroom 1&2 and Bedroom 3&4 remain permanently off for the initial implementation, or should future comfort modes for them be planned now?
3. When comfort mode changes, should TempTamer immediately force zone changes even if a zone is inside its 5-minute anti-flap window, or should anti-flap always win?
4. Does the climate entity accept the exact lowercase Home Assistant HVAC/fan mode service values expected by pyscript, especially for `fan_only`?
5. If Home Assistant or pyscript restarts, should last zone-change timestamps be restored from persisted state, or is it acceptable to reset anti-flap timers on startup?
6. Should manual user changes to zone switches or climate settings be treated as temporary overrides, or should the next control-loop pass always reconcile back to TempTamer's desired state?
