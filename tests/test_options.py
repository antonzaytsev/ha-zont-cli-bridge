"""Options flow interleavings: no stored replay, validation, partial writes."""
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_lifecycle import module
from test_model import load


class FlowBase:
    def __init_subclass__(cls, **_kwargs):
        pass

    def async_show_form(self, **kwargs):
        return kwargs

    def async_create_entry(self, **kwargs):
        return {"created": True, **kwargs}


class Marker:
    def __init__(self, key, default=None):
        self.key, self.default = key, default


class ServiceError(Exception):
    pass


class OptionsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        stubs = {"probatio": module("probatio", Required=Marker, Optional=Marker, Schema=lambda value: value),
                 "homeassistant": module("homeassistant"),
                 "homeassistant.config_entries": module("homeassistant.config_entries", ConfigFlow=FlowBase, OptionsFlow=FlowBase),
                 "homeassistant.core": module("homeassistant.core", callback=lambda fn: fn),
                 "homeassistant.exceptions": module("homeassistant.exceptions", HomeAssistantError=ServiceError),
                 "homeassistant.helpers": module("homeassistant.helpers"),
                 "homeassistant.helpers.selector": module("homeassistant.helpers.selector", BooleanSelector=lambda: None, EntitySelector=lambda config: config, EntitySelectorConfig=lambda **values: values)}
        self.patcher = patch.dict(sys.modules, stubs)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.flow_module = load("config_flow")
        self.states = {}
        self.calls = []
        self.mode = None
        for entity in ("sensor.old", "sensor.new", "sensor.reserve", "sensor.concurrent"):
            self.states[entity] = SimpleNamespace(state="unavailable", attributes={"device_class": "temperature", "unit_of_measurement": "°C"}, last_updated=0)
        self.set_helper("input_text.primary", "sensor.old")
        self.set_helper("input_text.fallback", "zont:physical")
        self.flow = self.flow_module.ZontCliBridgeOptionsFlow()
        self.flow.config_entry = SimpleNamespace(data={"primary_helper": "input_text.primary", "fallback_helper": "input_text.fallback"})
        self.flow.hass = SimpleNamespace(states=SimpleNamespace(get=self.states.get), services=SimpleNamespace(async_call=self.call))

    def set_helper(self, entity, value):
        previous = self.states.get(entity)
        self.states[entity] = SimpleNamespace(state=value, attributes={}, last_updated=(previous.last_updated + 1 if previous else 0))

    async def call(self, domain, service, data, **_kwargs):
        self.calls.append((domain, service, data))
        if len(self.calls) == 2 and self.mode == "failure":
            raise ServiceError("failed")
        self.set_helper(data["entity_id"], data["value"])
        if len(self.calls) == 1 and self.mode == "concurrent":
            self.set_helper("input_text.fallback", "sensor.concurrent")

    def defaults(self, result):
        return {marker.key: marker.default for marker in result["data_schema"]}

    async def test_open_only_reads_current_helpers_no_writes(self):
        result = await self.flow.async_step_init()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.defaults(result)["primary_temperature_entity"], "sensor.old")
        self.assertTrue(self.defaults(result)["physical_fallback"])
        selector = next(value for marker, value in result["data_schema"].items() if marker.key == "primary_temperature_entity")
        self.assertEqual(selector, {"filter": [{"domain": "sensor", "device_class": "temperature"}], "multiple": False})

    async def test_invalid_or_duplicate_never_writes(self):
        for value in ("sensor.missing", "sensor.new{{bad}}", "light.any"):
            await self.flow.async_step_init({"primary_temperature_entity": value, "physical_fallback": True})
        await self.flow.async_step_init({"primary_temperature_entity": "sensor.new", "fallback_temperature_entity": "sensor.new"})
        self.assertEqual(self.calls, [])

    async def test_unavailable_sensor_with_metadata_allowed_and_only_fixed_helper_targets(self):
        result = await self.flow.async_step_init({"primary_temperature_entity": "sensor.new", "physical_fallback": True})
        self.assertTrue(result["created"])
        self.assertEqual([call[2]["entity_id"] for call in self.calls], ["input_text.primary", "input_text.fallback"])
        self.assertTrue(all(call[:2] == ("input_text", "set_value") for call in self.calls))

    async def test_concurrent_change_preserved_and_live_pair_displayed(self):
        self.mode = "concurrent"
        result = await self.flow.async_step_init({"primary_temperature_entity": "sensor.new", "fallback_temperature_entity": "sensor.reserve"})
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result["errors"]["base"], "selection_changed")
        self.assertEqual(self.defaults(result)["primary_temperature_entity"], "sensor.new")
        self.assertEqual(self.defaults(result)["fallback_temperature_entity"], "sensor.concurrent")

    async def test_second_write_failure_displays_result_without_rollback(self):
        self.mode = "failure"
        result = await self.flow.async_step_init({"primary_temperature_entity": "sensor.new", "fallback_temperature_entity": "sensor.reserve"})
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(result["errors"]["base"], "write_failed")
        self.assertEqual(self.defaults(result)["primary_temperature_entity"], "sensor.new")
        self.assertTrue(self.defaults(result)["physical_fallback"])
