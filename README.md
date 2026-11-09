# ZONT CLI Bridge for Home Assistant

**0.1.0a1 — alpha companion integration. An already configured external ZONT CLI bridge is required.** This is not a standalone ZONT integration and does not configure a controller or accept ZONT credentials. The backend remains responsible for source arbitration, verified temperature delivery, physical fallback and controller commands.

The integration observes a bridge status entity already present in Home Assistant. It provides native boiler sensors, a room climate entity and native temperature entity pickers. A later BLE, Zigbee or other temperature entity appears automatically when it has the temperature device class. Select the temperature entity belonging to the desired device; a device can expose several measurements.

## Installation

Requires Home Assistant **2026.11.0 or newer** and external bridge protocol v1. This alpha is distributed as a custom integration; add this repository to the Home Assistant Marketplace as an integration repository and explicitly select the prerelease. Alternatively copy `custom_components/zont_cli_bridge` to your HA configuration's `custom_components` directory, then restart HA.

Add **ZONT CLI Bridge** in Settings → Devices & services → Add integration. Select the existing bridge status sensor and the three helpers. Default entity IDs are `sensor.zont_bridge_status`, `input_text.zont_primary_temperature_entity`, `input_text.zont_fallback_temperature_entity`, and `input_number.zont_room_target`. Existing values are preserved. No helper or controller writes occur during setup, reload or restart.

Open the integration's configuration/options dialog to select a primary temperature entity and either another HA temperature entity or the wired controller fallback. The selectors use HA's native temperature entity selector with no integration allowlist. The form reads the helpers' current values each time; it does not replay stored selections. Both selections are validated before any write. Saving updates only the two source helpers. These are separate service calls: if a concurrent change or partial failure occurs, the dialog displays the resulting live selections and requires review; it does not blindly roll back another change.

The climate entity adjusts only the configured room-target helper, within 5–30 °C. It supports heat mode only. It does not offer power-off or controller mode commands. The backend applies and verifies the request using its existing rules. A successful helper write is not a controller acknowledgement.

Selecting physical fallback configures a preference; it does not verify that fallback has worked on the controller. Verification belongs to the external bridge and deployment checks.

## Entities and freshness

The table lists metric ID hints, not a fixed entity-ID contract. Home Assistant 2026.11 can generate IDs from the device and translated entity names instead, for example `sensor.zont_cli_bridge_boiler_supply_temperature` and `climate.zont_cli_bridge_room_heating`. Use the actual IDs shown on the integration's device page when building dashboards; HA can also add collision suffixes.

| Entity | Meaning |
| --- | --- |
| `climate.zont_cli_bridge_heating` | Room temperature, observed room target and target-helper control |
| `sensor.zont_boiler_supply_temperature` | Boiler water supply, °C |
| `sensor.zont_boiler_return_temperature` | Boiler water return, °C |
| `sensor.zont_boiler_target_temperature` | Boiler water target, °C |
| `sensor.zont_boiler_circuit_water_request` | Heating circuit water request, °C |
| `sensor.zont_boiler_burner_modulation` | Burner modulation, % |
| `sensor.zont_boiler_operation` | Explicit flame/heating/DHW flag combination |
| `sensor.zont_boiler_read_timestamp` | Last successful cloud read timestamp |
| `binary_sensor.zont_boiler_flame` | Explicit flame flag |
| `binary_sensor.zont_boiler_heating` | Explicit heating mode flag |
| `binary_sensor.zont_boiler_dhw` | Explicit hot water mode flag |

Heating/DHW mode flags do not by themselves mean that fuel is burning. The operation enum distinguishes modes without flame. No pump state is invented. Climate heating action requires flame and heating with no DHW flag; ambiguous combinations have no inferred action.

The local 15-second timer expires stale observations even when no new state event arrives. Boiler data requires a heartbeat no older than 90 seconds, cloud read and controller receipt no older than 180 seconds, explicit online/healthy flags and schema v1. Timestamps more than 60 seconds in the future are invalid. Individual missing, malformed or nonfinite values are unavailable, not zero. Zero modulation and false flags are valid. Room feedback separately requires a fresh heartbeat, verified delivery within 90 seconds and a source measurement within 90 minutes. Controls require a fresh heartbeat, active bridge phase and no configuration block.

The external backend's polling interval determines measurement resolution; this integration makes no extra ZONT requests. HA history begins when entities are installed and recorder is enabled. Cloud read/receipt times do not prove when individual physical registers were sampled. See [protocol](docs/protocol.md).

## Development

Run `python -m unittest discover -s tests -v` and `python -m compileall -q custom_components`. Pure protocol/lifecycle tests do not require a running HA or a ZONT account. Deployment must additionally validate against the actual supported HA release. No tests operate heating equipment.

MIT licensed. This independent companion is not an official ZONT product.
