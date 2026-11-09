# Bridge status protocol v1

The configured HA sensor exposes the following attributes. The sensor state `delivery_verified` permits room feedback only while delivery/source timestamps are fresh. Other state strings remain diagnostic and do not prove delivery.

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

The native integration is a consumer. It never refreshes timestamp fields, requests cloud reads or performs raw ZONT HTTP. Source choices are strings stored in two configured `input_text` helpers: a `sensor.*` temperature entity, or `zont:physical` for the fallback only. The selected entities must exist with device_class `temperature` and °C/°F/K units; temporarily unavailable values may still be selected if metadata exists. Primary and HA fallback must differ. The backend handles units, delivery, expiry and physical fallback. Native selector saving does not claim physical fallback has been verified.

The configured `input_number` is the requested room goal. The bridge's `actual_target` is the observed/applied room goal shown by the climate entity. Helper writes occur only on an explicit options save or climate temperature request. Stable entity unique IDs contain the HA config-entry ID and metric key; no controller serial or installation ID is needed.
