from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, time
from typing import ClassVar, Protocol

from .constants import (
    COMFORT_MODE_POWER_DAY,
    CONTROL_HVAC_MODE_HEAT,
    POWERDAY_HEATSOAK_FULL,
    POWERDAY_HEATSOAK_REDUCED,
    POWERDAY_HEATSOAK_SUPPRESSED,
    SCHEME_BEDROOM,
    SCHEME_DAY_LIVING,
    SCHEME_DINING_BASIC,
    SCHEME_DOWNSTAIRS,
    SCHEME_NIGHT,
    SCHEME_OFF,
)


class StateReaderLike(Protocol):
    def get_state(self, entity_id: str) -> object | None: ...


@dataclass(frozen=True)
class ComfortModeSnapshotData:
    comfort_mode: str
    selected_hvac_mode: str
    inlet_temp: float
    free_power_available: bool
    heat_sink_available: bool
    battery_free_power_boost_available: bool = False
    free_power_later_available: bool = False
    downstairs_temp: float | None = None
    poweroff_active: bool = False
    now: datetime | None = None
    free_power_heat_soak_level: str = POWERDAY_HEATSOAK_FULL
    surplus_heat_sink_available: bool = False


@dataclass(frozen=True)
class FreePowerSetpointBoost:
    """The initial and 13:00 setpoint increases for one PowerDay zone."""

    initial: float = 1.0
    later: float = 2.0


@dataclass(frozen=True)
class ComfortMode:
    name: str

    def scheme_for_zone(
        self,
        zone_key: str,
        reader: StateReaderLike,
        snapshot_data: ComfortModeSnapshotData | None = None,
    ) -> str:
        raise NotImplementedError

    def adjust_zone(self, zone, snapshot_data: ComfortModeSnapshotData):
        return zone


@dataclass(frozen=True)
class DefaultComfortMode(ComfortMode):
    heat_start_medium_fan_differential: ClassVar[float] = 2.5
    low_to_medium_fan_differential: ClassVar[float] = 3.0
    medium_to_low_fan_differential: ClassVar[float] = 1.25

    zone_schemes: Mapping[str, str]
    trigger_entity_ids: tuple[str, ...] = ()

    def scheme_for_zone(
        self,
        zone_key: str,
        reader: StateReaderLike,
        snapshot_data: ComfortModeSnapshotData | None = None,
    ) -> str:
        return self.zone_schemes.get(zone_key, SCHEME_OFF)

    def fan_speed_level(
        self,
        temperature_differential: float,
        open_zone_count: int,
        *,
        current_speed_level: int | None = None,
        starting: bool = False,
        free_power_available: bool = False,
    ) -> int:
        return self._fan_speed_level_from_thresholds(
            temperature_differential,
            open_zone_count,
            current_speed_level=current_speed_level,
            starting=starting,
            heat_start_medium_fan_differential=self.heat_start_medium_fan_differential,
            low_to_medium_fan_differential=self.low_to_medium_fan_differential,
            medium_to_low_fan_differential=self.medium_to_low_fan_differential,
        )

    @staticmethod
    def _fan_speed_level_from_thresholds(
        temperature_differential: float,
        open_zone_count: int,
        *,
        current_speed_level: int | None,
        starting: bool,
        heat_start_medium_fan_differential: float,
        low_to_medium_fan_differential: float,
        medium_to_low_fan_differential: float,
    ) -> int:
        current_base_level = 2 if current_speed_level is not None and current_speed_level >= 2 else 1
        if starting or current_speed_level is None:
            base_level = 2 if temperature_differential > heat_start_medium_fan_differential else 1
        elif current_base_level >= 2:
            base_level = 1 if temperature_differential < medium_to_low_fan_differential else 2
        else:
            base_level = 2 if temperature_differential > low_to_medium_fan_differential else 1

        if open_zone_count >= 4:
            return base_level * 3
        if open_zone_count >= 3:
            return base_level * 2
        return base_level

    def get(self, zone_key: str, default: str | None = None) -> str | None:
        return self.zone_schemes.get(zone_key, default)

    def __getitem__(self, zone_key: str) -> str:
        return self.zone_schemes[zone_key]

    def __contains__(self, zone_key: object) -> bool:
        return zone_key in self.zone_schemes

    def items(self) -> Iterable[tuple[str, str]]:
        return self.zone_schemes.items()


@dataclass(frozen=True)
class NightComfortMode(DefaultComfortMode):
    heat_start_medium_fan_differential: ClassVar[float] = 2.5
    low_to_medium_fan_differential: ClassVar[float] = 6.0
    medium_to_low_fan_differential: ClassVar[float] = 3.0


