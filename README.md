# ZONT for Home Assistant — standalone alpha

**0.2.0a1 connects directly to ZONT from Home Assistant.** Configure the server address, API token and required client contact email in the native HA integration dialog. No CLI, Node process, external bridge or HA helper entities are required.

Requires Home Assistant **2026.11.0 or newer** and an already configured ZONT controller with a heating circuit assigned to an existing external serial-zero virtual temperature sensor and physical reserve. This alpha discovers and validates that setup; it does not create sensors or modify controller configuration, PID, weather compensation or heating modes.

## Setup / Настройка

Install this repository through Home Assistant Marketplace as a custom integration, selecting the prerelease, or copy `custom_components/zont_cli_bridge` to HA's custom components directory and restart HA. Add **ZONT** in Settings → Devices & services. Enter the HTTPS origin (default `https://my.zont.online`), token and client email; select the controller, configured heating circuit, existing virtual sensor and wired reserve. Select the primary HA temperature entity and a second HA entity or wired fallback. Feedback initially remains **disabled** while telemetry is read. Stop every external feeder before enabling native feedback in integration options.

Откройте Настройки → Устройства и службы → Добавить интеграцию → ZONT. Укажите адрес сервера, API-токен и email клиента. Выберите контроллер, уже настроенный контур, виртуальный датчик с нулевым серийным номером и проводной резерв. Затем выберите температурную сущность HA: поддерживаются BLE, Zigbee и любые другие интеграции. Новые датчики автоматически появляются в стандартном выборе HA. После настройки передача температуры выключена. Сначала остановите другие процессы передачи, затем включите её в параметрах интеграции.

A device can expose multiple measurements: choose its temperature entity. Any existing entity with temperature device class and °C, °F or K units is eligible, including one currently unavailable if its metadata remains. The primary and HA fallback must differ. Options are stored in HA's config entry, with native selectors and no fixed sensor list. Token fields never display stored tokens. Credentials remain in HA's config-entry storage; diagnostics exclude entry data and raw controller responses.

## Upgrade from 0.1 companion

The domain and config-entry/entity unique IDs are preserved. A legacy entry initially observes its old status entity **read-only**, with climate writes disabled. Use the entry's Reconfigure action to enter native credentials and selections. No credentials are imported automatically and no old target is replayed. Blank token on later native reconfiguration keeps the stored native token. Feedback remains disabled after reconfiguration. Stop and retire the old external feeder before explicitly enabling feedback; only one process may feed a controller. Old helpers can remain for rollback but are unused by native operation.

One native entry reserves the whole controller at a canonical server origin. A second entry is rejected even for another circuit. Ownership is held until socket and pending task shutdown completes. Reloads stop the old runtime before starting a new one. Failed/cancelled shutdown retains ownership rather than risking two writers.

## Controls, telemetry and limits

`climate.zont_cli_bridge_heating` shows verified room feedback and observed room target. Explicit temperature changes call the native runtime, require fresh validated controller identity/assignment and active feedback lease, write once and verify by reread. There is no startup target write, pending target replay or power-off control. External concurrent target changes cause rejection instead of overwriting the last observed value.

Suggested telemetry IDs are `sensor.zont_boiler_supply_temperature`, `return_temperature`, `target_temperature`, `circuit_water_request`, `burner_modulation`, `operation`, `read_timestamp` (each prefixed `sensor.zont_boiler_`), plus `binary_sensor.zont_boiler_flame`, `heating`, `dhw` (each prefixed `binary_sensor.zont_boiler_`). Existing IDs survive upgrades; HA may suffix new IDs on collision. Native status uses `sensor.zont_native_bridge_status`; the old REST-published entity is not written by this integration.

Boiler telemetry is independent of feedback/source availability. Full cloud reads occur every 120 seconds; feedback ticks every 30 seconds, expiry is checked every 15 seconds. Boiler availability requires heartbeat within 90 seconds, cloud-read/controller-receipt within 180 seconds, schema v1 and online/healthy flags. More than 60 seconds of future skew is rejected. Missing values stay unavailable; zero modulation and false flags remain valid. Room source readings expire after 90 minutes and verified delivery after 90 seconds. History resolution follows polling and begins when recorder receives installed entities.

Heating and DHW flags describe modes, not proof of burning. The operation sensor distinguishes modes without flame; climate heating action requires flame plus heating without DHW. No pump state is inferred. Controller/configuration drift blocks feedback without repairing settings. Transient failures reconnect with backoff and uncertain writes are reconciled before later feeds.

Selecting physical fallback means stopping virtual injection. It **does not verify hardware takeover**. HA downtime stops feedback; wired fallback must be independently tested on the controller. This alpha does not provide a safety guarantee. See [protocol](docs/protocol.md).

## Development

Run `python -m unittest discover -s tests -v`, `python -m compileall -q custom_components` and `ruff check .`. Tests use loopback HTTP/WS or doubles and never operate heating equipment. Runtime imports and native selectors must also be validated on the supported HA release.

MIT licensed; independent and not an official ZONT product.
