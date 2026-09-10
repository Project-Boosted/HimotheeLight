from __future__ import annotations

import copy
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict


def _mode_template(brightness: int) -> Dict[str, Any]:
    return {
        "source": "direct",
        "on": True,
        "brightness": brightness,
        "effect_id": 0,
        "palette_id": 0,
        "speed": 128,
        "intensity": 128,
        "segment_id": None,
        "transition_ms": 300,
        "colors": [[138, 43, 226], [255, 255, 255], [0, 0, 0]],
        "preset_id": 1,
    }


def default_device_modes() -> Dict[str, Dict[str, Any]]:
    return {
        "idle": _mode_template(64),
        "active": _mode_template(180),
    }


def default_autodarts_settings() -> Dict[str, Any]:
    return {
        "enabled": True,
        "host": "127.0.0.1",
        "port": 3180,
        "auto_connect": True,
        "auto_lighting": True,
        "idle_on_disconnect": True,
        "reconnect_seconds": 2.0,
        "bridge_timeout_seconds": 10.0,
        "takeout_flow_enabled": True,
        "takeout_after_darts": 3,
        "takeout_finish_flash_ms": 500,
        "inactivity_idle_enabled": True,
        "inactivity_idle_seconds": 60.0,
    }


def trigger_effect_template() -> Dict[str, Any]:
    effect = _mode_template(255)
    effect["transition_ms"] = 0
    effect["effect_id"] = 0
    effect["palette_id"] = 0
    effect["speed"] = 200
    effect["intensity"] = 220
    effect["colors"] = [[255, 255, 255], [138, 43, 226], [0, 0, 0]]
    return effect


def trigger_template(
    name: str = "New Trigger",
    trigger_type: str = "dart",
    *,
    value: str = "T20",
    minimum: int = 0,
    maximum: int = 180,
    duration_ms: int = 1500,
    priority: int = 20,
    enabled: bool = False,
) -> Dict[str, Any]:
    return {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "enabled": enabled,
        "trigger_type": trigger_type,
        "value": value,
        "minimum": minimum,
        "maximum": maximum,
        "duration_ms": duration_ms,
        "priority": priority,
        "device_ids": [],  # empty means every enabled lighting endpoint
        "effect": trigger_effect_template(),
    }


def default_triggers() -> list[Dict[str, Any]]:
    """Useful starter rows, disabled so upgrading never causes surprise lighting."""
    return [
        trigger_template("Treble 20", "dart", value="T20", duration_ms=650, priority=20),
        trigger_template("Bull", "dart", value="BULL", duration_ms=900, priority=30),
        trigger_template("100+ Visit", "visit_range", value="", minimum=100, maximum=139, duration_ms=1500, priority=45),
        trigger_template("140+ Visit", "visit_range", value="", minimum=140, maximum=179, duration_ms=2200, priority=60),
        trigger_template("Maximum 180", "visit_exact", value="180", minimum=180, maximum=180, duration_ms=4000, priority=80),
        trigger_template("Bust", "game_event", value="bust", duration_ms=2200, priority=75),
        trigger_template("Game Shot", "game_event", value="gameshot", duration_ms=4500, priority=90),
        trigger_template("Match Shot", "game_event", value="matchshot", duration_ms=7000, priority=100),
        trigger_template("Turn Start", "game_event", value="turn_start", duration_ms=600, priority=15),
        trigger_template("Bot Turn", "game_event", value="bot_turn", duration_ms=800, priority=25),
        trigger_template("Three T20s", "combination", value="T20_T20_T20", duration_ms=4500, priority=85),
        trigger_template("Target Bull", "target", value="BULL", duration_ms=700, priority=18),
    ]