@dataclass(frozen=True)
class ScheduledComfortMode(DefaultComfortMode):
    scheduled_zone_schemes: Mapping[str, tuple[tuple[time, str], ...]] = field(default_factory=dict)

    def scheme_for_zone(
        self,
        zone_key: str,
        reader: StateReaderLike,
        snapshot_data: ComfortModeSnapshotData | None = None,
    ) -> str:
        scheme_name = super().scheme_for_zone(zone_key, reader, snapshot_data)
        if snapshot_data is None or snapshot_data.now is None:
            return scheme_name

        current_time = snapshot_data.now.time()
        for start_time, scheduled_scheme_name in self.scheduled_zone_schemes.get(zone_key, ()):
            if current_time >= start_time:
                scheme_name = scheduled_scheme_name
        return scheme_name


@dataclass(frozen=True)
class PowerComfortMode(DefaultComfortMode):
    free_power_heat_start_medium_fan_differential: ClassVar[float] = 1.5
    free_power_low_to_medium_fan_differential: ClassVar[float] = 2.5
    free_power_medium_to_low_fan_differential: ClassVar[float] = 1.25
    free_power_downstairs_zone_key: ClassVar[str] = "downstairs"
    free_power_downstairs_enable_outside_supplement: ClassVar[float] = 1.0
    free_power_downstairs_temperature_threshold: ClassVar[float] = 19.0
    free_power_downstairs_gated_zone_keys: ClassVar[frozenset[str]] = frozenset({"office", "dining"})

    power_price_entity_id: str = ""
    downstairs_heat_start_time: time = time(11, 0)
    free_power_later_start_time: time = time(13, 0)
    # This is immutable, so a direct default is safe.  Avoiding a class-valued
    # default_factory also keeps PyScript's generated dataclass initializer
    # from trying to call its EvalLocalVar wrapper at runtime.
    free_power_setpoint_boost: FreePowerSetpointBoost = FreePowerSetpointBoost()
    free_power_zone_setpoint_boosts: Mapping[str, FreePowerSetpointBoost] = field(default_factory=dict)
    reduced_heat_soak_multiplier: float = 0.5
    free_power_state: str = "0"
    heat_soak_source_schemes: frozenset[str] = frozenset({SCHEME_DINING_BASIC, SCHEME_BEDROOM})
    heat_soak_scheme: str = SCHEME_DAY_LIVING

    def scheme_for_zone(
        self,
        zone_key: str,
        reader: StateReaderLike,
        snapshot_data: ComfortModeSnapshotData | None = None,
    ) -> str:
        scheme_name = self.zone_schemes.get(zone_key, SCHEME_OFF)
        if (
            zone_key == self.free_power_downstairs_zone_key
            and snapshot_data is not None
            and snapshot_data.selected_hvac_mode == CONTROL_HVAC_MODE_HEAT
            and snapshot_data.now is not None
            and snapshot_data.now.time() < self.downstairs_heat_start_time
        ):
            return SCHEME_NIGHT
        full_heat_sink_available = self._full_heat_sink_available(snapshot_data, reader)
        if scheme_name in self.heat_soak_source_schemes and full_heat_sink_available:
            return self.heat_soak_scheme
        return scheme_name

    def _full_heat_sink_available(
        self,
        snapshot_data: ComfortModeSnapshotData | None,
        reader: StateReaderLike,
    ) -> bool:
        if snapshot_data is None:
            return self._free_power_is_available(reader)
        if snapshot_data.surplus_heat_sink_available:
            return True
        return (
            snapshot_data.free_power_available
            and snapshot_data.free_power_heat_soak_level == POWERDAY_HEATSOAK_FULL
        )

    def adjust_zone(self, zone, snapshot_data: ComfortModeSnapshotData):
        if zone.scheme.name == SCHEME_OFF:
            return zone
        independent_heat_sink_available = (
            snapshot_data.surplus_heat_sink_available
            or snapshot_data.battery_free_power_boost_available
        )
        if (
            snapshot_data.free_power_available
            and snapshot_data.free_power_heat_soak_level == POWERDAY_HEATSOAK_REDUCED
            and not independent_heat_sink_available
        ):
            return self._adjust_zone_for_reduced_heat_soak(zone)
        if (
            snapshot_data.free_power_available
            and snapshot_data.free_power_heat_soak_level == POWERDAY_HEATSOAK_SUPPRESSED
            and not independent_heat_sink_available
        ):
            return zone
        if (
            zone.key in self.free_power_downstairs_gated_zone_keys
            and (
                snapshot_data.downstairs_temp is None
                or snapshot_data.downstairs_temp <= self.free_power_downstairs_temperature_threshold
            )
        ):
            return zone
        if not snapshot_data.heat_sink_available and not independent_heat_sink_available:
            return zone
        adjusted_continue_until = zone.scheme.continue_until + self._free_power_setpoint_boost(
            zone.key,
            snapshot_data,
        )
        adjusted_enable_outside = adjusted_continue_until - 0.75
        if (
            snapshot_data.comfort_mode == COMFORT_MODE_POWER_DAY
            and snapshot_data.free_power_available
            and snapshot_data.free_power_heat_soak_level == POWERDAY_HEATSOAK_FULL
            and zone.key == self.free_power_downstairs_zone_key
        ):
            adjusted_enable_outside += self.free_power_downstairs_enable_outside_supplement
        adjusted_scheme = replace(
            zone.scheme,
            continue_until=adjusted_continue_until,
            enable_outside=adjusted_enable_outside,
            ideal_target=adjusted_continue_until - 0.5,
        )
        return replace(zone, scheme=adjusted_scheme)

    def _adjust_zone_for_reduced_heat_soak(self, zone):
        boost = self.free_power_zone_setpoint_boosts.get(
            zone.key,
            self.free_power_setpoint_boost,
        )
        adjustment = boost.initial * self.reduced_heat_soak_multiplier
        return replace(
            zone,
            scheme=replace(
                zone.scheme,
                enable_outside=zone.scheme.enable_outside + adjustment,
                continue_until=zone.scheme.continue_until + adjustment,
                ideal_target=zone.scheme.ideal_target + adjustment,
            ),
        )

    def _free_power_setpoint_boost(
        self,
        zone_key: str,
        snapshot_data: ComfortModeSnapshotData,
    ) -> float:
        boost = self.free_power_zone_setpoint_boosts.get(
            zone_key,
            self.free_power_setpoint_boost,
        )
        if snapshot_data.battery_free_power_boost_available and (
            not snapshot_data.free_power_available
            or snapshot_data.free_power_heat_soak_level != POWERDAY_HEATSOAK_FULL
        ):
            return boost.initial
        if snapshot_data.free_power_later_available or (
            snapshot_data.now is not None and snapshot_data.now.time() >= self.free_power_later_start_time
        ):
            return boost.later
        return boost.initial

    def fan_speed_level(
        self,
        temperature_differential: float,
        open_zone_count: int,
        *,
        current_speed_level: int | None = None,
        starting: bool = False,
        free_power_available: bool = False,
    ) -> int:
        if not free_power_available:
            return DefaultComfortMode.fan_speed_level(
                self,
                temperature_differential,
                open_zone_count,
                current_speed_level=current_speed_level,
                starting=starting,
                free_power_available=free_power_available,
            )
        return self._fan_speed_level_from_thresholds(
            temperature_differential,
            open_zone_count,
            current_speed_level=current_speed_level,
            starting=starting,
            heat_start_medium_fan_differential=self.free_power_heat_start_medium_fan_differential,
            low_to_medium_fan_differential=self.free_power_low_to_medium_fan_differential,
            medium_to_low_fan_differential=self.free_power_medium_to_low_fan_differential,
        )

    def free_power_is_available(self, reader: StateReaderLike) -> bool:
        return self._free_power_is_available(reader)

    def _free_power_is_available(self, reader: StateReaderLike) -> bool:
        price_state = reader.get_state(self.power_price_entity_id)
        price_state_text = str(price_state).strip()
        if price_state_text == self.free_power_state:
            return True
        try:
            return float(price_state_text) == float(self.free_power_state)
        except (TypeError, ValueError):
            return False


@dataclass(frozen=True)
class PowerOffComfortMode(DefaultComfortMode):
    """Use another comfort mode while the PowerOff activation is latched."""

    # PyScript evaluates dataclass field annotations at runtime and does not
    # support ``Type | None`` expressions there.
    power_day_mode: object = None
    night_mode: object = None
    off_mode: object = None
    day_start_time: time = time(8, 0)
    day_end_time: time = time(22, 0)

    def effective_mode(self, snapshot_data: ComfortModeSnapshotData) -> DefaultComfortMode:
        if self.power_day_mode is None or self.night_mode is None or self.off_mode is None:
            raise ValueError("PowerOffComfortMode requires PowerDay, Night, and Off mode definitions")
        if not snapshot_data.poweroff_active:
            return self.off_mode
        if snapshot_data.now is None:
            return self.power_day_mode

        current_time = snapshot_data.now.time()
        if self.day_start_time <= current_time < self.day_end_time:
            return self.power_day_mode
        return self.night_mode


DefaultComforMode = DefaultComfortMode
