import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from himotheelight.config_store import ConfigStore, default_device_modes, trigger_effect_template, trigger_template
from himotheelight.lighting import LightingStateManager
from himotheelight.trigger_engine import TriggerEngine, normalize_dart_name


class MockWLEDHandler(BaseHTTPRequestHandler):
    payloads = []

    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        if self.path != "/json/state":
            self.send_response(404); self.end_headers(); return
        length = int(self.headers.get("Content-Length", "0"))
        MockWLEDHandler.payloads.append(json.loads(self.rfile.read(length).decode("utf-8")))
        raw = b'{"success":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers(); self.wfile.write(raw)


class TriggerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), MockWLEDHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close()

    def setUp(self):
        MockWLEDHandler.payloads.clear()
        self.temp = tempfile.TemporaryDirectory()
        self.store = ConfigStore(Path(self.temp.name))
        modes = default_device_modes()
        modes["idle"]["brightness"] = 25
        modes["active"]["brightness"] = 190
        self.store.update(lambda cfg: cfg.update({
            "devices": [{
                "id": "w1", "name": "Mock WLED", "enabled": True,
                "host": f"http://127.0.0.1:{self.port}", "modes": modes,
            }],
            "triggers": [],
        }))
        self.lighting = LightingStateManager(self.store)
        self.engine = TriggerEngine(self.store, self.lighting)
        self.lighting.apply_base_mode("active", source="test", reason="test active", force=True)

    def tearDown(self):
        self.lighting.cancel_trigger("test cleanup", restore=False)
        self.temp.cleanup()

    def add_trigger(self, name, kind, value="T20", priority=20, duration=150, brightness=240, minimum=0, maximum=180):
        trig = trigger_template(name, kind, value=value, minimum=minimum, maximum=maximum,
                                duration_ms=duration, priority=priority, enabled=True)
        effect = trigger_effect_template(); effect["brightness"] = brightness
        trig["effect"] = effect
        self.store.update(lambda cfg: cfg["triggers"].append(trig))
        return trig

    def test_dart_aliases(self):
        self.assertEqual(normalize_dart_name("D25"), "BULL")
        self.assertEqual(normalize_dart_name("SB"), "25")
        self.assertEqual(normalize_dart_name("outside"), "MISS")

    def test_timed_dart_trigger_restores_active(self):
        self.add_trigger("T20 Flash", "dart", value="T20", brightness=241, duration=120)
        result = self.engine.dispatch({"kind": "dart", "dart": "T20", "score": 60})
        self.assertTrue(result["results"][0]["accepted"])
        self.assertEqual(MockWLEDHandler.payloads[-1]["bri"], 241)
        self.assertIsNotNone(self.lighting.trigger_snapshot()["active"])
        time.sleep(0.20)
        self.assertIsNone(self.lighting.trigger_snapshot()["active"])
        self.assertEqual(MockWLEDHandler.payloads[-1]["bri"], 190)

    def test_lower_priority_cannot_interrupt(self):
        self.add_trigger("High", "dart", value="T20", priority=80, duration=500, brightness=250)
        self.add_trigger("Low", "dart", value="D20", priority=10, duration=500, brightness=100)
        self.engine.dispatch({"kind": "dart", "dart": "T20", "score": 60})
        count = len(MockWLEDHandler.payloads)
        result = self.engine.dispatch({"kind": "dart", "dart": "D20", "score": 40})
        self.assertFalse(result["results"][0]["accepted"])
        self.assertEqual(len(MockWLEDHandler.payloads), count)
        self.assertEqual(self.lighting.trigger_snapshot()["active"]["name"], "High")

    def test_180_visit_fires_only_on_third_dart(self):
        self.add_trigger("Maximum", "visit_exact", value="180", priority=80, duration=300, brightness=252)
        t20 = {"name": "T20", "score": 60, "multiplier": 3, "number": 20, "bed": "Triple"}
        base_count = len(MockWLEDHandler.payloads)
        self.engine.handle_autodarts_state({"event":"Throw detected","num_throws":1,"previous_num_throws":0,"throws":[t20],"last_throw":t20})
        self.engine.handle_autodarts_state({"event":"Throw detected","num_throws":2,"previous_num_throws":1,"throws":[t20,t20],"last_throw":t20})
        self.assertEqual(len(MockWLEDHandler.payloads), base_count)
        self.engine.handle_autodarts_state({"event":"Throw detected","num_throws":3,"previous_num_throws":2,"throws":[t20,t20,t20],"last_throw":t20})
        # The 180 celebration must be visible first. Yellow is queued as the
        # follow-up and begins only after the configured 180 duration ends.
        self.assertTrue(any(row.get("trigger") == "Maximum" and row.get("accepted") for row in self.engine.snapshot()["history"]))
        self.assertEqual(MockWLEDHandler.payloads[-1]["bri"], 252)
        active = self.lighting.trigger_snapshot()["active"]
        self.assertEqual(active["name"], "Maximum")
        self.assertEqual(active.get("followup"), "Takeout -> Yellow")
        time.sleep(0.36)
        self.assertEqual(MockWLEDHandler.payloads[-1]["seg"]["col"][0], [255,255,0])
        self.assertEqual(self.lighting.trigger_snapshot()["active"]["name"], "Takeout • Yellow")


    def test_board_event_takeout_started_fires(self):
        # Generic Board Manager behaviour remains available when the automatic
        # three-dart takeout flow is disabled.
        self.store.update(lambda cfg: cfg["autodarts"].__setitem__("takeout_flow_enabled", False))
        self.add_trigger("Takeout", "board_event", value="Takeout started", brightness=244, duration=120)
        self.engine.handle_autodarts_state({
            "event":"Takeout started", "status":"Takeout in progress", "num_throws":0,
            "previous_num_throws":0, "throws":[], "last_throw":None,
        })
        self.assertEqual(MockWLEDHandler.payloads[-1]["bri"], 244)
        self.assertEqual(self.lighting.trigger_snapshot()["active"]["name"], "Takeout")

    def test_board_event_throw_detected_is_selectable_separately_from_dart(self):
        self.add_trigger("Any Throw", "board_event", value="Throw detected", brightness=245, duration=120)
        t20 = {"name":"T20","score":60,"multiplier":3,"number":20,"bed":"Triple"}
        self.engine.handle_autodarts_state({
            "event":"Throw detected", "status":"Throw", "num_throws":1,
            "previous_num_throws":0, "throws":[t20], "last_throw":t20,
        })
        self.assertEqual(MockWLEDHandler.payloads[-1]["bri"], 245)
        self.assertEqual(self.lighting.trigger_snapshot()["active"]["name"], "Any Throw")

    def test_visit_range_completes_on_takeout(self):
        self.add_trigger("100-139", "visit_range", minimum=100, maximum=139, duration=300, brightness=230)
        t20 = {"name":"T20","score":60}
        d20 = {"name":"D20","score":40}
        self.engine.handle_autodarts_state({"event":"Throw detected","num_throws":1,"previous_num_throws":0,"throws":[t20],"last_throw":t20})
        self.engine.handle_autodarts_state({"event":"Throw detected","num_throws":2,"previous_num_throws":1,"throws":[t20,d20],"last_throw":d20})
        self.engine.handle_autodarts_state({"event":"Takeout started","num_throws":2,"previous_num_throws":2,"throws":[t20,d20],"last_throw":None})
        self.assertEqual(MockWLEDHandler.payloads[-1]["bri"], 230)


if __name__ == "__main__":
    unittest.main()
