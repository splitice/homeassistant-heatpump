# Better Temp Control Plan

## Current behavior

The current heating path has a useful safety property: once there is no strong heating demand, TempTamer stops driving the heatpump toward a high room-derived target and instead falls back to a neutral setpoint.

That behavior currently comes from three places:

- `pyscript/apps/temptamer/demand_resolver.py`
  - `resolve_equipment_demand()` raises `maintain_heat_mode=True` when zones are below `continue_until` but none are below `enable_outside`.
- `pyscript/apps/temptamer/heatpump_dispatcher.py`
  - `_requested_setpoint()` only applies the more aggressive `max(zone.enable_outside, inlet_temp + SETPOINT_DELTA_FROM_INLET)` rule for `heat_requested`.
  - For `maintain_heat_mode`, it falls through to `normalize_setpoint(snapshot.inlet_temp)`.
- `pyscript/apps/temptamer/heatpump_dispatcher.py`
  - `build_dispatch_plan()` treats `maintain_heat_mode` the same as active heating and keeps the unit in `HVAC_HEAT`.

In practice, that means the maintain phase still asks the heatpump to stay in heating mode while repeatedly moving the target to the current inlet temperature.

## Why this can still overshoot

If the heatpump is already producing heat, the inlet temperature can keep rising for a while due to compressor inertia, exchanger heat, and delayed room response. Because maintain mode currently reuses the live inlet reading as the next setpoint, the controller can accidentally ratchet the target upward every control pass.

That creates a loop like this:

1. Zone leaves `heat_requested` and enters `maintain_heat_mode`.
2. The heatpump is still hot, so inlet temperature continues climbing.
3. TempTamer sets the next target to the new, higher inlet temperature.
4. The heatpump sees no reason to back off quickly.
5. Inlet temperature climbs further until the hardware over-temperature protection turns the unit off.

So the root issue is not just the threshold choice. It is that **maintain mode is still an active heating mode, and its target is tied to a measurement that can drift upward while the equipment is still adding heat**.

## Goal

Keep the good parts of the current design:

- strong response when a zone is genuinely cold
- continued heating when zones are still meaningfully below `continue_until`
- simple, deterministic logic that remains unit-testable

But change maintain behavior so it:

- does not chase a rising inlet temperature
- backs heat delivery down in predictable steps
- transitions cleanly into idle before hardware protection needs to intervene

## Recommended control strategy

### 1. Split heating into three explicit stages

Instead of treating heating as only `heat_requested` or `maintain_heat_mode`, introduce three stages:

- `boost_heat`
  - Used when a zone is below `enable_outside` or the maximum room deficit is still large.
  - This is the current aggressive mode.
- `trim_heat`
  - Used when zones are below `continue_until`, but only by a small or moderate amount.
  - This should request heat gently and should never increase target temperature pass-to-pass.
- `idle_heat`
  - Used when no zone is below `continue_until`, or when trim heating has already run long enough without reducing room error.
  - This is effectively the existing idle concept, but entered earlier and deliberately.

This gives TempTamer a proper “soft landing” instead of jumping directly from full heat to a neutral setpoint that still tracks a moving temperature.

### 2. Preferred trim rule: ideal-vs-continue proximity scoring

For `trim_heat`, use the current inlet temperature only when the active zones are closer to `ideal_target` than to `continue_until`.

If the active zones are closer to `continue_until`, or already beyond it, release heat by moving the target 1 degree away from the current inlet temperature.

This is the preferred option because it is:

- easy to reason about
- deterministic for multiple zones
- easy to mirror for cooling
- conservative near the end of a cycle

For each heating zone that is still participating in `maintain_heat_mode`, compute two distances:

- `distance_to_ideal = abs(zone.current_temp - zone.scheme.ideal_target)`
- `distance_to_continue = abs(zone.current_temp - zone.scheme.continue_until)`

Then derive a simple trim bias:

- if the zone is closer to `ideal_target`, use a neutral trim target at the current inlet temperature
- if the zone is closer to `continue_until`, use a release target that is 1 degree below inlet temperature
- if the zone is already above `continue_until`, weight it even more strongly toward the release target

In other words:

