import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from himotheelight.autodarts import AutodartsClient, normalize_board_host
from himotheelight.config_store import ConfigStore, default_device_modes
from himotheelight.lighting import LightingStateManager


class MockCombinedHandler(BaseHTTPRequestHandler):
    payloads = []

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path == "/api/state":
            return self._send({
                "connected": True,
                "running": True,
                "status": "Throw",
                "event": "Started",
                "numThrows": 0,
            })
        self.send_response(404); self.end_headers()

    def do_POST(self):
        if self.path != "/json/state":
            self.send_response(404); self.end_headers(); return
        length = int(self.headers.get("Content-Length", "0"))
        MockCombinedHandler.payloads.append(json.loads(self.rfile.read(length).decode("utf-8")))
        self._send({"success": True})

    def _send(self, body):
        raw = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class AutodartsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), MockCombinedHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close()

    def setUp(self):
        MockCombinedHandler.payloads.clear()
        self.temp = tempfile.TemporaryDirectory()
        self.store = ConfigStore(Path(self.temp.name))
        modes = default_device_modes()
        modes["idle"]["brightness"] = 33
        modes["active"]["brightness"] = 211
        self.store.update(lambda cfg: cfg["devices"].append({
            "id": "mock1",
            "name": "Mock WLED",
            "host": f"http://127.0.0.1:{self.port}",
            "enabled": True,
            "modes": modes,
        }))
        self.manager = LightingStateManager(self.store)
        self.settings = self.store.get()["autodarts"]
        self.client = AutodartsClient(lambda: self.settings, self.manager.autodarts_apply)

    def tearDown(self):
        self.temp.cleanup()

    def test_host_normalization(self):
        self.assertEqual(normalize_board_host("http://127.0.0.1:3180/api/state"), "127.0.0.1")
        self.assertEqual(normalize_board_host("localhost:3180"), "localhost")

    def test_http_probe(self):
        result = self.client.test_connection("127.0.0.1", self.port)
        self.assertTrue(result["ok"])
        self.assertTrue(result["running"])
        self.assertEqual(result["status"], "Throw")

    def test_started_and_stopped_switch_base_lighting(self):
        self.client.simulate("started")
        self.assertEqual(self.manager.snapshot()["mode"], "active")
        self.assertEqual(MockCombinedHandler.payloads[-1]["bri"], 211)

        self.client.simulate("stopped")
        self.assertEqual(self.manager.snapshot()["mode"], "idle")
        self.assertEqual(MockCombinedHandler.payloads[-1]["bri"], 33)



    def test_takeout_simulations_expose_real_board_manager_event_names(self):
        self.client.simulate("takeout_started")
        snap = self.client.snapshot()
        self.assertEqual(snap["event"], "Takeout started")
        self.assertEqual(snap["status"], "Takeout in progress")
        self.client.simulate("takeout_finished")
        snap = self.client.snapshot()
        self.assertEqual(snap["event"], "Takeout finished")
        self.assertEqual(snap["status"], "Throw")

    def test_v01_config_migrates_without_losing_devices(self):
        import json as _json
        old_dir = Path(self.temp.name) / "migration"
        old_dir.mkdir()
        old = {
            "schema_version": 1,
            "app": {"last_manual_mode": "active"},
            "devices": [{
                "id": "legacy", "name": "Legacy WLED", "host": "http://192.168.1.50",
                "enabled": True, "modes": default_device_modes(),
            }],
        }
        (old_dir / "config.json").write_text(_json.dumps(old), encoding="utf-8")
        migrated = ConfigStore(old_dir).get()
        self.assertEqual(migrated["schema_version"], 8)
        self.assertTrue(migrated["profiles"])
        self.assertEqual(migrated["app"]["active_profile_id"], migrated["profiles"][0]["id"])
        self.assertEqual(migrated["devices"][0]["name"], "Legacy WLED")
        self.assertEqual(migrated["app"]["current_mode"], "active")
        self.assertEqual(migrated["autodarts"]["port"], 3180)
        self.assertGreaterEqual(len(migrated["triggers"]), 5)
        self.assertTrue(all(not t["enabled"] for t in migrated["triggers"]))

    def test_throw_updates_live_state_without_leaving_active(self):
        self.client.simulate("started")
        count = len(MockCombinedHandler.payloads)
        self.client.simulate("throw")
        snap = self.client.snapshot()
        self.assertEqual(snap["last_throw"]["name"], "T20")
        self.assertEqual(snap["last_throw"]["score"], 60)
        self.assertEqual(snap["num_throws"], 1)
        self.assertEqual(self.manager.snapshot()["mode"], "active")
        self.assertEqual(len(MockCombinedHandler.payloads), count)


if __name__ == "__main__":
    unittest.main()
