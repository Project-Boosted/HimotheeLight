import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from himotheelight.config_store import ConfigStore, trigger_effect_template
from himotheelight.lighting import LightingStateManager


def effect(brightness):
    row = trigger_effect_template()
    row["brightness"] = brightness
    return row


def device(device_id="d1", host="127.0.0.1:10001", active_brightness=180):
    idle = effect(40)
    active = effect(active_brightness)
    return {
        "id": device_id,
        "name": device_id,
        "host": host,
        "enabled": True,
        "modes": {"idle": idle, "active": active},
    }


class Stage7ChoreographyTests(unittest.TestCase):
    def make_store(self, devices=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = ConfigStore(Path(temp.name))
        cfg = store.get()
        cfg["devices"] = devices or [device()]
        store.save(cfg)
        return store

    def test_schema7_migrates_old_scene_to_simple_parallel_defaults(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        data = Path(temp.name)
        old = {
            "schema_version": 6,
            "app": {"last_manual_mode": "idle"},
            "autodarts": {},
            "devices": [device()],
            "device_groups": [],
            "triggers": [],
            "profiles": [],
            "scenes": [{
                "id": "scene1",
                "name": "Old Scene",
                "actions": [{
                    "id": "a1",
                    "name": "Old Action",
                    "target_type": "all",
                    "target_id": "",
                    "effect": effect(100),
                }],
            }],
        }
        (data / "config.json").write_text(json.dumps(old), encoding="utf-8")
        cfg = ConfigStore(data).get()
        self.assertEqual(cfg["schema_version"], 8)
        scene = cfg["scenes"][0]
        self.assertEqual(scene["repeat_count"], 1)
        action = scene["actions"][0]
        self.assertEqual(action["step"], 1)
        self.assertEqual(action["delay_ms"], 0)
        self.assertEqual(action["hold_ms"], 0)

    def test_choreography_runs_steps_in_order_and_restores_active(self):
        store = self.make_store([device(host="127.0.0.1:17101")])
        manager = LightingStateManager(store)
        calls = []

        def fake_apply(_host, mode):
            if _host == "127.0.0.1:17101":
                calls.append((time.monotonic(), mode.get("brightness")))
            return {"ok": True}

        actions = [
            {"name": "Step 1", "target_type": "all", "target_id": "", "step": 1, "delay_ms": 0, "hold_ms": 40, "effect": effect(10)},
            {"name": "Step 2", "target_type": "all", "target_id": "", "step": 2, "delay_ms": 0, "hold_ms": 50, "effect": effect(20)},
        ]
        with patch("himotheelight.lighting.apply_mode", side_effect=fake_apply):
            manager.apply_base_mode("active", source="manual", reason="test", force=True)
            calls.clear()
            result = manager.apply_trigger_effect(
                trigger_id="t1", trigger_name="Sequence", effect=effect(99),
                duration_ms=9999, priority=50, device_ids=[], reason="test",
                scene_name="Sequence", scene_actions=actions, scene_repeat_count=1,
            )
            self.assertTrue(result["accepted"])
            self.assertTrue(result["choreography"])
            self.assertEqual(result["duration_ms"], 90)
            time.sleep(0.18)

        brightness = [b for _, b in calls]
        self.assertGreaterEqual(len(brightness), 3)
        self.assertEqual(brightness[0:2], [10, 20])
        self.assertEqual(brightness[-1], 180)
        self.assertIsNone(manager.trigger_snapshot()["active"])

    def test_repeat_count_replays_scene(self):
        store = self.make_store([device(host="127.0.0.1:17102")])
        manager = LightingStateManager(store)
        calls = []

        def fake_apply(_host, mode):
            if _host == "127.0.0.1:17102":
                calls.append(mode.get("brightness"))
            return {"ok": True}

        actions = [{"name": "Pulse", "target_type": "all", "target_id": "", "step": 1, "delay_ms": 0, "hold_ms": 30, "effect": effect(33)}]
        with patch("himotheelight.lighting.apply_mode", side_effect=fake_apply):
            manager.apply_base_mode("active", source="manual", reason="test", force=True)
            calls.clear()
            result = manager.apply_trigger_effect(
                trigger_id="t2", trigger_name="Repeat", effect=effect(1),
                duration_ms=1000, priority=40, device_ids=[], reason="test",
                scene_name="Repeat", scene_actions=actions, scene_repeat_count=3,
            )
            self.assertEqual(result["duration_ms"], 90)
            time.sleep(0.16)

        self.assertEqual(calls.count(33), 3)
        self.assertEqual(calls[-1], 180)

    def test_loop_until_interrupted_is_cancelled_by_higher_priority_trigger(self):
        store = self.make_store([device(host="127.0.0.1:17103")])
        manager = LightingStateManager(store)
        calls = []

        def fake_apply(_host, mode):
            if _host == "127.0.0.1:17103":
                calls.append(mode.get("brightness"))
            return {"ok": True}

        loop_actions = [{"name": "Loop", "target_type": "all", "target_id": "", "step": 1, "delay_ms": 0, "hold_ms": 25, "effect": effect(44)}]
        with patch("himotheelight.lighting.apply_mode", side_effect=fake_apply):
            manager.apply_base_mode("active", source="manual", reason="test", force=True)
            calls.clear()
            low = manager.apply_trigger_effect(
                trigger_id="loop", trigger_name="Loop", effect=effect(1),
                duration_ms=1000, priority=20, device_ids=[], reason="test",
                scene_name="Loop", scene_actions=loop_actions, scene_repeat_count=0,
            )
            self.assertIsNone(low["duration_ms"])
            time.sleep(0.07)
            active = manager.trigger_snapshot()["active"]
            self.assertEqual(active["name"], "Loop")
            self.assertIsNone(active["remaining_ms"])

            high = manager.apply_trigger_effect(
                trigger_id="high", trigger_name="High", effect=effect(99),
                duration_ms=80, priority=90, device_ids=[], reason="interrupt",
            )
            self.assertTrue(high["accepted"])
            self.assertEqual(manager.trigger_snapshot()["active"]["name"], "High")
            time.sleep(0.14)

        self.assertIsNone(manager.trigger_snapshot()["active"])
        self.assertIn(99, calls)
        self.assertEqual(calls[-1], 180)

    def test_lower_priority_cannot_interrupt_looping_scene(self):
        store = self.make_store()
        manager = LightingStateManager(store)
        actions = [{"name": "Loop", "target_type": "all", "target_id": "", "step": 1, "delay_ms": 0, "hold_ms": 30, "effect": effect(55)}]
        with patch("himotheelight.lighting.apply_mode", return_value={"ok": True}):
            manager.apply_trigger_effect(
                trigger_id="loop", trigger_name="Loop", effect=effect(1),
                duration_ms=1000, priority=70, device_ids=[], reason="test",
                scene_name="Loop", scene_actions=actions, scene_repeat_count=0,
            )
            blocked = manager.apply_trigger_effect(
                trigger_id="low", trigger_name="Low", effect=effect(2),
                duration_ms=100, priority=20, device_ids=[], reason="test",
            )
            self.assertFalse(blocked["accepted"])
            self.assertEqual(manager.trigger_snapshot()["active"]["name"], "Loop")
            manager.cancel_trigger("cleanup", restore=False)


if __name__ == "__main__":
    unittest.main()
