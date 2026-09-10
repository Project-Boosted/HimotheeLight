import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from himotheelight.activity import AutodartsActivityController
from himotheelight.config_store import ConfigStore, default_device_modes, trigger_template
from himotheelight.lighting import LightingStateManager
from himotheelight.trigger_engine import TriggerEngine


def make_device():
    modes = default_device_modes()
    modes["idle"]["brightness"] = 30
    modes["active"]["brightness"] = 180
    return {
        "id": "d1", "name": "Test WLED", "host": "127.0.0.1:19999",
        "enabled": True, "modes": modes,
    }


def dart(name, score, multiplier, number, bed="SingleOuter"):
    return {"name": name, "score": score, "multiplier": multiplier, "number": number, "bed": bed}


class V071TakeoutInactivityTests(unittest.TestCase):
    def make_stack(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = ConfigStore(Path(temp.name))
        cfg = store.get()
        cfg["devices"] = [make_device()]
        cfg["autodarts"]["takeout_flow_enabled"] = True
        cfg["autodarts"]["takeout_after_darts"] = 3
        cfg["autodarts"]["takeout_finish_flash_ms"] = 100
        cfg["autodarts"]["inactivity_idle_enabled"] = True
        cfg["autodarts"]["inactivity_idle_seconds"] = 60
        wait = trigger_template("Takeout Wait", "board_event", value="Takeout started", duration_ms=5000, priority=40, enabled=True)
        wait["effect"]["brightness"] = 77
        cfg["triggers"] = [wait]
        store.save(cfg)
        lighting = LightingStateManager(store)
        engine = TriggerEngine(store, lighting)
        return store, lighting, engine

    def test_third_dart_synthesizes_takeout_started_immediately(self):
        store, lighting, engine = self.make_stack()
        calls = []
        with patch("himotheelight.lighting.apply_mode", side_effect=lambda host, mode: calls.append(mode.copy()) or {"ok": True}):
            lighting.apply_base_mode("active", source="manual", reason="test", force=True)
            engine.handle_autodarts_state({
                "event": "Throw detected", "status": "Takeout", "running": True,
                "num_throws": 3, "previous_num_throws": 2,
                "throws": [
                    dart("S20", 20, 1, 20), dart("S20", 20, 1, 20), dart("T20", 60, 3, 20, "Triple"),
                ],
                "last_throw": dart("T20", 60, 3, 20, "Triple"),
            })
            active = lighting.trigger_snapshot()["active"]
            self.assertIsNotNone(active)
            self.assertEqual(active["name"], "Takeout • Yellow")
            self.assertTrue(active.get("persistent"))
            self.assertIsNone(active.get("remaining_ms"))
            self.assertEqual(calls[-1]["effect_id"], 0)
            self.assertEqual(calls[-1]["colors"][0], [255, 255, 0])

            # Yellow is persistent; it must not expire while the darts remain.
            time.sleep(0.12)
            self.assertEqual(lighting.trigger_snapshot()["active"]["name"], "Takeout • Yellow")

            # A later real Board Manager Takeout started frame must not duplicate it.
            before = len(engine.snapshot()["history"])
            engine.handle_autodarts_state({
                "event": "Takeout started", "status": "Takeout in progress", "running": True,
                "num_throws": 3, "previous_num_throws": 3,
                "throws": [
                    dart("S20", 20, 1, 20), dart("S20", 20, 1, 20), dart("T20", 60, 3, 20, "Triple"),
                ],
                "last_throw": None,
            })
            self.assertEqual(len(engine.snapshot()["history"]), before)
            lighting.cancel_trigger("cleanup", restore=False)

    def test_takeout_finished_flashes_red_then_restores_active(self):
        store, lighting, engine = self.make_stack()
        calls = []
        with patch("himotheelight.lighting.apply_mode", side_effect=lambda host, mode: calls.append(mode.copy()) or {"ok": True}):
            lighting.apply_base_mode("active", source="manual", reason="test", force=True)
            engine.handle_autodarts_state({
                "event": "Throw detected", "status": "Takeout", "running": True,
                "num_throws": 3, "previous_num_throws": 2,
                "throws": [dart("S1", 1, 1, 1), dart("S2", 2, 1, 2), dart("S3", 3, 1, 3)],
                "last_throw": dart("S3", 3, 1, 3),
            })
            engine.handle_autodarts_state({
                "event": "Takeout finished", "status": "Throw", "running": True,
                "num_throws": 0, "previous_num_throws": 3, "throws": [], "last_throw": None,
            })
            red = calls[-1]
            self.assertEqual(red["effect_id"], 0)
            self.assertEqual(red["colors"][0], [255, 0, 0])
            time.sleep(0.16)
            self.assertIsNone(lighting.trigger_snapshot()["active"])
            self.assertEqual(calls[-1]["brightness"], 180)
            self.assertEqual(lighting.snapshot()["mode"], "active")

    def test_inactivity_idle_is_latched_until_new_game(self):
        store, lighting, _engine = self.make_stack()
        activity = AutodartsActivityController(store, lighting)
        calls = []
        with patch("himotheelight.lighting.apply_mode", side_effect=lambda host, mode: calls.append(mode.copy()) or {"ok": True}):
            activity.game_base_mode("active", "Autodarts match state synchronised", False)
            self.assertEqual(lighting.snapshot()["mode"], "active")
            with activity._lock:
                activity._active_since_monotonic = time.monotonic() - 61
            self.assertTrue(activity.evaluate_now())
            self.assertEqual(lighting.snapshot()["mode"], "idle")
            self.assertTrue(activity.snapshot()["inactivity_latched"])

            # A dart in the same stalled game does not wake the lighting.
            activity.note_dart()
            suppressed = activity.game_base_mode("active", "Autodarts match active", False)
            self.assertTrue(suppressed.get("inactivity_latched"))
            self.assertEqual(lighting.snapshot()["mode"], "idle")

            # A genuine new leg/game clears the latch and allows Active again.
            activity.note_game_event("game_start")
            activity.game_base_mode("active", "Autodarts match active", False)
            self.assertFalse(activity.snapshot()["inactivity_latched"])
            self.assertEqual(lighting.snapshot()["mode"], "active")

    def test_new_defaults_match_requested_flow(self):
        store, _lighting, engine = self.make_stack()
        settings = store.get()["autodarts"]
        self.assertTrue(settings["takeout_flow_enabled"])
        self.assertEqual(settings["takeout_after_darts"], 3)
        self.assertEqual(settings["takeout_finish_flash_ms"], 100)  # test override persisted
        self.assertTrue(settings["inactivity_idle_enabled"])
        self.assertEqual(settings["inactivity_idle_seconds"], 60)

        # Even a stale/hand-edited old config cannot start Takeout before dart 3.
        store.update(lambda cfg: cfg["autodarts"].__setitem__("takeout_after_darts", 1))
        with patch("himotheelight.lighting.apply_mode", return_value={"ok": True}):
            d1 = dart("S20", 20, 1, 20)
            engine.handle_autodarts_state({"event":"Throw detected","num_throws":1,"previous_num_throws":0,"throws":[d1],"last_throw":d1})
            self.assertIsNone(engine.lighting.trigger_snapshot()["active"])


if __name__ == "__main__":
    unittest.main()
