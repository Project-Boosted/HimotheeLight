from __future__ import annotations

import copy
import logging
import re
import time
import uuid
from typing import Any, Dict, Iterable

from . import __version__
from .config_store import ConfigStore, default_device_modes, trigger_effect_template
from .lighting import LightingStateManager
from .wled import build_mode_payload

logger = logging.getLogger("himotheelight")

PROFILE_SHARE_FORMAT = "HimotheeLightProfile"
PROFILE_SHARE_VERSION = 1
MAX_SHARE_DEVICES = 50
MAX_SHARE_TRIGGERS = 200
MAX_SHARE_SCENES = 100
MAX_SHARE_GROUPS = 100
MAX_SHARE_ACTIONS = 500


def _slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "Profile").strip()).strip("-._")
    return (text or "Profile")[:80]


def _unique_name(base: str, existing: Iterable[str]) -> str:
    base = str(base or "Imported Profile").strip()[:60] or "Imported Profile"
    used = {str(x).casefold() for x in existing}
    if base.casefold() not in used:
        return base
    for index in range(2, 1000):
        suffix = f" ({index})"
        candidate = f"{base[:max(1, 60-len(suffix))]}{suffix}"
        if candidate.casefold() not in used:
            return candidate
    return f"{base[:47]}-{uuid.uuid4().hex[:8]}"


def _deep_mode(mode: Any) -> Dict[str, Any]:
    if not isinstance(mode, dict):
        raise ValueError("Shared profile contains an invalid lighting mode")
    result = copy.deepcopy(mode)
    build_mode_payload(result)
    return result


def _validate_modes(modes: Any) -> Dict[str, Any]:
    if not isinstance(modes, dict):
        raise ValueError("Shared profile contains invalid device modes")
    result: Dict[str, Any] = {}
    for mode_name in ("idle", "active"):
        value = modes.get(mode_name)
        if not isinstance(value, dict):
            raise ValueError(f"Shared profile is missing the {mode_name.title()} lighting mode")
        result[mode_name] = _deep_mode(value)
    return result


def _clean_effect(effect: Any) -> Dict[str, Any]:
    if not isinstance(effect, dict):
        effect = trigger_effect_template()
    result = copy.deepcopy(effect)
    build_mode_payload(result)
    return result