```python
if distance_to_ideal <= distance_to_continue:
  trim_target = normalize_setpoint(snapshot.inlet_temp)
else:
  trim_target = normalize_setpoint(snapshot.inlet_temp - 1.0)
```

That gives a very intuitive behavior:

- zones nearly at ideal get gentle heat holding
- zones only just above the continue boundary start shedding heat sooner
- zones that have clearly crossed the continue threshold push harder toward idle
- if the computed heating target drops below minimum heating, treat it as fan-only circulation instead of heating

For cooling, apply the same rule in reverse:

```python
if distance_to_ideal <= distance_to_continue:
  trim_target = normalize_setpoint(snapshot.inlet_temp)
else:
  trim_target = normalize_setpoint(snapshot.inlet_temp + 1.0)
```

That means:

- zones nearly at their cooling ideal get a neutral hold target
- zones closer to or below cooling `continue_until` ask for less cooling
- zones already past the continue boundary push sooner toward cooling idle/off

### 2a. Recommended way to aggregate across multiple zones

If more than one zone is active, do not average the raw temperatures first. Instead, score each active zone individually, then aggregate the score.

Suggested approach:

- score `+1` if a heating zone is closer to `ideal_target`
- score `-1` if a heating zone is closer to `continue_until`
- subtract an additional `1` if the zone is already above `continue_until`

Then:

- total score `> 0`: use neutral inlet setpoint
- total score `<= 0`: use inlet minus 1 degree

For cooling, mirror the signs:

- score `+1` if a cooling zone is closer to `ideal_target`
- score `-1` if a cooling zone is closer to `continue_until`
- subtract an additional `1` if the zone is already below `continue_until`
- total score `> 0`: use neutral inlet setpoint
- total score `<= 0`: use inlet plus 1 degree

This keeps the logic deterministic and simple while still letting the “most finished” zones pull the equipment toward release.

### 2b. Heating fan fallback below minimum heat setpoint

If the raw requested heating trim target is below `MIN_HEAT_SETPOINT`, do not dispatch `HVAC_HEAT`.

Instead, interpret that as a circulation-only state:

- `hvac_mode = HVAC_FAN_ONLY`
- `fan_mode = FAN_LOW`
- no heating temperature command

Using the current constants, that means:

- if raw heating trim wants less than `17`, switch to fan-only low

This keeps the logic physically meaningful. Below the minimum supported heating setpoint, the controller is no longer really asking for heat; it is asking to keep air moving without adding more heat.

Cooling does not need the same special case because the trim release direction moves upward, away from stronger cooling, rather than below a heating floor.

### 2c. Caveat: keep trim lower-only for heating and upper-only for cooling

This proximity heuristic is good, but it should still obey the anti-ratcheting rule:

- in heating trim, setpoint may stay the same or decrease, but must not increase
- in cooling trim, setpoint may stay the same or increase, but must not decrease

That means the final heating trim decision should be:

```python
requested_trim = (
  normalize_setpoint(snapshot.inlet_temp)
  if total_trim_score > 0
  else normalize_setpoint(snapshot.inlet_temp - 1.0)
)
trim_target = min(current_setpoint, previous_trim_setpoint, requested_trim)
```

And the cooling mirror should be:

```python
requested_trim = (
  normalize_setpoint(snapshot.inlet_temp)
  if total_trim_score > 0
  else normalize_setpoint(snapshot.inlet_temp + 1.0)
)
trim_target = max(current_setpoint, previous_trim_setpoint, requested_trim)
```

The proximity score decides the trim direction, while the lower-only / upper-only clamp prevents drift caused by inlet lag.

### 3. Add an overshoot guard based on trim entry conditions

When entering `trim_heat`, store:

- `trim_started_at`
- `trim_entry_inlet_temp`
- `trim_entry_setpoint`

Then exit trim early if either of these is true:

- inlet temperature has risen more than `MAX_TRIM_INLET_RISE`
- trim duration exceeds `MAX_TRIM_DURATION`

Example defaults:

- `MAX_TRIM_INLET_RISE = 1.0`
- `MAX_TRIM_DURATION = 10 * 60`

This catches the exact failure mode being observed: the system continues heating even though demand is already marginal.

