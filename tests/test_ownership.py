"""Controller-wide ownership handoff and identity collision tests."""
from types import SimpleNamespace
import unittest

from test_model import load

ownership = load("ownership")


class OwnershipTests(unittest.TestCase):
    def test_whole_controller_reserved_until_exact_owner_releases(self):
        hass = SimpleNamespace(data={})
        data = {"origin": "https://example.com", "device_id": 1, "identity": {"serial": "public-test"}}
        first = SimpleNamespace(entry_id="first", data=data)
        second = SimpleNamespace(entry_id="second", data={**data, "circuit_id": 2})
        key = ownership.claim(hass, first)
        with self.assertRaises(ValueError):
            ownership.claim(hass, second)
        ownership.release(hass, key, "second")
        with self.assertRaises(ValueError):
            ownership.claim(hass, second)
        ownership.release(hass, key, "first")
        ownership.claim(hass, second)

    def test_captured_key_survives_entry_reconfigure(self):
        hass = SimpleNamespace(data={})
        entry = SimpleNamespace(entry_id="one", data={"origin": "https://example.com", "device_id": 1})
        key = ownership.claim(hass, entry)
        entry.data = {"origin": "https://other.example.com", "device_id": 2}
        ownership.release(hass, key, entry.entry_id)
        self.assertEqual(hass.data["zont_cli_bridge"]["owners"], {})
