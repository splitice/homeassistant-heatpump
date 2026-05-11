from __future__ import annotations

from .constants import (
    COMFORT_MODE_DAY,
    COMFORT_MODE_OFF,
    COMFORT_MODE_NIGHT,
    COMFORT_MODE_OFFICE,
    SCHEME_BEDROOM,
    SCHEME_DAY_LIVING,
    SCHEME_DINING_BASIC,
    SCHEME_NIGHT,
    SCHEME_OFF,
)
from .models import ControlScheme, SystemConfig, ZoneConfig

DEFAULT_HEAT_CONTROL_SCHEMES = {
    SCHEME_OFF: ControlScheme(name=SCHEME_OFF, enable_outside=0.0, continue_until=0.0, ideal_target=0.0),
    SCHEME_NIGHT: ControlScheme(name=SCHEME_NIGHT, enable_outside=15.0, continue_until=17.0, ideal_target=16.0),
    SCHEME_DAY_LIVING: ControlScheme(
        name=SCHEME_DAY_LIVING,
        enable_outside=20.0,
        continue_until=22.0,
        ideal_target=21.0,
    ),
    SCHEME_DINING_BASIC: ControlScheme(
        name=SCHEME_DINING_BASIC,
        enable_outside=14.0,
        continue_until=17.0,
        ideal_target=15.0,
    ),
    SCHEME_BEDROOM: ControlScheme(name=SCHEME_BEDROOM, enable_outside=14.0, continue_until=16.0, ideal_target=14.0),
}


def _mirror_heat_scheme_for_cooling(heat_scheme: ControlScheme) -> ControlScheme:
    return ControlScheme(
        name=heat_scheme.name,
        enable_outside=heat_scheme.ideal_target + (heat_scheme.ideal_target - heat_scheme.enable_outside),
        continue_until=heat_scheme.ideal_target + (heat_scheme.ideal_target - heat_scheme.continue_until),
        ideal_target=heat_scheme.ideal_target,
    )


DEFAULT_COOL_CONTROL_SCHEMES = {
    scheme_name: _mirror_heat_scheme_for_cooling(heat_scheme)
    for scheme_name, heat_scheme in DEFAULT_HEAT_CONTROL_SCHEMES.items()
}

DEFAULT_ZONES = {
    "office": ZoneConfig(
        key="office",
        label="Office",
        sensor_entity_id="sensor.office_average_temperature",
        switch_entity_id="switch.wt32_hpctrl_e8dbd0_office",
    ),
    "dining": ZoneConfig(
        key="dining",
        label="Dining",
        sensor_entity_id="sensor.average_dining_zone_temp",
        switch_entity_id="switch.wt32_hpctrl_e8dbd0_dining",
    ),
    "bedroom_1_2": ZoneConfig(
        key="bedroom_1_2",
        label="Bedroom 1&2",
        sensor_entity_id="sensor.average_bed1_2_zone_temp",
        switch_entity_id="switch.wt32_hpctrl_e8dbd0_bed_12",
    ),
    "bedroom_3_4": ZoneConfig(
        key="bedroom_3_4",
        label="Bedroom 3&4",
        sensor_entity_id="sensor.average_bed3_4_zone_temp",
        switch_entity_id="switch.wt32_hpctrl_e8dbd0_bed_34",
    ),
}

_comfort_mode_off_mapping: dict[str, str] = {}
for zone_key in DEFAULT_ZONES:
    _comfort_mode_off_mapping[zone_key] = SCHEME_OFF

DEFAULT_COMFORT_MODES = {
    COMFORT_MODE_OFF: _comfort_mode_off_mapping,
    COMFORT_MODE_NIGHT: {
        "office": SCHEME_NIGHT,
        "dining": SCHEME_NIGHT,
        "bedroom_1_2": SCHEME_NIGHT,
        "bedroom_3_4": SCHEME_NIGHT,
    },
    COMFORT_MODE_DAY: {
        "office": SCHEME_DAY_LIVING,
        "dining": SCHEME_DAY_LIVING,
        "bedroom_1_2": SCHEME_BEDROOM,
        "bedroom_3_4": SCHEME_BEDROOM,
    },
    COMFORT_MODE_OFFICE: {
        "office": SCHEME_DAY_LIVING,
        "dining": SCHEME_DINING_BASIC,
        "bedroom_1_2": SCHEME_BEDROOM,
        "bedroom_3_4": SCHEME_BEDROOM,
    },
}

DEFAULT_ZONE_COMFORT_MODE_ENTITIES = {
    "office": "input_select.temptamer_comfort_mode_office",
    "dining": "input_select.temptamer_comfort_mode_dining",
    "bedroom_1_2": "input_select.temptamer_comfort_mode_bed12",
    "bedroom_3_4": "input_select.temptamer_comfort_mode_bed34",
}

DEFAULT_SYSTEM_CONFIG = SystemConfig(
    house_temperature_sensor="sensor.home_temperature",
    comfort_mode_entity="input_select.temptamer_comfort_mode",
    hvac_mode_entity="input_select.temptamer_hvac_mode",
    climate_entity="climate.wt32_hpctrl_e8dbd0_heatpump",
    zones=DEFAULT_ZONES,
    zone_comfort_mode_entities=DEFAULT_ZONE_COMFORT_MODE_ENTITIES,
    comfort_modes=DEFAULT_COMFORT_MODES,
    heat_control_schemes=DEFAULT_HEAT_CONTROL_SCHEMES,
    cool_control_schemes=DEFAULT_COOL_CONTROL_SCHEMES,
)

_temperature_trigger_entities: list[str] = []


def _add_temperature_trigger_entity(entity_id: str | None) -> None:
    if entity_id and entity_id not in _temperature_trigger_entities:
        _temperature_trigger_entities.append(entity_id)


_add_temperature_trigger_entity(DEFAULT_SYSTEM_CONFIG.house_temperature_sensor)
_add_temperature_trigger_entity(DEFAULT_SYSTEM_CONFIG.climate_entity)
for zone in DEFAULT_SYSTEM_CONFIG.zones.values():
    _add_temperature_trigger_entity(zone.sensor_entity_id)

TEMPERATURE_TRIGGER_ENTITIES = tuple(_temperature_trigger_entities)

_mode_trigger_entities = [
    DEFAULT_SYSTEM_CONFIG.comfort_mode_entity,
    DEFAULT_SYSTEM_CONFIG.hvac_mode_entity,
    *DEFAULT_SYSTEM_CONFIG.zone_comfort_mode_entities.values(),
]

MODE_TRIGGER_ENTITIES = tuple(_mode_trigger_entities)
