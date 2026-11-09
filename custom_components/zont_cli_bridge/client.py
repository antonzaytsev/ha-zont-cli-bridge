"""Bounded asynchronous ZONT transport. Never expose authenticated URLs/errors."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import math
import re
import secrets
import time
from urllib.parse import urlsplit, urlencode

import aiohttp


class ZontError(Exception):
    """An intentionally bounded diagnostic code, never a remote error string."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ZontAuthError(ZontError):
    """Credentials rejected by the server."""


def normalize_origin(origin: str) -> str:
    """Allow only an HTTPS origin, or an explicit numeric loopback test origin."""
    try:
        url = urlsplit(origin)
        if (url.username or url.password or url.query or url.fragment or
                url.path not in ("", "/") or not url.hostname or
                (url.scheme != "https" and not
                 (url.scheme == "http" and url.hostname in ("127.0.0.1", "::1") and url.port))):
            raise ValueError
        port = url.port
        host = url.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        if any(c.isspace() for c in origin):
            raise ValueError
        return f"{url.scheme}://{host}" + (f":{port}" if port and not (url.scheme == "https" and port == 443) else "")
    except (ValueError, TypeError, AttributeError):
        raise ZontError("invalid_origin") from None


def decode_sensor(text, sensor_id: int, allow_unavailable: bool = False) -> float | None:
    if not isinstance(text, str) or len(text) > 65536:
        raise ZontError("invalid_sensor_reply")
    rows = [s.strip() for s in text.splitlines() if s.strip().startswith(f"#Y{sensor_id}$")]
    match = re.fullmatch(rf"#Y{sensor_id}\$(\d+),(\d+),(-?\d+),(\d+)", rows[0]) if len(rows) == 1 else None
    if not match:
        raise ZontError("invalid_sensor_reply")
    raw, previous, unused, flags = map(int, match.groups())
    if raw > 65535 or previous > 65535 or not -2147483648 <= unused <= 2147483647 or flags > 4294967295:
        raise ZontError("invalid_sensor_reply")
    if raw == 65535 or flags & 1 or flags & 8:
        if allow_unavailable:
            return None
        raise ZontError("sensor_unavailable")
    return raw / 10 - 273


validate_origin = normalize_origin


class _NoRedirectWebSocket:
    """Use aiohttp's handshake/parser, with redirects disabled on its HTTP call.

    aiohttp exposes no allow_redirects argument on ws_connect. The narrow facade
    supplies the three session members used by _ws_connect, without modifying
    the shared HA session. Loopback tests pin this adapter's behavior.
    """
    def __init__(self, session):
        self._session = session
        self._loop = asyncio.get_running_loop()
        self._ws_response_class = aiohttp.ClientWebSocketResponse

    def request(self, method, url, **kwargs):
        return self._session.request(method, url, allow_redirects=False,
                                     timeout=aiohttp.ClientTimeout(total=15), **kwargs)