### 4. Use room deficit severity to decide between boost, trim, and idle

Right now, any zone below `continue_until` keeps the system heating.

A better split is:

- `boost_heat` if max room deficit to `continue_until` is large
- `trim_heat` if deficit is small but non-zero
- `idle_heat` if deficit is within a deadband

Suggested first-pass thresholds:

- `BOOST_HEAT_MIN_DEFICIT = 1.0`
- `TRIM_HEAT_MIN_DEFICIT = 0.3`
- below `0.3`, enter idle

This helps with small sensor noise and avoids re-heating for tiny deficits that the room will often recover from naturally.

### 5. Make trim heat lower-only

The most important implementation rule is:

> once the system enters trim heat, the commanded setpoint may stay the same or go down, but it must not go up until a new `boost_heat` demand appears.

That single rule prevents target chasing even if inlet readings are noisy or still climbing.

## Suggested implementation shape

## Phase 1: Minimal-risk improvement

This phase should fix the immediate issue with the smallest architectural change.

### Code changes

#### `pyscript/apps/temptamer/constants.py`

Add:

- `MAX_TRIM_INLET_RISE = 1.0`
- `MAX_TRIM_DURATION_SECONDS = 10 * 60`
- `TRIM_HEAT_MIN_DEFICIT = 0.3`
- use the existing `MIN_HEAT_SETPOINT = 17` as the trim-to-fan cutoff reference

#### `pyscript/apps/temptamer/models.py`

Extend `EquipmentDemand` with a lightweight stage marker, for example:

- `heat_stage: str = "none"`

Expected values:

- `none`
- `boost`
- `trim`

You do not need to expose full runtime state here yet.

#### `pyscript/apps/temptamer/demand_resolver.py`

Change `resolve_equipment_demand()` so heating returns:

- `heat_requested=True`, `heat_stage="boost"` for current strong demand
- `maintain_heat_mode=True`, `heat_stage="trim"` only when the max deficit to `continue_until` is above `TRIM_HEAT_MIN_DEFICIT`
- no heating demand when the remaining deficit is at or below the trim deadband

That means small residual deficits will fall through to the existing idle path instead of keeping the compressor active.

#### `pyscript/apps/temptamer/heatpump_dispatcher.py`

Refactor `_requested_setpoint()` into explicit heating branches:

- `boost`: keep current behavior
- `trim`: compute a proximity-scored, bounded, lower-only target
- `idle`: keep current idle behavior

A practical first version for heating trim is:

```python
raw_requested_trim = (
  snapshot.inlet_temp
  if total_trim_score > 0
  else snapshot.inlet_temp - 1.0
)

trim_target = min(
  current_setpoint,
  previous_trim_setpoint,
  normalize_setpoint(raw_requested_trim),
)
```

If `raw_requested_trim < MIN_HEAT_SETPOINT`, switch to:

```python
hvac_mode = HVAC_FAN_ONLY
fan_mode = FAN_LOW
setpoint = None
```

The cooling mirror should use the same score with `snapshot.inlet_temp + 1.0` in the release branch and `max(...)` for the trim clamp.

#### `pyscript/apps/temptamer/main.py`

Track trim runtime state in `RUNTIME_STATE`:

- `trim_started_at`
- `trim_entry_inlet_temp`
- `trim_entry_setpoint`

Reset these when:

- leaving heat mode
- switching back to boost heat
- turning off

Use them in dispatcher input or demand resolution so trim mode can stop early if the inlet keeps climbing.

### Expected effect

- full heat still behaves the same
- marginal demand stops earlier
- maintain mode no longer follows a rising inlet upward
- the unit reaches idle sooner, reducing hardware over-temp cutouts

## Phase 2: Better stage-aware heat control

Once Phase 1 is proven, move to a cleaner design.

### Refactor goal

Replace boolean flags with a clearer representation of equipment intent.

For example:

```python
@dataclass(frozen=True)
class EquipmentDemand:
    mode: str = "none"
    heat_stage: str = "none"
    requested_by_zones: tuple[str, ...] = field(default_factory=tuple)
    max_temperature_deficit: float = 0.0
    reason: str = ""
```

