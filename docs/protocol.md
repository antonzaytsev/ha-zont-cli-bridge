# Bridge status protocol v1

The native runtime exposes the following sanitized status attributes through its status entity. A legacy 0.1 entry can still observe its old sensor read-only until reconfigured. The state `delivery_verified` permits room feedback only while delivery/source timestamps are fresh. Other state strings remain diagnostic and do not prove delivery.

```json
{
  "heartbeat": "2026-01-01T12:00:00Z",
  "phase": "active",
  "configuration_block": null,
  "actual_target": 24,
  "source_temperature": 22.5,
  "source_measured_at": "2026-01-01T12:00:00Z",
  "last_verified_delivery": "2026-01-01T12:00:00Z",
  "boiler": {
    "schema_version": 1,
    "read_at": "2026-01-01T12:00:00Z",
    "controller_received_at": "2026-01-01T11:59:55Z",
    "online": true,
    "adapter_healthy": true,
    "adapter_id": null,
    "supply_temperature": 50,
    "return_temperature": 42,
    "target_temperature": 55,
    "circuit_water_request": 55,
    "burner_modulation": 30,
    "flame": true,
    "heating": true,
    "dhw": false
  }
}
```

`read_at` is the successful full backend cloud read; `controller_received_at` is the controller receipt timestamp supplied by the backend. Neither is a fabricated measurement capture time. Reusing cached values must preserve both timestamps. Absent boiler data must be null, never a fabricated all-zero object. Flags are actual JSON booleans; numeric strings and booleans used as numbers are rejected. Times must include a timezone.

The native asynchronous runtime performs HTTPS cloud reads and authenticated Comet websocket feedback using the configured origin, API token and client contact. Full reads occur every 120 seconds; successful full reads retain their actual timestamps. Source choices live in config-entry options as `primary_temperature_entity`, `fallback_temperature_entity`, `physical_fallback` and `feedback_enabled`. The selected entities must exist with device_class `temperature` and °C/°F/K units; unavailable values may still be selected if metadata exists. Primary and HA fallback must differ. The runtime converts units, validates measurement age and delivers a primary or HA fallback reading. Physical fallback stops injection and does not claim hardware takeover has been verified.

`actual_target` is the observed room goal shown by the climate entity. Explicit climate requests call the runtime directly; no helpers or CLI are involved. Controls require fresh heartbeat, active feedback and a controller-validation lease within 180 seconds. `control_available` and `control_verified_at` expose that lease. Target writes additionally require manual mode and compare the current target with the last observation before a single write, then verify by reread. Uncertain target outcomes never cause automatic replay. Stable entity unique IDs contain the existing HA config-entry ID and metric key, preserving entities through migration.
