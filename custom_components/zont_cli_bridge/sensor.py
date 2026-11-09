"""Native boiler telemetry sensors, unavailable when source observations expire."""
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature

from .entity import ZontBridgeEntity
from .model import OPERATION_OPTIONS

_TEMPERATURES = ("supply_temperature", "return_temperature", "target_temperature", "circuit_water_request")
DESCRIPTIONS = tuple(
    SensorEntityDescription(key=key, translation_key=key, device_class=SensorDeviceClass.TEMPERATURE,
                            native_unit_of_measurement=UnitOfTemperature.CELSIUS, state_class=SensorStateClass.MEASUREMENT,
                            suggested_display_precision=1)
    for key in _TEMPERATURES
) + (
    SensorEntityDescription(key="burner_modulation", translation_key="burner_modulation", native_unit_of_measurement=PERCENTAGE,
                            state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=1, icon="mdi:fire"),
    SensorEntityDescription(key="operation", translation_key="operation", device_class=SensorDeviceClass.ENUM,
                            options=OPERATION_OPTIONS, icon="mdi:state-machine"),
    SensorEntityDescription(key="read_timestamp", translation_key="read_timestamp", device_class=SensorDeviceClass.TIMESTAMP,
                            entity_category=EntityCategory.DIAGNOSTIC),
)


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ZontBoilerSensor(entry.runtime_data, description) for description in DESCRIPTIONS] + [ZontBridgeStatus(entry.runtime_data)])


class ZontBridgeStatus(ZontBridgeEntity, SensorEntity):
    _attr_icon = "mdi:connection"

    def __init__(self, coordinator):
        super().__init__(coordinator, "bridge_status", "zont_native_bridge_status")

    @property
    def available(self):
        return self.coordinator.data.heartbeat_fresh

    @property
    def native_value(self):
        return self.coordinator.data.bridge_state

    @property
    def extra_state_attributes(self):
        # Runtime status is a deliberately sanitized public protocol, never entry data.
        allowed = ("heartbeat", "phase", "configuration_block", "actual_target", "source_temperature",
                   "source_measured_at", "last_verified_delivery", "source_entity", "source_label",
                   "source_role", "error", "boiler", "control_available", "control_verified_at", "source_entity_id",
                   "active_sensor", "primary_source_label", "fallback_source_label", "source_age_seconds", "actual_mode", "goal_state")
        return {key: value for key, value in self.coordinator.raw_status.items() if key in allowed}


class ZontBoilerSensor(ZontBridgeEntity, SensorEntity):
    def __init__(self, coordinator, description):
        super().__init__(coordinator, description.key, f"zont_boiler_{description.key}")
        self.entity_description = description

    @property
    def available(self):
        return self.coordinator.data.boiler_available and self.native_value is not None

    @property
    def native_value(self):
        return self.coordinator.data.boiler_values.get(self.entity_description.key)
