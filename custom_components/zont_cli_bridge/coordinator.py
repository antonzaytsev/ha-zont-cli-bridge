"""Standalone runtime owner and legacy read-only observer."""
import asyncio
from datetime import timedelta
import logging

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import CONF_BRIDGE_ENTITY, DOMAIN
from .model import project

_LOGGER = logging.getLogger(__name__)


class ZontBridgeCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, runtime=None):
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN)
        self.entry, self.runtime = entry, runtime
        self.raw_status = {}
        self._unsubscribers = []
        self._closed = True
        self._poll_task = None
        self._runtime_lock = asyncio.Lock()
        self._reauth_started = False

    @callback
    def async_start(self):
        if not self._closed:
            return
        self._closed = False
        self._unsubscribers.append(async_track_time_interval(self.hass, self._time_changed, timedelta(seconds=15)))
        if self.runtime is None:
            self._unsubscribers.append(async_track_state_change_event(self.hass, [self.entry.data[CONF_BRIDGE_ENTITY]], self._state_changed))
        else:
            self._unsubscribers.append(async_track_time_interval(self.hass, self._poll, timedelta(seconds=30)))
        self.async_refresh_snapshot()

    @callback
    def _poll(self, _now):
        if not self._closed and not self._runtime_lock.locked() and (self._poll_task is None or self._poll_task.done()):
            self._poll_task = self.hass.async_create_task(self.async_tick())

    async def async_tick(self):
        if self._closed or self.runtime is None:
            return
        try:
            async with self._runtime_lock:
                self.raw_status = await self.runtime.tick()
            if self.raw_status.get("state") == "authentication_failed" and not self._reauth_started:
                self._reauth_started = True
                self.entry.async_start_reauth(self.hass)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.raw_status = {**self.raw_status, "state": "runtime_error", "control_available": False}
        self.async_refresh_snapshot()

    async def async_stop(self):
        self._closed = True
        while self._unsubscribers:
            self._unsubscribers.pop()()
        if self._poll_task is not None and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if self.runtime is not None:
            await self.runtime.close()

    @callback
    def _state_changed(self, _event):
        self.async_refresh_snapshot()

    @callback
    def _time_changed(self, _now):
        self.async_refresh_snapshot()

    @callback
    def async_refresh_snapshot(self):
        if self._closed:
            return
        if self.runtime is None:
            state = self.hass.states.get(self.entry.data[CONF_BRIDGE_ENTITY])
            attrs = dict(state.attributes) if state else {}
            attrs["control_available"] = False
            self.raw_status = {**attrs, "state": state.state if state else "unavailable"}
        self.async_set_updated_data(project(self.raw_status, self.raw_status.get("state"), dt_util.utcnow()))

    async def async_set_target(self, value):
        if self.runtime is None or self._closed:
            raise ValueError("read_only")
        async with self._runtime_lock:
            result = await self.runtime.set_target(value)
        if isinstance(result, dict):
            self.raw_status = result
        self.async_refresh_snapshot()