Where `mode` might be:

- `none`
- `heat`
- `cool`
- `fan_only`

And `heat_stage` might be:

- `boost`
- `trim`
- `idle`

This makes the dispatch rules much easier to reason about than overlapping booleans like `heat_requested` and `maintain_heat_mode`.

### Dispatcher logic

Create a dedicated helper such as:

- `resolve_heat_setpoint(snapshot, demand, current_setpoint, runtime_state)`

That helper should:

- allow increases only in `boost`
- never increase in `trim`
- move to `idle` if trim overshoot guard triggers
- log the exact branch and limits used

## Phase 3: Add adaptive protection

If the hardware still occasionally overshoots, add one or both of these:

### Inlet rise-rate guard

Track the inlet trend across the last few control passes.

If:

- room deficit is shrinking
- but inlet temperature is still rising quickly

then switch from `trim_heat` to idle immediately.

### Minimum recovery band before re-entering boost

After leaving trim for idle, require a larger deficit before re-entering boost heat.

Example:

- enter trim below `1.0`
- leave trim to idle below `0.3`
- only re-enter boost above `0.8`

That hysteresis prevents rapid boost/trim/idle oscillation.

## Recommended first implementation

If the goal is to improve behavior quickly with limited churn, implement this exact combination first:

1. Add a trim deadband to heating demand resolution.
2. Score each maintain/trim zone by whether it is closer to `ideal_target` or `continue_until`.
3. Use `inlet_temp` when the combined score is positive, otherwise use `inlet_temp - 1.0` for heating and `inlet_temp + 1.0` for cooling.
4. Clamp heating trim lower-only and cooling trim upper-only.
5. If the final heating trim target falls below `17`, switch to `HVAC_FAN_ONLY` with `FAN_LOW`.
6. Store trim entry inlet and duration.
7. Exit trim to idle if inlet rises by more than `1.0` or trim lasts more than 10 minutes.
8. Add tests proving trim setpoint never increases while trim remains active.

That should materially reduce temperature creep without requiring a full rewrite, while also giving the controller a clean handoff from gentle heating to pure circulation.

## Test plan

Update `tests/test_temptamer.py` with focused cases:

- `maintain_heat_mode` chooses `inlet_temp` when zones are closer to `ideal_target`
- `maintain_heat_mode` chooses `inlet_temp - 1` when zones are closer to `continue_until`
- heating trim below `17` becomes `HVAC_FAN_ONLY` with `FAN_LOW`
- mirrored cooling trim chooses `inlet_temp` or `inlet_temp + 1` from the same proximity rule
- `maintain_heat_mode` with rising inlet does not increase setpoint across passes
- small remaining deficits fall into idle instead of maintain heat
- trim heat exits to idle after exceeding `MAX_TRIM_DURATION_SECONDS`
- trim heat exits to idle after inlet rises beyond `MAX_TRIM_INLET_RISE`
- boost heat can still increase setpoint when a zone drops below `enable_outside`
- cooling behavior remains unchanged

Useful regression cases based on the current implementation:

- existing test where `maintain_heat_mode` currently returns `plan.setpoint == 20`
- new multi-pass test showing the old algorithm would go `20 -> 21 -> 22`, while the new one stays flat, steps down, or drops into fan-only

## Logging suggestions

Improve observability while tuning:

- log heat stage: `boost`, `trim`, `idle`
- log trim entry inlet and current inlet
- log whether a setpoint was held, reduced, or clamped
- log which overshoot guard caused the transition to idle

Example log shape:

```text
SETPOINT: stage=trim inlet=21.4 current_setpoint=21 requested=20 reason=release_offset
SETPOINT: stage=trim inlet=22.2 trim_entry_inlet=21.0 action=idle reason=inlet_rise_guard
```

## Summary

The best fix is to treat “maintain heat” as a controlled de-escalation stage rather than a weaker version of active heating.

The most impactful rule is simple:

- **trim heating must never raise the target temperature**

If you implement only one change, make it that one. If you implement the full Phase 1 plan, TempTamer should stop chasing a rising inlet temperature and should hand off to idle before the heatpump’s own over-temperature protection needs to intervene.
