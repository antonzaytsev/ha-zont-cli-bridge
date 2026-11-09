"""Shared device identity and diagnostic attributes."""
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, VERSION


class ZontBridgeEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, key, object_id):
        super().__init__(coordinator)
        entry = coordinator.entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_suggested_object_id = object_id
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="ZONT",
            manufacturer="ZONT",
            model="Native cloud integration",
            sw_version=VERSION,
        )

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        return {
            "bridge_state": data.bridge_state,
            "bridge_phase": data.phase,
            "bridge_heartbeat": data.heartbeat.isoformat() if data.heartbeat else None,
            "boiler_read_at": data.boiler_read_at.isoformat() if data.boiler_read_at else None,
            "controller_received_at": data.controller_received_at.isoformat() if data.controller_received_at else None,
        }
