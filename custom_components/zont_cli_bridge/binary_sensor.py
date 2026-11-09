"""Explicit boiler flags; unknown is never silently interpreted as off."""
from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity

from .entity import ZontBridgeEntity

KEYS = ("flame", "heating", "dhw")


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(ZontBoilerFlag(entry.runtime_data, key) for key in KEYS)


class ZontBoilerFlag(ZontBridgeEntity, BinarySensorEntity):
    def __init__(self, coordinator, key):
        super().__init__(coordinator, key, f"zont_boiler_{key}")
        self._key = key
        self._attr_device_class = BinarySensorDeviceClass.HEAT if key == "flame" else BinarySensorDeviceClass.RUNNING
        self._attr_icon = {"flame": "mdi:fire", "heating": "mdi:radiator", "dhw": "mdi:water-boiler"}[key]

    @property
    def available(self):
        return self.coordinator.data.boiler_available and self.is_on is not None

    @property
    def is_on(self):
        value = self.coordinator.data.boiler_values.get(self._key)
        return value if isinstance(value, bool) else None
