import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from himotheelight.config_store import ConfigStore, default_device_modes, trigger_template
from himotheelight.lighting import LightingStateManager
from himotheelight.profiles import ProfileManager, PROFILE_SHARE_FORMAT, PROFILE_SHARE_VERSION


def device(device_id, name, host, active=180):
    modes = default_device_modes()
    modes["active"]["brightness"] = active
    return {"id": device_id, "name": name, "host": host, "enabled": True, "modes": modes}


class ProfileSharingTests(unittest.TestCase):
    def make_manager(self, devices):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = ConfigStore(Path(temp.name))
        cfg = store.get()
        cfg["devices"] = devices
        cfg["triggers"] = []
        cfg["profiles"] = []
        cfg["scenes"] = []
        cfg["device_groups"] = []
        store.save(cfg)
        lighting = LightingStateManager(store)
        profiles = ProfileManager(store, lighting)
        return store, lighting, profiles

    def build_source_bundle(self):
        store, lighting, profiles = self.make_manager([
            device("src-board", "Dartboard", "192.168.1.44", 201),
            device("src-room", "Room LEDs", "wled-room.local", 151),
        ])
        cfg = store.get()
        group = {"id": "src-group", "name": "Room", "device_ids": ["src-room"]}
        scene = {
            "id": "src-scene", "name": "180 Celebration", "repeat_count": 1,
            "actions": [
                {"id": "a1", "name": "Board Flash", "target_type": "device", "target_id": "src-board", "step": 1, "delay_ms": 0, "hold_ms": 200, "effect": default_device_modes()["active"]},
                {"id": "a2", "name": "Room Chase", "target_type": "group", "target_id": "src-group", "step": 2, "delay_ms": 0, "hold_ms": 500, "effect": default_device_modes()["active"]},
            ],
        }
        trigger = trigger_template("Maximum 180", "visit_exact", value="180", minimum=180, maximum=180, enabled=True, priority=80)
        trigger["scene_id"] = "src-scene"
        trigger["device_ids"] = ["src-board"]
        cfg["device_groups"] = [group]
        cfg["scenes"] = [scene]
        cfg["triggers"] = [trigger]
        store.save(cfg)
        profiles.sync_active()
        profile_id = store.get()["app"]["active_profile_id"]
        return profiles.export_bundle(profile_id)["bundle"]

    def test_export_is_portable_and_never_contains_wled_hosts(self):
        bundle = self.build_source_bundle()
        self.assertEqual(bundle["format"], PROFILE_SHARE_FORMAT)
        self.assertEqual(bundle["format_version"], PROFILE_SHARE_VERSION)
        raw = json.dumps(bundle)
        self.assertNotIn("192.168.1.44", raw)
        self.assertNotIn("wled-room.local", raw)
        self.assertNotIn('"host"', raw)
        self.assertNotIn("src-board", raw)
        self.assertNotIn("src-room", raw)
        self.assertEqual([d["slot_id"] for d in bundle["devices"]], ["device-1", "device-2"])
        self.assertEqual(len(bundle["scenes"]), 1)
        self.assertEqual(len(bundle["device_groups"]), 1)
        self.assertEqual(bundle["profile"]["triggers"][0]["scene_id"], "scene-1")

    def test_preview_suggests_exact_device_names_and_marks_required(self):
        bundle = self.build_source_bundle()
        _store, _lighting, profiles = self.make_manager([
            device("dst-board", "Dartboard", "10.0.0.4"),
            device("dst-room", "Room LEDs", "10.0.0.5"),
        ])
        preview = profiles.preview_import(bundle)
        self.assertEqual(preview["profile_name"], "Default")
        by_name = {row["name"]: row for row in preview["devices"]}
        self.assertEqual(by_name["Dartboard"]["suggested_device_id"], "dst-board")
        self.assertEqual(by_name["Room LEDs"]["suggested_device_id"], "dst-room")
        self.assertTrue(by_name["Dartboard"]["required"])
        self.assertTrue(by_name["Room LEDs"]["required"])

    def test_import_requires_devices_used_by_triggers_and_scenes(self):
        bundle = self.build_source_bundle()
        _store, _lighting, profiles = self.make_manager([device("dst-board", "Dartboard", "10.0.0.4")])
        with self.assertRaisesRegex(ValueError, "Map all devices"):
            profiles.import_bundle(bundle, {"device-1": "dst-board"})

    def test_round_trip_remaps_dependencies_and_preserves_local_hosts(self):
        bundle = self.build_source_bundle()
        store, lighting, profiles = self.make_manager([
            device("dst-board", "Dartboard", "10.0.0.4", 99),
            device("dst-room", "Room LEDs", "10.0.0.5", 98),
        ])
        original_active = store.get()["app"]["active_profile_id"]
        result = profiles.import_bundle(bundle, {"device-1": "dst-board", "device-2": "dst-room"}, "Shared 180")
        cfg = store.get()
        self.assertEqual(cfg["app"]["active_profile_id"], original_active)  # import is safe/inactive by default
        imported = next(p for p in cfg["profiles"] if p["id"] == result["profile_id"])
        self.assertEqual(imported["name"], "Shared 180")
        self.assertEqual(set(imported["device_modes"]), {"dst-board", "dst-room"})
        self.assertEqual(imported["device_modes"]["dst-board"]["active"]["brightness"], 201)
        self.assertEqual(imported["device_modes"]["dst-room"]["active"]["brightness"], 151)
        trig = imported["triggers"][0]
        self.assertEqual(trig["device_ids"], ["dst-board"])
        self.assertTrue(trig["scene_id"])
        imported_scene = next(s for s in cfg["scenes"] if s["id"] == trig["scene_id"])
        imported_group = next(g for g in cfg["device_groups"] if g["id"] == imported_scene["actions"][1]["target_id"])
        self.assertEqual(imported_scene["actions"][0]["target_id"], "dst-board")
        self.assertEqual(imported_group["device_ids"], ["dst-room"])
        hosts = {d["id"]: d["host"] for d in cfg["devices"]}
        self.assertEqual(hosts, {"dst-board": "10.0.0.4", "dst-room": "10.0.0.5"})

        calls = []
        with patch("himotheelight.lighting.apply_mode", side_effect=lambda host, mode: calls.append((host, mode.get("brightness"))) or {"ok": True}):
            profiles.apply(result["profile_id"])
        cfg = store.get()
        self.assertEqual(cfg["app"]["active_profile_id"], result["profile_id"])
        hosts_after = {d["id"]: d["host"] for d in cfg["devices"]}
        self.assertEqual(hosts_after, hosts)
        self.assertTrue(calls)

    def test_duplicate_import_profile_name_gets_safe_suffix(self):
        bundle = self.build_source_bundle()
        store, _lighting, profiles = self.make_manager([
            device("dst-board", "Dartboard", "10.0.0.4"),
            device("dst-room", "Room LEDs", "10.0.0.5"),
        ])
        mapping = {"device-1": "dst-board", "device-2": "dst-room"}
        first = profiles.import_bundle(bundle, mapping, "Shared")
        second = profiles.import_bundle(bundle, mapping, "Shared")
        self.assertEqual(first["name"], "Shared")
        self.assertEqual(second["name"], "Shared (2)")
        names = [p["name"] for p in store.get()["profiles"]]
        self.assertIn("Shared", names)
        self.assertIn("Shared (2)", names)

    def test_invalid_or_future_share_format_is_rejected(self):
        _store, _lighting, profiles = self.make_manager([])
        with self.assertRaisesRegex(ValueError, "not a HimotheeLight"):
            profiles.preview_import({"format": "SomethingElse", "format_version": 1})
        with self.assertRaisesRegex(ValueError, "Unsupported profile share format"):
            profiles.preview_import({
                "format": PROFILE_SHARE_FORMAT, "format_version": 999,
                "profile": {"name": "X", "device_modes": {}, "triggers": []},
                "devices": [], "device_groups": [], "scenes": [],
            })


if __name__ == "__main__":
    unittest.main()
