"""Protocol boundary tests independent of HA or heating equipment."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "custom_components/zont_cli_bridge"


def load(name):
    package = "zont_test_package"
    if package not in sys.modules:
        module = types.ModuleType(package)
        module.__path__ = [str(PACKAGE)]
        sys.modules[package] = module
    qualified = f"{package}.{name}"
    spec = importlib.util.spec_from_file_location(qualified, PACKAGE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    spec.loader.exec_module(module)
    return module


model = load("model")
NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


def sample():
    stamp = NOW.isoformat()
    return {"heartbeat": stamp, "phase": "active", "configuration_block": None,
            "actual_target": 24, "source_temperature": 22, "source_measured_at": stamp,
            "last_verified_delivery": stamp, "boiler": {"schema_version": 1, "read_at": stamp,
            "controller_received_at": stamp, "online": True, "adapter_healthy": True,
            "supply_temperature": 50, "return_temperature": 40, "target_temperature": 55,
            "circuit_water_request": 55, "burner_modulation": 0,
            "flame": False, "heating": True, "dhw": False}}


class ProtocolTests(unittest.TestCase):
    def project(self, attrs=None, now=NOW, state="delivery_verified"):
        return model.project(sample() if attrs is None else attrs, state, now)

    def test_valid_zero_false_and_separate_room_target(self):
        result = self.project()
        self.assertTrue(result.boiler_available)
        self.assertEqual(result.boiler_values["burner_modulation"], 0)
        self.assertIs(result.boiler_values["flame"], False)
        self.assertEqual(result.room_target, 24)
        self.assertEqual(result.boiler_values["target_temperature"], 55)

    def test_each_timestamp_expires_independently(self):
        for key, seconds in (("heartbeat", 91), ("read_at", 181), ("controller_received_at", 181)):
            with self.subTest(key=key):
                attrs = sample()
                target = attrs if key == "heartbeat" else attrs["boiler"]
                target[key] = (NOW - timedelta(seconds=seconds)).isoformat()
                self.assertFalse(self.project(attrs).boiler_available)

    def test_future_naive_and_malformed_timestamps(self):
        for value in ((NOW + timedelta(seconds=61)).isoformat(), "2026-01-01T12:00:00", "bad", None):
            attrs = sample()
            attrs["boiler"]["read_at"] = value
            self.assertFalse(self.project(attrs).boiler_available)

    def test_online_health_schema_and_missing_payload(self):
        for key, value in (("online", False), ("online", 1), ("adapter_healthy", None), ("schema_version", True), ("schema_version", 2)):
            attrs = sample()
            attrs["boiler"][key] = value
            self.assertFalse(self.project(attrs).boiler_available)
        for value in (None, {}, "bad"):
            attrs = sample()
            attrs["boiler"] = value
            self.assertFalse(self.project(attrs).boiler_available)

    def test_invalid_individual_values_never_become_zero(self):
        for value in (True, "50", float("nan"), float("inf"), 151):
            attrs = sample()
            attrs["boiler"]["supply_temperature"] = value
            result = self.project(attrs)
            self.assertTrue(result.boiler_available)
            self.assertIsNone(result.boiler_values["supply_temperature"])

    def test_feedback_delivery_failure_and_source_age(self):
        self.assertIsNone(self.project(state="feed_error").current_temperature)
        for key, age in (("last_verified_delivery", 91), ("source_measured_at", 5401)):
            attrs = sample()
            attrs[key] = (NOW - timedelta(seconds=age)).isoformat()
            self.assertIsNone(self.project(attrs).current_temperature)

    def test_control_gate_and_target_expiry(self):
        for key, value in (("phase", "preflight"), ("configuration_block", "changed")):
            attrs = sample()
            attrs[key] = value
            self.assertFalse(self.project(attrs).control_available)
        expired = self.project(now=NOW + timedelta(seconds=91))
        self.assertFalse(expired.control_available)
        self.assertIsNone(expired.room_target)

    def test_all_flag_combinations_and_ambiguity(self):
        expected = {(False, False, False): "idle", (True, False, False): "flame_only",
                    (False, True, False): "heating_without_flame", (False, False, True): "dhw_without_flame",
                    (False, True, True): "both_without_flame", (True, True, False): "heating",
                    (True, False, True): "dhw", (True, True, True): "heating_and_dhw"}
        for flags, value in expected.items():
            self.assertEqual(model.operation(*flags), value)
        self.assertIsNone(model.operation(True, None, False))
        self.assertEqual(model.room_action(True, True, False), "heating")
        self.assertEqual(model.room_action(True, False, True), "idle")
        self.assertIsNone(model.room_action(True, True, True))
