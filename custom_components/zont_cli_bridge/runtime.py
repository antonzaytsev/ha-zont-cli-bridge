"""Pure asynchronous feedback engine; HA supplies states, HTTP and scheduling."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import inspect
import math

from .client import ZontError


def _number(value, lo, hi):
    return value if type(value) in (int, float) and math.isfinite(value) and lo <= value <= hi else None


def _stamp(value):
    try:
        if isinstance(value, datetime):
            return value.timestamp() if value.tzinfo else None
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.timestamp() if parsed.tzinfo else None
    except (ValueError, OverflowError):
        pass
    return None


def _iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None


def _field(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def fingerprint(device):
    """Public identity fields captured at setup, never credentials/config blobs."""
    return {"serial": device.get("serial"), "model": device.get("device_type", {}).get("code"),
            "hardware": str(device.get("hardware_type", {}).get("code")),
            "firmware": device.get("firmware_version", [None])[0]}


def managed_device(device, config):
    if device.get("id", device.get("device_id")) != config["device_id"] or fingerprint(device) != config["identity"]:
        raise ZontError("device_identity_changed")
    raw = device.get("z3k_config", {})
    virtuals = [s for s in raw.get("wired_temperature_sensors", []) if s.get("id") == config["virtual_sensor_id"]]
    circuits = [s for s in raw.get("heating_circuits", []) if s.get("id") == config["circuit_id"]]
    if len(virtuals) != 1 or len(circuits) != 1:
        raise ZontError("managed_object_missing_or_duplicate")
    virtual, circuit = virtuals[0], circuits[0]
    expected = config.get("virtual_sensor", {})
    if (virtual.get("serial") != "0" or
            any(virtual.get(k) != expected.get(k) for k in ("name", "no_connection_timeout")) or
            not expected.get("name") or _number(expected.get("no_connection_timeout"), 0.1, 180) is None or
            circuit.get("air_temp_sensor") != config["virtual_sensor_id"] or
            circuit.get("air_temp_sensor_reserve") != config["physical_sensor_id"]):
        raise ZontError("managed_configuration_changed")
    def refs(obj):
        if isinstance(obj, dict):
            return any(refs(x) for x in obj.values())
        if isinstance(obj, list):
            return any(refs(x) for x in obj)
        return str(obj) == str(config["virtual_sensor_id"])
    for row in raw.get("heating_circuits", []):
        copy = dict(row)
        if row.get("id") == config["circuit_id"]:
            copy.pop("air_temp_sensor", None)
        if refs(copy):
            raise ZontError("virtual_sensor_referenced_elsewhere")
    state = device.get("io", {}).get("z3k-state", {})
    actual = state.get(str(config["circuit_id"]), state.get(config["circuit_id"], {}))
    physical = state.get(str(config["physical_sensor_id"]), state.get(config["physical_sensor_id"]))
    if not isinstance(physical, dict) or _number(actual.get("target_temp"), -100, 150) is None or type(actual.get("mode_id")) is not int:
        raise ZontError("actual_state_missing")
    return {"target": actual["target_temp"], "mode": actual["mode_id"],
            "active_sensor": actual.get("target_sensor_id"), "online": device.get("online") is True}


def _boiler_telemetry(device, config, now):
    result = {"schema_version": 1, "read_at": _iso(now), "controller_received_at": None,
        "online": device.get("online") is True, "adapter_healthy": False, "adapter_id": None,
        "supply_temperature": None, "return_temperature": None, "target_temperature": None,
        "circuit_water_request": None, "burner_modulation": None, "flame": None, "heating": None, "dhw": None}
    received = _number(device.get("last_receive_time"), 1, 253402300799)
    result["controller_received_at"] = _iso(received)
    raw = device.get("z3k_config", {})
    circuit = next((r for r in raw.get("heating_circuits", []) if r.get("id") == config["circuit_id"]), {})
    adapter_id = circuit.get("water_temp_sensor")
    if type(adapter_id) is not int or not any(r.get("id") == adapter_id for r in raw.get("boiler_adapters", [])):
        return result
    result["adapter_id"] = adapter_id
    states = device.get("io", {}).get("z3k-state", {})
    adapter = states.get(str(adapter_id), states.get(adapter_id, {}))
    healthy = all(adapter.get("status", {}).get(k) is False for k in
                  ("no_adapter_connection", "no_boiler_connection", "no_valid_params", "boiler_fail"))
    result["adapter_healthy"] = healthy
    ot = adapter.get("ot", {})
    for key, field, lo, hi in (("supply_temperature", "bt", -40, 130), ("return_temperature", "rwt", -40, 130),
                              ("target_temperature", "cs", 0, 130), ("burner_modulation", "rml", 0, 100)):
        result[key] = _number(ot.get(field), lo, hi)
    result["circuit_water_request"] = _number(states.get(str(config["circuit_id"]), states.get(config["circuit_id"], {})).get("setpoint_temp"), 0, 130)
    flags = ot.get("s")
    if healthy and isinstance(flags, list) and all(f in ("ch", "dhw", "fl") for f in flags):
        result.update(flame="fl" in flags, heating="ch" in flags, dhw="dhw" in flags)
    return result


def boiler_telemetry(device, config, now):
    """Malformed optional boiler telemetry must never stop managed sensor feeds."""
    try:
        return _boiler_telemetry(device, config, now)
    except (TypeError, AttributeError, ValueError, KeyError, IndexError, OverflowError):
        return {"schema_version": 1, "read_at": _iso(now), "controller_received_at": None,
            "online": isinstance(device, dict) and device.get("online") is True,
            "adapter_healthy": False, "adapter_id": None, "supply_temperature": None,
            "return_temperature": None, "target_temperature": None, "circuit_water_request": None,
            "burner_modulation": None, "flame": None, "heating": None, "dhw": None}


def source_value(entity, state, now):
    if not isinstance(entity, str) or not entity.startswith("sensor.") or state is None:
        raise ZontError("source_unavailable")
    attributes = _field(state, "attributes", {})
    if attributes.get("device_class") != "temperature":
        raise ZontError("source_not_temperature")
    raw = _field(state, "state")
    try:
        if isinstance(raw, bool) or raw is None:
            raise ValueError
        value = float(raw)
    except (ValueError, TypeError):
        raise ZontError("source_unavailable") from None
    unit = attributes.get("unit_of_measurement")
    if unit in ("°F", "F", "fahrenheit"):
        value = (value - 32) * 5 / 9
    elif unit in ("K", "kelvin"):
        value -= 273.15
    elif unit not in ("°C", "C", "celsius"):
        raise ZontError("source_unit_invalid")
    measured = _stamp(_field(state, "last_reported") or _field(state, "last_updated"))
    if _number(value, 5, 35) is None or measured is None or not -60 <= now - measured <= 5400:
        raise ZontError("source_stale_or_invalid")
    return {"entity": entity, "label": attributes.get("friendly_name", entity), "value": round(value, 1),
            "measured": measured, "age": max(0, int(now - measured))}


class Runtime:
    """Serialized ticks and explicit target writes; no restored target intent."""
    def __init__(self, client, config, get_state, now=None):
        self.client, self.config, self.get_state = client, dict(config), get_state
        self.now = now or (lambda: datetime.now(timezone.utc).timestamp())
        self._lock = asyncio.Lock()
        self._lease = self._refresh_at = self._next_feed = None
        self._actual = self._boiler = None
        self._blocked = None
        self._uncertain_feed = self._uncertain_goal = False
        self._last_delivery = None
        self._closed = False
        self._operations = set()
        self.status = {}

    @property
    def control_available(self):
        return bool(not self._closed and self.config.get("feedback_enabled") and not self._blocked and
            self._lease is not None and self.now() - self._lease <= 180 and self._actual and
            self._actual["online"] and self._actual["mode"] == 0 and not self._uncertain_goal)

    def _guard(self):
        if not self.control_available:
            raise ZontError("control_unavailable")

    def _feed_guard(self):
        # Named modes alter goal ownership, not the origin of room measurements.
        if (self._closed or not self.config.get("feedback_enabled") or self._blocked or
                self._lease is None or self.now() - self._lease > 180 or
                not self._actual or not self._actual["online"]):
            raise ZontError("control_unavailable")

    async def _full(self):
        try:
            device = await self.client.full()
        except ZontError as err:
            if err.code == "device_missing_or_ambiguous":
                self._blocked = err.code
                self._lease = None
            raise
        if self._closed:
            raise ZontError("runtime_closed")
        try:
            self._boiler = boiler_telemetry(device, self.config, self.now())
        except (TypeError, AttributeError, ValueError, IndexError):
            self._boiler = None
        try:
            self._actual = managed_device(device, self.config)
        except ZontError as err:
            self._blocked, self._lease = err.code, None
            raise
        except (TypeError, AttributeError, ValueError, KeyError, IndexError):
            self._blocked, self._lease = "managed_configuration_invalid", None
            raise ZontError("managed_configuration_invalid") from None
        self._lease, self._refresh_at = self.now(), self.now() + 120
        return self._actual

    async def _state(self, entity):
        result = self.get_state(entity)
        return await result if inspect.isawaitable(result) else result

    async def _source(self):
        primary = self.config.get("primary_temperature_entity")
        fallback = self.config.get("fallback_temperature_entity")
        state = await self._state(primary)
        self.status["primary_source_label"] = _field(state, "attributes", {}).get("friendly_name", primary)
        try:
            source = source_value(primary, state, self.now())
            self.status.update(source_role="primary", primary_failure_reason=None)
            return source
        except ZontError as err:
            self.status["primary_failure_reason"] = err.code
        if self.config.get("physical_fallback", True):
            self.status["fallback_source_label"] = "Проводной резерв ZONT"
            raise ZontError("physical_reserve_selected")
        if not fallback or fallback == primary:
            raise ZontError("duplicate_or_missing_fallback")
        state = await self._state(fallback)
        self.status["fallback_source_label"] = _field(state, "attributes", {}).get("friendly_name", fallback)
        source = source_value(fallback, state, self.now())
        self.status["source_role"] = "fallback"
        return source

    @asynccontextmanager
    async def _operation(self):
        task = asyncio.current_task()
        self._operations.add(task)
        try:
            async with self._lock:
                if self._closed:
                    raise ZontError("runtime_closed")
                yield
        finally:
            self._operations.discard(task)

    async def tick(self):
        async with self._operation():
            if self._closed:
                raise ZontError("runtime_closed")
            self.status = {"state": "read_only", "phase": "active" if self.config.get("feedback_enabled") else "read_only",
                "heartbeat": _iso(self.now()), "last_verified_delivery": self._last_delivery,
                "source_role": "none", "primary_failure_reason": None,
                "primary_source_label": self.config.get("primary_temperature_entity"),
                "fallback_source_label": "Проводной резерв ZONT" if self.config.get("physical_fallback", True) else self.config.get("fallback_temperature_entity")}
            try:
                if self._refresh_at is None or self.now() >= self._refresh_at:
                    try:
                        await self._full()
                        if self._uncertain_goal:
                            self._uncertain_goal = False
                    except ZontError:
                        if self._lease is None or self.now() - self._lease > 180:
                            raise
                if self._actual:
                    self.status.update(actual_target=self._actual["target"], actual_mode=self._actual["mode"], active_sensor=self._actual["active_sensor"])
                source = await self._source()
                self.status.update(source_entity_id=source["entity"], source_entity=source["entity"], source_label=source["label"],
                    source_temperature=source["value"], source_measured_at=_iso(source["measured"]), source_age_seconds=source["age"])
                if not self.config.get("feedback_enabled"):
                    return self._publish()
                self._feed_guard()
                if self._next_feed is not None and self.now() < self._next_feed:
                    self.status["state"] = "delivery_verified" if self._last_delivery else "waiting_for_feed"
                    return self._publish()
                # HA schedules ticks every 30s. Leave 5s for callback/read jitter
                # so a slightly early tick does not create a 60s delivery gap.
                # Keep a minimum interval to reject rapid duplicate ticks.
                self._next_feed = self.now() + 25
                await self.client.connect()
                if self._uncertain_feed:
                    await self.client.read_sensor()
                    self._uncertain_feed = False
                    self.status["state"] = "feed_reconciled_waiting_next_tick"
                    return self._publish()
                def authorize():
                    self._feed_guard()
                    if not -60 <= self.now() - source["measured"] <= 5400:
                        raise ZontError("source_stale_or_invalid")
                authorize()
                self._uncertain_feed = True
                actual = await self.client.feed(source["value"], guard=authorize)
                if actual is None or abs(actual - source["value"]) > 0.11:
                    raise ZontError("feed_readback_mismatch")
                self._uncertain_feed = False
                self._last_delivery = _iso(self.now())
                self.status.update(state="delivery_verified", last_verified_delivery=self._last_delivery)
            except asyncio.CancelledError:
                raise
            except ZontError as err:
                self.status["state"] = err.code
            except Exception:
                self.status["state"] = "operation_failed"
            return self._publish()

    def _publish(self):
        self.status.update(configuration_block=self._blocked, boiler=self._boiler,
            control_available=self.control_available, control_verified_at=_iso(self._lease))
        return dict(self.status)

    async def set_target(self, value):
        async with self._operation():
            if _number(value, 5, 30) is None:
                raise ZontError("invalid_target")
            # A fresh observation must exist before this explicit request.
            baseline = dict(self._actual) if self._actual else None
            if not baseline or not self.config.get("feedback_enabled") or self._closed:
                raise ZontError("control_unavailable")
            uncertain = self._uncertain_goal
            actual = await self._full()
            if uncertain:
                self._uncertain_goal = False
                raise ZontError("goal_reconciled_new_request_required")
            self._guard()
            if actual["target"] != baseline["target"] or actual["mode"] != baseline["mode"]:
                raise ZontError("external_goal_changed")
            if value == actual["target"]:
                return self._publish()
            self._uncertain_goal = True
            def authorize():
                # This request owns the uncertain marker, but all other guards apply.
                if self._closed or self._blocked or self._lease is None or self.now() - self._lease > 180 or not actual["online"] or actual["mode"] != 0:
                    raise ZontError("control_unavailable")
            try:
                await self.client.set_target(self.config["circuit_id"], value, guard=authorize)
                after = await self._full()
                if after["target"] != value or after["mode"] != 0:
                    raise ZontError("goal_readback_mismatch")
                self._uncertain_goal = False
                self.status.update(actual_target=after["target"], actual_mode=after["mode"])
                return self._publish()
            except asyncio.CancelledError:
                raise
            except ZontError:
                raise
            except Exception:
                raise ZontError("target_failed") from None

    async def close(self):
        self._closed = True
        tasks = [task for task in self._operations if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        async with self._lock:
            await self.client.close()
