import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from himotheelight.wled import apply_mode, build_mode_payload, normalize_host, probe


class MockWLEDHandler(BaseHTTPRequestHandler):
    last_payload = None

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path == "/json":
            body = {
                "state": {"on": True, "bri": 100, "seg": [{"id": 0, "start": 0, "stop": 60, "fx": 0, "pal": 0}]},
                "info": {"name": "Mock Board", "ver": "0.15.0", "arch": "esp32", "ip": "127.0.0.1", "leds": {"count": 60, "maxseg": 16}},
                "effects": ["Solid", "RSVD", "Blink", "-"],
                "palettes": ["Default", "Party"],
            }
            return self._send(body)
        if self.path == "/presets.json":
            return self._send({"0": {}, "1": {"n": "Idle Purple"}, "2": {"n": "Maximum"}})
        if self.path == "/json/state":
            return self._send({"on": True, "bri": 100, "seg": []})
        self.send_response(404); self.end_headers()

    def do_POST(self):
        if self.path != "/json/state":
            self.send_response(404); self.end_headers(); return
        length = int(self.headers.get("Content-Length", "0"))
        MockWLEDHandler.last_payload = json.loads(self.rfile.read(length).decode("utf-8"))
        self._send({"success": True})

    def _send(self, body):
        raw = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class WLEDTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), MockWLEDHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.host = f"127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close()

    def test_normalize(self):
        self.assertEqual(normalize_host(self.host), f"http://{self.host}")

    def test_probe_filters_reserved_effects_and_loads_presets(self):
        meta = probe(self.host)
        self.assertEqual(meta["name"], "Mock Board")
        self.assertEqual([e["id"] for e in meta["effects"]], [0, 2])
        self.assertEqual(meta["presets"][1]["name"], "Maximum")
        self.assertEqual(meta["segments"][0]["stop"], 60)

    def test_build_direct_mode(self):
        payload = build_mode_payload({
            "source": "direct", "on": True, "brightness": 200, "effect_id": 2,
            "palette_id": 1, "speed": 220, "intensity": 180, "transition_ms": 500,
            "segment_id": 0, "colors": [[1,2,3],[4,5,6],[7,8,9]],
        })
        self.assertEqual(payload["bri"], 200)
        self.assertEqual(payload["tt"], 5)
        self.assertEqual(payload["seg"][0]["fx"], 2)
        self.assertEqual(payload["seg"][0]["col"][1], [4,5,6])

    def test_apply_preset(self):
        apply_mode(self.host, {"source":"preset", "on":True, "brightness":150, "preset_id":2, "transition_ms":0})
        self.assertEqual(MockWLEDHandler.last_payload["ps"], 2)
        self.assertEqual(MockWLEDHandler.last_payload["bri"], 150)


if __name__ == "__main__":
    unittest.main()
