"""Exercise actual coordinator with small documented HA API doubles."""
from datetime import timedelta
from types import ModuleType, SimpleNamespace
import sys
import unittest
from unittest.mock import patch

from test_model import load, NOW, sample


class CoordinatorDouble:
    @classmethod
    def __class_getitem__(cls, _type):
        return cls

    def __init__(self, *_args, **_kwargs):
        self.hass = _args[0]
        self.updates = []

    def async_set_updated_data(self, data):
        self.data = data
        self.updates.append(data)


def module(name, **values):
    result = ModuleType(name)
    result.__dict__.update(values)
    return result


class LifecycleTests(unittest.TestCase):
    def test_timer_expiry_and_unload_with_no_service_writes(self):
        callbacks = {}
        removed = []

        def track_state(_hass, entities, callback):
            self.assertEqual(len(entities), 4)
            callbacks["state"] = callback
            return lambda: removed.append("state")

        def track_time(_hass, callback, interval):
            self.assertEqual(interval, timedelta(seconds=15))
            callbacks["time"] = callback
            return lambda: removed.append("time")

        clock = SimpleNamespace(utcnow=lambda: NOW)
        stubs = {"homeassistant": module("homeassistant"),
                 "homeassistant.core": module("homeassistant.core", callback=lambda fn: fn),
                 "homeassistant.helpers": module("homeassistant.helpers"),
                 "homeassistant.helpers.event": module("homeassistant.helpers.event", async_track_state_change_event=track_state, async_track_time_interval=track_time),
                 "homeassistant.helpers.update_coordinator": module("homeassistant.helpers.update_coordinator", DataUpdateCoordinator=CoordinatorDouble),
                 "homeassistant.util": module("homeassistant.util", dt=clock)}
        with patch.dict(sys.modules, stubs):
            coordinator = load("coordinator")
            status = SimpleNamespace(state="delivery_verified", attributes=sample())
            # Deliberately no services/network API: starting and refreshing must only read states.
            hass = SimpleNamespace(states=SimpleNamespace(get=lambda _entity: status))
            entry = SimpleNamespace(data={"bridge_entity": "sensor.status", "primary_helper": "input_text.primary", "fallback_helper": "input_text.fallback", "goal_helper": "input_number.goal"})
            instance = coordinator.ZontBridgeCoordinator(hass, entry)
            instance.async_start()
            self.assertTrue(instance.data.boiler_available)
            instance.async_start()
            self.assertEqual(len(instance.updates), 1)
            clock.utcnow = lambda: NOW + timedelta(seconds=181)
            callbacks["time"](None)
            self.assertFalse(instance.data.boiler_available)
            self.assertFalse(instance.data.control_available)
            instance.async_stop()
            self.assertCountEqual(removed, ["state", "time"])
            count = len(instance.updates)
            callbacks["time"](None)
            callbacks["state"](None)
            instance.async_stop()
            self.assertEqual(len(instance.updates), count)
