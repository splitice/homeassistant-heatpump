from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .comfort_modes import ComfortMode, DefaultComfortMode


@dataclass(frozen=True)
class ControlScheme:
    name: str
    enable_outside: float
    continue_until: float
    ideal_target: float


@dataclass(frozen=True)
class ZoneConfig:
    key: str
    label: str
    sensor_entity_id: str | None
    switch_entity_id: str
    setpoint_delta_from_inlet: float = -1.0
    scheme_sensor_entity_ids: dict[str, str] = field(default_factory=dict)
    min_sensor_entity_id: str | None = None
    max_sensor_entity_id: str | None = None


@dataclass(frozen=True)
class SystemConfig:
    house_temperature_sensor: str
    comfort_mode_entity: str
    hvac_mode_entity: str
    climate_entity: str
    zones: dict[str, ZoneConfig]
    zone_comfort_mode_entities: dict[str, str]
    comfort_modes: dict[str, ComfortMode]
    heat_control_schemes: dict[str, ControlScheme]
    cool_control_schemes: dict[str, ControlScheme]


@dataclass(frozen=True)
class ZoneRuntimeState:
    key: str
    current_temp: float
    min_temp: float | None
    max_temp: float | None
    setpoint_delta_from_inlet: float
    scheme: ControlScheme
    cool_scheme: ControlScheme
    applied_comfort_mode: str
    is_enabled_by_mode: bool
    switch_is_on: bool
    last_switch_change: datetime | None


@dataclass(frozen=True)
class DemandSnapshot:
    comfort_mode: str
    comfort_mode_behavior: DefaultComfortMode
    selected_hvac_mode: str
    inlet_temp: float
    free_power_available: bool
    zones: dict[str, ZoneRuntimeState]
    heat_calling_zones: tuple[str, ...]
    continue_heating_zones: tuple[str, ...]
    below_ideal_zones: tuple[str, ...]
    at_ideal_zones: tuple[str, ...]
    cool_calling_zones: tuple[str, ...]
    continue_cooling_zones: tuple[str, ...]
    above_ideal_zones: tuple[str, ...]
    at_or_below_ideal_zones: tuple[str, ...]


@dataclass(frozen=True)
class ZoneAction:
    zone_key: str
    turn_on: bool
    reason: str
    safety_required: bool = False
    discretionary: bool = True


@dataclass(frozen=True)
class EquipmentDemand:
    heat_requested: bool = False
    cool_requested: bool = False
    fan_only_requested: bool = False
    maintain_heat_mode: bool = False
    maintain_cool_mode: bool = False
    requested_by_zones: tuple[str, ...] = field(default_factory=tuple)
    max_temperature_deficit: float = 0.0
    reason: str = ""


@dataclass(frozen=True)
class DispatchPlan:
    turn_off: bool = False
    idle: bool = False
    idle_shutdown: bool = False
    hvac_mode: str | None = None
    fan_mode: str | None = None
    setpoint: int | float | None = None
    idle_heat_step: int | None = None
    idle_heat_step_changed: bool = False
    requested_by_zones: tuple[str, ...] = field(default_factory=tuple)
    open_zones: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""
