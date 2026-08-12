from __future__ import annotations

from datetime import time

from .constants import (
    COMFORT_MODE_DAY,
    COMFORT_MODE_OFF,
    COMFORT_MODE_NIGHT,
    COMFORT_MODE_OFFICE,
    COMFORT_MODE_POWER_DAY,
    SCHEME_BATHROOM,
    SCHEME_BEDROOM,
    SCHEME_DAY_LIVING,
    SCHEME_DINING_BASIC,
    SCHEME_NIGHT,
    SCHEME_OFF,
)
from .comfort_modes import DefaultComfortMode, NightComfortMode, PowerComfortMode, ScheduledComfortMode
from .models import ControlScheme, SystemConfig, ZoneConfig

GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR = "sensor.entry_goodwe_inverter_current_electricity_price"
GOODWE_BATTERY_REMAINING_SENSOR = "sensor.goodwe_actual_battery_remaining"
GOODWE_PV_POWER_SENSOR = "sensor.goodwe_pv_power"
EAGLE_200_POWER_DEMAND_SENSOR = "sensor.eagle_200_power_demand"
POWERDAY_BATTERY_THRESHOLD = 95.0
POWERDAY_EXPORT_POWER_THRESHOLD = -1.0
POWERDAY_EXPORT_AVERAGE_WINDOW_SECONDS = 10 * 60
POWERDAY_HEAT_SINK_MIN_SECONDS = 15 * 60
POWERDAY_FREE_POWER_START_TIME = time(11, 0)
POWERDAY_FREE_POWER_PV_POWER_THRESHOLD = 6.0
POWERDAY_FREE_POWER_PV_AVERAGE_WINDOW_SECONDS = 15 * 60

DEFAULT_HEAT_CONTROL_SCHEMES = {
    SCHEME_OFF: ControlScheme(name=SCHEME_OFF, enable_outside=0.0, continue_until=0.0, ideal_target=0.0),
    SCHEME_NIGHT: ControlScheme(name=SCHEME_NIGHT, enable_outside=15.0, continue_until=17.0, ideal_target=16.0),
    SCHEME_DAY_LIVING: ControlScheme(
        name=SCHEME_DAY_LIVING,
        enable_outside=18.6,
        continue_until=20.1,
        ideal_target=19.7,
    ),
    SCHEME_DINING_BASIC: ControlScheme(
        name=SCHEME_DINING_BASIC,
        enable_outside=14.0,
        continue_until=17.0,
        ideal_target=15.0,
    ),
    SCHEME_BEDROOM: ControlScheme(name=SCHEME_BEDROOM, enable_outside=12.0, continue_until=14.5, ideal_target=14.0),
    SCHEME_BATHROOM: ControlScheme(
        name=SCHEME_BATHROOM, 
        enable_outside=20,
        continue_until=21.5,
        ideal_target=20.5
    ),
}

DEFAULT_COOL_CONTROL_SCHEMES = {
    SCHEME_OFF: ControlScheme(name=SCHEME_OFF, enable_outside=0.0, continue_until=0.0, ideal_target=0.0),
    SCHEME_NIGHT: ControlScheme(name=SCHEME_NIGHT, enable_outside=17.0, continue_until=15.0, ideal_target=16.0),
    SCHEME_DAY_LIVING: ControlScheme(
        name=SCHEME_DAY_LIVING,
        enable_outside=21.5,
        continue_until=19.5,
        ideal_target=20.5,
    ),
    SCHEME_DINING_BASIC: ControlScheme(
        name=SCHEME_DINING_BASIC,
        enable_outside=17.0,
        continue_until=17.0,
        ideal_target=15.0,
    ),
    SCHEME_BEDROOM: ControlScheme(name=SCHEME_BEDROOM, enable_outside=16.0, continue_until=14.0, ideal_target=14.0),
    SCHEME_BATHROOM: ControlScheme(
        name=SCHEME_BATHROOM, 
        enable_outside=31,
        continue_until=26,
        ideal_target=29,
    ),
}

DEFAULT_ZONES = {
    "office": ZoneConfig(
        key="office",
        label="Office",
        sensor_entity_id="sensor.office_average_temperature",
        switch_entity_id="switch.wt32_hpctrl_e8dbd0_office",
        setpoint_delta_from_inlet=-2.0,
        min_sensor_entity_id="sensor.office_minimum_temperature",
        max_sensor_entity_id="sensor.office_maximum_temperature",
    ),
    "dining": ZoneConfig(
        key="dining",
        label="Dining",
        sensor_entity_id="sensor.average_dining_zone_temp",
        switch_entity_id="switch.wt32_hpctrl_e8dbd0_dining",
    ),
    "downstairs": ZoneConfig(
        key="downstairs",
        label="Downstairs",
        sensor_entity_id="sensor.downstairs_zone_average_temperature",
        switch_entity_id="switch.roof_wt32_hpctrl_e8dbd0_downstairs",
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
        scheme_sensor_entity_ids={
            SCHEME_BATHROOM: "sensor.bathroom_motion_temperature",
        },
    ),
}

