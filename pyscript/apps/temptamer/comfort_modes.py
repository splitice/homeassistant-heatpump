from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, time
from typing import ClassVar, Protocol

from .constants import SCHEME_BEDROOM, SCHEME_DAY_LIVING, SCHEME_DINING_BASIC, SCHEME_OFF


class StateReaderLike(Protocol):
    def get_state(self, entity_id: str) -> object | None: ...


@dataclass(frozen=True)
class ComfortModeSnapshotData:
    comfort_mode: str
    selected_hvac_mode: str
    inlet_temp: float
    free_power_available: bool
    heat_sink_available: bool
    free_power_later_available: bool = False
    now: datetime | None = None


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
    free_power_initial_suppliment: ClassVar[float] = 1.25
    free_power_later: ClassVar[float] = 3.0
    free_power_later_start: ClassVar[time] = time(13, 0)

    power_price_entity_id: str = ""
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
        heat_sink_available = (
            snapshot_data.heat_sink_available if snapshot_data is not None else self._free_power_is_available(reader)
        )
        if scheme_name in self.heat_soak_source_schemes and heat_sink_available:
            return self.heat_soak_scheme
        return scheme_name

    def adjust_zone(self, zone, snapshot_data: ComfortModeSnapshotData):
        if not snapshot_data.heat_sink_available or zone.scheme.name == SCHEME_OFF:
            return zone
        adjusted_continue_until = zone.scheme.continue_until + self._free_power_heating_supplement(snapshot_data)
        adjusted_scheme = replace(
            zone.scheme,
            continue_until=adjusted_continue_until,
            enable_outside=adjusted_continue_until - 0.75,
            ideal_target=adjusted_continue_until - 0.5,
        )
        return replace(zone, scheme=adjusted_scheme)

    def _free_power_heating_supplement(self, snapshot_data: ComfortModeSnapshotData) -> float:
        if snapshot_data.free_power_later_available:
            return self.free_power_later
        if snapshot_data.now is not None and snapshot_data.now.time() > self.free_power_later_start:
            return self.free_power_later
        return self.free_power_initial_suppliment

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


DefaultComforMode = DefaultComfortMode