class ZontClient:
    """One controller's REST and Comet session using a caller-owned aiohttp session."""

    def __init__(self, session, origin: str, token: str, client: str,
                 device_id: int | None = None, virtual_sensor_id: int | None = None,
                 *, now=None, sleep=None, reply_timeout: float = 12, pacing: float = 1,
                 connect_timeout: float = 10):
        self.origin = normalize_origin(origin)
        if not all(isinstance(x, str) and x and len(x) <= 8192 and not any(c.isspace() for c in x) for x in (token, client)):
            raise ZontError("invalid_credentials")
        self._http = session
        self._headers = {"X-ZONT-Token": token, "X-ZONT-Client": client}
        self.device_id, self.virtual_sensor_id = device_id, virtual_sensor_id
        self._now, self._sleep = now or time.monotonic, sleep or asyncio.sleep
        self._reply_timeout, self._pacing = reply_timeout, max(1, pacing)
        self._connect_timeout = connect_timeout
        self._rest_lock, self._connect_lock = asyncio.Lock(), asyncio.Lock()
        self._next_request = self._backoff = 0.0
        self._socket = self._reader = self._session_id = None
        self._pending = {}
        self._generation = 0
        self._ready = None
        self._closed = False

    @property
    def connected(self) -> bool:
        return self._socket is not None and not self._socket.closed and self._session_id is not None

    async def _request(self, path: str, body: dict, guard=None) -> dict:
        try:
            async with self._rest_lock:
                if self._closed:
                    raise ZontError("client_closed")
                if self._now() < self._backoff:
                    raise ZontError("rate_limited")
                await self._sleep(max(0, self._next_request - self._now()))
                if guard:
                    guard()
                self._next_request = self._now() + self._pacing
                async with self._http.post(self.origin + path, json=body, headers=self._headers,
                                           allow_redirects=False, timeout=aiohttp.ClientTimeout(total=15)) as response:
                    if response.status in (401, 403):
                        raise ZontAuthError("authentication_failed")
                    if response.status == 429:
                        raw = response.headers.get("Retry-After", "")
                        try:
                            delay = float(raw)
                            if not math.isfinite(delay) or delay <= 0:
                                raise ValueError
                        except ValueError:
                            try:
                                delay = (parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds()
                            except (ValueError, TypeError, OverflowError):
                                delay = 60
                        self._backoff = self._now() + max(1, delay if delay > 0 else 60)
                        raise ZontError("rate_limited")
                    if response.status != 200:
                        raise ZontError("http_failure")
                    payload = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        payload.extend(chunk)
                        if len(payload) > 8 * 1024 * 1024:
                            raise ZontError("response_too_large")
                    data = json.loads(payload)
                    if not isinstance(data, dict) or data.get("ok") is not True:
                        raise ZontError("api_failure")
                    return data
        except asyncio.CancelledError:
            raise
        except ZontError:
            raise
        except Exception:
            raise ZontError("transport_failed") from None

    async def devices(self) -> list[dict]:
        response = await self._request("/api/devices", {"load_io": True})
        devices = response.get("devices")
        if not isinstance(devices, list) or not all(isinstance(d, dict) for d in devices):
            raise ZontError("invalid_devices")
        return devices

    async def full(self, device_id: int | None = None) -> dict:
        wanted = device_id if device_id is not None else self.device_id
        devices = [d for d in await self.devices() if d.get("id", d.get("device_id")) == wanted]
        if len(devices) != 1:
            raise ZontError("device_missing_or_ambiguous")
        return devices[0]

    async def _retire(self):
        self._generation += 1
        self._session_id = None
        for future, _ in self._pending.values():
            if not future.done():
                future.set_exception(ZontError("comet_disconnected"))
        self._pending.clear()
        if self._ready is not None and not self._ready.done():
            self._ready.set_exception(ZontError("comet_disconnected"))
        socket, reader = self._socket, self._reader
        self._socket = self._reader = None
        if reader and reader is not asyncio.current_task():
            reader.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await reader
        if socket is not None:
            with suppress(Exception):
                await socket.close()

    async def _read(self, generation: int, socket):
        try:
            async for message in socket:
                if generation != self._generation:
                    return
                if message.type != aiohttp.WSMsgType.TEXT:
                    raise ZontError("invalid_comet_message")
                events = json.loads(message.data)
                if not isinstance(events, list):
                    raise ZontError("invalid_comet_message")
                for event in events:
                    if not isinstance(event, dict):
                        raise ZontError("invalid_comet_message")
                    kind = event.get("event")
                    if kind == "_comet_session":
                        session = event.get("session")
                        if (self._session_id is not None or not isinstance(session, str) or
                                not re.fullmatch(r"[\x20-\x7e]{1,256}", session) or
                                any(secret in session for secret in self._headers.values())):
                            raise ZontError("invalid_comet_session")
                        self._session_id = session
                        if not self._ready.done():
                            self._ready.set_result(True)
                    elif kind == "_comet_session_expired":
                        raise ZontError("comet_session_expired")
                    elif kind == "text_response" and event.get("request_id") in self._pending:
                        future, allow_unavailable = self._pending[event["request_id"]]
                        if (not self._session_id or event.get("ok") is not True or
                                ("device_id" in event and str(event["device_id"]) != str(self.device_id))):
                            raise ZontError("conflicting_controller_reply")
                        value = decode_sensor(event.get("text"), self.virtual_sensor_id, allow_unavailable)
                        if not future.done():
                            future.set_result(value)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            if generation == self._generation:
                await self._retire()

    async def connect(self):
        async with self._connect_lock:
            if self.connected:
                return
            if self._closed:
                raise ZontError("client_closed")
            if self._now() < self._backoff:
                raise ZontError("rate_limited")
            await self._retire()
            generation = self._generation
            self._ready = asyncio.get_running_loop().create_future()
            try:
                ws_origin = self.origin.replace("https:", "wss:", 1).replace("http:", "ws:", 1)
                url = ws_origin + "/ws?" + urlencode({"_auth_token": self._headers["X-ZONT-Token"]})
                socket = await asyncio.wait_for(aiohttp.ClientSession._ws_connect(_NoRedirectWebSocket(self._http), url, protocols=("zont-comet",),
                    timeout=aiohttp.ClientWSTimeout(ws_close=3), max_msg_size=1048576,
                    autoclose=True, autoping=True), timeout=self._connect_timeout)
                if generation != self._generation or self._closed:
                    await socket.close()
                    raise ZontError("client_closed")
                self._socket = socket
                self._reader = asyncio.create_task(self._read(generation, self._socket))
                await asyncio.wait_for(asyncio.shield(self._ready), timeout=self._connect_timeout)
            except asyncio.CancelledError:
                if generation == self._generation:
                    await self._retire()
                raise
            except Exception:
                if generation == self._generation:
                    await self._retire()
                raise ZontError("comet_connection_failed") from None
            finally:
                if self._ready is not None and self._ready.done() and not self._ready.cancelled():
                    self._ready.exception()

    async def _exchange(self, text, allow_unavailable=False, guard=None):
        if not self.connected or not isinstance(self.virtual_sensor_id, int) or not isinstance(self.device_id, int):
            raise ZontError("comet_session_missing")
        generation, session = self._generation, self._session_id
        request_id = secrets.token_hex(16)
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (future, allow_unavailable)
        def authorize():
            if not self.connected or generation != self._generation or session != self._session_id:
                raise ZontError("comet_session_missing")
            if guard:
                guard()
        try:
            await self._request("/api/send_text_to_device", {"device_id": self.device_id, "text": text,
                "request_id": request_id, "session_id": session}, authorize)
            return await asyncio.wait_for(asyncio.shield(future), timeout=self._reply_timeout)
        except asyncio.CancelledError:
            if generation == self._generation:
                await self._retire()
            raise
        except Exception:
            if generation == self._generation:
                await self._retire()
            raise ZontError("controller_exchange_failed") from None
        finally:
            self._pending.pop(request_id, None)
            if future.done() and not future.cancelled():
                future.exception()
            else:
                future.cancel()

    async def feed(self, value: float, guard=None):
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 5 <= value <= 35:
            raise ZontError("invalid_temperature")
        return await self._exchange(f"#Y{self.virtual_sensor_id}=1 {value:.1f}", guard=guard)

    async def read_sensor(self):
        return await self._exchange(f"#Y{self.virtual_sensor_id}?", allow_unavailable=True)

    async def set_target(self, circuit_id: int, value: float, guard=None):
        if type(circuit_id) is not int or circuit_id <= 0 or isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 5 <= value <= 30:
            raise ZontError("invalid_target")
        return await self._request(f"/api/widget/v3/devices/{self.device_id}/circuits/{circuit_id}/actions/target-temp", {"target_temp": value}, guard)

    async def close(self):
        self._closed = True
        await self._retire()
