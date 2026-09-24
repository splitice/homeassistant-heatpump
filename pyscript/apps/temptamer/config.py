from __future__ import annotations

from datetime import time

from .constants import (
    COMFORT_MODE_DAY,
    COMFORT_MODE_OFF,
    COMFORT_MODE_NIGHT,
    COMFORT_MODE_OFFICE,
    COMFORT_MODE_POWER_DAY,
    COMFORT_MODE_POWER_OFF,
    SCHEME_BATHROOM,
    SCHEME_BEDROOM,
    SCHEME_DAY_LIVING,
    SCHEME_DOWNSTAIRS,
    SCHEME_DINING_BASIC,
    SCHEME_NIGHT,
    SCHEME_OFF,
)
from .comfort_modes import (
    DefaultComfortMode,
    FreePowerSetpointBoost,
    NightComfortMode,
    PowerComfortMode,
    PowerOffComfortMode,
    ScheduledComfortMode,
)
from .comfort_adjustments import (
    ComfortAdjustmentConfig,
    ComfortAdjustmentRoomConfig,
    ComfortAdjustmentZoneConfig,
    ConstructionProfile,
    FabricSolarConfig,
    RoomEnvelopeConfig,
    WindowConfig,
)
from .models import ControlScheme, SystemConfig, ZoneConfig

GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR = "sensor.entry_goodwe_inverter_current_electricity_price"
GOODWE_BATTERY_REMAINING_SENSOR = "sensor.goodwe_actual_battery_remaining"
GOODWE_PV_POWER_SENSOR = "sensor.goodwe_pv_power"
EAGLE_200_POWER_DEMAND_SENSOR = "sensor.eagle_200_power_demand"
# This must be a kW-valued, rolling five-minute maximum-demand sensor.  Change
# the entity ID to match the helper configured in Home Assistant.
EAGLE_200_MAX_POWER_DEMAND_5M_SENSOR = "sensor.eagle_200_max_power_demand_5m"
# A one-line runtime state file.  It lives outside the app package so updates do
# not trigger a PyScript app reload.
HEAT_DEMAND_FAN_BOOST_STATE_FILE = "/config/pyscript/temptamer_fan_boost.state"
# Persist the score convention separately from the helpers themselves.  When
# this number changes, the publisher reseeds every helper before control reads
# one of the old-convention values.
COMFORT_SCORE_SEMANTICS_VERSION = 5
COMFORT_SCORE_SEMANTICS_STATE_FILE = "/config/pyscript/temptamer_comfort_score_semantics.state"
# Fabric solar filters represent heat retained by contents and fabric, so they
# must survive PyScript reloads and Home Assistant restarts.
COMFORT_FABRIC_SOLAR_FILTER_STATE_FILE = "/config/pyscript/temptamer_fabric_solar_filters.state"
IDLE_DEMAND_FORECAST_WEATHER_ENTITY = "weather.epping_hourly"
IDLE_DEMAND_FORECAST_REFRESH_SECONDS = 15 * 60
WEATHER_FORECAST_MAX_AGE_SECONDS = 60 * 60
IDLE_DEMAND_FORECAST_HORIZON_SECONDS = 30 * 60
IDLE_DEMAND_FORECAST_STEP_SECONDS = 60
IDLE_DEMAND_FORECAST_HEAT_BASE_DRIFT_CELSIUS_PER_HOUR = 0.4
IDLE_DEMAND_FORECAST_HEAT_REFERENCE_INDOOR_CELSIUS = 20.0
IDLE_DEMAND_FORECAST_HEAT_REFERENCE_OUTDOOR_CELSIUS = 9.5
IDLE_DEMAND_FORECAST_COOL_BASE_DRIFT_CELSIUS_PER_HOUR = 0.4
IDLE_DEMAND_FORECAST_COOL_REFERENCE_INDOOR_CELSIUS = 20.0
IDLE_DEMAND_FORECAST_COOL_REFERENCE_OUTDOOR_CELSIUS = 30.0
POWERDAY_BATTERY_THRESHOLD = 95.0
POWERDAY_BATTERY_FREE_POWER_BOOST_RELEASE_THRESHOLD = 90.0
POWERDAY_EXPORT_POWER_THRESHOLD = -1.0
POWERDAY_EXPORT_AVERAGE_WINDOW_SECONDS = 10 * 60
POWERDAY_HEAT_SINK_MIN_SECONDS = 15 * 60
POWERDAY_FREE_POWER_START_TIME = time(11, 0)
POWERDAY_FREE_POWER_PV_POWER_THRESHOLD = 6.0
POWERDAY_FREE_POWER_PV_AVERAGE_WINDOW_SECONDS = 15 * 60
POWERDAY_FORECAST_DAY_START_TIME = time(11, 0)
POWERDAY_FORECAST_DAY_END_TIME = time(15, 0)
POWERDAY_FORECAST_EVENING_START_TIME = time(17, 0)
POWERDAY_FORECAST_EVENING_END_TIME = time(22, 0)
POWERDAY_DRY_FORECAST_HUMIDITY_START_TIME = time(15, 0)
POWERDAY_DRY_FORECAST_HUMIDITY_END_TIME = time(22, 0)
POWERDAY_FORECAST_FULL_DAY_MAX_CELSIUS = 18.0
POWERDAY_FORECAST_FULL_EVENING_MIN_CELSIUS = 14.0
POWERDAY_FORECAST_SUPPRESSED_DAY_MIN_CELSIUS = 21.0
POWERDAY_FORECAST_SUPPRESSED_EVENING_MIN_CELSIUS = 17.0
POWERDAY_REDUCED_HEATSOAK_MULTIPLIER = 0.5
POWERDAY_INDOOR_HUMIDITY_SENSOR = "sensor.climate_indoor_humidity"
POWERDAY_DRY_FORECAST_HUMIDITY_THRESHOLD = 60.0
POWERDAY_DRY_CONDITIONAL_INDOOR_HUMIDITY_THRESHOLD = 45.0
POWERDAY_DRY_UNCONDITIONAL_INDOOR_HUMIDITY_THRESHOLD = 50.0
POWERDAY_DRY_START_MIN_ZONE_CELSIUS = 20.0
POWERDAY_DRY_ABORT_MIN_ZONE_CELSIUS = 19.0
POWERDAY_DRY_MAX_SECONDS = 30 * 60
POWERDAY_DRY_HEAT_TRANSITION_SECONDS = 5 * 60
POWERDAY_DRY_CYCLE_STATE_FILE = "/config/pyscript/temptamer_powerday_dry.state"
POWERDAY_DOWNSTAIRS_PRIORITY_ZONE_KEY = "downstairs"
POWERDAY_DOWNSTAIRS_PRIORITY_UPSTAIRS_ZONE_KEYS = ("office", "dining", "bedroom_1_2", "bedroom_3_4")
POWERDAY_DOWNSTAIRS_PRIORITY_ENTER_GAP = 1.75
POWERDAY_DOWNSTAIRS_PRIORITY_EXIT_GAP = 1.0
POWERDAY_DOWNSTAIRS_PRIORITY_MIN_SECONDS = 10 * 60
POWERDAY_DOWNSTAIRS_PRIORITY_FAN_BOOST_LEVELS = 2
POWERDAY_DOWNSTAIRS_FREE_POWER_FAN_BOOST_LEVELS = 1
# Applied directly to the heat-pump target while downstairs is planned open
# during an active free-power PowerDay call.  The dispatcher uses the inverse
# adjustment for cooling.
POWERDAY_DOWNSTAIRS_FREE_POWER_DIRECT_TARGET_BOOST = 1.0
DOWNSTAIRS_STARTUP_PRIORITY_ZONE_KEY = "downstairs"
DOWNSTAIRS_STARTUP_PRIORITY_UPSTAIRS_ZONE_KEYS = (
    "office",
    "dining",
    "bedroom_1_2",
    "bedroom_3_4",
)
DOWNSTAIRS_STARTUP_PRIORITY_INACTIVE_SECONDS = 60 * 60
DOWNSTAIRS_STARTUP_PRIORITY_MIN_SECONDS = 3 * 60
POWEROFF_ACTIVATION_BATTERY_THRESHOLD = 95.0
POWEROFF_DEACTIVATION_BATTERY_THRESHOLD = 90.0
POWEROFF_PV_POWER_THRESHOLD = 1.0
POWEROFF_MIN_ACTIVATION_SECONDS = 15 * 60
POWEROFF_DAY_START_TIME = time(8, 0)
POWEROFF_DAY_END_TIME = time(22, 0)

