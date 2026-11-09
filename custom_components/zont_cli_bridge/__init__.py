"""Native companion entities for an independently installed ZONT CLI backend."""
from homeassistant.const import Platform

from .coordinator import ZontBridgeCoordinator

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.CLIMATE]


async def async_setup_entry(hass, entry):
    coordinator = ZontBridgeCoordinator(hass, entry)
    entry.runtime_data = coordinator
    coordinator.async_start()
    entry.async_on_unload(coordinator.async_stop)
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        coordinator.async_stop()
        raise
    return True


async def async_unload_entry(hass, entry):
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        entry.runtime_data.async_stop()
    return unloaded
