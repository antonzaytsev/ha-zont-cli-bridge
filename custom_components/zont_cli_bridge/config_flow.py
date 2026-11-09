"""Native configuration and current-value source selection flows."""
from __future__ import annotations

import re

import probatio
from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import BooleanSelector, EntitySelector, EntitySelectorConfig

from .const import (CONF_BRIDGE_ENTITY, CONF_FALLBACK_HELPER, CONF_FALLBACK_SOURCE, CONF_GOAL_HELPER,
                    CONF_PHYSICAL_FALLBACK, CONF_PRIMARY_HELPER, CONF_PRIMARY_SOURCE, DEFAULTS, DOMAIN, PHYSICAL_FALLBACK)


def validate_connection(hass, values):
    for key, domain in ((CONF_BRIDGE_ENTITY, "sensor"), (CONF_PRIMARY_HELPER, "input_text"),
                        (CONF_FALLBACK_HELPER, "input_text"), (CONF_GOAL_HELPER, "input_number")):
        entity = values.get(key)
        state = hass.states.get(entity) if isinstance(entity, str) else None
        if not isinstance(entity, str) or not re.fullmatch(rf"{domain}\.[a-z0-9_]+", entity) or state is None or state.state in ("unknown", "unavailable"):
            raise ValueError("invalid_helper")
    if values[CONF_PRIMARY_HELPER] == values[CONF_FALLBACK_HELPER]:
        raise ValueError("same_helpers")
    boiler = hass.states.get(values[CONF_BRIDGE_ENTITY]).attributes.get("boiler")
    if not isinstance(boiler, dict) or type(boiler.get("schema_version")) is not int or boiler["schema_version"] != 1:
        raise ValueError("backend_protocol")


def valid_temperature_entity(hass, entity):
    if not isinstance(entity, str) or len(entity) > 255 or not re.fullmatch(r"sensor\.[a-z0-9_]+", entity):
        return False
    state = hass.states.get(entity)
    return state is not None and state.attributes.get("device_class") == "temperature" and state.attributes.get("unit_of_measurement") in ("°C", "°F", "K")


class ZontCliBridgeConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                validate_connection(self.hass, user_input)
            except ValueError as error:
                errors["base"] = str(error)
            else:
                await self.async_set_unique_id(user_input[CONF_BRIDGE_ENTITY])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="ZONT CLI Bridge", data={key: user_input[key] for key in DEFAULTS})
        fields = {}
        for key, domain in ((CONF_BRIDGE_ENTITY, "sensor"), (CONF_PRIMARY_HELPER, "input_text"),
                            (CONF_FALLBACK_HELPER, "input_text"), (CONF_GOAL_HELPER, "input_number")):
            default = (user_input or {}).get(key, DEFAULTS[key])
            marker = probatio.Required(key, default=default) if self.hass.states.get(default) is not None else probatio.Required(key)
            fields[marker] = EntitySelector(EntitySelectorConfig(domain=domain, multiple=False))
        return self.async_show_form(step_id="user", data_schema=probatio.Schema(fields), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ZontCliBridgeOptionsFlow()


class ZontCliBridgeOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input=None):
        errors = {}
        config = self.config_entry.data
        if user_input is not None:
            primary = user_input.get(CONF_PRIMARY_SOURCE)
            physical = user_input.get(CONF_PHYSICAL_FALLBACK) is True
            fallback = PHYSICAL_FALLBACK if physical else user_input.get(CONF_FALLBACK_SOURCE)
            if not valid_temperature_entity(self.hass, primary) or (not physical and not valid_temperature_entity(self.hass, fallback)):
                errors["base"] = "invalid_temperature"
            elif primary == fallback:
                errors["base"] = "duplicate_source"
            else:
                for helper in (config[CONF_PRIMARY_HELPER], config[CONF_FALLBACK_HELPER]):
                    state = self.hass.states.get(helper)
                    if state is None or state.state in ("unknown", "unavailable"):
                        errors["base"] = "invalid_helper"
                if not errors:
                    before = {key: self.hass.states.get(config[key]) for key in (CONF_PRIMARY_HELPER, CONF_FALLBACK_HELPER)}
                    try:
                        await self.hass.services.async_call("input_text", "set_value", {"entity_id": config[CONF_PRIMARY_HELPER], "value": primary}, blocking=True)
                        current = self.hass.states.get(config[CONF_FALLBACK_HELPER])
                        baseline = before[CONF_FALLBACK_HELPER]
                        if current is None or (current.state, current.last_updated) != (baseline.state, baseline.last_updated):
                            errors["base"] = "selection_changed"
                        else:
                            await self.hass.services.async_call("input_text", "set_value", {"entity_id": config[CONF_FALLBACK_HELPER], "value": fallback}, blocking=True)
                    except HomeAssistantError:
                        errors["base"] = "write_failed"
                    else:
                        if not errors:
                            return self.async_create_entry(title="", data={})
                    # Partial writes are possible. Display the resulting live pair, never pretend rollback.
                    user_input = None
        # Never replay stored choices: every form uses the helpers' current values.
        primary_state = self.hass.states.get(config[CONF_PRIMARY_HELPER])
        fallback_state = self.hass.states.get(config[CONF_FALLBACK_HELPER])
        primary = primary_state.state if primary_state else None
        fallback = fallback_state.state if fallback_state else None
        inputs = user_input or {}
        selector = EntitySelector(EntitySelectorConfig(filter=[{"domain": "sensor", "device_class": "temperature"}], multiple=False))
        primary_default = inputs.get(CONF_PRIMARY_SOURCE, primary)
        fallback_default = inputs.get(CONF_FALLBACK_SOURCE, fallback)
        primary_marker = probatio.Required(CONF_PRIMARY_SOURCE, default=primary_default) if valid_temperature_entity(self.hass, primary_default) else probatio.Required(CONF_PRIMARY_SOURCE)
        fallback_marker = probatio.Optional(CONF_FALLBACK_SOURCE, default=fallback_default) if valid_temperature_entity(self.hass, fallback_default) else probatio.Optional(CONF_FALLBACK_SOURCE)
        schema = probatio.Schema({
            primary_marker: selector,
            probatio.Required(CONF_PHYSICAL_FALLBACK, default=inputs.get(CONF_PHYSICAL_FALLBACK, fallback == PHYSICAL_FALLBACK)): BooleanSelector(),
            fallback_marker: selector,
        })
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