# Toggle TempTamer log streams independently.  Leave operational categories on
# for normal use; enable the verbose Phase 1 comfort diagnostics only while
# inspecting or calibrating the model.
TEMPTAMER_LOGGING_CATEGORIES = {
    "lifecycle": True,
    "comfort_adjustment": True,
    "comfort_adjustment_diagnostics": False,
    "fan_boost": True,
    "powerday": True,
    "poweroff": True,
    "zones": True,
    "dispatch": True,
    "setpoint": True,
}

DEFAULT_HEAT_CONTROL_SCHEMES = {
    SCHEME_OFF: ControlScheme(name=SCHEME_OFF, enable_outside=0.0, continue_until=0.0, ideal_target=0.0),
    SCHEME_NIGHT: ControlScheme(name=SCHEME_NIGHT, enable_outside=15.0, continue_until=17.0, ideal_target=16.0),
    SCHEME_DAY_LIVING: ControlScheme(
        name=SCHEME_DAY_LIVING,
        enable_outside=18.6,
        continue_until=20.1,
        ideal_target=19.7,
    ),
    SCHEME_DOWNSTAIRS: ControlScheme(
        name=SCHEME_DOWNSTAIRS,
        enable_outside=19.1,
        continue_until=20.6,
        ideal_target=20.2,
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
    SCHEME_DOWNSTAIRS: ControlScheme(
        name=SCHEME_DOWNSTAIRS,
        enable_outside=22.0,
        continue_until=20.0,
        ideal_target=21.0,
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
    "downstairs": SCHEME_DOWNSTAIRS,
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

DEFAULT_COMFORT_MODE_POWER_DAY_MAPPING = {
    **DEFAULT_COMFORT_MODE_OFFICE_MAPPING,
    "downstairs": SCHEME_DOWNSTAIRS,
}

DEFAULT_COMFORT_MODE_OFF = DefaultComfortMode(name=COMFORT_MODE_OFF, zone_schemes=DEFAULT_COMFORT_MODE_OFF_MAPPING)
DEFAULT_COMFORT_MODE_NIGHT = NightComfortMode(name=COMFORT_MODE_NIGHT, zone_schemes=DEFAULT_COMFORT_MODE_NIGHT_MAPPING)
DEFAULT_COMFORT_MODE_POWER_DAY = PowerComfortMode(
    name=COMFORT_MODE_POWER_DAY,
    zone_schemes=DEFAULT_COMFORT_MODE_POWER_DAY_MAPPING,
    trigger_entity_ids=(
        GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR,
        GOODWE_BATTERY_REMAINING_SENSOR,
        GOODWE_PV_POWER_SENSOR,
        EAGLE_200_POWER_DEMAND_SENSOR,
    ),
    power_price_entity_id=GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR,
    downstairs_heat_start_time=POWERDAY_FREE_POWER_START_TIME,
    free_power_zone_setpoint_boosts={
        "office": FreePowerSetpointBoost(initial=0.5, later=1.0),
        "bedroom_1_2": FreePowerSetpointBoost(initial=0.25, later=0.25),
        "downstairs": FreePowerSetpointBoost(initial=1.5, later=2.25),
    },
    reduced_heat_soak_multiplier=POWERDAY_REDUCED_HEATSOAK_MULTIPLIER,
)

DEFAULT_COMFORT_MODES = {
    COMFORT_MODE_OFF: DEFAULT_COMFORT_MODE_OFF,
    COMFORT_MODE_NIGHT: DEFAULT_COMFORT_MODE_NIGHT,
    COMFORT_MODE_DAY: ScheduledComfortMode(
        name=COMFORT_MODE_DAY,
        zone_schemes=DEFAULT_COMFORT_MODE_DAY_MAPPING,
    ),
    COMFORT_MODE_OFFICE: DefaultComfortMode(name=COMFORT_MODE_OFFICE, zone_schemes=DEFAULT_COMFORT_MODE_OFFICE_MAPPING),
    COMFORT_MODE_POWER_DAY: DEFAULT_COMFORT_MODE_POWER_DAY,
    COMFORT_MODE_POWER_OFF: PowerOffComfortMode(
        name=COMFORT_MODE_POWER_OFF,
        zone_schemes=DEFAULT_COMFORT_MODE_OFF_MAPPING,
        power_day_mode=DEFAULT_COMFORT_MODE_POWER_DAY,
        night_mode=DEFAULT_COMFORT_MODE_NIGHT,
        off_mode=DEFAULT_COMFORT_MODE_OFF,
        day_start_time=POWEROFF_DAY_START_TIME,
        day_end_time=POWEROFF_DAY_END_TIME,
        trigger_entity_ids=(
            GOODWE_CURRENT_ELECTRICITY_PRICE_SENSOR,
            GOODWE_BATTERY_REMAINING_SENSOR,
            GOODWE_PV_POWER_SENSOR,
            EAGLE_200_POWER_DEMAND_SENSOR,
        ),
    ),
}

DEFAULT_ZONE_COMFORT_MODE_ENTITIES = {
    "office": "input_select.temptamer_comfort_mode_office",
    "dining": "input_select.temptamer_comfort_mode_dining",
    "downstairs": "input_select.temptamer_comfort_mode_downstairs",
    "bedroom_1_2": "input_select.temptamer_comfort_mode_bed12",
    "bedroom_3_4": "input_select.temptamer_comfort_mode_bed34",
}

DEFAULT_ZONE_COMFORT_ADJUSTMENT_ENTITIES = {
    "downstairs": "input_number.comfort_adjustment_downstairs",
    "bedroom_1_2": "input_number.comfort_adjustment_bed_1_2",
    "bedroom_3_4": "input_number.comfort_adjustment_bed_3_4",
    "office": "input_number.comfort_adjustment_office",
    "dining": "input_number.comfort_adjustment_dining",
}

GLOBAL_SETPOINT_ADJUSTMENT_ENTITY = "input_number.temptamer_setpoint_adjustment"

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
    zone_comfort_adjustment_entities=DEFAULT_ZONE_COMFORT_ADJUSTMENT_ENTITIES,
    global_setpoint_adjustment_entity=GLOBAL_SETPOINT_ADJUSTMENT_ENTITY,
)

DEFAULT_CONSTRUCTION_PROFILES = {
    "upstairs_brick_veneer": ConstructionProfile(
        key="upstairs_brick_veneer",
        wall_u_value=1.50,
        wall_filter_time_constant_seconds=3 * 60 * 60,
    ),
    "downstairs_double_brick": ConstructionProfile(
        key="downstairs_double_brick",
        wall_u_value=1.45,
        wall_filter_time_constant_seconds=8 * 60 * 60,
    ),
}

DEFAULT_COMFORT_ADJUSTMENT_CONFIG = ComfortAdjustmentConfig(
    zones=(
        ComfortAdjustmentZoneConfig(
            key="downstairs",
            output_entity_id=DEFAULT_ZONE_COMFORT_ADJUSTMENT_ENTITIES["downstairs"],
            fallback_temperature_entity_id="sensor.downstairs_zone_average_temperature",
            humidity_entity_id="sensor.rumpus_white_clock_humidity",
            rooms=(
                ComfortAdjustmentRoomConfig(
                    primary_temperature_entity_id="sensor.rumpus_average_temperature",
                    fallback_temperature_entity_id="sensor.develco_products_a_s_moszb_140_temperature_2",
                    envelope=RoomEnvelopeConfig(
                        windows=(
                            WindowConfig(
                                facade="w",
                                direct_shade_factor=0.0,
                                diffuse_shade_factor=0.10,
                            ),
                        ),
                        opaque_wall_view_factor=0.20,
                        construction_profile="downstairs_double_brick",
                        comfort_weight=0.5,
                    ),
                ),
                ComfortAdjustmentRoomConfig(
                    primary_temperature_entity_id="sensor.kitchen_average_temperature",
                    fallback_temperature_entity_id="sensor.kitchen_motion_temperature",
                    envelope=RoomEnvelopeConfig(
                        windows=(WindowConfig(facade="e"),),
                        opaque_wall_view_factor=0.20,
                        construction_profile="downstairs_double_brick",
                        comfort_weight=0.5,
                    ),
                ),
            ),
            upstairs=False,
            fabric_solar=FabricSolarConfig(
                filter_time_constant_seconds=120 * 60,
                irradiance_threshold=30.0,
                score_coefficient=0.0015,
                score_limit=0.25,
            ),
        ),
        ComfortAdjustmentZoneConfig(
            key="bedroom_1_2",
            output_entity_id=DEFAULT_ZONE_COMFORT_ADJUSTMENT_ENTITIES["bedroom_1_2"],
            fallback_temperature_entity_id="sensor.average_bed1_2_zone_temp",
            rooms=(
                ComfortAdjustmentRoomConfig(
                    primary_temperature_entity_id="sensor.bedroom_1_average_temperature",
                    envelope=RoomEnvelopeConfig(
                        windows=(WindowConfig(facade="s", cover_entity_id="cover.bed1shutters"),),
                        opaque_wall_view_factor=0.15,
                        construction_profile="upstairs_brick_veneer",
                        comfort_weight=0.5,
                    ),
                ),
                ComfortAdjustmentRoomConfig(
                    primary_temperature_entity_id="sensor.bedroom_2_average_temperature",
                    envelope=RoomEnvelopeConfig(
                        windows=(WindowConfig(facade="e", cover_entity_id="cover.bed_2_shutters"),),
                        opaque_wall_view_factor=0.15,
                        construction_profile="upstairs_brick_veneer",
                        comfort_weight=0.5,
                    ),
                ),
            ),
            upstairs=True,
            fabric_solar=FabricSolarConfig(
                filter_time_constant_seconds=45 * 60,
                irradiance_threshold=30.0,
                score_coefficient=0.0012,
                score_limit=0.20,
            ),
        ),
        ComfortAdjustmentZoneConfig(
            key="bedroom_3_4",
            output_entity_id=DEFAULT_ZONE_COMFORT_ADJUSTMENT_ENTITIES["bedroom_3_4"],
            fallback_temperature_entity_id="sensor.average_bed3_4_zone_temp",
            rooms=(
                ComfortAdjustmentRoomConfig(
                    primary_temperature_entity_id="sensor.bedroom_3_average_temperature",
                    envelope=RoomEnvelopeConfig(
                        windows=(WindowConfig(facade="e", cover_entity_id="cover.bed_3_shutters"),),
                        opaque_wall_view_factor=0.15,
                        construction_profile="upstairs_brick_veneer",
                        comfort_weight=0.4,
                    ),
                ),
                ComfortAdjustmentRoomConfig(
                    primary_temperature_entity_id="sensor.bedroom_4_average_temperature",
                    envelope=RoomEnvelopeConfig(
                        windows=(WindowConfig(facade="e", cover_entity_id="cover.bed_4_shutters"),),
                        opaque_wall_view_factor=0.15,
                        construction_profile="upstairs_brick_veneer",
                        comfort_weight=0.4,
                    ),
                ),
                ComfortAdjustmentRoomConfig(
                    primary_temperature_entity_id="sensor.bathroom_average_temperature",
                    fallback_temperature_entity_id="sensor.bathroom_motion_temperature",
                    # Bathroom façade is unknown; retain diffuse-only gain
                    # until its actual façade can be configured.
                    envelope=RoomEnvelopeConfig(
                        windows=(
                            WindowConfig(
                                facade=None,
                                direct_shade_factor=0.0,
                                diffuse_shade_factor=0.60,
                            ),
                        ),
                        opaque_wall_view_factor=0.15,
                        construction_profile="upstairs_brick_veneer",
                        comfort_weight=0.2,
                    ),
                ),
            ),
            upstairs=True,
            fabric_solar=FabricSolarConfig(
                filter_time_constant_seconds=45 * 60,
                irradiance_threshold=25.0,
                score_coefficient=0.0020,
                score_limit=0.30,
            ),
        ),
        ComfortAdjustmentZoneConfig(
            key="office",
            output_entity_id=DEFAULT_ZONE_COMFORT_ADJUSTMENT_ENTITIES["office"],
            fallback_temperature_entity_id="sensor.office_average_temperature",
            humidity_entity_id="sensor.air_monitor_lite_c705_humidity",
            rooms=(
                ComfortAdjustmentRoomConfig(
                    primary_temperature_entity_id="sensor.office_average_temperature",
                    envelope=RoomEnvelopeConfig(
                        windows=(WindowConfig(facade="s", cover_entity_id="cover.officeshutters"),),
                        opaque_wall_view_factor=0.15,
                        construction_profile="upstairs_brick_veneer",
                        comfort_weight=1.0,
                    ),
                ),
            ),
            upstairs=True,
            fabric_solar=FabricSolarConfig(
                filter_time_constant_seconds=30 * 60,
                irradiance_threshold=20.0,
                score_coefficient=0.0045,
                score_limit=0.40,
            ),
        ),
        ComfortAdjustmentZoneConfig(
            key="dining",
            output_entity_id=DEFAULT_ZONE_COMFORT_ADJUSTMENT_ENTITIES["dining"],
            fallback_temperature_entity_id="sensor.average_dining_zone_temp",
            rooms=(
                ComfortAdjustmentRoomConfig(
                    primary_temperature_entity_id="sensor.dining_average_temperature",
                    envelope=RoomEnvelopeConfig(
                        windows=(WindowConfig(facade="w", cover_entity_id="cover.kitchen_shutters"),),
                        opaque_wall_view_factor=0.15,
                        construction_profile="upstairs_brick_veneer",
                        comfort_weight=0.5,
                    ),
                ),
                ComfortAdjustmentRoomConfig(
                    primary_temperature_entity_id="sensor.lego_room_average_temperature",
                    envelope=RoomEnvelopeConfig(
                        windows=(WindowConfig(facade="w", cover_entity_id="cover.lego_shutters"),),
                        opaque_wall_view_factor=0.15,
                        construction_profile="upstairs_brick_veneer",
                        comfort_weight=0.5,
                    ),
                ),
            ),
            upstairs=True,
            fabric_solar=FabricSolarConfig(
                filter_time_constant_seconds=60 * 60,
                irradiance_threshold=25.0,
                score_coefficient=0.0025,
                score_limit=0.40,
            ),
        ),
    ),
    house_temperature_entity_id="sensor.home_temperature",
    house_humidity_entity_id="sensor.climate_indoor_humidity",
    heatpump_mode_user_entity_id="input_select.heatpump_mode_user",
    controller_hvac_mode_entity_id=DEFAULT_SYSTEM_CONFIG.hvac_mode_entity,
    climate_entity_id=DEFAULT_SYSTEM_CONFIG.climate_entity,
    outdoor_temperature_entity_id="sensor.gw3000c_outdoor_temperature",
    weather_entity_id="weather.epping",
    solar_radiation_entity_id="sensor.gw3000c_solar_radiation",
    sun_entity_id="sun.sun",
    awning_min_sun_elevation_entity_id="input_number.awning_min_sun_elevation",
    awning_exposure_half_band_entity_id="input_number.awning_exposure_half_band",
    facade_labels={"awning_n": "n", "awning_e": "e", "awning_s": "s", "awning_w": "w"},
    output_hysteresis=0.025,
    construction_profiles=DEFAULT_CONSTRUCTION_PROFILES,
)

_comfort_adjustment_trigger_entities: list[str] = []


def _add_comfort_adjustment_trigger_entity(entity_id: str | None) -> None:
    if entity_id and entity_id not in _comfort_adjustment_trigger_entities:
        _comfort_adjustment_trigger_entities.append(entity_id)


_add_comfort_adjustment_trigger_entity(DEFAULT_COMFORT_ADJUSTMENT_CONFIG.heatpump_mode_user_entity_id)
_add_comfort_adjustment_trigger_entity(DEFAULT_COMFORT_ADJUSTMENT_CONFIG.controller_hvac_mode_entity_id)
_add_comfort_adjustment_trigger_entity(DEFAULT_COMFORT_ADJUSTMENT_CONFIG.climate_entity_id)
_add_comfort_adjustment_trigger_entity(f"{DEFAULT_COMFORT_ADJUSTMENT_CONFIG.climate_entity_id}.hvac_action")
_add_comfort_adjustment_trigger_entity(DEFAULT_COMFORT_ADJUSTMENT_CONFIG.outdoor_temperature_entity_id)
_add_comfort_adjustment_trigger_entity(DEFAULT_COMFORT_ADJUSTMENT_CONFIG.weather_entity_id)
_add_comfort_adjustment_trigger_entity(f"{DEFAULT_COMFORT_ADJUSTMENT_CONFIG.weather_entity_id}.*")
_add_comfort_adjustment_trigger_entity(DEFAULT_COMFORT_ADJUSTMENT_CONFIG.solar_radiation_entity_id)
_add_comfort_adjustment_trigger_entity(f"{DEFAULT_COMFORT_ADJUSTMENT_CONFIG.sun_entity_id}.*")
_add_comfort_adjustment_trigger_entity(DEFAULT_COMFORT_ADJUSTMENT_CONFIG.awning_min_sun_elevation_entity_id)
_add_comfort_adjustment_trigger_entity(DEFAULT_COMFORT_ADJUSTMENT_CONFIG.awning_exposure_half_band_entity_id)
_add_comfort_adjustment_trigger_entity(DEFAULT_COMFORT_ADJUSTMENT_CONFIG.house_temperature_entity_id)
_add_comfort_adjustment_trigger_entity(DEFAULT_COMFORT_ADJUSTMENT_CONFIG.house_humidity_entity_id)
for comfort_adjustment_zone in DEFAULT_COMFORT_ADJUSTMENT_CONFIG.zones:
    _add_comfort_adjustment_trigger_entity(comfort_adjustment_zone.fallback_temperature_entity_id)
    _add_comfort_adjustment_trigger_entity(comfort_adjustment_zone.humidity_entity_id)
    for comfort_adjustment_room in comfort_adjustment_zone.rooms:
        _add_comfort_adjustment_trigger_entity(comfort_adjustment_room.primary_temperature_entity_id)
        _add_comfort_adjustment_trigger_entity(comfort_adjustment_room.fallback_temperature_entity_id)
        if comfort_adjustment_room.envelope is not None:
            for comfort_adjustment_window in comfort_adjustment_room.envelope.windows:
                if comfort_adjustment_window.cover_entity_id:
                    _add_comfort_adjustment_trigger_entity(f"{comfort_adjustment_window.cover_entity_id}.current_position")

COMFORT_ADJUSTMENT_TRIGGER_ENTITIES = tuple(_comfort_adjustment_trigger_entities)

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

# Keep vents open briefly after an immediate heatpump shutdown so its fan can
# finish running down with an unobstructed airflow path.
IMMEDIATE_SHUTDOWN_ZONE_CLOSE_DELAY_SECONDS = 2 * 60

_immediate_reconciliation_trigger_entities: list[str] = []


def _add_immediate_reconciliation_trigger_entity(entity_id: str | None) -> None:
    if entity_id and entity_id not in _immediate_reconciliation_trigger_entities:
        _immediate_reconciliation_trigger_entities.append(entity_id)


# Only explicit user selections may bypass zone anti-flap and idle handling.
_add_immediate_reconciliation_trigger_entity(DEFAULT_SYSTEM_CONFIG.comfort_mode_entity)
_add_immediate_reconciliation_trigger_entity(DEFAULT_SYSTEM_CONFIG.hvac_mode_entity)
for entity_id in DEFAULT_SYSTEM_CONFIG.zone_comfort_mode_entities.values():
    _add_immediate_reconciliation_trigger_entity(entity_id)

IMMEDIATE_RECONCILIATION_TRIGGER_ENTITIES = tuple(_immediate_reconciliation_trigger_entities)

_normal_recalculation_trigger_entities: list[str] = []


def _add_normal_recalculation_trigger_entity(entity_id: str | None) -> None:
    if entity_id and entity_id not in _normal_recalculation_trigger_entities:
        _normal_recalculation_trigger_entities.append(entity_id)


# Telemetry and calculated targets should retain normal zone, idle, and forecast safeguards.
for entity_id in TEMPERATURE_TRIGGER_ENTITIES:
    _add_normal_recalculation_trigger_entity(entity_id)
_add_normal_recalculation_trigger_entity(EAGLE_200_MAX_POWER_DEMAND_5M_SENSOR)
_add_normal_recalculation_trigger_entity(POWERDAY_INDOOR_HUMIDITY_SENSOR)
for entity_id in DEFAULT_SYSTEM_CONFIG.zone_comfort_adjustment_entities.values():
    _add_normal_recalculation_trigger_entity(entity_id)
_add_normal_recalculation_trigger_entity(DEFAULT_SYSTEM_CONFIG.global_setpoint_adjustment_entity)
for comfort_mode in DEFAULT_SYSTEM_CONFIG.comfort_modes.values():
    for entity_id in comfort_mode.trigger_entity_ids:
        _add_normal_recalculation_trigger_entity(entity_id)

NORMAL_RECALCULATION_TRIGGER_ENTITIES = tuple(_normal_recalculation_trigger_entities)