def _profile_from_config(cfg: Dict[str, Any], name: str = "Default") -> Dict[str, Any]:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    device_modes: Dict[str, Any] = {}
    for collection in (cfg.get("devices", []), cfg.get("hue_lights", [])):
        for device in collection:
            if not isinstance(device, dict) or not device.get("id"):
                continue
            device_modes[str(device["id"])] = copy.deepcopy(device.get("modes", default_device_modes()))
    return {
        "id": uuid.uuid4().hex[:12],
        "name": str(name or "Profile")[:60],
        "created_at": stamp,
        "updated_at": stamp,
        "device_modes": device_modes,
        "triggers": copy.deepcopy(cfg.get("triggers", [])),
    }


DEFAULT_CONFIG: Dict[str, Any] = {
    "schema_version": 8,
    "app": {
        "last_manual_mode": "idle",
        "current_mode": "idle",
        "mode_source": "startup",
        "mode_reason": "Startup",
        "mode_applied_at": None,
        "active_profile_id": None,
    },
    "autodarts": default_autodarts_settings(),
    "triggers": default_triggers(),
    "profiles": [],
    "device_groups": [],
    "scenes": [],
    "devices": [],
    "hue_bridges": [],
    "hue_lights": [],
}


def default_data_dir() -> Path:
    override = os.getenv("HIMOTHEELIGHT_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        root = Path(os.getenv("APPDATA", Path.home()))
        return root / "HimotheeLight"
    return Path.home() / ".config" / "HimotheeLight"


class ConfigStore:
    def __init__(self, data_dir: Path | None = None):
        self.data_dir = (data_dir or default_data_dir()).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "config.json"
        self._lock = threading.RLock()
        self._config = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            cfg = copy.deepcopy(DEFAULT_CONFIG)
            cfg = self._migrate(cfg)
            self._write(cfg)
            return cfg
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                cfg = json.load(handle)
        except Exception:
            backup = self.path.with_suffix(".broken.json")
            try:
                self.path.replace(backup)
            except Exception:
                pass
            cfg = copy.deepcopy(DEFAULT_CONFIG)
            cfg = self._migrate(cfg)
            self._write(cfg)
            return cfg
        cfg = self._migrate(cfg)
        self._write(cfg)
        return cfg

    def _migrate(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(cfg, dict):
            cfg = copy.deepcopy(DEFAULT_CONFIG)
        old_schema = int(cfg.get("schema_version", 0) or 0)
        cfg["schema_version"] = 8

        cfg.setdefault("app", {})
        if not isinstance(cfg["app"], dict):
            cfg["app"] = {}
        cfg["app"].setdefault("last_manual_mode", "idle")
        cfg["app"].setdefault("current_mode", cfg["app"].get("last_manual_mode", "idle"))
        cfg["app"].setdefault("mode_source", "startup")
        cfg["app"].setdefault("mode_reason", "Startup")
        cfg["app"].setdefault("mode_applied_at", None)
        cfg["app"].setdefault("active_profile_id", None)

        cfg.setdefault("autodarts", {})
        if not isinstance(cfg["autodarts"], dict):
            cfg["autodarts"] = {}
        for key, value in default_autodarts_settings().items():
            cfg["autodarts"].setdefault(key, copy.deepcopy(value))

        if "triggers" not in cfg or not isinstance(cfg.get("triggers"), list):
            cfg["triggers"] = default_triggers()
        for raw in cfg["triggers"]:
            self._normalize_trigger(raw)

        # Stage 4 migration: add disabled game-event starter rules exactly once.
        if old_schema < 4:
            existing_game_events = {
                str(t.get("value") or "").strip().lower()
                for t in cfg.get("triggers", [])
                if isinstance(t, dict) and t.get("trigger_type") == "game_event"
            }
            for starter in [
                trigger_template("Bust", "game_event", value="bust", duration_ms=2200, priority=75),
                trigger_template("Game Shot", "game_event", value="gameshot", duration_ms=4500, priority=90),
                trigger_template("Match Shot", "game_event", value="matchshot", duration_ms=7000, priority=100),
                trigger_template("Turn Start", "game_event", value="turn_start", duration_ms=600, priority=15),
                trigger_template("Bot Turn", "game_event", value="bot_turn", duration_ms=800, priority=25),
            ]:
                if starter["value"] not in existing_game_events:
                    cfg["triggers"].append(starter)

        # Stage 5 starter rows. Disabled, so existing installations behave exactly
        # as they did until the user explicitly turns these rules on.
        if old_schema < 5:
            kinds_values = {
                (str(t.get("trigger_type") or ""), str(t.get("value") or "").upper())
                for t in cfg.get("triggers", []) if isinstance(t, dict)
            }
            for starter in [
                trigger_template("Three T20s", "combination", value="T20_T20_T20", duration_ms=4500, priority=85),
                trigger_template("Target Bull", "target", value="BULL", duration_ms=700, priority=18),
            ]:
                key = (starter["trigger_type"], str(starter["value"]).upper())
                if key not in kinds_values:
                    cfg["triggers"].append(starter)

        cfg.setdefault("devices", [])
        if not isinstance(cfg["devices"], list):
            cfg["devices"] = []
        for device in cfg["devices"]:
            if not isinstance(device, dict):
                continue
            device.setdefault("enabled", True)
            device.setdefault("modes", default_device_modes())
            if not isinstance(device.get("modes"), dict):
                device["modes"] = default_device_modes()
            for mode_name, fallback in default_device_modes().items():
                device["modes"].setdefault(mode_name, copy.deepcopy(fallback))
                if not isinstance(device["modes"].get(mode_name), dict):
                    device["modes"][mode_name] = copy.deepcopy(fallback)
                for key, value in fallback.items():
                    device["modes"][mode_name].setdefault(key, copy.deepcopy(value))

        # Stage 8: Philips Hue bridges and light endpoints. Bridge credentials are
        # local-only configuration; individual lights get the same portable
        # Idle/Active mode shape as WLED so triggers/scenes can share colours.
        cfg.setdefault("hue_bridges", [])
        if not isinstance(cfg["hue_bridges"], list):
            cfg["hue_bridges"] = []
        clean_bridges = []
        bridge_ids = set()
        for bridge in cfg["hue_bridges"]:
            if not isinstance(bridge, dict):
                continue
            bridge.setdefault("id", uuid.uuid4().hex[:12])
            bridge["id"] = str(bridge.get("id") or uuid.uuid4().hex[:12])[:40]
            bridge["name"] = str(bridge.get("name") or "Philips Hue Bridge").strip()[:80]
            bridge["host"] = str(bridge.get("host") or "").strip()[:255]
            bridge["application_key"] = str(bridge.get("application_key") or "")[:256]
            bridge["client_key"] = str(bridge.get("client_key") or "")[:256]
            bridge["enabled"] = bool(bridge.get("enabled", True))
            bridge_ids.add(bridge["id"])
            clean_bridges.append(bridge)
        cfg["hue_bridges"] = clean_bridges

        cfg.setdefault("hue_lights", [])
        if not isinstance(cfg["hue_lights"], list):
            cfg["hue_lights"] = []
        clean_hue_lights = []
        seen_hue_ids = set()
        for light in cfg["hue_lights"]:
            if not isinstance(light, dict):
                continue
            bridge_id = str(light.get("bridge_id") or "")
            resource_id = str(light.get("resource_id") or "")
            if bridge_id not in bridge_ids or not resource_id:
                continue
            light.setdefault("id", uuid.uuid4().hex[:12])
            light["id"] = str(light.get("id") or uuid.uuid4().hex[:12])[:40]
            if light["id"] in seen_hue_ids:
                light["id"] = uuid.uuid4().hex[:12]
            seen_hue_ids.add(light["id"])
            light["bridge_id"] = bridge_id
            light["resource_id"] = resource_id[:80]
            light["name"] = str(light.get("name") or "Hue Light").strip()[:80]
            light["enabled"] = bool(light.get("enabled", True))
            light["supports_color"] = bool(light.get("supports_color", False))
            light["supports_color_temperature"] = bool(light.get("supports_color_temperature", False))
            light["supports_dimming"] = bool(light.get("supports_dimming", True))
            light.setdefault("modes", default_device_modes())
            if not isinstance(light.get("modes"), dict):
                light["modes"] = default_device_modes()
            for mode_name, fallback in default_device_modes().items():
                light["modes"].setdefault(mode_name, copy.deepcopy(fallback))
                if not isinstance(light["modes"].get(mode_name), dict):
                    light["modes"][mode_name] = copy.deepcopy(fallback)
                for key, value in fallback.items():
                    light["modes"][mode_name].setdefault(key, copy.deepcopy(value))
            clean_hue_lights.append(light)
        cfg["hue_lights"] = clean_hue_lights

        # Stage 6: named device groups and multi-device scenes are global resources.
        cfg.setdefault("device_groups", [])
        if not isinstance(cfg["device_groups"], list):
            cfg["device_groups"] = []
        valid_device_ids = {str(d.get("id")) for d in cfg["devices"] if isinstance(d, dict) and d.get("id")}
        valid_device_ids.update(str(d.get("id")) for d in cfg.get("hue_lights", []) if isinstance(d, dict) and d.get("id"))
        clean_groups = []
        for group in cfg["device_groups"]:
            if not isinstance(group, dict):
                continue
            group.setdefault("id", uuid.uuid4().hex[:12])
            group["id"] = str(group.get("id") or uuid.uuid4().hex[:12])[:40]
            group["name"] = str(group.get("name") or "Device Group").strip()[:60]
            ids = group.get("device_ids") if isinstance(group.get("device_ids"), list) else []
            group["device_ids"] = [str(x) for x in ids if str(x) in valid_device_ids]
            clean_groups.append(group)
        cfg["device_groups"] = clean_groups
        valid_group_ids = {str(g.get("id")) for g in clean_groups}

        cfg.setdefault("scenes", [])
        if not isinstance(cfg["scenes"], list):
            cfg["scenes"] = []
        clean_scenes = []
        for scene in cfg["scenes"]:
            if not isinstance(scene, dict):
                continue
            scene.setdefault("id", uuid.uuid4().hex[:12])
            scene["id"] = str(scene.get("id") or uuid.uuid4().hex[:12])[:40]
            scene["name"] = str(scene.get("name") or "Lighting Scene").strip()[:60]
            try:
                scene["repeat_count"] = max(0, min(20, int(scene.get("repeat_count", 1))))
            except (TypeError, ValueError):
                scene["repeat_count"] = 1
            actions = scene.get("actions") if isinstance(scene.get("actions"), list) else []
            clean_actions = []
            for action in actions:
                if not isinstance(action, dict):
                    continue
                action.setdefault("id", uuid.uuid4().hex[:12])
                action["id"] = str(action.get("id") or uuid.uuid4().hex[:12])[:40]
                action["name"] = str(action.get("name") or "Scene Action").strip()[:60]
                target_type = str(action.get("target_type") or "all").lower()
                if target_type not in {"all", "device", "group"}:
                    target_type = "all"
                action["target_type"] = target_type
                target_id = str(action.get("target_id") or "")
                if target_type == "device" and target_id not in valid_device_ids:
                    target_id = ""
                if target_type == "group" and target_id not in valid_group_ids:
                    target_id = ""
                action["target_id"] = target_id
                try:
                    action["step"] = max(1, min(50, int(action.get("step", 1))))
                except (TypeError, ValueError):
                    action["step"] = 1
                try:
                    action["delay_ms"] = max(0, min(60000, int(action.get("delay_ms", 0))))
                except (TypeError, ValueError):
                    action["delay_ms"] = 0
                try:
                    action["hold_ms"] = max(0, min(60000, int(action.get("hold_ms", 0))))
                except (TypeError, ValueError):
                    action["hold_ms"] = 0
                if not isinstance(action.get("effect"), dict):
                    action["effect"] = trigger_effect_template()
                for key, value in trigger_effect_template().items():
                    action["effect"].setdefault(key, copy.deepcopy(value))
                clean_actions.append(action)
            scene["actions"] = clean_actions
            clean_scenes.append(scene)
        cfg["scenes"] = clean_scenes
        valid_scene_ids = {str(scene.get("id")) for scene in clean_scenes}
        for trigger in cfg.get("triggers", []):
            if isinstance(trigger, dict) and str(trigger.get("scene_id") or "") not in valid_scene_ids:
                trigger["scene_id"] = ""

        # Stage 5 profiles snapshot the current modes + trigger list. Device host
        # information stays global so switching a profile can never lose a WLED.
        if "profiles" not in cfg or not isinstance(cfg.get("profiles"), list):
            cfg["profiles"] = []
        clean_profiles = []
        valid_device_ids = {str(d.get("id")) for d in cfg["devices"] if isinstance(d, dict) and d.get("id")}
        valid_device_ids.update(str(d.get("id")) for d in cfg.get("hue_lights", []) if isinstance(d, dict) and d.get("id"))
        for profile in cfg["profiles"]:
            if not isinstance(profile, dict):
                continue
            profile.setdefault("id", uuid.uuid4().hex[:12])
            profile["id"] = str(profile["id"] or uuid.uuid4().hex[:12])[:40]
            profile["name"] = str(profile.get("name") or "Profile").strip()[:60]
            profile.setdefault("created_at", time.strftime("%Y-%m-%d %H:%M:%S"))
            profile.setdefault("updated_at", profile["created_at"])
            if not isinstance(profile.get("device_modes"), dict):
                profile["device_modes"] = {}
            profile["device_modes"] = {
                str(device_id): copy.deepcopy(modes)
                for device_id, modes in profile["device_modes"].items()
                if str(device_id) in valid_device_ids and isinstance(modes, dict)
            }
            if not isinstance(profile.get("triggers"), list):
                profile["triggers"] = []
            for raw in profile["triggers"]:
                self._normalize_trigger(raw)
            clean_profiles.append(profile)
        cfg["profiles"] = clean_profiles

        if not cfg["profiles"]:
            cfg["profiles"] = [_profile_from_config(cfg, "Default")]
        active_id = str(cfg["app"].get("active_profile_id") or "")
        if not any(str(p.get("id")) == active_id for p in cfg["profiles"]):
            cfg["app"]["active_profile_id"] = cfg["profiles"][0]["id"]

        return cfg

    @staticmethod
    def _normalize_trigger(raw: Any) -> None:
        if not isinstance(raw, dict):
            return
        raw.setdefault("id", uuid.uuid4().hex[:12])
        raw.setdefault("name", "Trigger")
        raw.setdefault("enabled", False)
        raw.setdefault("trigger_type", "dart")
        raw.setdefault("value", "T20")
        raw.setdefault("minimum", 0)
        raw.setdefault("maximum", 180)
        raw.setdefault("duration_ms", 1500)
        raw.setdefault("priority", 20)
        raw.setdefault("device_ids", [])
        if not isinstance(raw.get("device_ids"), list):
            raw["device_ids"] = []
        raw.setdefault("scene_id", "")
        raw["scene_id"] = str(raw.get("scene_id") or "")[:40]
        raw.setdefault("effect", trigger_effect_template())
        if not isinstance(raw.get("effect"), dict):
            raw["effect"] = trigger_effect_template()
        for key, value in trigger_effect_template().items():
            raw["effect"].setdefault(key, copy.deepcopy(value))

    def _write(self, cfg: Dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(cfg, handle, indent=2)
        temp.replace(self.path)

    def get(self) -> Dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._config)

    def save(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._config = self._migrate(copy.deepcopy(cfg))
            self._write(self._config)
            return copy.deepcopy(self._config)

    def update(self, mutator) -> Dict[str, Any]:
        with self._lock:
            cfg = copy.deepcopy(self._config)
            mutator(cfg)
            self._config = self._migrate(cfg)
            self._write(self._config)
            return copy.deepcopy(self._config)
