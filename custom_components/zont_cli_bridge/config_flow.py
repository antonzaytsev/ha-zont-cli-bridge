"""Native server/token onboarding, discovery and temperature selectors."""
import re

import probatio
from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (BooleanSelector, EntitySelector, EntitySelectorConfig,
    SelectSelector, SelectSelectorConfig, TextSelector, TextSelectorConfig)

from .client import ZontClient, ZontAuthError, ZontError, normalize_origin
from .const import (CONF_CLIENT, CONF_FALLBACK_SOURCE, CONF_FEEDBACK, CONF_ORIGIN, CONF_PHYSICAL_FALLBACK,
    CONF_PRIMARY_SOURCE, CONF_TOKEN, DEFAULT_ORIGIN, DOMAIN)
from .ownership import identity


def valid_temperature_entity(hass, entity):
    if not isinstance(entity, str) or len(entity) > 255 or not re.fullmatch(r"sensor\.[a-z0-9_]+", entity):
        return False
    state = hass.states.get(entity)
    return state is not None and state.attributes.get("device_class") == "temperature" and state.attributes.get("unit_of_measurement") in ("°C", "°F", "K")


def source_schema(hass, values):
    selector = EntitySelector(EntitySelectorConfig(filter=[{"domain": "sensor", "device_class": "temperature"}], multiple=False))
    fields = {}
    for key in (CONF_PRIMARY_SOURCE, CONF_FALLBACK_SOURCE):
        marker_type = probatio.Required if key == CONF_PRIMARY_SOURCE else probatio.Optional
        value = values.get(key)
        marker = marker_type(key, default=value) if valid_temperature_entity(hass, value) else marker_type(key)
        fields[marker] = selector
    fields[probatio.Required(CONF_PHYSICAL_FALLBACK, default=values.get(CONF_PHYSICAL_FALLBACK, True))] = BooleanSelector()
    fields[probatio.Required(CONF_FEEDBACK, default=values.get(CONF_FEEDBACK, False))] = BooleanSelector()
    return probatio.Schema(fields)


def source_error(hass, values):
    if not valid_temperature_entity(hass, values.get(CONF_PRIMARY_SOURCE)):
        return "invalid_temperature"
    if not values.get(CONF_PHYSICAL_FALLBACK, True):
        if not valid_temperature_entity(hass, values.get(CONF_FALLBACK_SOURCE)):
            return "invalid_temperature"
        if values[CONF_PRIMARY_SOURCE] == values[CONF_FALLBACK_SOURCE]:
            return "duplicate_source"
    return None


class ZontCliBridgeConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self._credentials = {}
        self._devices = []
        self._full = None
        self._selection = {}
        self._entry = None

    async def async_step_user(self, user_input=None):
        return await self._credentials_step(user_input)

    async def async_step_reconfigure(self, user_input=None):
        self._entry = self._get_reconfigure_entry()
        return await self._credentials_step(user_input)

    async def async_step_reauth(self, entry_data):
        self._entry = self._get_reauth_entry()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        return await self._credentials_step(user_input, "reauth_confirm")

    def _client(self, device_id=0):
        return ZontClient(async_get_clientsession(self.hass), self._credentials[CONF_ORIGIN], self._credentials[CONF_TOKEN], self._credentials[CONF_CLIENT], device_id, 0)

    async def _credentials_step(self, user_input, step=None):
        step = step or ("reconfigure" if self._entry else "user")
        errors = {}
        old = self._entry.data if self._entry else {}
        if user_input is not None:
            try:
                origin = normalize_origin(user_input.get(CONF_ORIGIN, DEFAULT_ORIGIN))
                token = user_input.get(CONF_TOKEN) or old.get(CONF_TOKEN)
                contact = user_input.get(CONF_CLIENT, "").strip()
                if not token or not isinstance(token, str) or len(token) > 4096 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", contact):
                    raise ValueError("invalid_credentials")
                self._credentials = {CONF_ORIGIN: origin, CONF_TOKEN: token, CONF_CLIENT: contact}
                client = self._client()
                try:
                    self._devices = await client.devices()
                finally:
                    await client.close()
                if not self._devices:
                    errors["base"] = "no_devices"
                else:
                    return await self.async_step_device()
            except ZontAuthError:
                errors["base"] = "invalid_auth"
            except (ZontError, ValueError):
                errors["base"] = "cannot_connect"
        fields = {probatio.Required(CONF_ORIGIN, default=old.get(CONF_ORIGIN, DEFAULT_ORIGIN)): TextSelector(),
                  probatio.Required(CONF_CLIENT, default=old.get(CONF_CLIENT, "")): TextSelector(TextSelectorConfig(type="email"))}
        # Never populate a token default, including failed submissions and reauth.
        fields[(probatio.Optional if old.get(CONF_TOKEN) else probatio.Required)(CONF_TOKEN)] = TextSelector(TextSelectorConfig(type="password"))
        return self.async_show_form(step_id=step, data_schema=probatio.Schema(fields), errors=errors)

    async def async_step_device(self, user_input=None):
        errors = {}
        rows = {str(row.get("id", row.get("device_id"))): row for row in self._devices}
        if user_input is not None:
            selected = str(user_input.get("device_id"))
            if selected in rows:
                client = self._client(int(selected))
                try:
                    self._full = await client.full(int(selected))
                    self._selection = {"device_id": int(selected)}
                    return await self.async_step_assignment()
                except ZontAuthError:
                    errors["base"] = "invalid_auth"
                except (ZontError, ValueError):
                    errors["base"] = "cannot_connect"
                finally:
                    await client.close()
        options = [{"value": key, "label": str(row.get("name") or row.get("model") or key)} for key, row in rows.items()]
        return self.async_show_form(step_id="device", data_schema=probatio.Schema({probatio.Required("device_id"): SelectSelector(SelectSelectorConfig(options=options))}), errors=errors)

    async def async_step_assignment(self, user_input=None):
        config = self._full.get("z3k_config", {})
        circuits = config.get("heating_circuits", [])
        sensors = config.get("wired_temperature_sensors", [])
        virtual = [row for row in sensors if str(row.get("serial")) == "0"]
        # ZONT stores analog probes separately from digital wired probes.
        physical_by_id = {row["id"]: row for row in [
            *[row for row in sensors if str(row.get("serial")) != "0"],
            *config.get("analog_temperature_sensors", []),
        ] if isinstance(row, dict) and "id" in row}
        physical = list(physical_by_id.values())
        if not circuits or not virtual or not physical:
            return self.async_abort(reason="configuration_required")
        errors = {}
        if user_input is not None:
            try:
                selected = {}
                for key, rows in (("circuit_id", circuits), ("virtual_sensor_id", virtual), ("physical_sensor_id", physical)):
                    selected[key] = next(row for row in rows if str(row["id"]) == str(user_input.get(key)))
                circuit, sensor = selected["circuit_id"], selected["virtual_sensor_id"]
                if (circuit.get("air_temp_sensor") != sensor["id"] or
                    circuit.get("air_temp_sensor_reserve") != selected["physical_sensor_id"]["id"] or
                    str(sensor.get("serial")) != "0" or not sensor.get("no_connection_timeout")):
                    raise ValueError("configuration_required")
                self._selection.update({key: row["id"] for key, row in selected.items()})
                fingerprint = {"serial": self._full.get("serial"), "model": self._full.get("device_type", {}).get("code"),
                    "hardware": str(self._full.get("hardware_type", {}).get("code")) if self._full.get("hardware_type", {}).get("code") is not None else None,
                    "firmware": (self._full.get("firmware_version") or [None])[0]}
                if any(value is None for value in fingerprint.values()):
                    raise ValueError("configuration_required")
                self._selection.update(identity=fingerprint,
                    assignment=dict(circuit), virtual_sensor={key: sensor.get(key) for key in ("id", "serial", "name", "no_connection_timeout")})
                candidate = {**self._credentials, **self._selection}
                canonical = "|".join(identity(candidate))
                for entry in self._async_current_entries():
                    if entry is not self._entry and (entry.unique_id == canonical or (CONF_ORIGIN in entry.data and identity(entry.data) == identity(candidate))):
                        return self.async_abort(reason="already_configured")
                self._canonical = canonical
                return await self.async_step_sources()
            except (StopIteration, ValueError):
                errors["base"] = "configuration_required"
        fields = {}
        for key, rows in (("circuit_id", circuits), ("virtual_sensor_id", virtual), ("physical_sensor_id", physical)):
            options = [{"value": str(row["id"]), "label": str(row.get("name") or row["id"])} for row in rows]
            fields[probatio.Required(key)] = SelectSelector(SelectSelectorConfig(options=options))
        return self.async_show_form(step_id="assignment", data_schema=probatio.Schema(fields), errors=errors)

    async def async_step_sources(self, user_input=None):
        errors = {}
        if user_input is not None:
            error = source_error(self.hass, user_input)
            if error:
                errors["base"] = error
            else:
                options = dict(user_input)
                options[CONF_FEEDBACK] = False  # Credential setup never starts feedback.
                data = {**self._credentials, **self._selection}
                for entry in self._async_current_entries():
                    if entry is not self._entry and (entry.unique_id == self._canonical or (CONF_ORIGIN in entry.data and identity(entry.data) == identity(data))):
                        return self.async_abort(reason="already_configured")
                if self._entry:
                    self.hass.config_entries.async_update_entry(self._entry, data=data, options=options, unique_id=self._canonical)
                    await self.hass.config_entries.async_reload(self._entry.entry_id)
                    return self.async_abort(reason="reconfigure_successful")
                await self.async_set_unique_id(self._canonical)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="ZONT", data=data, options=options)
        defaults = dict(self._entry.options) if self._entry else {}
        defaults[CONF_FEEDBACK] = False
        return self.async_show_form(step_id="sources", data_schema=source_schema(self.hass, user_input or defaults), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ZontCliBridgeOptionsFlow()


class ZontCliBridgeOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input=None):
        if CONF_ORIGIN not in self.config_entry.data:
            return self.async_abort(reason="reconfigure_required")
        errors = {}
        if user_input is not None:
            error = source_error(self.hass, user_input)
            if error:
                errors["base"] = error
            else:
                return self.async_create_entry(title="", data=dict(user_input))
        return self.async_show_form(step_id="init", data_schema=source_schema(self.hass, user_input or self.config_entry.options), errors=errors)
