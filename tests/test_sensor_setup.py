"""Legacy observers must not register a native sensor over their REST input."""
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_lifecycle import module
from test_model import load


class Description:
    def __init__(self, **values):
        self.__dict__.update(values)


class Base:
    def __init__(self, coordinator, *_args):
        self.coordinator = coordinator


class SensorSetupTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_is_standalone_only_with_explicit_distinct_entity_id(self):
        stubs = {"homeassistant": module("homeassistant"),
                 "homeassistant.components": module("homeassistant.components"),
                 "homeassistant.components.sensor": module("homeassistant.components.sensor", SensorEntity=type("SensorEntity", (), {}), SensorEntityDescription=Description,
                    SensorDeviceClass=SimpleNamespace(TEMPERATURE="temperature", ENUM="enum", TIMESTAMP="timestamp"), SensorStateClass=SimpleNamespace(MEASUREMENT="measurement")),
                 "homeassistant.const": module("homeassistant.const", PERCENTAGE="%", EntityCategory=SimpleNamespace(DIAGNOSTIC="diagnostic"), UnitOfTemperature=SimpleNamespace(CELSIUS="°C")),
                 "zont_test_package.entity": module("zont_test_package.entity", ZontBridgeEntity=Base)}
        with patch.dict(sys.modules, stubs):
            sensor = load("sensor")
            legacy = []
            await sensor.async_setup_entry(None, SimpleNamespace(runtime_data=SimpleNamespace(runtime=None)), legacy.extend)
            self.assertEqual(len(legacy), 7)
            self.assertFalse(any(isinstance(entity, sensor.ZontBridgeStatus) for entity in legacy))
            native = []
            await sensor.async_setup_entry(None, SimpleNamespace(runtime_data=SimpleNamespace(runtime=object())), native.extend)
            self.assertEqual(len(native), 8)
            status = next(entity for entity in native if isinstance(entity, sensor.ZontBridgeStatus))
            self.assertEqual(status.entity_id, "sensor.zont_native_bridge_status")
