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
from himotheelight.trigger_engine import TriggerEngine


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


class Stage4GameBridgeTests(unittest.TestCase):
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
        modes = default_device_modes(); modes["idle"]["brightness"] = 25; modes["active"]["brightness"] = 190
        self.store.update(lambda cfg: cfg.update({
            "devices": [{"id":"w1","name":"Mock","host":f"http://127.0.0.1:{self.server.server_address[1]}","enabled":True,"modes":modes}],
            "triggers": [],
        }))
        self.lighting = LightingStateManager(self.store)
        self.engine = TriggerEngine(self.store, self.lighting)
        self.bridge = AutodartsGameBridge(
            lambda: self.store.get().get("autodarts", {}),
            self.lighting.autodarts_game_apply,
            self.engine.dispatch,
        )

    def tearDown(self):
        self.lighting.cancel_trigger("cleanup", restore=False)
        self.temp.cleanup()

    def add_game_trigger(self, name, event, brightness, duration=120, priority=80):
        trig = trigger_template(name, "game_event", value=event, enabled=True, duration_ms=duration, priority=priority)
        fx = trigger_effect_template(); fx["brightness"] = brightness; trig["effect"] = fx
        self.store.update(lambda cfg: cfg["triggers"].append(trig))
        return trig

    @staticmethod
    def state(**changes):
        base = {
            "match_id":"match-1", "variant":"X01", "finished":False, "winner":-1,
            "game_finished":False, "game_winner":-1, "player":0,
            "current_player_name":"Paul", "current_player_is_bot":False,
            "turn_id":"turn-1", "turn_busted":False, "turn_points":0,
            "turn_throw_count":0, "last_dart":"", "round":1, "leg":1, "set":1,
            "winner_name":"", "game_winner_name":"",
        }
        base.update(changes)
        return base

    def ingest(self, state):
        return self.bridge.ingest({"type":"match_state","route":"match","page_url":"https://play.autodarts.com/matches/match-1","state":state})

    def test_first_match_frame_syncs_active_without_false_trigger(self):
        self.add_game_trigger("Bust", "bust", 240)
        self.ingest(self.state())
        self.assertEqual(self.lighting.snapshot()["mode"], "active")
        self.assertEqual(MockWLED.payloads[-1]["bri"], 190)
        self.assertIsNone(self.lighting.trigger_snapshot()["active"])

    def test_bust_fires_once_per_turn(self):
        self.add_game_trigger("Bust", "bust", 241, duration=300)
        self.ingest(self.state(turn_throw_count=2, last_dart="T20"))
        self.ingest(self.state(turn_busted=True, turn_points=100, turn_throw_count=3, last_dart="T20"))
        self.assertEqual(MockWLED.payloads[-1]["bri"], 241)
        count = len(MockWLED.payloads)
        self.ingest(self.state(turn_busted=True, turn_points=100, turn_throw_count=3, last_dart="T20"))
        self.assertEqual(len(MockWLED.payloads), count)

    def test_gameshot_restores_active(self):
        self.add_game_trigger("Game Shot", "gameshot", 245, duration=120, priority=90)
        self.ingest(self.state(turn_throw_count=2))
        self.ingest(self.state(game_finished=True, game_winner=0, game_winner_name="Paul", turn_throw_count=3, last_dart="D20"))
        self.assertEqual(MockWLED.payloads[-1]["bri"], 245)
        time.sleep(0.20)
        self.assertEqual(MockWLED.payloads[-1]["bri"], 190)
        self.assertEqual(self.lighting.snapshot()["mode"], "active")

    def test_matchshot_finishes_celebration_then_restores_idle(self):
        self.add_game_trigger("Match Shot", "matchshot", 250, duration=120, priority=100)
        self.ingest(self.state(turn_throw_count=2))
        self.ingest(self.state(
            finished=True, winner=0, winner_name="Paul", game_finished=True, game_winner=0,
            game_winner_name="Paul", turn_throw_count=3, last_dart="D20"
        ))
        self.assertEqual(MockWLED.payloads[-1]["bri"], 250)
        self.assertEqual(self.lighting.snapshot()["mode"], "idle")
        self.assertIsNotNone(self.lighting.trigger_snapshot()["active"])
        time.sleep(0.20)
        self.assertIsNone(self.lighting.trigger_snapshot()["active"])
        self.assertEqual(MockWLED.payloads[-1]["bri"], 25)

    def test_player_turn_matches_name(self):
        trig = trigger_template("Paul Turn", "player_turn", value="Paul", enabled=True, duration_ms=150, priority=30)
        fx = trigger_effect_template(); fx["brightness"] = 235; trig["effect"] = fx
        self.store.update(lambda cfg: cfg["triggers"].append(trig))
        self.ingest(self.state(turn_id="turn-1", player=1, current_player_name="Adam"))
        self.ingest(self.state(turn_id="turn-2", player=0, current_player_name="Paul", turn_throw_count=0))
        self.assertEqual(MockWLED.payloads[-1]["bri"], 235)


if __name__ == "__main__":
    unittest.main()
