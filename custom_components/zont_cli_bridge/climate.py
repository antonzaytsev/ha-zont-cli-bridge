"""HEAT-only room goal facade, writing only the configured HA helper."""
import math

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError

from .const import MAX_TEMPERATURE, MIN_TEMPERATURE
from .entity import ZontBridgeEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ZontBridgeClimate(entry.runtime_data)])


class ZontBridgeClimate(ZontBridgeEntity, ClimateEntity):
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_min_temp = MIN_TEMPERATURE
    _attr_max_temp = MAX_TEMPERATURE
    _attr_target_temperature_step = 0.5

    def __init__(self, coordinator):
        super().__init__(coordinator, "heating_control", "zont_cli_bridge_heating")

    @property
    def available(self):
        return self.coordinator.data.control_available

    @property
    def hvac_mode(self):
        return HVACMode.HEAT

    @property
    def native_current_temperature(self):
        return self.coordinator.data.current_temperature

    @property
    def native_target_temperature(self):
        return self.coordinator.data.room_target

    @property
    def hvac_action(self):
        value = self.coordinator.data.room_hvac_action
        return HVACAction(value) if value else None

    async def async_set_temperature(self, **kwargs):
        self.coordinator.async_refresh_snapshot()
        if not self.coordinator.data.control_available:
            raise HomeAssistantError("The bridge is not fresh, active, or is configuration-blocked")
        target = kwargs.get(ATTR_TEMPERATURE)
        if isinstance(target, bool) or not isinstance(target, (int, float)) or not math.isfinite(target) or not MIN_TEMPERATURE <= target <= MAX_TEMPERATURE:
            raise HomeAssistantError("The room target must be a finite number between 5 and 30 °C")
        try:
            await self.coordinator.async_set_target(target)
        except Exception as error:
            raise HomeAssistantError("The controller target request was not verified") from error
