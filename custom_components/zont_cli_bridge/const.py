"""Constants for the companion integration."""
DOMAIN = "zont_cli_bridge"
CONF_BRIDGE_ENTITY = "bridge_entity"
CONF_PRIMARY_HELPER = "primary_helper"
CONF_FALLBACK_HELPER = "fallback_helper"
CONF_GOAL_HELPER = "goal_helper"
CONF_PRIMARY_SOURCE = "primary_temperature_entity"
CONF_FALLBACK_SOURCE = "fallback_temperature_entity"
CONF_PHYSICAL_FALLBACK = "physical_fallback"
PHYSICAL_FALLBACK = "zont:physical"
DEFAULTS = {
    CONF_BRIDGE_ENTITY: "sensor.zont_bridge_status",
    CONF_PRIMARY_HELPER: "input_text.zont_primary_temperature_entity",
    CONF_FALLBACK_HELPER: "input_text.zont_fallback_temperature_entity",
    CONF_GOAL_HELPER: "input_number.zont_room_target",
}
HEARTBEAT_SECONDS = 90
BOILER_SECONDS = 180
SOURCE_SECONDS = 90 * 60
FUTURE_SECONDS = 60
MIN_TEMPERATURE = 5.0
MAX_TEMPERATURE = 30.0
VERSION = "0.1.0a1"
