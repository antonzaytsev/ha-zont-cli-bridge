"""Standalone transport tests against actual aiohttp loopback HTTP and Comet."""
import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest

import aiohttp
from aiohttp import web

PACKAGE = Path(__file__).resolve().parents[1] / "custom_components/zont_cli_bridge"
def load(name):
    parent = "zont_standalone_tests"
    if parent not in sys.modules:
        package = types.ModuleType(parent)
        package.__path__ = [str(PACKAGE)]
        sys.modules[parent] = package
    qualified = f"{parent}.{name}"
    if qualified in sys.modules:
        return sys.modules[qualified]
    spec = importlib.util.spec_from_file_location(qualified, PACKAGE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    spec.loader.exec_module(module)
    return module

client = load("client")

class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.socket = None
        self.requests = []
        self.behavior = "normal"
        self.now = 100.0
        self.sleeps = []
        self.redirect_hits = 0
        self.redirect_url = None
        app = web.Application()
        app.router.add_get("/ws", self.ws)
        app.router.add_route("*", "/target", self.target)
        app.router.add_post("/{path:.*}", self.api)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.origin = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        self.session = aiohttp.ClientSession()
        self.z = client.ZontClient(self.session, self.origin, "fake-secret-token", "fake-client", 123, 456,
            now=lambda: self.now, sleep=self.sleep, reply_timeout=0.05)

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)

    async def asyncTearDown(self):
        await self.z.close()
        await self.session.close()
        await self.runner.cleanup()

    async def target(self, request):
        self.redirect_hits += 1
        return web.Response(text="must not receive secrets")

    async def ws(self, request):
        if self.behavior == "redirect_ws":
            raise web.HTTPFound(self.redirect_url or self.origin + "/target?leaked=fake-secret-token")
        if self.behavior == "stalled_ws":
            await asyncio.sleep(0.2)
            return web.Response(status=503)
        self.assertEqual(request.query["_auth_token"], "fake-secret-token")
        self.socket = web.WebSocketResponse(protocols=("zont-comet",))
        await self.socket.prepare(request)
        await self.socket.send_json([{"event": "_comet_session", "session": "actual-session"}])
        async for _ in self.socket:
            pass
        return self.socket

    async def api(self, request):
        body = await request.json()
        self.requests.append((request.path, body, request.headers.get("X-ZONT-Token")))
        if self.behavior == "429":
            return web.Response(status=429, headers={"Retry-After": "5"})
        if self.behavior == "429_default":
            return web.Response(status=429)
        if self.behavior == "auth":
            return web.Response(status=401, text="fake-secret-token")
        if self.behavior == "redirect_rest":
            raise web.HTTPFound(self.redirect_url or self.origin + "/target")
        if self.behavior == "chunks":
            response = web.StreamResponse(headers={"Content-Type": "application/json"})
            await response.prepare(request)
            await response.write(b'{"ok":true,"devices":')
            await asyncio.sleep(0.01)
            await response.write(b'[{"id":123}]}')
            await response.write_eof()
            return response
        if self.behavior == "large":
            return web.Response(body=b" " * (8 * 1024 * 1024 + 1))
        if request.path == "/api/devices":
            return web.json_response({"ok": True, "devices": [{"id": 123}]})
        if request.path == "/api/send_text_to_device" and self.behavior != "timeout":
            reply = {"event": "text_response", "request_id": body["request_id"], "ok": True, "text": "#Y456$2943,2942,0,320"}
            if self.behavior != "missing_device":
                reply["device_id"] = 999 if self.behavior == "conflicting" else 123
            if self.behavior == "sentinel":
                reply["text"] = "#Y456$65535,2942,0,337"
            if self.behavior == "malformed":
                reply["text"] = "#Y456$2943,0,0"
            await self.socket.send_json([reply])
        return web.json_response({"ok": True})

    async def test_actual_session_feed_query_and_pacing(self):
        await self.z.connect()
        self.assertAlmostEqual(await self.z.feed(21.3), 21.3)
        self.assertAlmostEqual(await self.z.read_sensor(), 21.3)
        requests = self.requests
        self.assertEqual(requests[0][1]["session_id"], "actual-session")
        self.assertEqual(requests[0][1]["text"], "#Y456=1 21.3")
        self.assertNotEqual(requests[0][1]["request_id"], requests[1][1]["request_id"])
        self.assertEqual(requests[0][2], "fake-secret-token")
        self.assertEqual(self.sleeps[-1], 1)

    async def test_missing_device_accepted_conflicting_and_malformed_retire(self):
        self.behavior = "missing_device"
        await self.z.connect()
        self.assertAlmostEqual(await self.z.feed(21.3), 21.3)
        for behavior in ("conflicting", "malformed"):
            self.behavior = behavior
            if not self.z.connected:
                await self.z.connect()
            with self.assertRaises(client.ZontError):
                await self.z.feed(21.3)
            self.assertFalse(self.z.connected)

    async def test_unavailable_query_is_actual_read_not_valid_feed(self):
        await self.z.connect()
        self.behavior = "sentinel"
        self.assertIsNone(await self.z.read_sensor())
        with self.assertRaises(client.ZontError):
            await self.z.feed(21.3)

    async def test_timeout_retires_session_and_reconnects(self):
        await self.z.connect()
        generation = self.z._generation
        self.behavior = "timeout"
        with self.assertRaisesRegex(client.ZontError, "controller_exchange_failed"):
            await self.z.feed(21.3)
        self.assertGreater(self.z._generation, generation)
        self.assertFalse(self.z.connected)
        self.behavior = "normal"
        await self.z.connect()
        self.assertAlmostEqual(await self.z.feed(21.3), 21.3)

    async def test_429_retry_after_default_and_no_repeat(self):
        for behavior, delay in (("429", 5), ("429_default", 60)):
            self.behavior = behavior
            with self.assertRaises(client.ZontError):
                await self.z.devices()
            count = len(self.requests)
            with self.assertRaisesRegex(client.ZontError, "rate_limited"):
                await self.z.devices()
            self.assertEqual(len(self.requests), count)
            self.now += delay + 1

    async def test_rest_and_websocket_redirects_never_reach_target(self):
        app = web.Application()
        app.router.add_route("*", "/target", self.target)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        self.redirect_url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/target?fake-secret-token"
        try:
            self.behavior = "redirect_rest"
            with self.assertRaises(client.ZontError):
                await self.z.devices()
            self.behavior = "redirect_ws"
            with self.assertRaisesRegex(client.ZontError, "comet_connection_failed"):
                await self.z.connect()
            self.assertEqual(self.redirect_hits, 0)
        finally:
            await runner.cleanup()

    async def test_opening_handshake_has_separate_bounded_timeout(self):
        self.behavior = "stalled_ws"
        self.z._connect_timeout = 0.01
        with self.assertRaisesRegex(client.ZontError, "comet_connection_failed"):
            await asyncio.wait_for(self.z.connect(), timeout=0.1)
        self.assertFalse(self.z.connected)

    async def test_streamed_json_and_bounded_payload(self):
        self.behavior = "chunks"
        self.assertEqual(await self.z.full(), {"id": 123})
        self.behavior = "large"
        with self.assertRaisesRegex(client.ZontError, "response_too_large"):
            await self.z.devices()

    async def test_errors_never_contain_secret_url_or_body(self):
        self.behavior = "auth"
        with self.assertRaises(client.ZontAuthError) as caught:
            await self.z.devices()
        self.assertEqual(str(caught.exception), "authentication_failed")
        self.assertNotIn("fake-secret-token", str(caught.exception))

    async def test_guard_runs_after_pacing_before_http(self):
        await self.z.devices()
        count = len(self.requests)
        def guard():
            self.assertGreaterEqual(self.now, 101)
            raise client.ZontError("expired")
        with self.assertRaisesRegex(client.ZontError, "expired"):
            await self.z._request("/api/devices", {}, guard)
        self.assertEqual(len(self.requests), count)

    async def test_old_exchange_failure_does_not_retire_new_generation(self):
        await self.z.connect()
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.z._request
        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            raise client.ZontError("late_failure")
        self.z._request = delayed
        task = asyncio.create_task(self.z.feed(21.3))
        await entered.wait()
        await self.z._retire()
        self.z._request = original
        await self.z.connect()
        generation = self.z._generation
        release.set()
        with self.assertRaises(client.ZontError):
            await task
        self.assertEqual(self.z._generation, generation)
        self.assertTrue(self.z.connected)

class BoundaryTests(unittest.TestCase):
    def test_origin(self):
        self.assertEqual(client.normalize_origin("https://MY.ZONT.ONLINE:443/"), "https://my.zont.online")
        for invalid in ("http://my.zont.online", "https://x/path", "https://u:p@x", "https://x?a=b", "https://x#secret", "http://localhost:123"):
            with self.assertRaises(client.ZontError):
                client.normalize_origin(invalid)

    def test_four_field_decoder(self):
        self.assertAlmostEqual(client.decode_sensor("#Y456$2943,0,0,320", 456), 21.3)
        for value in ("#Y456$65535,0,0,320", "#Y456$2943,0,0,321", "#Y456$2943,0,0,328", "#Y456$2943,0,0", "#Y999$2943,0,0,320"):
            with self.assertRaises(client.ZontError):
                client.decode_sensor(value, 456)

if __name__ == "__main__":
    unittest.main()