class ProfileManager:
    """Stores/switches complete lighting profiles and portable share bundles."""

    def __init__(self, store: ConfigStore, lighting: LightingStateManager):
        self.store = store
        self.lighting = lighting

    def snapshot(self) -> Dict[str, Any]:
        cfg = self.store.get()
        active_id = cfg.get("app", {}).get("active_profile_id")
        profiles = []
        for profile in cfg.get("profiles", []):
            profiles.append({
                "id": profile.get("id"),
                "name": profile.get("name"),
                "created_at": profile.get("created_at"),
                "updated_at": profile.get("updated_at"),
                "trigger_count": len(profile.get("triggers", [])),
                "enabled_trigger_count": sum(1 for t in profile.get("triggers", []) if isinstance(t, dict) and t.get("enabled")),
                "device_mode_count": len(profile.get("device_modes", {})),
                "active": profile.get("id") == active_id,
            })
        return {"active_profile_id": active_id, "profiles": profiles}

    @staticmethod
    def _capture(cfg: Dict[str, Any]) -> Dict[str, Any]:
        device_modes = {}
        for collection in (cfg.get("devices", []), cfg.get("hue_lights", [])):
            for device in collection:
                if isinstance(device, dict) and device.get("id"):
                    device_modes[str(device["id"])] = copy.deepcopy(device.get("modes", default_device_modes()))
        return {"device_modes": device_modes, "triggers": copy.deepcopy(cfg.get("triggers", []))}

    def sync_active(self) -> None:
        """Persist current modes/triggers into the selected profile automatically."""
        def mutate(cfg: Dict[str, Any]) -> None:
            active_id = cfg.get("app", {}).get("active_profile_id")
            if not active_id:
                return
            captured = self._capture(cfg)
            for profile in cfg.get("profiles", []):
                if profile.get("id") == active_id:
                    profile.update(captured)
                    profile["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    return
        self.store.update(mutate)

    def create(self, name: str) -> Dict[str, Any]:
        clean_name = str(name or "New Profile").strip()[:60] or "New Profile"
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        profile_id = uuid.uuid4().hex[:12]

        def mutate(cfg: Dict[str, Any]) -> None:
            captured = self._capture(cfg)
            profile = {
                "id": profile_id,
                "name": clean_name,
                "created_at": stamp,
                "updated_at": stamp,
                **captured,
            }
            cfg.setdefault("profiles", []).append(profile)
            cfg.setdefault("app", {})["active_profile_id"] = profile_id
        cfg = self.store.update(mutate)
        logger.info("Created lighting profile '%s'", clean_name)
        return next(p for p in cfg["profiles"] if p.get("id") == profile_id)

    def rename(self, profile_id: str, name: str) -> Dict[str, Any]:
        clean_name = str(name or "Profile").strip()[:60] or "Profile"
        found = False

        def mutate(cfg: Dict[str, Any]) -> None:
            nonlocal found
            for profile in cfg.get("profiles", []):
                if profile.get("id") == profile_id:
                    profile["name"] = clean_name
                    profile["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    found = True
                    return
        cfg = self.store.update(mutate)
        if not found:
            raise ValueError("Profile not found")
        logger.info("Renamed lighting profile to '%s'", clean_name)
        return next(p for p in cfg["profiles"] if p.get("id") == profile_id)

    def apply(self, profile_id: str) -> Dict[str, Any]:
        cfg = self.store.get()
        profile = next((p for p in cfg.get("profiles", []) if p.get("id") == profile_id), None)
        if not profile:
            raise ValueError("Profile not found")
        valid_device_ids = {str(d.get("id")) for d in cfg.get("devices", []) if isinstance(d, dict)}
        valid_device_ids.update(str(d.get("id")) for d in cfg.get("hue_lights", []) if isinstance(d, dict))
        profile_copy = copy.deepcopy(profile)

        def mutate(current: Dict[str, Any]) -> None:
            modes_by_id = profile_copy.get("device_modes", {})
            for collection_name in ("devices", "hue_lights"):
                for device in current.get(collection_name, []):
                    device_id = str(device.get("id"))
                    modes = modes_by_id.get(device_id)
                    if isinstance(modes, dict):
                        device["modes"] = copy.deepcopy(modes)
            triggers = copy.deepcopy(profile_copy.get("triggers", []))
            for trigger in triggers:
                if isinstance(trigger, dict):
                    ids = trigger.get("device_ids") if isinstance(trigger.get("device_ids"), list) else []
                    trigger["device_ids"] = [str(x) for x in ids if str(x) in valid_device_ids]
            current["triggers"] = triggers
            current.setdefault("app", {})["active_profile_id"] = profile_id
        self.store.update(mutate)

        self.lighting.cancel_trigger("Lighting profile switched", restore=False)
        mode = self.lighting.snapshot().get("mode", "idle")
        result = self.lighting.apply_base_mode(
            mode, source="profile", reason=f"Profile: {profile_copy.get('name', 'Profile')}", force=True
        )
        logger.info("Applied lighting profile '%s'", profile_copy.get("name"))
        return {"profile": profile_copy, "lighting": result}

    def delete(self, profile_id: str) -> Dict[str, Any]:
        cfg = self.store.get()
        profiles = cfg.get("profiles", [])
        profile = next((p for p in profiles if p.get("id") == profile_id), None)
        if not profile:
            raise ValueError("Profile not found")
        if len(profiles) <= 1:
            raise ValueError("HimotheeLight must keep at least one profile")
        active = cfg.get("app", {}).get("active_profile_id")
        if active == profile_id:
            raise ValueError("Switch to another profile before deleting the active profile")
        self.store.update(lambda current: current.__setitem__("profiles", [p for p in current.get("profiles", []) if p.get("id") != profile_id]))
        logger.info("Deleted lighting profile '%s'", profile.get("name"))
        return {"ok": True}

    # ------------------------------------------------------------------
    # portable profile sharing
    # ------------------------------------------------------------------

    def export_bundle(self, profile_id: str) -> Dict[str, Any]:
        """Build a portable profile bundle with no WLED host/IP information."""
        cfg = self.store.get()
        profile = next((p for p in cfg.get("profiles", []) if str(p.get("id")) == str(profile_id)), None)
        if not profile:
            raise ValueError("Profile not found")

        devices_by_id = {}
        for d in cfg.get("devices", []):
            if isinstance(d, dict) and d.get("id"):
                row = dict(d); row["_share_type"] = "wled"; devices_by_id[str(d.get("id"))] = row
        for d in cfg.get("hue_lights", []):
            if isinstance(d, dict) and d.get("id"):
                row = dict(d); row["_share_type"] = "hue"; devices_by_id[str(d.get("id"))] = row
        scenes_by_id = {str(s.get("id")): s for s in cfg.get("scenes", []) if isinstance(s, dict) and s.get("id")}
        groups_by_id = {str(g.get("id")): g for g in cfg.get("device_groups", []) if isinstance(g, dict) and g.get("id")}

        needed_devices = set(str(x) for x in profile.get("device_modes", {}).keys())
        needed_scenes = set()
        for trigger in profile.get("triggers", []):
            if not isinstance(trigger, dict):
                continue
            needed_devices.update(str(x) for x in trigger.get("device_ids", []) if x)
            if trigger.get("scene_id"):
                needed_scenes.add(str(trigger.get("scene_id")))

        # Include device/group dependencies used by referenced scenes.
        needed_groups = set()
        for scene_id in list(needed_scenes):
            scene = scenes_by_id.get(scene_id)
            if not scene:
                continue
            for action in scene.get("actions", []):
                if not isinstance(action, dict):
                    continue
                target_type = str(action.get("target_type") or "all")
                target_id = str(action.get("target_id") or "")
                if target_type == "device" and target_id:
                    needed_devices.add(target_id)
                elif target_type == "group" and target_id:
                    needed_groups.add(target_id)
        for group_id in list(needed_groups):
            group = groups_by_id.get(group_id)
            if group:
                needed_devices.update(str(x) for x in group.get("device_ids", []) if x)

        device_order = [device_id for device_id in devices_by_id if device_id in needed_devices]
        # Preserve dangling profile slots too, but never expose their original ids/names.
        for device_id in sorted(needed_devices):
            if device_id not in device_order:
                device_order.append(device_id)
        device_map = {device_id: f"device-{index+1}" for index, device_id in enumerate(device_order)}
        scene_order = [scene_id for scene_id in scenes_by_id if scene_id in needed_scenes]
        scene_map = {scene_id: f"scene-{index+1}" for index, scene_id in enumerate(scene_order)}
        group_order = [group_id for group_id in groups_by_id if group_id in needed_groups]
        group_map = {group_id: f"group-{index+1}" for index, group_id in enumerate(group_order)}

        portable_devices = []
        for original_id in device_order:
            device = devices_by_id.get(original_id, {})
            portable_devices.append({
                "slot_id": device_map[original_id],
                "name": str(device.get("name") or f"Light {len(portable_devices)+1}")[:80],
                "type": str(device.get("_share_type") or "wled"),
            })

        portable_groups = []
        for original_id in group_order:
            group = groups_by_id[original_id]
            portable_groups.append({
                "id": group_map[original_id],
                "name": str(group.get("name") or "Device Group")[:60],
                "device_slots": [device_map[x] for x in map(str, group.get("device_ids", [])) if x in device_map],
            })

        portable_scenes = []
        for original_id in scene_order:
            scene = copy.deepcopy(scenes_by_id[original_id])
            scene["id"] = scene_map[original_id]
            for action in scene.get("actions", []):
                if not isinstance(action, dict):
                    continue
                target_type = str(action.get("target_type") or "all")
                target_id = str(action.get("target_id") or "")
                if target_type == "device":
                    action["target_id"] = device_map.get(target_id, "")
                elif target_type == "group":
                    action["target_id"] = group_map.get(target_id, "")
            portable_scenes.append(scene)

        portable_profile = {
            "name": str(profile.get("name") or "Profile")[:60],
            "device_modes": {
                device_map[device_id]: copy.deepcopy(modes)
                for device_id, modes in profile.get("device_modes", {}).items()
                if str(device_id) in device_map and isinstance(modes, dict)
            },
            "triggers": copy.deepcopy(profile.get("triggers", [])),
        }
        for trigger in portable_profile["triggers"]:
            if not isinstance(trigger, dict):
                continue
            trigger.pop("id", None)
            trigger["device_ids"] = [device_map[str(x)] for x in trigger.get("device_ids", []) if str(x) in device_map]
            trigger["scene_id"] = scene_map.get(str(trigger.get("scene_id") or ""), "")

        bundle = {
            "format": PROFILE_SHARE_FORMAT,
            "format_version": PROFILE_SHARE_VERSION,
            "app_version": __version__,
            "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "profile": portable_profile,
            "devices": portable_devices,
            "device_groups": portable_groups,
            "scenes": portable_scenes,
        }
        logger.info("Exported shareable profile '%s' (%d device slot(s), %d scene(s))", profile.get("name"), len(portable_devices), len(portable_scenes))
        return {
            "filename": f"HimotheeLight-Profile-{_slug(portable_profile['name'])}.json",
            "bundle": bundle,
        }

    def preview_import(self, bundle: Dict[str, Any]) -> Dict[str, Any]:
        clean = self._validate_bundle(bundle)
        cfg = self.store.get()
        local_devices = [
            {"id": str(d.get("id")), "name": str(d.get("name") or "WLED"), "type": "wled"}
            for d in cfg.get("devices", []) if isinstance(d, dict) and d.get("id")
        ]
        local_devices.extend(
            {"id": str(d.get("id")), "name": str(d.get("name") or "Hue Light"), "type": "hue"}
            for d in cfg.get("hue_lights", []) if isinstance(d, dict) and d.get("id")
        )
        used_slots = self._required_device_slots(clean)
        local_by_name: Dict[str, list[str]] = {}
        for device in local_devices:
            key = f"{device.get('type','wled')}:{device['name'].strip().casefold()}"
            local_by_name.setdefault(key, []).append(device["id"])

        device_rows = []
        for slot in clean["devices"]:
            matches = local_by_name.get(f"{slot.get('type','wled')}:{slot['name'].strip().casefold()}", [])
            suggested = matches[0] if len(matches) == 1 else ""
            device_rows.append({
                "slot_id": slot["slot_id"],
                "name": slot["name"],
                "type": slot.get("type", "wled"),
                "required": slot["slot_id"] in used_slots,
                "suggested_device_id": suggested,
                "suggestion": "name_match" if suggested else "none",
            })

        warnings = []
        if not local_devices and clean["devices"]:
            warnings.append("Add the required WLED/Hue lighting endpoints to HimotheeLight before importing this profile.")
        if clean["scenes"]:
            warnings.append("Imported scenes are copied into your local Scene Library with new IDs.")
        warnings.append("WLED hosts and Hue Bridge credentials are never imported; shared lighting slots map only to your existing local endpoints.")
        warnings.append("WLED effect, palette and preset numbers are portable settings, but may look different if the recipient uses different WLED firmware or preset layouts; test the imported profile before a live match.")
        return {
            "ok": True,
            "format": clean["format"],
            "format_version": clean["format_version"],
            "app_version": clean.get("app_version"),
            "profile_name": clean["profile"]["name"],
            "trigger_count": len(clean["profile"]["triggers"]),
            "enabled_trigger_count": sum(1 for t in clean["profile"]["triggers"] if isinstance(t, dict) and t.get("enabled")),
            "scene_count": len(clean["scenes"]),
            "group_count": len(clean["device_groups"]),
            "devices": device_rows,
            "local_devices": local_devices,
            "warnings": warnings,
        }

    def import_bundle(self, bundle: Dict[str, Any], mapping: Dict[str, Any], name: str | None = None) -> Dict[str, Any]:
        clean = self._validate_bundle(bundle)
        cfg = self.store.get()
        valid_local = {str(d.get("id")): dict(d, _share_type="wled") for d in cfg.get("devices", []) if isinstance(d, dict) and d.get("id")}
        valid_local.update({str(d.get("id")): dict(d, _share_type="hue") for d in cfg.get("hue_lights", []) if isinstance(d, dict) and d.get("id")})
        slot_types = {str(d.get("slot_id")): str(d.get("type") or "wled") for d in clean["devices"]}
        mapping = mapping if isinstance(mapping, dict) else {}
        slot_ids = {d["slot_id"] for d in clean["devices"]}
        mapped: Dict[str, str] = {}
        for slot_id, local_id in mapping.items():
            slot_id = str(slot_id)
            local_id = str(local_id or "")
            if slot_id not in slot_ids:
                continue
            if local_id:
                if local_id not in valid_local:
                    raise ValueError("Import mapping references an unknown local lighting endpoint")
                if str(valid_local[local_id].get("_share_type") or "wled") != slot_types.get(slot_id, "wled"):
                    raise ValueError("Import mapping must map WLED slots to WLED and Hue slots to Hue lights")
                mapped[slot_id] = local_id

        required = self._required_device_slots(clean)
        unresolved = sorted(required - set(mapped))
        if unresolved:
            names = [next((d["name"] for d in clean["devices"] if d["slot_id"] == slot), slot) for slot in unresolved]
            raise ValueError("Map all devices used by triggers/scenes before importing: " + ", ".join(names))

        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        profile_id = uuid.uuid4().hex[:12]
        profile_name = _unique_name(str(name or clean["profile"]["name"]), [p.get("name", "") for p in cfg.get("profiles", [])])

        # New local IDs avoid collisions with existing scene/group libraries.
        group_id_map = {str(g["id"]): uuid.uuid4().hex[:12] for g in clean["device_groups"]}
        scene_id_map = {str(s["id"]): uuid.uuid4().hex[:12] for s in clean["scenes"]}
        existing_group_names = [g.get("name", "") for g in cfg.get("device_groups", [])]
        existing_scene_names = [s.get("name", "") for s in cfg.get("scenes", [])]

        new_groups = []
        for source in clean["device_groups"]:
            group = {
                "id": group_id_map[str(source["id"])],
                "name": _unique_name(source["name"], existing_group_names + [g["name"] for g in new_groups]),
                "device_ids": [mapped[x] for x in source.get("device_slots", []) if x in mapped],
            }
            new_groups.append(group)

        new_scenes = []
        for source in clean["scenes"]:
            scene = copy.deepcopy(source)
            old_scene_id = str(scene.get("id"))
            scene["id"] = scene_id_map[old_scene_id]
            scene["name"] = _unique_name(scene.get("name") or "Lighting Scene", existing_scene_names + [s["name"] for s in new_scenes])
            for action in scene.get("actions", []):
                action["id"] = uuid.uuid4().hex[:12]
                target_type = str(action.get("target_type") or "all")
                target_id = str(action.get("target_id") or "")
                if target_type == "device":
                    action["target_id"] = mapped.get(target_id, "")
                elif target_type == "group":
                    action["target_id"] = group_id_map.get(target_id, "")
                action["effect"] = _clean_effect(action.get("effect"))
            new_scenes.append(scene)

        imported_modes = {}
        for slot_id, modes in clean["profile"].get("device_modes", {}).items():
            local_id = mapped.get(str(slot_id))
            if local_id:
                imported_modes[local_id] = _validate_modes(modes)

        new_triggers = []
        allowed_types = {"dart", "visit_exact", "visit_range", "combination", "target", "board_event", "game_event", "player_turn"}
        for source in clean["profile"].get("triggers", []):
            if not isinstance(source, dict):
                continue
            trigger = copy.deepcopy(source)
            trigger["id"] = uuid.uuid4().hex[:12]
            trigger["name"] = str(trigger.get("name") or "Imported Trigger")[:80]
            trigger_type = str(trigger.get("trigger_type") or "dart")
            if trigger_type not in allowed_types:
                raise ValueError(f"Shared profile contains unsupported trigger type: {trigger_type}")
            trigger["trigger_type"] = trigger_type
            try:
                trigger["duration_ms"] = max(100, min(60000, int(trigger.get("duration_ms", 1500))))
                trigger["priority"] = max(0, min(100, int(trigger.get("priority", 20))))
                trigger["minimum"] = max(0, min(180, int(trigger.get("minimum", 0))))
                trigger["maximum"] = max(trigger["minimum"], min(180, int(trigger.get("maximum", 180))))
            except (TypeError, ValueError) as exc:
                raise ValueError("Shared profile contains invalid trigger timing/score values") from exc
            trigger["enabled"] = bool(trigger.get("enabled", False))
            trigger["device_ids"] = [mapped[str(x)] for x in trigger.get("device_ids", []) if str(x) in mapped]
            trigger["scene_id"] = scene_id_map.get(str(trigger.get("scene_id") or ""), "")
            trigger["effect"] = _clean_effect(trigger.get("effect"))
            new_triggers.append(trigger)

        new_profile = {
            "id": profile_id,
            "name": profile_name,
            "created_at": stamp,
            "updated_at": stamp,
            "device_modes": imported_modes,
            "triggers": new_triggers,
            "imported_from": {
                "format_version": clean["format_version"],
                "app_version": str(clean.get("app_version") or ""),
                "profile_name": clean["profile"]["name"],
                "imported_at": stamp,
            },
        }

        def mutate(current: Dict[str, Any]) -> None:
            current.setdefault("device_groups", []).extend(copy.deepcopy(new_groups))
            current.setdefault("scenes", []).extend(copy.deepcopy(new_scenes))
            current.setdefault("profiles", []).append(copy.deepcopy(new_profile))

        saved = self.store.update(mutate)
        imported = next(p for p in saved.get("profiles", []) if p.get("id") == profile_id)
        logger.info(
            "Imported shared profile '%s' as '%s' (%d trigger(s), %d scene(s), %d group(s))",
            clean["profile"]["name"], profile_name, len(new_triggers), len(new_scenes), len(new_groups),
        )
        return {
            "profile": imported,
            "profile_id": profile_id,
            "name": profile_name,
            "mapped_devices": len(mapped),
            "trigger_count": len(new_triggers),
            "scene_count": len(new_scenes),
            "group_count": len(new_groups),
        }

    def _validate_bundle(self, bundle: Any) -> Dict[str, Any]:
        if not isinstance(bundle, dict):
            raise ValueError("Profile file must contain a JSON object")
        if bundle.get("format") != PROFILE_SHARE_FORMAT:
            raise ValueError("This is not a HimotheeLight shareable profile file")
        try:
            version = int(bundle.get("format_version", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("Profile share format version is invalid") from exc
        if version != PROFILE_SHARE_VERSION:
            raise ValueError(f"Unsupported profile share format version: {version}")
        profile = bundle.get("profile")
        if not isinstance(profile, dict):
            raise ValueError("Shared profile is missing profile data")
        profile_name = str(profile.get("name") or "").strip()
        if not profile_name:
            raise ValueError("Shared profile has no name")
        triggers = profile.get("triggers") if isinstance(profile.get("triggers"), list) else None
        device_modes = profile.get("device_modes") if isinstance(profile.get("device_modes"), dict) else None
        devices = bundle.get("devices") if isinstance(bundle.get("devices"), list) else None
        groups = bundle.get("device_groups") if isinstance(bundle.get("device_groups"), list) else None
        scenes = bundle.get("scenes") if isinstance(bundle.get("scenes"), list) else None
        if None in (triggers, device_modes, devices, groups, scenes):
            raise ValueError("Shared profile file is incomplete")
        if len(devices) > MAX_SHARE_DEVICES or len(triggers) > MAX_SHARE_TRIGGERS or len(groups) > MAX_SHARE_GROUPS or len(scenes) > MAX_SHARE_SCENES:
            raise ValueError("Shared profile exceeds HimotheeLight import limits")

        clean_devices = []
        slot_ids = set()
        for raw in devices:
            if not isinstance(raw, dict):
                raise ValueError("Shared profile contains an invalid device slot")
            slot_id = str(raw.get("slot_id") or "").strip()[:80]
            if not slot_id or slot_id in slot_ids:
                raise ValueError("Shared profile contains duplicate/invalid device slots")
            slot_ids.add(slot_id)
            device_type = str(raw.get("type") or "wled").lower().strip()
            if device_type not in {"wled", "hue"}:
                raise ValueError("Shared profile contains an unsupported lighting endpoint type")
            clean_devices.append({"slot_id": slot_id, "name": str(raw.get("name") or "Light")[:80], "type": device_type})
        if any(str(slot) not in slot_ids for slot in device_modes):
            raise ValueError("Shared profile device modes reference an unknown device slot")
        for modes in device_modes.values():
            _validate_modes(modes)

        clean_groups = []
        group_ids = set()
        for raw in groups:
            if not isinstance(raw, dict):
                raise ValueError("Shared profile contains an invalid device group")
            group_id = str(raw.get("id") or "").strip()[:80]
            if not group_id or group_id in group_ids:
                raise ValueError("Shared profile contains duplicate/invalid group IDs")
            slots = [str(x) for x in (raw.get("device_slots") or [])]
            if any(x not in slot_ids for x in slots):
                raise ValueError("Shared device group references an unknown device slot")
            group_ids.add(group_id)
            clean_groups.append({"id": group_id, "name": str(raw.get("name") or "Device Group")[:60], "device_slots": slots})

        clean_scenes = []
        scene_ids = set()
        action_count = 0
        for raw in scenes:
            if not isinstance(raw, dict):
                raise ValueError("Shared profile contains an invalid scene")
            scene = copy.deepcopy(raw)
            scene_id = str(scene.get("id") or "").strip()[:80]
            if not scene_id or scene_id in scene_ids:
                raise ValueError("Shared profile contains duplicate/invalid scene IDs")
            scene_ids.add(scene_id)
            scene["id"] = scene_id
            scene["name"] = str(scene.get("name") or "Lighting Scene")[:60]
            actions = scene.get("actions") if isinstance(scene.get("actions"), list) else []
            action_count += len(actions)
            if action_count > MAX_SHARE_ACTIONS:
                raise ValueError("Shared profile contains too many scene actions")
            for action in actions:
                if not isinstance(action, dict):
                    raise ValueError("Shared profile contains an invalid scene action")
                target_type = str(action.get("target_type") or "all")
                target_id = str(action.get("target_id") or "")
                if target_type not in {"all", "device", "group"}:
                    raise ValueError("Shared scene contains an invalid target type")
                if target_type == "device" and target_id not in slot_ids:
                    raise ValueError("Shared scene references an unknown device slot")
                if target_type == "group" and target_id not in group_ids:
                    raise ValueError("Shared scene references an unknown device group")
                _clean_effect(action.get("effect"))
            clean_scenes.append(scene)

        clean_triggers = copy.deepcopy(triggers)
        for trigger in clean_triggers:
            if not isinstance(trigger, dict):
                raise ValueError("Shared profile contains an invalid trigger")
            ids = [str(x) for x in (trigger.get("device_ids") or [])]
            if any(x not in slot_ids for x in ids):
                raise ValueError("Shared trigger references an unknown device slot")
            scene_id = str(trigger.get("scene_id") or "")
            if scene_id and scene_id not in scene_ids:
                raise ValueError("Shared trigger references a scene that is not included in the file")
            _clean_effect(trigger.get("effect"))

        return {
            "format": PROFILE_SHARE_FORMAT,
            "format_version": version,
            "app_version": str(bundle.get("app_version") or "")[:40],
            "exported_at": str(bundle.get("exported_at") or "")[:80],
            "profile": {
                "name": profile_name[:60],
                "device_modes": copy.deepcopy(device_modes),
                "triggers": clean_triggers,
            },
            "devices": clean_devices,
            "device_groups": clean_groups,
            "scenes": clean_scenes,
        }

    @staticmethod
    def _required_device_slots(bundle: Dict[str, Any]) -> set[str]:
        """Slots whose absence could turn a targeted effect into a different effect."""
        required = set()
        for trigger in bundle["profile"].get("triggers", []):
            if isinstance(trigger, dict):
                required.update(str(x) for x in trigger.get("device_ids", []) if x)
        groups_by_id = {str(g["id"]): g for g in bundle.get("device_groups", [])}
        for scene in bundle.get("scenes", []):
            for action in scene.get("actions", []):
                if not isinstance(action, dict):
                    continue
                target_type = str(action.get("target_type") or "all")
                target_id = str(action.get("target_id") or "")
                if target_type == "device" and target_id:
                    required.add(target_id)
                elif target_type == "group" and target_id in groups_by_id:
                    required.update(str(x) for x in groups_by_id[target_id].get("device_slots", []) if x)
        return required
