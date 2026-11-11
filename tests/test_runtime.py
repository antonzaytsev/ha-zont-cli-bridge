"""Feedback policy with a fake clock, no HA imports and no live controllers."""
import asyncio
import copy
from datetime import datetime, timezone
import unittest

from test_client import load

runtime = load("runtime")
ZontError = load("client").ZontError

def config():
    return {"device_id": 123, "circuit_id": 200, "virtual_sensor_id": 456, "physical_sensor_id": 789,
        "identity": {"serial": "test-controller", "model": "M", "hardware": "H", "firmware": 1},
        "virtual_sensor": {"id": 456, "serial": "0", "name": "HA", "no_connection_timeout": 1},
        "primary_temperature_entity": "sensor.primary", "fallback_temperature_entity": "sensor.fallback",
        "physical_fallback": False, "feedback_enabled": True}

def device():
    return {"id": 123, "serial": "test-controller", "device_type": {"code": "M"}, "hardware_type": {"code": "H"},
        "firmware_version": [1], "online": True, "last_receive_time": 100000,
        "z3k_config": {"wired_temperature_sensors": [config()["virtual_sensor"]], "boiler_adapters": [{"id": 300}],
            "heating_circuits": [{"id": 200, "air_temp_sensor": 456, "air_temp_sensor_reserve": 789, "water_temp_sensor": 300}]},
        "io": {"z3k-state": {"200": {"target_temp": 24, "mode_id": 0, "target_sensor_id": 456, "setpoint_temp": 45},
            "789": {"sensor_ok": True}, "300": {"status": {k: False for k in ("no_adapter_connection", "no_boiler_connection", "no_valid_params", "boiler_fail")},
                "ot": {"bt": 50, "rwt": 40, "cs": 55, "rml": 12, "s": ["ch", "fl"]}}}}}

class Fake:
    def __init__(self):
        self.device = device()
        self.feeds, self.goals, self.reads, self.fulls = [], [], 0, 0
        self.closed = False
    async def full(self):
        self.fulls += 1
        return copy.deepcopy(self.device)
    async def connect(self):
        pass
    async def feed(self, value, guard=None):
        if guard:
            guard()
        self.feeds.append(value)
        return value
    async def read_sensor(self):
        self.reads += 1
        return None
    async def set_target(self, circuit, value, guard=None):
        if guard:
            guard()
        self.goals.append(value)
        self.device["io"]["z3k-state"]["200"]["target_temp"] = value
    async def close(self):
        self.closed = True

