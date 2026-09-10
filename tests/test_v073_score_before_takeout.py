import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from himotheelight.config_store import ConfigStore, default_device_modes, trigger_template
from himotheelight.lighting import LightingStateManager
from himotheelight.trigger_engine import TriggerEngine


def dart(name, score, multiplier=1, number=20, bed="SingleOuter"):
    return {"name": name, "score": score, "multiplier": multiplier, "number": number, "bed": bed}


class ScoreBeforeTakeoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ConfigStore(Path(self.temp.name))
        modes = default_device_modes()
        modes["idle"]["brightness"] = 30
        modes["active"]["brightness"] = 180
        cfg = self.store.get()
        cfg["devices"] = [{"id":"d1","name":"WLED","host":"127.0.0.1:19998","enabled":True,"modes":modes}]
        cfg["autodarts"]["takeout_flow_enabled"] = True
        cfg["autodarts"]["takeout_finish_flash_ms"] = 100
        cfg["triggers"] = []
        self.store.save(cfg)
        self.lighting = LightingStateManager(self.store)
        self.engine = TriggerEngine(self.store, self.lighting)
        self.calls = []
        self.patch = patch("himotheelight.lighting.apply_mode", side_effect=self._apply)
        self.patch.start()
        self.lighting.apply_base_mode("active", source="manual", reason="test", force=True)
        self.calls.clear()

    def tearDown(self):
        self.lighting.cancel_trigger("cleanup", restore=False)
        self.patch.stop()
        self.temp.cleanup()

    def _apply(self, _host, mode):
        self.calls.append(mode.copy())
        return {"ok": True}

    def add_trigger(self, name, kind, value, brightness, priority, duration=120, minimum=0, maximum=180):
        trig = trigger_template(name, kind, value=value, minimum=minimum, maximum=maximum, duration_ms=duration, priority=priority, enabled=True)
        trig["effect"]["brightness"] = brightness
        self.store.update(lambda cfg: cfg["triggers"].append(trig))
        return trig

    def third_dart(self, throws):
        self.engine.handle_autodarts_state({
            "event":"Throw detected", "status":"Takeout", "running":True,
            "num_throws":3, "previous_num_throws":2, "throws":throws,
            "last_throw":throws[-1],
        })

    def test_t20_on_third_dart_plays_before_yellow(self):
        self.add_trigger("T20 Flash", "dart", "T20", 241, 30, duration=120)
        throws = [dart("S20",20), dart("S20",20), dart("T20",60,3,20,"Triple")]
        self.third_dart(throws)
        self.assertEqual(self.calls[-1]["brightness"], 241)
        self.assertEqual(self.lighting.trigger_snapshot()["active"]["name"], "T20 Flash")
        time.sleep(0.17)
        self.assertEqual(self.calls[-1]["colors"][0], [255,255,0])
        self.assertEqual(self.lighting.trigger_snapshot()["active"]["name"], "Takeout • Yellow")

    def test_high_visit_replaces_t20_then_yellow_waits_for_high_visit(self):
        self.add_trigger("T20 Flash", "dart", "T20", 221, 20, duration=120)
        self.add_trigger("100 Plus", "visit_range", "", 248, 60, duration=150, minimum=100, maximum=139)
        throws = [dart("S20",20), dart("S20",20), dart("T20",60,3,20,"Triple")]
        self.third_dart(throws)
        self.assertEqual(self.calls[-1]["brightness"], 248)
        self.assertEqual(self.lighting.trigger_snapshot()["active"]["name"], "100 Plus")
        time.sleep(0.20)
        self.assertEqual(self.calls[-1]["colors"][0], [255,255,0])

    def test_early_removal_finishes_score_effect_then_red_then_active(self):
        self.add_trigger("100 Plus", "visit_range", "", 249, 60, duration=150, minimum=100, maximum=139)
        throws = [dart("S20",20), dart("S20",20), dart("T20",60,3,20,"Triple")]
        self.third_dart(throws)
        self.assertEqual(self.calls[-1]["brightness"], 249)

        time.sleep(0.03)
        self.engine.handle_autodarts_state({
            "event":"Takeout finished", "status":"Throw", "running":True,
            "num_throws":0, "previous_num_throws":3, "throws":[], "last_throw":None,
        })
        # Removal must not cut the scoring animation short.
        self.assertEqual(self.calls[-1]["brightness"], 249)
        active = self.lighting.trigger_snapshot()["active"]
        self.assertEqual(active["name"], "100 Plus")
        self.assertEqual(active.get("followup"), "Takeout removed -> Red")

        time.sleep(0.16)
        self.assertEqual(self.calls[-1]["colors"][0], [255,0,0])
        self.assertEqual(self.lighting.trigger_snapshot()["active"]["name"], "Takeout Finished • Red")
        time.sleep(0.13)
        self.assertIsNone(self.lighting.trigger_snapshot()["active"])
        self.assertEqual(self.calls[-1]["brightness"], 180)

    def test_game_shot_replacement_discards_queued_yellow(self):
        self.add_trigger("100 Plus", "visit_range", "", 249, 60, duration=180, minimum=100, maximum=139)
        self.add_trigger("Game Shot", "game_event", "gameshot", 253, 90, duration=120)
        throws = [dart("S20",20), dart("S20",20), dart("T20",60,3,20,"Triple")]
        self.third_dart(throws)
        self.assertEqual(self.lighting.trigger_snapshot()["active"].get("followup"), "Takeout -> Yellow")
        self.engine.dispatch({"kind":"game_event", "event":"gameshot", "player":"Paul", "last_dart":"T20"})
        self.assertEqual(self.calls[-1]["brightness"], 253)
        self.assertEqual(self.lighting.trigger_snapshot()["active"]["name"], "Game Shot")
        time.sleep(0.20)
        self.assertIsNone(self.lighting.trigger_snapshot()["active"])
        # There must be no yellow system state after the Game Shot replacement.
        yellow = [row for row in self.calls if row.get("colors", [[None]])[0] == [255,255,0]]
        self.assertEqual(yellow, [])
        self.assertEqual(self.calls[-1]["brightness"], 180)

    def test_no_score_trigger_still_goes_yellow_immediately(self):
        throws = [dart("S1",1,1,1), dart("S2",2,1,2), dart("S3",3,1,3)]
        self.third_dart(throws)
        self.assertEqual(self.calls[-1]["colors"][0], [255,255,0])
        self.assertEqual(self.lighting.trigger_snapshot()["active"]["name"], "Takeout • Yellow")


if __name__ == "__main__":
    unittest.main()