DEFAULT_COMFORT_MODE_OFF_MAPPING: dict[str, str] = {}
for zone_key in DEFAULT_ZONES:
    DEFAULT_COMFORT_MODE_OFF_MAPPING[zone_key] = SCHEME_OFF

DEFAULT_COMFORT_MODE_NIGHT_MAPPING = {
    "office": SCHEME_NIGHT,
    "dining": SCHEME_NIGHT,
    "downstairs": SCHEME_NIGHT,
    "bedroom_1_2": SCHEME_NIGHT,
    "bedroom_3_4": SCHEME_NIGHT,
}

DEFAULT_COMFORT_MODE_DAY_MAPPING = {
    "office": SCHEME_DAY_LIVING,
    "dining": SCHEME_DAY_LIVING,
    "downstairs": SCHEME_DINING_BASIC,
    "bedroom_1_2": SCHEME_BEDROOM,
    "bedroom_3_4": SCHEME_BEDROOM,
}

DEFAULT_COMFORT_MODE_OFFICE_MAPPING = {
    "office": SCHEME_DAY_LIVING,
    "dining": SCHEME_DINING_BASIC,
    "downstairs": SCHEME_DINING_BASIC,
    "bedroom_1_2": SCHEME_BEDROOM,
    "bedroom_3_4": SCHEME_BEDROOM,
}

DEFAULT_COMFORT_MODES = {
    COMFORT_MODE_OFF: DefaultComfortMode(name=COMFORT_MODE_OFF, zone_schemes=DEFAULT_COMFORT_MODE_OFF_MAPPING),
    COMFORT_MODE_NIGHT: NightComfortMode(name=COMFORT_MODE_NIGHT, zone_schemes=DEFAULT_COMFORT_MODE_NIGHT_MAPPING),
    COMFORT_MODE_DAY: ScheduledComfortMode(
        name=COMFORT_MODE_DAY,
        zone_schemes=DEFAULT_COMFORT_MODE_DAY_MAPPING,
        scheduled_zone_schemes={
            "downstairs": ((time(16, 0), SCHEME_DAY_LIVING),),
        },
    ),
    COMFORT_MODE_OFFICE: DefaultComfortMode(name=COMFORT_MODE_OFFICE, zone_schemes=DEFAULT_COMFORT_MODE_OFFICE_MAPPING),
    COMFORT_MODE_POWER_DAY: PowerComfortMode(
        name=COMFORT_MODE_POWER_DAY,
        zone_schemes=DEFAULT_COMFORT_MODE_OFFICE_MAPPING,
        trigger_entity_ids=(
            GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR,
            GOODWE_BATTERY_REMAINING_SENSOR,
            GOODWE_PV_POWER_SENSOR,
            EAGLE_200_POWER_DEMAND_SENSOR,
        ),
        power_price_entity_id=GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR,
    ),
}

DEFAULT_ZONE_COMFORT_MODE_ENTITIES = {
    "office": "input_select.temptamer_comfort_mode_office",
    "dining": "input_select.temptamer_comfort_mode_dining",
    "downstairs": "input_select.temptamer_comfort_mode_downstairs",
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
    _add_temperature_trigger_entity(getattr(zone, "min_sensor_entity_id", None))
    _add_temperature_trigger_entity(getattr(zone, "max_sensor_entity_id", None))
    for entity_id in zone.scheme_sensor_entity_ids.values():
        _add_temperature_trigger_entity(entity_id)

TEMPERATURE_TRIGGER_ENTITIES = tuple(_temperature_trigger_entities)

_mode_trigger_entities: list[str] = []


def _add_mode_trigger_entity(entity_id: str | None) -> None:
    if entity_id and entity_id not in _mode_trigger_entities:
        _mode_trigger_entities.append(entity_id)


_add_mode_trigger_entity(DEFAULT_SYSTEM_CONFIG.comfort_mode_entity)
_add_mode_trigger_entity(DEFAULT_SYSTEM_CONFIG.hvac_mode_entity)
for entity_id in DEFAULT_SYSTEM_CONFIG.zone_comfort_mode_entities.values():
    _add_mode_trigger_entity(entity_id)
for comfort_mode in DEFAULT_SYSTEM_CONFIG.comfort_modes.values():
    for entity_id in comfort_mode.trigger_entity_ids:
        _add_mode_trigger_entity(entity_id)

MODE_TRIGGER_ENTITIES = tuple(_mode_trigger_entities)