class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.time = 100000
        self.z = Fake()
        self.cfg = config()
        def state(value):
            return {"state": value, "last_reported": datetime.fromtimestamp(self.time, timezone.utc),
                "attributes": {"device_class": "temperature", "unit_of_measurement": "°C", "friendly_name": "Room"}}
        self.states = {"sensor.primary": state("21.27"), "sensor.fallback": state("23.9")}
        self.r = runtime.Runtime(self.z, self.cfg, self.states.get, now=lambda: self.time)

    async def test_read_only_startup_no_feed_goal_or_connection(self):
        self.r.config["feedback_enabled"] = False
        result = await self.r.tick()
        self.assertEqual(result["phase"], "read_only")
        self.assertFalse(result["control_available"])
        self.assertEqual(self.z.feeds, [])
        with self.assertRaises(ZontError):
            await self.r.set_target(25)
        self.assertEqual(self.z.goals, [])

    async def test_primary_fallback_recovery_and_original_age(self):
        result = await self.r.tick()
        self.assertEqual(result["source_role"], "primary")
        self.assertEqual(self.z.feeds, [21.3])
        self.states["sensor.primary"]["state"] = "unavailable"
        self.time += 30
        result = await self.r.tick()
        self.assertEqual(result["source_role"], "fallback")
        self.assertEqual(result["source_age_seconds"], 30)
        self.assertEqual(self.z.feeds, [21.3, 23.9])
        self.states["sensor.primary"]["state"] = "22.1"
        self.time += 30
        result = await self.r.tick()
        self.assertEqual(result["source_role"], "primary")
        self.assertEqual(self.z.feeds[-1], 22.1)
        self.assertEqual(self.z.goals, [])

    async def test_invalid_primary_and_physical_duplicate_or_bad_fallback_stop(self):
        self.states["sensor.primary"]["state"] = "unavailable"
        for key, value in (("physical_fallback", True), ("fallback_temperature_entity", "sensor.primary")):
            r = runtime.Runtime(self.z, {**self.cfg, key: value}, self.states.get, now=lambda: self.time)
            await r.tick()
        self.states["sensor.fallback"]["state"] = "36"
        await self.r.tick()
        self.assertEqual(self.z.feeds, [])

    async def test_lease_failure_expiry_and_dispatch_recheck(self):
        await self.r.tick()
        async def failed():
            raise ZontError("transport_failed")
        self.z.full = failed
        self.time += 120
        await self.r.tick()
        self.assertEqual(len(self.z.feeds), 2)
        self.time += 61
        result = await self.r.tick()
        self.assertEqual(len(self.z.feeds), 2)
        self.assertFalse(result["control_available"])

    async def test_lease_can_expire_while_feed_waits_for_rate_spacing(self):
        async def delayed(value, guard=None):
            self.time += 181
            guard()
            self.z.feeds.append(value)
        self.z.feed = delayed
        result = await self.r.tick()
        self.assertEqual(self.z.feeds, [])
        self.assertEqual(result["state"], "control_unavailable")

    async def test_identity_or_assignment_drift_sticky_but_boiler_visible(self):
        await self.r.tick()
        self.z.device["z3k_config"]["heating_circuits"][0]["air_temp_sensor"] = 999
        self.time += 120
        result = await self.r.tick()
        self.assertEqual(result["configuration_block"], "managed_configuration_changed")
        self.assertEqual(result["boiler"]["supply_temperature"], 50)
        self.assertEqual(len(self.z.feeds), 1)
        self.z.device["z3k_config"]["heating_circuits"][0]["air_temp_sensor"] = 456
        self.time += 120
        await self.r.tick()
        self.assertEqual(len(self.z.feeds), 1)

    async def test_uncertain_feed_read_reconcile_no_same_tick_replay(self):
        original = self.z.feed
        async def fail(value, guard=None):
            raise ZontError("controller_exchange_failed")
        self.z.feed = fail
        await self.r.tick()
        self.z.feed = original
        self.time += 30
        await self.r.tick()
        self.assertEqual(self.z.reads, 1)
        self.assertEqual(self.z.feeds, [])
        self.time += 30
        await self.r.tick()
        self.assertEqual(self.z.feeds, [21.3])

    async def test_scheduler_jitter_does_not_skip_a_temperature_delivery(self):
        await self.r.tick()
        # HA's interval callback can arrive slightly early after a slower tick.
        for elapsed in (29.2, 30.3, 29.1, 30.5):
            self.time += elapsed
            result = await self.r.tick()
            self.assertEqual(result["last_verified_delivery"], runtime._iso(self.time))
        self.assertEqual(len(self.z.feeds), 5)

    async def test_feed_spacing_still_rejects_rapid_duplicate_ticks(self):
        await self.r.tick()
        for elapsed in (0, 1, 10, 13):
            self.time += elapsed
            await self.r.tick()
        self.assertEqual(len(self.z.feeds), 1)

    async def test_explicit_goal_fresh_manual_read_and_external_conflict(self):
        await self.r.tick()
        self.z.device["io"]["z3k-state"]["200"]["target_temp"] = 26
        with self.assertRaisesRegex(ZontError, "external_goal_changed"):
            await self.r.set_target(25)
        self.assertEqual(self.z.goals, [])
        await self.r.set_target(25)
        self.assertEqual(self.z.goals, [25])
        self.z.device["io"]["z3k-state"]["200"]["mode_id"] = 9
        with self.assertRaises(ZontError):
            await self.r.set_target(24)
        self.assertEqual(self.z.goals, [25])

    async def test_offline_goal_and_uncertain_goal_never_replayed(self):
        await self.r.tick()
        self.z.device["online"] = False
        with self.assertRaises(ZontError):
            await self.r.set_target(25)
        self.z.device["online"] = True
        async def ambiguous(circuit, value, guard=None):
            self.z.device["io"]["z3k-state"]["200"]["target_temp"] = value
            raise ZontError("transport_failed")
        self.z.set_target = ambiguous
        with self.assertRaises(ZontError):
            await self.r.set_target(25)
        with self.assertRaisesRegex(ZontError, "goal_reconciled_new_request_required"):
            await self.r.set_target(26)
        self.assertEqual(self.z.goals, [])
        self.time += 120
        await self.r.tick()
        self.assertEqual(self.z.goals, [])

    async def test_close_blocks_more_operations(self):
        await self.r.close()
        self.assertTrue(self.z.closed)
        with self.assertRaises(ZontError):
            await self.r.tick()

    async def test_named_mode_keeps_measurement_feeds_but_blocks_goal(self):
        self.z.device["io"]["z3k-state"]["200"]["mode_id"] = 9
        result = await self.r.tick()
        self.assertEqual(self.z.feeds, [21.3])
        self.assertFalse(result["control_available"])
        self.assertEqual(result["source_entity_id"], "sensor.primary")
        with self.assertRaises(ZontError):
            await self.r.set_target(25)
        self.assertEqual(self.z.goals, [])

    async def test_malformed_optional_boiler_telemetry_does_not_stop_feedback(self):
        for field in ("ot", "status"):
            self.z.device["io"]["z3k-state"]["300"][field] = None
            self.time += 120
            result = await self.r.tick()
            self.assertEqual(result["state"], "delivery_verified")
            self.assertFalse(result["boiler"]["adapter_healthy"])
            self.assertIsNone(result["boiler"]["flame"])
        self.assertEqual(len(self.z.feeds), 2)

    async def test_close_cancels_inflight_tick_and_pending_goal(self):
        entered = asyncio.Event()
        async def stalled():
            entered.set()
            await asyncio.Event().wait()
        self.z.full = stalled
        tick = asyncio.create_task(self.r.tick())
        await entered.wait()
        goal = asyncio.create_task(self.r.set_target(25))
        await asyncio.sleep(0)
        await self.r.close()
        self.assertTrue(tick.cancelled())
        self.assertTrue(goal.cancelled())
        self.assertTrue(self.z.closed)
        self.assertEqual(self.z.feeds, [])
        self.assertEqual(self.z.goals, [])

class SourceTests(unittest.TestCase):
    def test_units_metadata_freshness_and_no_zero_placeholders(self):
        for unit, value in (("°C", "21.3"), ("°F", "70.34"), ("K", "294.45")):
            state = {"state": value, "last_reported": "2026-01-01T00:00:00+00:00",
                "attributes": {"device_class": "temperature", "unit_of_measurement": unit}}
            now = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
            self.assertEqual(runtime.source_value("sensor.test", state, now)["value"], 21.3)
            with self.assertRaises(ZontError):
                runtime.source_value("sensor.test", state, now+5401)
            with self.assertRaises(ZontError):
                runtime.source_value("sensor.test", state, now-61)
        telemetry = runtime.boiler_telemetry({}, config(), now)
        self.assertIsNone(telemetry["supply_temperature"])
        self.assertIsNone(telemetry["flame"])

if __name__ == "__main__":
    unittest.main()
