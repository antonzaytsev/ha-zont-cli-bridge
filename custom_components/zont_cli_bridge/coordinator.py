"""Local state observer with an explicit wall-clock expiry timer."""
from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import CONF_BRIDGE_ENTITY, CONF_FALLBACK_HELPER, CONF_GOAL_HELPER, CONF_PRIMARY_HELPER, DOMAIN
from .model import BridgeSnapshot, project

_LOGGER = logging.getLogger(__name__)


class ZontBridgeCoordinator(DataUpdateCoordinator[BridgeSnapshot]):
    """Observe HA state only; never contact ZONT or write a helper here."""

    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN)
        self.entry = entry
        self._unsubscribers = []
        self._closed = True

    @callback
    def async_start(self):
        if not self._closed:
            return
        self._closed = False
        entities = [self.entry.data[key] for key in (CONF_BRIDGE_ENTITY, CONF_PRIMARY_HELPER, CONF_FALLBACK_HELPER, CONF_GOAL_HELPER)]
        self._unsubscribers = [
            async_track_state_change_event(self.hass, entities, self._state_changed),
            async_track_time_interval(self.hass, self._time_changed, timedelta(seconds=15)),
        ]
        self.async_refresh_snapshot()

    @callback
    def async_stop(self):
        self._closed = True
        while self._unsubscribers:
            self._unsubscribers.pop()()

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
        state = self.hass.states.get(self.entry.data[CONF_BRIDGE_ENTITY])
        attrs = dict(state.attributes) if state else None
        self.async_set_updated_data(project(attrs, state.state if state else None, dt_util.utcnow()))
