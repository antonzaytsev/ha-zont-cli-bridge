"""Synchronous ownership retained until async shutdown completes."""
from .const import DOMAIN


def identity(config):
    return (config["origin"], str(config.get("identity", {}).get("serial") or config["device_id"]))


def claim(hass, entry):
    owners = hass.data.setdefault(DOMAIN, {}).setdefault("owners", {})
    key = identity(entry.data)
    if key in owners:
        raise ValueError("controller_owned")
    owners[key] = entry.entry_id
    return key


def release(hass, key, entry_id):
    owners = hass.data.get(DOMAIN, {}).get("owners", {})
    if owners.get(key) == entry_id:
        del owners[key]
