"""Whitelisted diagnostics: no configuration, credentials or raw controller dump."""


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    data = coordinator.data
    return {"standalone": coordinator.runtime is not None,
            "heartbeat_fresh": data.heartbeat_fresh,
            "control_available": data.control_available,
            "boiler_available": data.boiler_available,
            "phase": data.phase,
            "bridge_state": data.bridge_state,
            "boiler_read_at": data.boiler_read_at.isoformat() if data.boiler_read_at else None}
