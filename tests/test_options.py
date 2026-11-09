"""Native options validation and credential defaults without live operations."""
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

    def async_abort(self, **kwargs):
        return {"aborted": True, **kwargs}


class Marker:
    def __init__(self, key, default=None):
        self.key, self.default = key, default


class ServiceError(Exception):
    pass


class OptionsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        selectors = {name: (lambda config=None: config) for name in ("BooleanSelector", "EntitySelector", "SelectSelector", "TextSelector")}
        selectors.update({name: (lambda **kw: kw) for name in ("EntitySelectorConfig", "SelectSelectorConfig", "TextSelectorConfig")})
        stubs = {"probatio": module("probatio", Required=Marker, Optional=Marker, Schema=lambda v: v),
                 "homeassistant": module("homeassistant"),
                 "homeassistant.config_entries": module("homeassistant.config_entries", ConfigFlow=FlowBase, OptionsFlow=FlowBase),
                 "homeassistant.core": module("homeassistant.core", callback=lambda fn: fn),
                 "homeassistant.helpers": module("homeassistant.helpers"),
                 "homeassistant.helpers.aiohttp_client": module("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda hass: None),
                 "homeassistant.helpers.selector": module("homeassistant.helpers.selector", **selectors)}
        self.patcher = patch.dict(sys.modules, stubs)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.flow_module = load("config_flow")
        self.states = {entity: SimpleNamespace(state="unavailable", attributes={"device_class": "temperature", "unit_of_measurement": "°C"}) for entity in ("sensor.primary", "sensor.reserve")}
        self.hass = SimpleNamespace(states=SimpleNamespace(get=self.states.get))  # No services API exists.
        self.flow = self.flow_module.ZontCliBridgeOptionsFlow()
        self.flow.config_entry = SimpleNamespace(data={"origin": "https://example.com"}, options={"primary_temperature_entity": "sensor.primary", "physical_fallback": True, "feedback_enabled": False})
        self.flow.hass = self.hass

    def defaults(self, result):
        return {marker.key: marker.default for marker in result["data_schema"]}

    async def test_options_no_helpers_and_native_unrestricted_selector(self):
        result = await self.flow.async_step_init()
        self.assertEqual(self.defaults(result)["primary_temperature_entity"], "sensor.primary")
        selector = next(value for marker, value in result["data_schema"].items() if marker.key == "primary_temperature_entity")
        self.assertEqual(selector, {"filter": [{"domain": "sensor", "device_class": "temperature"}], "multiple": False})

    async def test_valid_options_only_store_options_not_write(self):
        values = {"primary_temperature_entity": "sensor.primary", "physical_fallback": True, "feedback_enabled": True}
        result = await self.flow.async_step_init(values)
        self.assertEqual(result["data"], values)

    async def test_invalid_and_duplicate_rejected_before_commit(self):
        for values in ({"primary_temperature_entity": "sensor.missing"}, {"primary_temperature_entity": "sensor.primary", "physical_fallback": False, "fallback_temperature_entity": "sensor.primary"}):
            result = await self.flow.async_step_init(values)
            self.assertTrue(result["errors"])

    async def test_legacy_options_require_explicit_reconfigure(self):
        self.flow.config_entry.data = {"bridge_entity": "sensor.legacy"}
        self.assertEqual((await self.flow.async_step_init())["reason"], "reconfigure_required")

    async def test_credentials_never_prefill_token(self):
        flow = self.flow_module.ZontCliBridgeConfigFlow()
        flow.hass = self.hass
        flow._entry = SimpleNamespace(data={"origin": "https://example.com", "client": "test@example.com", "token": "private-sentinel"})
        form = await flow._credentials_step(None)
        self.assertIsNone(self.defaults(form)["token"])
        self.assertNotIn("private-sentinel", repr(form))

    async def test_final_commit_rechecks_controller_collision(self):
        flow = self.flow_module.ZontCliBridgeConfigFlow()
        flow.hass = self.hass
        flow._credentials = {"origin": "https://example.com"}
        flow._selection = {"device_id": 1, "identity": {"serial": "test"}}
        flow._canonical = "https://example.com|test"
        flow._async_current_entries = lambda: [SimpleNamespace(unique_id=flow._canonical)]
        result = await flow.async_step_sources({"primary_temperature_entity": "sensor.primary", "physical_fallback": True})
        self.assertEqual(result["reason"], "already_configured")

    async def test_reconfigure_preserves_entry_id_and_forces_feedback_disabled(self):
        flow = self.flow_module.ZontCliBridgeConfigFlow()
        updates, reloads = [], []

        async def reload(entry_id):
            reloads.append(entry_id)

        self.hass.config_entries = SimpleNamespace(async_update_entry=lambda entry, **kw: updates.append((entry, kw)), async_reload=reload)
        flow.hass = self.hass
        entry = SimpleNamespace(entry_id="existing-entry", options={})
        flow._entry = entry
        flow._credentials = {"origin": "https://example.com"}
        flow._selection = {"device_id": 1, "identity": {"serial": "test"}}
        flow._canonical = "https://example.com|test"
        flow._async_current_entries = lambda: [entry]
        result = await flow.async_step_sources({"primary_temperature_entity": "sensor.primary", "physical_fallback": True, "feedback_enabled": True})
        self.assertEqual(result["reason"], "reconfigure_successful")
        self.assertIs(updates[0][0], entry)
        self.assertFalse(updates[0][1]["options"]["feedback_enabled"])
        self.assertEqual(reloads, ["existing-entry"])

    async def test_assignment_includes_analog_reserve_and_preserves_assignment_guard(self):
        flow = self.flow_module.ZontCliBridgeConfigFlow()
        flow.hass = self.hass
        flow._credentials = {"origin": "https://example.com"}
        flow._selection = {"device_id": 100}
        flow._async_current_entries = lambda: []
        flow._full = {"serial": "test-controller", "device_type": {"code": "test-model"},
                      "hardware_type": {"code": 1}, "firmware_version": [2], "z3k_config": {
                          "heating_circuits": [{"id": 200, "air_temp_sensor": 300, "air_temp_sensor_reserve": 400}],
                          "wired_temperature_sensors": [{"id": 300, "serial": "0", "name": "Virtual", "no_connection_timeout": 1},
                                                        {"id": 301, "serial": "weather", "name": "Outdoor"}],
                          "analog_temperature_sensors": [{"id": 400, "name": "Analog reserve"}]}}
        form = await flow.async_step_assignment()
        physical = next(value for marker, value in form["data_schema"].items() if marker.key == "physical_sensor_id")
        self.assertEqual({row["value"] for row in physical["options"]}, {"301", "400"})
        rejected = await flow.async_step_assignment({"circuit_id": "200", "virtual_sensor_id": "300", "physical_sensor_id": "301"})
        self.assertEqual(rejected["errors"]["base"], "configuration_required")
        accepted = await flow.async_step_assignment({"circuit_id": "200", "virtual_sensor_id": "300", "physical_sensor_id": "400"})
        self.assertEqual(accepted["step_id"], "sources")
        self.assertEqual(flow._selection["physical_sensor_id"], 400)
