import json
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from himotheelight.config_store import ConfigStore, default_device_modes, trigger_effect_template, trigger_template
from himotheelight.logging_buffer import MemoryLogHandler
from himotheelight.server import build_server


class MockWLED(BaseHTTPRequestHandler):
    payloads = []
    def log_message(self, fmt, *args): pass
    def do_POST(self):
        if self.path != "/json/state":
            self.send_response(404); self.end_headers(); return
        n = int(self.headers.get("Content-Length", "0"))
        MockWLED.payloads.append(json.loads(self.rfile.read(n).decode()))
        raw = b'{"success":true}'
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)


class Stage3IntegrationTest(unittest.TestCase):
    def test_http_simulation_runs_trigger_and_restores_active(self):
        wled = HTTPServer(("127.0.0.1", 0), MockWLED)
        wled_thread = threading.Thread(target=wled.serve_forever, daemon=True); wled_thread.start()
        temp = tempfile.TemporaryDirectory()
        try:
            store = ConfigStore(Path(temp.name))
            modes = default_device_modes(); modes["idle"]["brightness"] = 30; modes["active"]["brightness"] = 190
            trig = trigger_template("T20 API", "dart", value="T20", enabled=True, duration_ms=120, priority=40)
            effect = trigger_effect_template(); effect["brightness"] = 245; trig["effect"] = effect
            def seed(cfg):
                cfg["autodarts"]["enabled"] = False
                cfg["autodarts"]["auto_connect"] = False
                cfg["devices"] = [{"id":"w1","name":"Mock","host":f"http://127.0.0.1:{wled.server_address[1]}","enabled":True,"modes":modes}]
                cfg["triggers"] = [trig]
            store.update(seed)

            web_dir = Path(__file__).resolve().parents[1] / "web"
            app = build_server("127.0.0.1", 0, store, web_dir, MemoryLogHandler(100))
            app.context.start()
            app_thread = threading.Thread(target=app.serve_forever, daemon=True); app_thread.start()
            base = f"http://127.0.0.1:{app.server_address[1]}"

            def post(path, body):
                req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST", headers={"Content-Type":"application/json"})
                with urllib.request.urlopen(req, timeout=2) as response:
                    return json.loads(response.read())

            MockWLED.payloads.clear()
            post("/api/autodarts/simulate", {"event":"started"})
            self.assertEqual(MockWLED.payloads[-1]["bri"], 190)
            post("/api/autodarts/simulate", {"event":"throw"})
            self.assertEqual(MockWLED.payloads[-1]["bri"], 245)
            time.sleep(0.30)
            self.assertEqual(MockWLED.payloads[-1]["bri"], 190)
            self.assertIsNone(app.context.lighting.trigger_snapshot()["active"])
        finally:
            try:
                app.context.stop(); app.shutdown(); app.server_close()
            except Exception:
                pass
            wled.shutdown(); wled.server_close(); temp.cleanup()


if __name__ == "__main__":
    unittest.main()
