"""The climate facade never sends controller commands or implicit target writes."""
from enum import Enum, IntFlag
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_lifecycle import module
from test_model import load
from test_options import ServiceError


class Mode(str, Enum):
    HEAT = "heat"


class Action(str, Enum):
    HEATING = "heating"
    IDLE = "idle"


class Feature(IntFlag):
    TARGET_TEMPERATURE = 1


class Base:
    def __init__(self, coordinator, *_args):
        self.coordinator = coordinator
        self.hass = coordinator.hass


class ClimateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        stubs = {"homeassistant": module("homeassistant"),
                 "homeassistant.components": module("homeassistant.components"),
                 "homeassistant.components.climate": module("homeassistant.components.climate", ClimateEntity=type("ClimateEntity", (), {}), ClimateEntityFeature=Feature, HVACMode=Mode, HVACAction=Action),
                 "homeassistant.const": module("homeassistant.const", ATTR_TEMPERATURE="temperature", UnitOfTemperature=SimpleNamespace(CELSIUS="°C")),
                 "homeassistant.exceptions": module("homeassistant.exceptions", HomeAssistantError=ServiceError),
                 "zont_test_package.entity": module("zont_test_package.entity", ZontBridgeEntity=Base)}
        self.patcher = patch.dict(sys.modules, stubs)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.climate_module = load("climate")
        self.calls = []

        async def call(*args, **kwargs):
            self.calls.append((args, kwargs))

        self.data = SimpleNamespace(control_available=True, current_temperature=21, room_target=24, room_hvac_action="idle")
        self.hass = SimpleNamespace(states=SimpleNamespace(get=lambda _id: SimpleNamespace(state="24")), services=SimpleNamespace(async_call=call))
        self.coordinator = SimpleNamespace(hass=self.hass, data=self.data, async_refresh_snapshot=lambda: None, async_set_target=call)

    async def test_platform_setup_and_properties_never_write(self):
        entities = []
        await self.climate_module.async_setup_entry(self.hass, SimpleNamespace(runtime_data=self.coordinator), entities.extend)
        climate = entities[0]
        self.assertEqual(climate.native_target_temperature, 24)
        self.assertEqual(climate.native_current_temperature, 21)
        self.assertEqual(climate.hvac_action, Action.IDLE)
        self.assertEqual(climate._attr_hvac_modes, [Mode.HEAT])
        self.assertEqual(self.calls, [])

    async def test_explicit_setter_only_calls_native_guarded_target(self):
        climate = self.climate_module.ZontBridgeClimate(self.coordinator)
        await climate.async_set_temperature(temperature=25)
        self.assertEqual(self.calls, [((25,), {})])

    async def test_stale_and_invalid_targets_cannot_write(self):
        climate = self.climate_module.ZontBridgeClimate(self.coordinator)
        for value in (True, "25", float("nan"), 4, 31):
            with self.assertRaises(ServiceError):
                await climate.async_set_temperature(temperature=value)
        self.data.control_available = False
        with self.assertRaises(ServiceError):
            await climate.async_set_temperature(temperature=24)
        self.assertEqual(self.calls, [])
