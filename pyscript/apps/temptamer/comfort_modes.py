from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

from .constants import SCHEME_BEDROOM, SCHEME_DAY_LIVING, SCHEME_DINING_BASIC, SCHEME_OFF


class StateReaderLike(Protocol):
    def get_state(self, entity_id: str) -> object | None: ...


@dataclass(frozen=True)
class ComfortMode:
    name: str

    @property
    def trigger_entity_ids(self) -> tuple[str, ...]:
        return ()

    def scheme_for_zone(self, zone_key: str, reader: StateReaderLike) -> str:
        raise NotImplementedError


@dataclass(frozen=True)
class DefaultComfortMode(ComfortMode):
    zone_schemes: Mapping[str, str]

    def scheme_for_zone(self, zone_key: str, reader: StateReaderLike) -> str:
        return self.zone_schemes.get(zone_key, SCHEME_OFF)

    def get(self, zone_key: str, default: str | None = None) -> str | None:
        return self.zone_schemes.get(zone_key, default)

    def __getitem__(self, zone_key: str) -> str:
        return self.zone_schemes[zone_key]

    def __contains__(self, zone_key: object) -> bool:
        return zone_key in self.zone_schemes

    def items(self) -> Iterable[tuple[str, str]]:
        return self.zone_schemes.items()


@dataclass(frozen=True)
class PowerComfortMode(DefaultComfortMode):
    power_price_entity_id: str
    free_power_state: str = "0"
    heat_soak_source_schemes: frozenset[str] = frozenset({SCHEME_DINING_BASIC, SCHEME_BEDROOM})
    heat_soak_scheme: str = SCHEME_DAY_LIVING

    @property
    def trigger_entity_ids(self) -> tuple[str, ...]:
        return (self.power_price_entity_id,)

    def scheme_for_zone(self, zone_key: str, reader: StateReaderLike) -> str:
        scheme_name = super().scheme_for_zone(zone_key, reader)
        if scheme_name in self.heat_soak_source_schemes and self._free_power_is_available(reader):
            return self.heat_soak_scheme
        return scheme_name

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
