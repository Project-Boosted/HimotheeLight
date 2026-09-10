import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from himotheelight.config_store import ConfigStore, default_device_modes, trigger_effect_template, trigger_template
from himotheelight.lighting import LightingStateManager
from himotheelight.trigger_engine import TriggerEngine


class Stage6SceneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ConfigStore(Path(self.temp.name))
        self.store.update(lambda cfg: cfg.update({
            "devices": [
                {"id":"board","name":"Board","host":"http://board","enabled":True,"modes":default_device_modes()},
                {"id":"ceiling","name":"Ceiling","host":"http://ceiling","enabled":True,"modes":default_device_modes()},
                {"id":"desk","name":"Desk","host":"http://desk","enabled":True,"modes":default_device_modes()},
            ],
            "device_groups": [{"id":"room","name":"Room","device_ids":["ceiling","desk"]}],
            "scenes": [],
        }))
        self.lighting = LightingStateManager(self.store)
        self.engine = TriggerEngine(self.store, self.lighting)

    def tearDown(self):
        self.temp.cleanup()

    def test_scene_resolves_group_and_device_with_last_action_winning(self):
        e1 = trigger_effect_template(); e1["brightness"] = 100
        e2 = trigger_effect_template(); e2["brightness"] = 240
        calls = []
        def fake_apply(host, effect):
            calls.append((host, effect["brightness"]))
            return {"success": True}
        actions = [
            {"id":"a1","name":"Room pulse","target_type":"group","target_id":"room","effect":e1},
            {"id":"a2","name":"Desk override","target_type":"device","target_id":"desk","effect":e2},
        ]
        with patch("himotheelight.lighting.apply_mode", side_effect=fake_apply):
            result = self.lighting.apply_trigger_effect(trigger_id="x", trigger_name="Scene", effect={}, duration_ms=200,
                priority=50, device_ids=[], reason="test", force=True, scene_name="Maximum", scene_actions=actions)
        self.assertTrue(result["accepted"])
        by_host = dict(calls)
        self.assertEqual(by_host["http://ceiling"], 100)
        self.assertEqual(by_host["http://desk"], 240)
        self.assertNotIn("http://board", by_host)

    def test_trigger_scene_fires_and_restores_base(self):
        effect = trigger_effect_template(); effect["brightness"] = 222
        scene = {"id":"max","name":"Maximum Room","actions":[{"id":"a","name":"All","target_type":"all","target_id":"","effect":effect}]}
        trig = trigger_template("180 Scene","visit_exact",value="180",duration_ms=120,priority=90,enabled=True)
        trig["scene_id"] = "max"
        self.store.update(lambda cfg: (cfg.__setitem__("scenes", [scene]), cfg.__setitem__("triggers", [trig])))
        calls=[]
        with patch("himotheelight.lighting.apply_mode", side_effect=lambda host, mode: calls.append((host, mode.get("brightness"))) or {"success":True}):
            self.lighting.apply_base_mode("active", source="test", reason="active", force=True)
            fired = self.engine.dispatch({"kind":"visit","score":180,"darts":["T20","T20","T20"]})
            self.assertTrue(fired["results"][0]["accepted"])
            self.assertEqual(self.lighting.trigger_snapshot()["active"]["scene"], "Maximum Room")
            time.sleep(0.20)
            self.assertIsNone(self.lighting.trigger_snapshot()["active"])
        # 3 active base calls + 3 scene calls + 3 restore calls
        self.assertGreaterEqual(len(calls), 9)
        self.assertIn(("http://board", 222), calls)

    def test_schema6_preserves_old_trigger_and_adds_empty_scene_link(self):
        cfg = self.store.get()
        self.assertEqual(cfg["schema_version"], 8)
        self.assertIn("device_groups", cfg)
        self.assertIn("scenes", cfg)
        self.assertTrue(all("scene_id" in t for t in cfg.get("triggers", [])))


if __name__ == "__main__":
    unittest.main()
