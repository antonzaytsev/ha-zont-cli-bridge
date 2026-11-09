"""Standalone integration with upgrade-safe read-only legacy mode."""
import asyncio

from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import ZontClient
from .const import CONF_CLIENT, CONF_ORIGIN, CONF_TOKEN
from .coordinator import ZontBridgeCoordinator
from .ownership import claim, release
from .runtime import Runtime

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.CLIMATE]


async def async_setup_entry(hass, entry):
    key = None
    runtime = None
    if CONF_ORIGIN in entry.data:
        try:
            key = claim(hass, entry)
        except ValueError as error:
            raise ConfigEntryNotReady("This controller is already owned by another entry") from error
        try:
            config = {**entry.data, **entry.options}
            client = ZontClient(async_get_clientsession(hass), config[CONF_ORIGIN], config[CONF_TOKEN], config[CONF_CLIENT], config["device_id"], config["virtual_sensor_id"])
            runtime = Runtime(client, config, hass.states.get)
        except BaseException:
            release(hass, key, entry.entry_id)
            raise
    coordinator = ZontBridgeCoordinator(hass, entry, runtime)
    coordinator.ownership_key = key
    entry.runtime_data = coordinator
    coordinator.async_start()
    try:
        if runtime is not None:
            await coordinator.async_tick()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await asyncio.shield(coordinator.async_stop())
        if key is not None:
            release(hass, key, entry.entry_id)
        raise

    async def stop(_event):
        await coordinator.async_stop()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop))
    entry.async_on_unload(entry.add_update_listener(_async_update_entry))
    return True


async def _async_update_entry(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unloaded:
        return False
    await asyncio.shield(entry.runtime_data.async_stop())
    if coordinator_key := getattr(entry.runtime_data, "ownership_key", None):
        release(hass, coordinator_key, entry.entry_id)
    return True
