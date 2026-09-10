import json
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from himotheelight.config_store import ConfigStore, default_device_modes, trigger_effect_template, trigger_template
from himotheelight.hue import HueError, build_light_payload, pair_bridge, rgb_to_xy
from himotheelight.lighting import LightingStateManager
from himotheelight.profiles import ProfileManager
from himotheelight.server import Handler, HimotheeLightContext
from himotheelight.logging_buffer import MemoryLogHandler
from himotheelight.trigger_engine import TriggerEngine


def hue_bridge():
    return {
        "id":"hb1", "name":"Hue Bridge", "host":"https://192.168.1.2",
        "application_key":"SUPER-SECRET-HUE-KEY", "client_key":"CLIENT-SECRET", "enabled":True,
    }


def hue_light():
    return {
        "id":"hl1", "bridge_id":"hb1", "resource_id":"rid-light-1", "name":"Hue Lamp",
        "enabled":True, "supports_color":True, "supports_dimming":True,
        "supports_color_temperature":True, "modes":default_device_modes(),
    }


class HueIntegrationTests(unittest.TestCase):
    def test_pair_requires_physical_link_button(self):
        with patch("himotheelight.hue._request_json", return_value=[{"error":{"type":101,"description":"link button not pressed"}}]):
            with self.assertRaisesRegex(HueError, "physical button"):
                pair_bridge("192.168.1.2")

    def test_pair_returns_application_key(self):
        with patch("himotheelight.hue._request_json", return_value=[{"success":{"username":"app-key","clientkey":"client-key"}}]):
            result = pair_bridge("192.168.1.2")
        self.assertEqual(result["application_key"], "app-key")
        self.assertEqual(result["client_key"], "client-key")

    def test_hue_payload_uses_brightness_transition_and_colour(self):
        mode = trigger_effect_template()
        mode["brightness"] = 255
        mode["transition_ms"] = 500
        mode["colors"][0] = [255, 255, 0]
        payload = build_light_payload(mode, hue_light())
        self.assertEqual(payload["on"], {"on": True})
        self.assertEqual(payload["dimming"]["brightness"], 100.0)
        self.assertEqual(payload["dynamics"]["duration"], 500)
        self.assertIn("xy", payload["color"])
        yellow = rgb_to_xy([255,255,0])
        self.assertEqual(payload["color"]["xy"], yellow)

    def test_mixed_wled_and_hue_trigger_dispatch(self):
        with tempfile.TemporaryDirectory() as td:
            store=ConfigStore(Path(td))
            cfg=store.get()
            cfg["devices"]=[{"id":"w1","name":"WLED","host":"http://wled","enabled":True,"modes":default_device_modes()}]
            cfg["hue_bridges"]=[hue_bridge()]
            cfg["hue_lights"]=[hue_light()]
            trig=trigger_template("T20 both","dart",value="T20",enabled=True,duration_ms=120,priority=20)
            trig["device_ids"]=["w1","hl1"]
            cfg["triggers"]=[trig]
            store.save(cfg)
            lighting=LightingStateManager(store); engine=TriggerEngine(store,lighting)
            with patch("himotheelight.lighting.apply_mode", return_value={"ok":True}) as wled_apply, \
                 patch("himotheelight.lighting.apply_light_mode", return_value={"data":[{}],"errors":[]}) as hue_apply:
                out=engine.dispatch({"kind":"dart","dart":"T20","score":60})
                self.assertTrue(out["results"][0]["accepted"])
                self.assertEqual(wled_apply.call_count,1)
                self.assertEqual(hue_apply.call_count,1)
                lighting.cancel_trigger("cleanup",restore=False)

    def test_base_mode_applies_to_hue_and_wled(self):
        with tempfile.TemporaryDirectory() as td:
            store=ConfigStore(Path(td)); cfg=store.get()
            cfg["devices"]=[{"id":"w1","name":"W","host":"http://w","enabled":True,"modes":default_device_modes()}]
            cfg["hue_bridges"]=[hue_bridge()]; cfg["hue_lights"]=[hue_light()]; store.save(cfg)
            manager=LightingStateManager(store)
            with patch("himotheelight.lighting.apply_mode", return_value={"ok":True}) as w, \
                 patch("himotheelight.lighting.apply_light_mode", return_value={"data":[{}],"errors":[]}) as h:
                result=manager.apply_base_mode("active",source="manual",reason="test",force=True)
            self.assertTrue(result["ok"]); self.assertEqual(w.call_count,1); self.assertEqual(h.call_count,1)
            self.assertEqual({x["device_type"] for x in result["results"]},{"wled","hue"})


    def test_schema7_upgrade_adds_hue_without_changing_wled(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)
            legacy = {
                "schema_version": 7,
                "app": {"last_manual_mode":"idle", "current_mode":"idle"},
                "autodarts": {},
                "devices": [{"id":"w1","name":"Legacy WLED","host":"http://192.168.1.50","enabled":True,"modes":default_device_modes()}],
                "triggers": [], "profiles": [], "device_groups": [], "scenes": [],
            }
            (path / "config.json").write_text(json.dumps(legacy), encoding="utf-8")
            cfg = ConfigStore(path).get()
            self.assertEqual(cfg["schema_version"], 8)
            self.assertEqual(cfg["devices"][0]["host"], "http://192.168.1.50")
            self.assertEqual(cfg["hue_bridges"], [])
            self.assertEqual(cfg["hue_lights"], [])

    def test_public_config_redacts_hue_bridge_keys(self):
        with tempfile.TemporaryDirectory() as td:
            store = ConfigStore(Path(td)); cfg = store.get()
            cfg["hue_bridges"] = [hue_bridge()]; cfg["hue_lights"] = [hue_light()]; store.save(cfg)
            handler = object.__new__(Handler)
            handler.server = SimpleNamespace(context=SimpleNamespace(store=store))
            public = handler._public_config()
            bridge = public["hue_bridges"][0]
            self.assertTrue(bridge["paired"])
            self.assertNotIn("application_key", bridge)
            self.assertNotIn("client_key", bridge)
            self.assertIn("SUPER-SECRET-HUE-KEY", json.dumps(store.get()))

    def test_profile_import_cannot_map_hue_slot_to_wled(self):
        with tempfile.TemporaryDirectory() as source_td, tempfile.TemporaryDirectory() as dest_td:
            source = ConfigStore(Path(source_td)); cfg = source.get()
            cfg["hue_bridges"]=[hue_bridge()]; cfg["hue_lights"]=[hue_light()]; source.save(cfg)
            source_manager=ProfileManager(source, LightingStateManager(source)); source_manager.sync_active()
            bundle=source_manager.export_bundle(source.get()["app"]["active_profile_id"])["bundle"]
            hue_slot=next(d["slot_id"] for d in bundle["devices"] if d.get("type")=="hue")

            dest=ConfigStore(Path(dest_td)); dcfg=dest.get()
            dcfg["devices"]=[{"id":"w1","name":"Hue Lamp","host":"http://wled","enabled":True,"modes":default_device_modes()}]
            dest.save(dcfg)
            dest_manager=ProfileManager(dest, LightingStateManager(dest))
            with self.assertRaisesRegex(ValueError, "must map WLED slots to WLED and Hue slots to Hue"):
                dest_manager.import_bundle(bundle, {hue_slot:"w1"})

    def test_profile_export_has_hue_slot_but_no_bridge_credentials(self):
        with tempfile.TemporaryDirectory() as td:
            store=ConfigStore(Path(td)); cfg=store.get()
            cfg["hue_bridges"]=[hue_bridge()]; cfg["hue_lights"]=[hue_light()]; store.save(cfg)
            manager=LightingStateManager(store); profiles=ProfileManager(store,manager); profiles.sync_active()
            pid=store.get()["app"]["active_profile_id"]
            exported=profiles.export_bundle(pid)["bundle"]
            raw=json.dumps(exported)
            self.assertNotIn("SUPER-SECRET-HUE-KEY",raw)
            self.assertNotIn("CLIENT-SECRET",raw)
            hue_slots=[d for d in exported["devices"] if d.get("type")=="hue"]
            self.assertEqual(len(hue_slots),1)
            self.assertEqual(hue_slots[0]["name"],"Hue Lamp")


if __name__ == "__main__":
    unittest.main()
