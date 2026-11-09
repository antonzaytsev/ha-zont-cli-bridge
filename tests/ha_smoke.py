"""Explicit supported-HA runtime smoke; run inside HA 2026.11's environment."""
import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from homeassistant.core import HomeAssistant  # noqa: E402
from custom_components.zont_cli_bridge import config_flow, coordinator, sensor, binary_sensor, climate, diagnostics  # noqa: E402,F401


async def main():
    hass = HomeAssistant(str(Path(sys.argv[1]).resolve()))
    hass.states.async_set("sensor.smoke_temperature", "unavailable", {"device_class": "temperature", "unit_of_measurement": "°C"})
    schema = config_flow.source_schema(hass, {})
    validated = schema({"primary_temperature_entity": "sensor.smoke_temperature", "physical_fallback": True, "feedback_enabled": False})
    assert validated["primary_temperature_entity"] == "sensor.smoke_temperature"
    callbacks = []
    entry = SimpleNamespace(entry_id="smoke", data={"bridge_entity": "sensor.smoke_status"}, async_on_unload=callbacks.append)
    instance = coordinator.ZontBridgeCoordinator(hass, entry)
    instance.async_start()
    assert not instance.data.control_available
    await instance.async_stop()
    assert not instance._unsubscribers
    facade = climate.ZontBridgeClimate.__new__(climate.ZontBridgeClimate)
    assert facade.hvac_modes == ["heat"]
    assert facade.native_temperature_unit == "°C"
    print("PASS actual HA imports, native source selector schema, legacy read-only coordinator start/stop, native climate API")


if __name__ == "__main__":
    asyncio.run(main())
