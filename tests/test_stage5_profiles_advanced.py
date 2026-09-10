import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from himotheelight.config_store import ConfigStore, default_device_modes, trigger_effect_template, trigger_template
from himotheelight.game_bridge import AutodartsGameBridge
from himotheelight.lighting import LightingStateManager
from himotheelight.profiles import ProfileManager
from himotheelight.trigger_engine import TriggerEngine, parse_combination, trigger_matches


class MockWLED(BaseHTTPRequestHandler):
    payloads = []

    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        if self.path != "/json/state":
            self.send_response(404); self.end_headers(); return
        n = int(self.headers.get("Content-Length", "0"))
        MockWLED.payloads.append(json.loads(self.rfile.read(n).decode("utf-8")))
        raw = b'{"success":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers(); self.wfile.write(raw)


class Stage5ProfilesAdvancedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), MockWLED)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close()

    def setUp(self):
        MockWLED.payloads.clear()
        self.temp = tempfile.TemporaryDirectory()
        self.store = ConfigStore(Path(self.temp.name))
        modes = default_device_modes()
        modes["idle"]["brightness"] = 25
        modes["active"]["brightness"] = 190
        self.store.update(lambda cfg: cfg.update({
            "devices": [{
                "id": "w1", "name": "Mock", "host": f"http://127.0.0.1:{self.server.server_address[1]}",
                "enabled": True, "modes": modes,
            }],
            "triggers": [],
        }))
        self.lighting = LightingStateManager(self.store)
        self.engine = TriggerEngine(self.store, self.lighting)
        self.profiles = ProfileManager(self.store, self.lighting)
        self.profiles.sync_active()
        self.lighting.apply_base_mode("active", source="test", reason="active", force=True)

    def tearDown(self):
        self.lighting.cancel_trigger("cleanup", restore=False)
        self.temp.cleanup()

    def add_trigger(self, name, kind, value, priority, brightness):
        trig = trigger_template(name, kind, value=value, enabled=True, duration_ms=300, priority=priority)
        fx = trigger_effect_template(); fx["brightness"] = brightness; trig["effect"] = fx
        self.store.update(lambda cfg: cfg["triggers"].append(trig))
        return trig

    def test_v4_migration_seeds_profile_from_exact_current_setup(self):
        legacy_dir = Path(self.temp.name) / "v4"
        legacy_dir.mkdir()
        modes = default_device_modes(); modes["active"]["brightness"] = 177
        trig = trigger_template("Legacy Enabled", "dart", value="T20", enabled=True)
        legacy = {
            "schema_version": 4,
            "app": {"last_manual_mode":"active", "current_mode":"active"},
            "autodarts": {},
            "devices": [{"id":"legacy","name":"Legacy","host":"http://192.168.1.50","enabled":True,"modes":modes}],
            "triggers": [trig],
        }
        (legacy_dir / "config.json").write_text(json.dumps(legacy), encoding="utf-8")
        migrated = ConfigStore(legacy_dir).get()
        profile = migrated["profiles"][0]
        self.assertEqual(migrated["schema_version"], 8)
        self.assertEqual(profile["device_modes"]["legacy"]["active"]["brightness"], 177)
        self.assertEqual(profile["triggers"][0]["name"], "Legacy Enabled")
        self.assertTrue(profile["triggers"][0]["enabled"])

    def test_combination_parser_accepts_two_or_three_darts(self):
        self.assertEqual(parse_combination("T20_T20_T20"), ["T20", "T20", "T20"])
        self.assertEqual(parse_combination("s10 > d16"), ["S10", "D16"])

    def test_combination_and_visit_compete_by_priority_in_one_dispatch(self):
        self.add_trigger("Maximum", "visit_exact", "180", 80, 220)
        self.add_trigger("Exact 3xT20", "combination", "T20_T20_T20", 90, 250)
        result = self.engine.dispatch({"kind":"visit", "score":180, "darts":["T20","T20","T20"]})
        self.assertEqual(result["matched"], 2)
        self.assertEqual(result["results"][0]["trigger"], "Exact 3xT20")
        self.assertEqual(MockWLED.payloads[-1]["bri"], 250)
        self.assertEqual(len(result["results"]), 1)

    def test_two_dart_combination_completes_on_takeout(self):
        self.add_trigger("S10 D16", "combination", "S10_D16", 85, 247)
        s10 = {"name":"S10", "score":10}
        d16 = {"name":"D16", "score":32}
        self.engine.handle_autodarts_state({"event":"Throw detected","num_throws":1,"previous_num_throws":0,"throws":[s10],"last_throw":s10})
        self.engine.handle_autodarts_state({"event":"Throw detected","num_throws":2,"previous_num_throws":1,"throws":[s10,d16],"last_throw":d16})
        self.engine.handle_autodarts_state({"event":"Takeout started","num_throws":2,"previous_num_throws":2,"throws":[s10,d16]})
        self.assertEqual(MockWLED.payloads[-1]["bri"], 247)

    def test_target_matcher_and_bridge_target_event(self):
        trig = self.add_trigger("Target Bull", "target", "BULL", 30, 244)
        self.assertTrue(trigger_matches(trig, {"kind":"game_target", "target":"bull"}))
        bridge = AutodartsGameBridge(lambda: self.store.get()["autodarts"], self.lighting.autodarts_game_apply, self.engine.dispatch)
        state = {
            "match_id":"m1", "variant":"ATC", "finished":False, "winner":-1, "game_finished":False,
            "game_winner":-1, "player":0, "current_player_name":"Paul", "current_player_is_bot":False,
            "current_target":"BULL", "turn_id":"t1", "turn_busted":False, "turn_points":0,
            "turn_throw_count":0, "last_dart":"", "round":1, "leg":1, "set":1,
        }
        bridge.ingest({"type":"match_state","route":"match","state":state})
        self.assertEqual(MockWLED.payloads[-1]["bri"], 244)
        self.assertEqual(bridge.snapshot()["current_target"], "BULL")

    def test_profile_switch_restores_modes_and_triggers(self):
        first = self.store.get()["profiles"][0]
        self.store.update(lambda cfg: cfg["devices"][0]["modes"]["active"].__setitem__("brightness", 200))
        t1 = self.add_trigger("Profile A Trigger", "dart", "T20", 20, 230)
        self.profiles.sync_active()

        created = self.profiles.create("Tournament")
        self.store.update(lambda cfg: cfg["devices"][0]["modes"]["active"].__setitem__("brightness", 150))
        self.store.update(lambda cfg: cfg.__setitem__("triggers", []))
        self.profiles.sync_active()

        self.profiles.apply(first["id"])
        cfg = self.store.get()
        self.assertEqual(cfg["app"]["active_profile_id"], first["id"])
        self.assertEqual(cfg["devices"][0]["modes"]["active"]["brightness"], 200)
        self.assertEqual(cfg["triggers"][0]["name"], "Profile A Trigger")
        self.assertEqual(MockWLED.payloads[-1]["bri"], 200)

        self.profiles.apply(created["id"])
        cfg = self.store.get()
        self.assertEqual(cfg["devices"][0]["modes"]["active"]["brightness"], 150)
        self.assertEqual(cfg["triggers"], [])
        self.assertEqual(MockWLED.payloads[-1]["bri"], 150)


if __name__ == "__main__":
    unittest.main()
