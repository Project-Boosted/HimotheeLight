from __future__ import annotations

import json
import logging
import mimetypes
import os
import posixpath
import re
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import unquote, urlparse

from . import APP_NAME, __version__
from .autodarts import AutodartsError, AutodartsClient, normalize_board_host
from .activity import AutodartsActivityController
from .config_store import ConfigStore, default_device_modes, trigger_template
from .game_bridge import AutodartsGameBridge
from .hue import HueError, apply_light_mode, discover_bridges, list_lights, normalize_bridge_host, pair_bridge, probe_bridge
from .lighting import LightingStateManager
from .logging_buffer import MemoryLogHandler
from .profiles import ProfileManager
from .trigger_engine import TriggerEngine, parse_combination, normalize_target
from .wled import WLEDError, apply_mode, build_mode_payload, get_state, normalize_host, probe, send_state

logger = logging.getLogger("himotheelight")


class HimotheeLightContext:
    def __init__(self, store: ConfigStore, web_dir: Path, memory_logs: MemoryLogHandler):
        self.store = store
        self.web_dir = web_dir.resolve()
        self.memory_logs = memory_logs
        self._probe_cache: Dict[str, Dict[str, Any]] = {}
        self._cache_lock = threading.Lock()
        self.lighting = LightingStateManager(store)
        self.triggers = TriggerEngine(store, self.lighting)
        self.profiles = ProfileManager(store, self.lighting)
        self.activity = AutodartsActivityController(store, self.lighting)
        self.game_bridge = AutodartsGameBridge(
            self._autodarts_settings,
            self.activity.game_base_mode,
            self._handle_game_event,
        )
        self.autodarts = AutodartsClient(
            self._autodarts_settings,
            self.activity.board_base_mode,
            self._handle_board_event,
            allow_base_mode=lambda: not self.game_bridge.is_authoritative(),
        )

    def _autodarts_settings(self) -> Dict[str, Any]:
        return self.store.get().get("autodarts", {})

    def start(self) -> None:
        self.activity.start()
        self.game_bridge.start()
        self.autodarts.start()

    def stop(self) -> None:
        self.autodarts.stop()
        self.game_bridge.stop()
        self.activity.stop()

    def _handle_board_event(self, event: Dict[str, Any]) -> None:
        if str(event.get("event") or "") == "Throw detected" and event.get("last_throw"):
            self.activity.note_dart()
        self.triggers.handle_autodarts_state(event)

    def _handle_game_event(self, event: Dict[str, Any]) -> None:
        if event.get("kind") == "game_event":
            self.activity.note_game_event(str(event.get("event") or ""))
        self.triggers.dispatch(event)

    def find_device(self, device_id: str) -> Dict[str, Any] | None:
        cfg = self.store.get()
        for device in cfg.get("devices", []):
            if device.get("id") == device_id:
                return device
        return None

    def find_hue_bridge(self, bridge_id: str) -> Dict[str, Any] | None:
        for bridge in self.store.get().get("hue_bridges", []):
            if str(bridge.get("id")) == str(bridge_id):
                return bridge
        return None

    def find_hue_light(self, light_id: str) -> Dict[str, Any] | None:
        for light in self.store.get().get("hue_lights", []):
            if str(light.get("id")) == str(light_id):
                return light
        return None

    def sync_hue_lights(self, bridge_id: str) -> Dict[str, Any]:
        bridge = self.find_hue_bridge(bridge_id)
        if not bridge:
            raise HueError("Hue Bridge not found")
        remote = list_lights(str(bridge.get("host") or ""), str(bridge.get("application_key") or ""))
        cfg = self.store.get()
        existing = {
            str(x.get("resource_id")): x
            for x in cfg.get("hue_lights", [])
            if isinstance(x, dict) and str(x.get("bridge_id")) == str(bridge_id)
        }
        keep_other = [
            x for x in cfg.get("hue_lights", [])
            if not (isinstance(x, dict) and str(x.get("bridge_id")) == str(bridge_id))
        ]
        synced = []
        for row in remote:
            rid = str(row.get("resource_id") or "")
            old = existing.get(rid, {})
            synced.append({
                "id": str(old.get("id") or uuid.uuid4().hex[:12]),
                "bridge_id": str(bridge_id),
                "resource_id": rid,
                "owner_id": str(row.get("owner_id") or ""),
                "name": str(row.get("name") or old.get("name") or "Hue Light")[:80],
                "archetype": str(row.get("archetype") or "light")[:80],
                "enabled": bool(old.get("enabled", True)),
                "supports_color": bool(row.get("supports_color", False)),
                "supports_color_temperature": bool(row.get("supports_color_temperature", False)),
                "supports_dimming": bool(row.get("supports_dimming", True)),
                "modes": old.get("modes") if isinstance(old.get("modes"), dict) else default_device_modes(),
                "last_probe": {
                    "ok": True, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "on": row.get("on"), "brightness": row.get("brightness"),
                },
            })
        valid_ids = {str(x.get("id")) for x in keep_other + synced}
        def mutate(current):
            current["hue_lights"] = keep_other + synced
            # Remove references only to Hue endpoints from this bridge that no
            # longer exist. WLED and other Hue bridge IDs remain untouched.
            for group in current.get("device_groups", []):
                ids = group.get("device_ids") if isinstance(group.get("device_ids"), list) else []
                group["device_ids"] = [str(x) for x in ids if str(x) in valid_ids or any(str(d.get("id")) == str(x) for d in current.get("devices", []))]
            for trigger in current.get("triggers", []):
                ids = trigger.get("device_ids") if isinstance(trigger.get("device_ids"), list) else []
                trigger["device_ids"] = [str(x) for x in ids if str(x) in valid_ids or any(str(d.get("id")) == str(x) for d in current.get("devices", []))]
        self.store.update(mutate)
        self.profiles.sync_active()
        return {
            "bridge_id": str(bridge.get("id") or ""),
            "bridge_name": str(bridge.get("name") or "Philips Hue Bridge"),
            "lights": synced,
            "light_count": len(synced),
        }

    def set_probe_cache(self, device_id: str, metadata: Dict[str, Any]) -> None:
        with self._cache_lock:
            self._probe_cache[device_id] = metadata

    def get_probe_cache(self, device_id: str) -> Dict[str, Any] | None:
        with self._cache_lock:
            return self._probe_cache.get(device_id)


class HimotheeLightHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, RequestHandlerClass, context: HimotheeLightContext):
        super().__init__(server_address, RequestHandlerClass)
        self.context = context


class Handler(BaseHTTPRequestHandler):
    server_version = f"{APP_NAME}/{__version__}"

    @property
    def ctx(self) -> HimotheeLightContext:
        return self.server.context  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:
        logger.debug("HTTP %s - %s", self.address_string(), fmt % args)

    def _json(self, status: int, payload: Any) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _error(self, status: int, message: str, **extra: Any) -> None:
        payload = {"ok": False, "error": message}
        payload.update(extra)
        self._json(status, payload)

    def _read_json(self) -> Dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        if length > 2_000_000:
            raise ValueError("Request too large")
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise ValueError("Invalid JSON request") from exc
        if not isinstance(body, dict):
            raise ValueError("JSON body must be an object")
        return body

    def _public_config(self) -> Dict[str, Any]:
        cfg = self.ctx.store.get()
        for bridge in cfg.get("hue_bridges", []):
            if isinstance(bridge, dict):
                bridge["paired"] = bool(bridge.get("application_key"))
                bridge.pop("application_key", None)
                bridge.pop("client_key", None)
        return cfg

    def _device_or_404(self, device_id: str) -> Dict[str, Any] | None:
        device = self.ctx.find_device(device_id)
        if device is None:
            self._error(404, "WLED device not found")
            return None
        return device

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            try:
                return self._api_get(path)
            except (WLEDError, HueError, AutodartsError) as exc:
                logger.warning("Device/API error: %s", exc)
                return self._error(502, str(exc))
            except Exception as exc:
                logger.exception("Unhandled GET error")
                return self._error(500, f"Internal error: {exc}")
        return self._serve_static(path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if not path.startswith("/api/"):
            return self._error(404, "Not found")
        try:
            body = self._read_json()
            return self._api_post(path, body)
        except ValueError as exc:
            return self._error(400, str(exc))
        except (WLEDError, HueError, AutodartsError) as exc:
            logger.warning("Device/API error: %s", exc)
            return self._error(502, str(exc))
        except Exception as exc:
            logger.exception("Unhandled POST error")
            return self._error(500, f"Internal error: {exc}")

    def do_PATCH(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if not path.startswith("/api/"):
            return self._error(404, "Not found")
        try:
            body = self._read_json()
            return self._api_patch(path, body)
        except ValueError as exc:
            return self._error(400, str(exc))
        except (WLEDError, HueError, AutodartsError) as exc:
            return self._error(502, str(exc))
        except Exception as exc:
            logger.exception("Unhandled PATCH error")
            return self._error(500, f"Internal error: {exc}")

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if not path.startswith("/api/"):
            return self._error(404, "Not found")
        try:
            return self._api_delete(path)
        except Exception as exc:
            logger.exception("Unhandled DELETE error")
            return self._error(500, f"Internal error: {exc}")

    def _api_get(self, path: str) -> None:
        if path == "/api/status":
            cfg = self.ctx.store.get()
            return self._json(200, {
                "ok": True,
                "name": APP_NAME,
                "version": __version__,
                "data_dir": str(self.ctx.store.data_dir),
                "device_count": len(cfg.get("devices", [])),
                "hue_bridge_count": len(cfg.get("hue_bridges", [])),
                "hue_light_count": len(cfg.get("hue_lights", [])),
                "lighting_endpoint_count": len(cfg.get("devices", [])) + len(cfg.get("hue_lights", [])),
                "last_manual_mode": cfg.get("app", {}).get("last_manual_mode", "idle"),
                "lighting": self.ctx.lighting.snapshot(),
                "autodarts": self.ctx.autodarts.snapshot(),
                "game_bridge": self.ctx.game_bridge.snapshot(),
                "activity": self.ctx.activity.snapshot(),
                "triggers": self.ctx.triggers.snapshot(),
                "profiles": self.ctx.profiles.snapshot(),
                "scene_count": len(cfg.get("scenes", [])),
                "group_count": len(cfg.get("device_groups", [])),
            })
        if path == "/api/config":
            return self._json(200, {"ok": True, "config": self._public_config()})
        if path == "/api/logs":
            return self._json(200, {"ok": True, "logs": self.ctx.memory_logs.items(250)})
        if path == "/api/triggers/status":
            return self._json(200, {"ok": True, **self.ctx.triggers.snapshot()})
        if path == "/api/profiles":
            return self._json(200, {"ok": True, **self.ctx.profiles.snapshot()})
        profile_export = re.fullmatch(r"/api/profiles/([^/]+)/export", path)
        if profile_export:
            # Ensure the active profile snapshot reflects the latest modes/triggers.
            snap = self.ctx.profiles.snapshot()
            if snap.get("active_profile_id") == profile_export.group(1):
                self.ctx.profiles.sync_active()
            exported = self.ctx.profiles.export_bundle(profile_export.group(1))
            return self._json(200, {"ok": True, **exported})
        if path == "/api/autodarts/status":
            return self._json(200, {
                "ok": True,
                "settings": self.ctx.store.get().get("autodarts", {}),
                "status": self.ctx.autodarts.snapshot(),
                "game_bridge": self.ctx.game_bridge.snapshot(),
                "activity": self.ctx.activity.snapshot(),
                "lighting": self.ctx.lighting.snapshot(),
                "triggers": self.ctx.triggers.snapshot(),
            })
        if path == "/api/autodarts/events":
            return self._json(200, {"ok": True, "events": self.ctx.autodarts.events(100)})
        if path == "/api/autodarts/bridge/status":
            return self._json(200, {"ok": True, "game_bridge": self.ctx.game_bridge.snapshot()})

        if path == "/api/hue/discover":
            return self._json(200, {"ok": True, "bridges": discover_bridges()})
        hue_bridge = re.fullmatch(r"/api/hue/bridges/([^/]+)/(metadata|lights)", path)
        if hue_bridge:
            bridge_id, action = hue_bridge.groups()
            bridge = self.ctx.find_hue_bridge(bridge_id)
            if not bridge:
                return self._error(404, "Hue Bridge not found")
            if action == "metadata":
                metadata = probe_bridge(str(bridge.get("host") or ""), str(bridge.get("application_key") or ""))
                return self._json(200, {"ok": True, "metadata": metadata})
            lights = [x for x in self.ctx.store.get().get("hue_lights", []) if str(x.get("bridge_id")) == bridge_id]
            return self._json(200, {"ok": True, "lights": lights})

        match = re.fullmatch(r"/api/devices/([^/]+)/(metadata|live)", path)
        if match:
            device_id, action = match.groups()
            device = self._device_or_404(device_id)
            if device is None:
                return
            if action == "metadata":
                metadata = probe(device["host"])
                self.ctx.set_probe_cache(device_id, metadata)
                stamp = time.strftime("%Y-%m-%d %H:%M:%S")

                def mutate(cfg):
                    for item in cfg["devices"]:
                        if item.get("id") == device_id:
                            item["last_probe"] = {
                                "ok": True,
                                "time": stamp,
                                "version": metadata.get("version"),
                                "wled_name": metadata.get("name"),
                                "led_count": metadata.get("led_count"),
                            }
                self.ctx.store.update(mutate)
                logger.info("Connected to %s at %s (WLED %s)", device.get("name"), device.get("host"), metadata.get("version"))
                return self._json(200, {"ok": True, "metadata": metadata})
            state = get_state(device["host"])
            return self._json(200, {"ok": True, "state": state})

        self._error(404, "API route not found")

    def _api_post(self, path: str, body: Dict[str, Any]) -> None:
        if path == "/api/hue/bridges/pair":
            host = normalize_bridge_host(str(body.get("host") or ""))
            name = str(body.get("name") or "Philips Hue Bridge").strip()[:80]
            paired = pair_bridge(host, str(body.get("device_name") or "HimotheeLight"))
            metadata = probe_bridge(host, paired["application_key"])
            cfg = self.ctx.store.get()
            existing = next((b for b in cfg.get("hue_bridges", []) if str(b.get("host")) == host), None)
            bridge_id = str(existing.get("id")) if existing else uuid.uuid4().hex[:12]
            bridge = {
                "id": bridge_id, "name": name or str(metadata.get("name") or "Philips Hue Bridge"),
                "host": host, "application_key": paired["application_key"],
                "client_key": paired.get("client_key", ""), "enabled": True,
                "last_probe": {"ok": True, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "model": metadata.get("model"), "light_count": metadata.get("light_count")},
            }
            def mutate(current):
                rows = current.setdefault("hue_bridges", [])
                for i, row in enumerate(rows):
                    if str(row.get("id")) == bridge_id:
                        rows[i] = bridge; break
                else:
                    rows.append(bridge)
            self.ctx.store.update(mutate)
            synced = self.ctx.sync_hue_lights(bridge_id)
            public = dict(bridge); public["paired"] = True; public.pop("application_key", None); public.pop("client_key", None)
            logger.info("Paired Philips Hue Bridge '%s' at %s with %d light(s)", name, host, synced["light_count"])
            return self._json(201, {"ok": True, "bridge": public, "lights": synced["lights"], "metadata": metadata})

        hue_sync = re.fullmatch(r"/api/hue/bridges/([^/]+)/(sync|test)", path)
        if hue_sync:
            bridge_id, action = hue_sync.groups()
            bridge = self.ctx.find_hue_bridge(bridge_id)
            if not bridge:
                return self._error(404, "Hue Bridge not found")
            if action == "test":
                metadata = probe_bridge(str(bridge.get("host") or ""), str(bridge.get("application_key") or ""))
                return self._json(200, {"ok": True, "metadata": metadata})
            synced = self.ctx.sync_hue_lights(bridge_id)
            return self._json(200, {"ok": True, **synced})

        hue_light_action = re.fullmatch(r"/api/hue/lights/([^/]+)/(test|off)", path)
        if hue_light_action:
            light_id, action = hue_light_action.groups()
            light = self.ctx.find_hue_light(light_id)
            if not light:
                return self._error(404, "Hue light not found")
            bridge = self.ctx.find_hue_bridge(str(light.get("bridge_id") or ""))
            if not bridge:
                return self._error(404, "Hue Bridge not found")
            if action == "off":
                mode = {"on": False, "transition_ms": 0}
            else:
                mode = light.get("modes", {}).get("active", default_device_modes()["active"])
            result = apply_light_mode(bridge, light, mode)
            return self._json(200, {"ok": True, "result": result})

        hue_mode = re.fullmatch(r"/api/hue/lights/([^/]+)/modes/(idle|active)(?:/(apply))?", path)
        if hue_mode:
            light_id, mode_name, action = hue_mode.groups()
            light = self.ctx.find_hue_light(light_id)
            if not light:
                return self._error(404, "Hue light not found")
            mode_config = body.get("mode") if "mode" in body else body
            if not isinstance(mode_config, dict):
                raise ValueError("Mode configuration is required")
            # Reuse WLED mode validation: Hue ignores WLED-only fx/palette fields
            # but the common brightness/colour/transition structure stays safe.
            build_mode_payload(mode_config)
            def mutate(current):
                for item in current.get("hue_lights", []):
                    if str(item.get("id")) == light_id:
                        item.setdefault("modes", default_device_modes())
                        item["modes"][mode_name] = mode_config
            self.ctx.store.update(mutate)
            self.ctx.profiles.sync_active()
            result = None
            if action == "apply":
                bridge = self.ctx.find_hue_bridge(str(light.get("bridge_id") or ""))
                if not bridge:
                    return self._error(404, "Hue Bridge not found")
                result = apply_light_mode(bridge, light, mode_config)
                self.ctx.store.update(lambda c: c["app"].__setitem__("last_manual_mode", mode_name))
            return self._json(200, {"ok": True, "mode": mode_config, "result": result})

        if path == "/api/devices":
            name = str(body.get("name") or "WLED Device").strip()[:80]
            host = normalize_host(str(body.get("host") or ""))
            metadata = probe(host)
            device_id = uuid.uuid4().hex[:12]
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            device = {
                "id": device_id,
                "name": name,
                "host": host,
                "enabled": True,
                "modes": default_device_modes(),
                "last_probe": {
                    "ok": True,
                    "time": now,
                    "version": metadata.get("version"),
                    "wled_name": metadata.get("name"),
                    "led_count": metadata.get("led_count"),
                },
            }

            def mutate(cfg):
                cfg["devices"].append(device)
            self.ctx.store.update(mutate)
            self.ctx.profiles.sync_active()
            self.ctx.set_probe_cache(device_id, metadata)
            logger.info("Added WLED device '%s' at %s", name, host)
            return self._json(201, {"ok": True, "device": device, "metadata": metadata})

        if path == "/api/modes/idle/apply-all" or path == "/api/modes/active/apply-all":
            mode_name = "idle" if "/idle/" in path else "active"
            result = self.ctx.lighting.manual_apply(mode_name, "Manual base-state control")
            failures = [item for item in result.get("results", []) if not item.get("ok")]
            return self._json(207 if failures else 200, result)

        if path == "/api/autodarts/settings":
            current = self.ctx.store.get().get("autodarts", {})
            updated = dict(current)
            allowed = {
                "enabled", "host", "port", "auto_connect", "auto_lighting", "idle_on_disconnect",
                "reconnect_seconds", "bridge_timeout_seconds", "takeout_flow_enabled",
                "takeout_after_darts", "takeout_finish_flash_ms", "inactivity_idle_enabled",
                "inactivity_idle_seconds",
            }
            for key in allowed:
                if key in body:
                    updated[key] = body[key]
            updated["host"] = normalize_board_host(str(updated.get("host") or "127.0.0.1"))
            try:
                updated["port"] = int(updated.get("port", 3180))
            except (TypeError, ValueError) as exc:
                raise ValueError("Autodarts port must be a number") from exc
            if not 1 <= updated["port"] <= 65535:
                raise ValueError("Autodarts port must be between 1 and 65535")
            try:
                updated["reconnect_seconds"] = max(0.5, min(30.0, float(updated.get("reconnect_seconds", 2.0))))
                updated["bridge_timeout_seconds"] = max(5.0, min(60.0, float(updated.get("bridge_timeout_seconds", 10.0))))
                updated["takeout_after_darts"] = 3
                updated["takeout_finish_flash_ms"] = max(100, min(5000, int(updated.get("takeout_finish_flash_ms", 500))))
                updated["inactivity_idle_seconds"] = max(10.0, min(3600.0, float(updated.get("inactivity_idle_seconds", 60.0))))
            except (TypeError, ValueError) as exc:
                raise ValueError("Autodarts timing values must be numbers") from exc
            for key in ("enabled", "auto_connect", "auto_lighting", "idle_on_disconnect", "takeout_flow_enabled", "inactivity_idle_enabled"):
                updated[key] = bool(updated.get(key, True))
            self.ctx.store.update(lambda cfg: cfg.__setitem__("autodarts", updated))
            self.ctx.activity.settings_changed()
            self.ctx.autodarts.disconnect(manual=False)
            if updated["enabled"] and updated["auto_connect"]:
                self.ctx.autodarts.connect()
            logger.info("Saved Autodarts settings for %s:%s", updated["host"], updated["port"] )
            return self._json(200, {"ok": True, "settings": updated})

        if path == "/api/autodarts/connect":
            self.ctx.autodarts.connect()
            return self._json(200, {"ok": True, "status": self.ctx.autodarts.snapshot()})
        if path == "/api/autodarts/disconnect":
            self.ctx.autodarts.disconnect()
            return self._json(200, {"ok": True, "status": self.ctx.autodarts.snapshot()})
        if path == "/api/autodarts/test":
            test_host = str(body.get("host")) if body.get("host") is not None else None
            test_port = body.get("port") if body.get("port") is not None else None
            result = self.ctx.autodarts.test_connection(test_host, test_port)
            logger.info("Autodarts HTTP test succeeded at %s:%s", result.get("host"), result.get("port"))
            return self._json(200, result)
        if path == "/api/autodarts/simulate":
            status = self.ctx.autodarts.simulate(str(body.get("event") or ""))
            logger.info("Simulated Autodarts event: %s", body.get("event"))
            return self._json(200, {"ok": True, "status": status, "lighting": self.ctx.lighting.snapshot(), "triggers": self.ctx.triggers.snapshot()})

        if path == "/api/autodarts/bridge":
            status = self.ctx.game_bridge.ingest(body)
            return self._json(200, {"ok": True, "game_bridge": status, "lighting": self.ctx.lighting.snapshot(), "triggers": self.ctx.triggers.snapshot()})
        if path == "/api/autodarts/bridge/simulate":
            status = self.ctx.game_bridge.simulate(str(body.get("event") or ""))
            logger.info("Simulated Autodarts game event: %s", body.get("event"))
            return self._json(200, {"ok": True, "game_bridge": status, "lighting": self.ctx.lighting.snapshot(), "triggers": self.ctx.triggers.snapshot()})

        if path == "/api/profiles/import/preview":
            bundle = body.get("bundle")
            if not isinstance(bundle, dict):
                raise ValueError("A HimotheeLight profile bundle is required")
            preview = self.ctx.profiles.preview_import(bundle)
            return self._json(200, preview)

        if path == "/api/profiles/import":
            bundle = body.get("bundle")
            mapping = body.get("mapping", {})
            if not isinstance(bundle, dict):
                raise ValueError("A HimotheeLight profile bundle is required")
            if not isinstance(mapping, dict):
                raise ValueError("Device mapping must be an object")
            result = self.ctx.profiles.import_bundle(bundle, mapping, str(body.get("name") or "").strip() or None)
            return self._json(201, {"ok": True, "result": result, **self.ctx.profiles.snapshot()})

        if path == "/api/profiles":
            profile = self.ctx.profiles.create(str(body.get("name") or "New Profile"))
            return self._json(201, {"ok": True, "profile": profile, **self.ctx.profiles.snapshot()})
        profile_apply = re.fullmatch(r"/api/profiles/([^/]+)/apply", path)
        if profile_apply:
            result = self.ctx.profiles.apply(profile_apply.group(1))
            return self._json(200, {"ok": True, "result": result, "config": self._public_config(), **self.ctx.profiles.snapshot()})

        if path == "/api/device-groups":
            group = self._validated_group(body, existing=None)
            self.ctx.store.update(lambda cfg: cfg.setdefault("device_groups", []).append(group))
            logger.info("Added device group '%s'", group.get("name"))
            return self._json(201, {"ok": True, "group": group})

        if path == "/api/scenes":
            scene = self._validated_scene(body, existing=None)
            self.ctx.store.update(lambda cfg: cfg.setdefault("scenes", []).append(scene))
            logger.info("Added lighting scene '%s'", scene.get("name"))
            return self._json(201, {"ok": True, "scene": scene})

        scene_test = re.fullmatch(r"/api/scenes/([^/]+)/test", path)
        if scene_test:
            cfg = self.ctx.store.get()
            scene = next((x for x in cfg.get("scenes", []) if str(x.get("id")) == scene_test.group(1)), None)
            if not scene:
                return self._error(404, "Scene not found")
            result = self.ctx.lighting.apply_trigger_effect(
                trigger_id=f"scene:{scene.get('id')}", trigger_name=f"Scene: {scene.get('name')}",
                effect={}, duration_ms=max(250, min(10000, int(body.get("duration_ms", 2000)))),
                priority=100, device_ids=[], reason="Manual scene test", force=True,
                scene_name=str(scene.get("name") or "Scene"), scene_actions=list(scene.get("actions") or []),
                scene_repeat_count=(1 if int(scene.get("repeat_count", 1)) == 0 else int(scene.get("repeat_count", 1))),
            )
            return self._json(200, {"ok": True, "result": result})

        if path == "/api/triggers":
            trigger = self._validated_trigger(body, existing=None)
            self.ctx.store.update(lambda cfg: cfg.setdefault("triggers", []).append(trigger))
            self.ctx.profiles.sync_active()
            logger.info("Added trigger '%s'", trigger.get("name"))
            return self._json(201, {"ok": True, "trigger": trigger})
        if path == "/api/triggers/cancel":
            result = self.ctx.lighting.cancel_trigger("Manual trigger stop")
            return self._json(200, result)
        match = re.fullmatch(r"/api/triggers/([^/]+)/(test)", path)
        if match:
            trigger_id, _action = match.groups()
            result = self.ctx.triggers.fire_by_id(trigger_id)
            return self._json(200, {"ok": True, "result": result, "runtime": self.ctx.lighting.trigger_snapshot()})

        match = re.fullmatch(r"/api/devices/([^/]+)/(test|off|raw)", path)
        if match:
            device_id, action = match.groups()
            device = self._device_or_404(device_id)
            if device is None:
                return
            if action == "test":
                metadata = probe(device["host"])
                self.ctx.set_probe_cache(device_id, metadata)
                logger.info("Test connection succeeded for '%s'", device.get("name"))
                return self._json(200, {"ok": True, "metadata": metadata})
            if action == "off":
                result = send_state(device["host"], {"on": False, "tt": 0})
                logger.info("Turned off '%s'", device.get("name"))
                return self._json(200, {"ok": True, "result": result})
            payload = body.get("payload")
            if not isinstance(payload, dict):
                raise ValueError("raw payload must be a JSON object")
            result = send_state(device["host"], payload)
            logger.info("Sent raw WLED state to '%s'", device.get("name"))
            return self._json(200, {"ok": True, "result": result})

        match = re.fullmatch(r"/api/devices/([^/]+)/modes/(idle|active)(?:/(apply))?", path)
        if match:
            device_id, mode_name, action = match.groups()
            device = self._device_or_404(device_id)
            if device is None:
                return
            mode_config = body.get("mode") if "mode" in body else body
            if not isinstance(mode_config, dict):
                raise ValueError("Mode configuration is required")

            def mutate(cfg):
                for item in cfg["devices"]:
                    if item.get("id") == device_id:
                        item.setdefault("modes", default_device_modes())
                        item["modes"][mode_name] = mode_config
            self.ctx.store.update(mutate)
            self.ctx.profiles.sync_active()
            result = None
            if action == "apply":
                result = apply_mode(device["host"], mode_config)
                self.ctx.store.update(lambda c: c["app"].__setitem__("last_manual_mode", mode_name))
                logger.info("Saved and applied %s mode to '%s'", mode_name, device.get("name"))
            else:
                logger.info("Saved %s mode for '%s'", mode_name, device.get("name"))
            return self._json(200, {"ok": True, "mode": mode_config, "result": result})

        self._error(404, "API route not found")

    def _api_patch(self, path: str, body: Dict[str, Any]) -> None:
        hue_bridge_match = re.fullmatch(r"/api/hue/bridges/([^/]+)", path)
        if hue_bridge_match:
            bridge_id = hue_bridge_match.group(1)
            bridge = self.ctx.find_hue_bridge(bridge_id)
            if not bridge:
                return self._error(404, "Hue Bridge not found")
            updates = {}
            if "name" in body: updates["name"] = str(body.get("name") or "Philips Hue Bridge").strip()[:80]
            if "enabled" in body: updates["enabled"] = bool(body.get("enabled"))
            if "host" in body:
                host = normalize_bridge_host(str(body.get("host") or ""))
                probe_bridge(host, str(bridge.get("application_key") or ""))
                updates["host"] = host
            def mutate(current):
                for row in current.get("hue_bridges", []):
                    if str(row.get("id")) == bridge_id: row.update(updates)
            self.ctx.store.update(mutate)
            return self._json(200, {"ok": True, "config": self._public_config()})

        hue_light_match = re.fullmatch(r"/api/hue/lights/([^/]+)", path)
        if hue_light_match:
            light_id = hue_light_match.group(1)
            light = self.ctx.find_hue_light(light_id)
            if not light:
                return self._error(404, "Hue light not found")
            updates = {}
            if "name" in body: updates["name"] = str(body.get("name") or "Hue Light").strip()[:80]
            if "enabled" in body: updates["enabled"] = bool(body.get("enabled"))
            def mutate(current):
                for row in current.get("hue_lights", []):
                    if str(row.get("id")) == light_id: row.update(updates)
            self.ctx.store.update(mutate); self.ctx.profiles.sync_active()
            return self._json(200, {"ok": True, "config": self._public_config()})

        group_match = re.fullmatch(r"/api/device-groups/([^/]+)", path)
        if group_match:
            group_id = group_match.group(1)
            cfg = self.ctx.store.get()
            existing = next((g for g in cfg.get("device_groups", []) if str(g.get("id")) == group_id), None)
            if not existing:
                return self._error(404, "Device group not found")
            updated = self._validated_group(body, existing)
            self.ctx.store.update(lambda current: current.__setitem__("device_groups", [updated if str(g.get("id")) == group_id else g for g in current.get("device_groups", [])]))
            return self._json(200, {"ok": True, "group": updated})

        scene_match = re.fullmatch(r"/api/scenes/([^/]+)", path)
        if scene_match:
            scene_id = scene_match.group(1)
            cfg = self.ctx.store.get()
            existing = next((x for x in cfg.get("scenes", []) if str(x.get("id")) == scene_id), None)
            if not existing:
                return self._error(404, "Scene not found")
            updated = self._validated_scene(body, existing)
            self.ctx.store.update(lambda current: current.__setitem__("scenes", [updated if str(x.get("id")) == scene_id else x for x in current.get("scenes", [])]))
            return self._json(200, {"ok": True, "scene": updated})

        profile_match = re.fullmatch(r"/api/profiles/([^/]+)", path)
        if profile_match:
            profile = self.ctx.profiles.rename(profile_match.group(1), str(body.get("name") or "Profile"))
            return self._json(200, {"ok": True, "profile": profile, **self.ctx.profiles.snapshot()})

        trigger_match = re.fullmatch(r"/api/triggers/([^/]+)", path)
        if trigger_match:
            trigger_id = trigger_match.group(1)
            cfg = self.ctx.store.get()
            existing = next((t for t in cfg.get("triggers", []) if t.get("id") == trigger_id), None)
            if existing is None:
                return self._error(404, "Trigger not found")
            updated = self._validated_trigger(body, existing=existing)
            def mutate_trigger(current):
                for index, item in enumerate(current.get("triggers", [])):
                    if item.get("id") == trigger_id:
                        current["triggers"][index] = updated
                        break
            self.ctx.store.update(mutate_trigger)
            self.ctx.profiles.sync_active()
            logger.info("Updated trigger '%s'", updated.get("name"))
            return self._json(200, {"ok": True, "trigger": updated})

        match = re.fullmatch(r"/api/devices/([^/]+)", path)
        if not match:
            return self._error(404, "API route not found")
        device_id = match.group(1)
        device = self._device_or_404(device_id)
        if device is None:
            return
        updates: Dict[str, Any] = {}
        if "name" in body:
            updates["name"] = str(body["name"] or "WLED Device").strip()[:80]
        if "host" in body:
            new_host = normalize_host(str(body["host"] or ""))
            probe(new_host)
            updates["host"] = new_host
        if "enabled" in body:
            updates["enabled"] = bool(body["enabled"])

        def mutate(cfg):
            for item in cfg["devices"]:
                if item.get("id") == device_id:
                    item.update(updates)
        cfg = self.ctx.store.update(mutate)
        self.ctx.profiles.sync_active()
        updated = next(item for item in cfg["devices"] if item.get("id") == device_id)
        logger.info("Updated WLED device '%s'", updated.get("name"))
        self._json(200, {"ok": True, "device": updated})

    def _api_delete(self, path: str) -> None:
        hue_bridge_match = re.fullmatch(r"/api/hue/bridges/([^/]+)", path)
        if hue_bridge_match:
            bridge_id = hue_bridge_match.group(1)
            cfg = self.ctx.store.get()
            bridge = next((b for b in cfg.get("hue_bridges", []) if str(b.get("id")) == bridge_id), None)
            if not bridge:
                return self._error(404, "Hue Bridge not found")
            removed_ids = {str(x.get("id")) for x in cfg.get("hue_lights", []) if str(x.get("bridge_id")) == bridge_id}
            def mutate(current):
                current["hue_bridges"] = [b for b in current.get("hue_bridges", []) if str(b.get("id")) != bridge_id]
                current["hue_lights"] = [x for x in current.get("hue_lights", []) if str(x.get("bridge_id")) != bridge_id]
                for group in current.get("device_groups", []):
                    group["device_ids"] = [str(x) for x in group.get("device_ids", []) if str(x) not in removed_ids]
                for trigger in current.get("triggers", []):
                    trigger["device_ids"] = [str(x) for x in trigger.get("device_ids", []) if str(x) not in removed_ids]
                for profile in current.get("profiles", []):
                    modes = profile.get("device_modes") if isinstance(profile.get("device_modes"), dict) else {}
                    for rid in removed_ids: modes.pop(rid, None)
                    for trigger in profile.get("triggers", []) if isinstance(profile, dict) else []:
                        if isinstance(trigger, dict): trigger["device_ids"] = [str(x) for x in trigger.get("device_ids", []) if str(x) not in removed_ids]
            self.ctx.store.update(mutate)
            return self._json(200, {"ok": True})

        group_match = re.fullmatch(r"/api/device-groups/([^/]+)", path)
        if group_match:
            group_id = group_match.group(1)
            cfg = self.ctx.store.get()
            if not any(str(g.get("id")) == group_id for g in cfg.get("device_groups", [])):
                return self._error(404, "Device group not found")
            self.ctx.store.update(lambda current: current.__setitem__("device_groups", [g for g in current.get("device_groups", []) if str(g.get("id")) != group_id]))
            return self._json(200, {"ok": True})

        scene_match = re.fullmatch(r"/api/scenes/([^/]+)", path)
        if scene_match:
            scene_id = scene_match.group(1)
            cfg = self.ctx.store.get()
            if not any(str(x.get("id")) == scene_id for x in cfg.get("scenes", [])):
                return self._error(404, "Scene not found")
            def remove_scene(current):
                current["scenes"] = [x for x in current.get("scenes", []) if str(x.get("id")) != scene_id]
                for trigger in current.get("triggers", []):
                    if isinstance(trigger, dict) and str(trigger.get("scene_id") or "") == scene_id:
                        trigger["scene_id"] = ""
                for profile in current.get("profiles", []):
                    for trigger in profile.get("triggers", []) if isinstance(profile, dict) else []:
                        if isinstance(trigger, dict) and str(trigger.get("scene_id") or "") == scene_id:
                            trigger["scene_id"] = ""
            self.ctx.store.update(remove_scene)
            return self._json(200, {"ok": True})

        profile_match = re.fullmatch(r"/api/profiles/([^/]+)", path)
        if profile_match:
            result = self.ctx.profiles.delete(profile_match.group(1))
            return self._json(200, {**result, **self.ctx.profiles.snapshot()})

        trigger_match = re.fullmatch(r"/api/triggers/([^/]+)", path)
        if trigger_match:
            trigger_id = trigger_match.group(1)
            cfg = self.ctx.store.get()
            trigger = next((t for t in cfg.get("triggers", []) if t.get("id") == trigger_id), None)
            if trigger is None:
                return self._error(404, "Trigger not found")
            self.ctx.store.update(lambda current: current.__setitem__("triggers", [t for t in current.get("triggers", []) if t.get("id") != trigger_id]))
            self.ctx.profiles.sync_active()
            active = self.ctx.lighting.trigger_snapshot().get("active")
            if active and active.get("id") == trigger_id:
                self.ctx.lighting.cancel_trigger("Active trigger was deleted")
            logger.info("Removed trigger '%s'", trigger.get("name"))
            return self._json(200, {"ok": True})

        match = re.fullmatch(r"/api/devices/([^/]+)", path)
        if not match:
            return self._error(404, "API route not found")
        device_id = match.group(1)
        device = self._device_or_404(device_id)
        if device is None:
            return

        def mutate(cfg):
            cfg["devices"] = [item for item in cfg["devices"] if item.get("id") != device_id]
        self.ctx.store.update(mutate)
        self.ctx.profiles.sync_active()
        logger.info("Removed WLED device '%s'", device.get("name"))
        self._json(200, {"ok": True})

    def _validated_trigger(self, body: Dict[str, Any], existing: Dict[str, Any] | None) -> Dict[str, Any]:
        base = dict(existing) if existing else trigger_template()
        for key in ("name", "enabled", "trigger_type", "value", "minimum", "maximum", "duration_ms", "priority", "device_ids", "effect", "scene_id"):
            if key in body:
                base[key] = body[key]
        base["id"] = str((existing or base).get("id") or uuid.uuid4().hex[:12])
        base["name"] = str(base.get("name") or "Trigger").strip()[:80]
        base["enabled"] = bool(base.get("enabled", False))
        trigger_type = str(base.get("trigger_type") or "dart").lower().strip()
        if trigger_type not in {"dart", "visit_exact", "visit_range", "combination", "target", "board_event", "game_event", "player_turn"}:
            raise ValueError("Unsupported trigger type")
        base["trigger_type"] = trigger_type
        base["value"] = str(base.get("value") or "").strip()[:80]
        try:
            base["minimum"] = max(0, min(180, int(base.get("minimum", 0))))
            base["maximum"] = max(base["minimum"], min(180, int(base.get("maximum", 180))))
            base["duration_ms"] = max(100, min(60000, int(base.get("duration_ms", 1500))))
            base["priority"] = max(0, min(100, int(base.get("priority", 20))))
        except (TypeError, ValueError) as exc:
            raise ValueError("Trigger score, duration and priority values must be numbers") from exc
        device_ids = base.get("device_ids")
        if not isinstance(device_ids, list):
            raise ValueError("Trigger device list is invalid")
        cfg_now = self.ctx.store.get()
        valid_ids = {str(d.get("id")) for d in cfg_now.get("devices", [])}
        valid_ids.update(str(d.get("id")) for d in cfg_now.get("hue_lights", []))
        base["device_ids"] = [str(x) for x in device_ids if str(x) in valid_ids]
        scene_id = str(base.get("scene_id") or "")
        valid_scenes = {str(x.get("id")) for x in self.ctx.store.get().get("scenes", []) if isinstance(x, dict)}
        if scene_id and scene_id not in valid_scenes:
            raise ValueError("Selected lighting scene does not exist")
        base["scene_id"] = scene_id
        effect = base.get("effect")
        if not isinstance(effect, dict):
            raise ValueError("Trigger lighting effect configuration is required")
        # build_mode_payload validates the shared mode structure without touching
        # an actual endpoint; Hue ignores WLED-only fields at dispatch time.
        build_mode_payload(effect)
        base["effect"] = effect
        if trigger_type == "visit_exact":
            try:
                exact = max(0, min(180, int(base.get("value") or base.get("minimum", 0))))
            except (TypeError, ValueError) as exc:
                raise ValueError("Exact visit trigger must contain a score from 0 to 180") from exc
            base["value"] = str(exact)
            base["minimum"] = exact
            base["maximum"] = exact
        if trigger_type == "dart" and not base["value"]:
            raise ValueError("Dart trigger must contain a dart such as T20 or BULL")
        if trigger_type == "combination":
            combo = parse_combination(base["value"])
            if not 1 <= len(combo) <= 3:
                raise ValueError("Combination must contain 1 to 3 darts, for example T20_T20_T20 or S10_D16")
            valid_dart = re.compile(r"^(?:[SDT](?:[1-9]|1[0-9]|20)|BULL|25|MISS)$")
            if any(not valid_dart.fullmatch(dart) for dart in combo):
                raise ValueError("Combination contains an invalid dart")
            base["value"] = "_".join(combo)
        if trigger_type == "target":
            target = normalize_target(base["value"])
            if target == "BULL":
                base["value"] = target
            else:
                try:
                    number = int(target)
                except (TypeError, ValueError) as exc:
                    raise ValueError("Target must be 1-20, 25 or BULL") from exc
                if number not in set(range(1, 21)) | {25}:
                    raise ValueError("Target must be 1-20, 25 or BULL")
                base["value"] = str(number)
        if trigger_type == "game_event":
            allowed_game_events = {"bust", "gameshot", "matchshot", "turn_start", "game_start", "match_start", "bot_turn"}
            base["value"] = base["value"].lower().replace(" ", "_")
            if base["value"] not in allowed_game_events:
                raise ValueError("Unsupported Autodarts game event")
        if trigger_type == "player_turn" and not base["value"]:
            raise ValueError("Player-turn trigger must contain an Autodarts player name")
        return base

    def _validated_group(self, body: Dict[str, Any], existing: Dict[str, Any] | None) -> Dict[str, Any]:
        base = dict(existing) if existing else {"id": uuid.uuid4().hex[:12], "name": "Device Group", "device_ids": []}
        if "name" in body:
            base["name"] = str(body.get("name") or "Device Group").strip()[:60]
        if "device_ids" in body:
            if not isinstance(body.get("device_ids"), list):
                raise ValueError("Device group device list is invalid")
            cfg_now = self.ctx.store.get()
            valid = {str(d.get("id")) for d in cfg_now.get("devices", []) if isinstance(d, dict)}
            valid.update(str(d.get("id")) for d in cfg_now.get("hue_lights", []) if isinstance(d, dict))
            base["device_ids"] = [str(x) for x in body.get("device_ids", []) if str(x) in valid]
        return base

    def _validated_scene(self, body: Dict[str, Any], existing: Dict[str, Any] | None) -> Dict[str, Any]:
        base = dict(existing) if existing else {"id": uuid.uuid4().hex[:12], "name": "Lighting Scene", "repeat_count": 1, "actions": []}
        if "name" in body:
            base["name"] = str(body.get("name") or "Lighting Scene").strip()[:60]
        if "repeat_count" in body:
            try:
                base["repeat_count"] = max(0, min(20, int(body.get("repeat_count", 1))))
            except (TypeError, ValueError) as exc:
                raise ValueError("Scene repeat count must be 0 to 20; 0 loops until interrupted") from exc
        else:
            try:
                base["repeat_count"] = max(0, min(20, int(base.get("repeat_count", 1))))
            except (TypeError, ValueError):
                base["repeat_count"] = 1
        if "actions" in body:
            if not isinstance(body.get("actions"), list):
                raise ValueError("Scene actions must be a list")
            cfg = self.ctx.store.get()
            valid_devices = {str(d.get("id")) for d in cfg.get("devices", []) if isinstance(d, dict)}
            valid_devices.update(str(d.get("id")) for d in cfg.get("hue_lights", []) if isinstance(d, dict))
            valid_groups = {str(g.get("id")) for g in cfg.get("device_groups", []) if isinstance(g, dict)}
            actions = []
            for raw in body.get("actions", []):
                if not isinstance(raw, dict):
                    continue
                target_type = str(raw.get("target_type") or "all").lower()
                if target_type not in {"all", "device", "group"}:
                    raise ValueError("Scene target must be all, device or group")
                target_id = str(raw.get("target_id") or "")
                if target_type == "device" and target_id not in valid_devices:
                    raise ValueError("Scene action references an unknown lighting endpoint")
                if target_type == "group" and target_id not in valid_groups:
                    raise ValueError("Scene action references an unknown device group")
                effect = raw.get("effect")
                if not isinstance(effect, dict):
                    raise ValueError("Scene action requires a lighting effect")
                build_mode_payload(effect)
                try:
                    step = max(1, min(50, int(raw.get("step", 1))))
                    delay_ms = max(0, min(60000, int(raw.get("delay_ms", 0))))
                    hold_ms = max(0, min(60000, int(raw.get("hold_ms", 0))))
                except (TypeError, ValueError) as exc:
                    raise ValueError("Scene step, delay and hold values must be numbers") from exc
                actions.append({
                    "id": str(raw.get("id") or uuid.uuid4().hex[:12])[:40],
                    "name": str(raw.get("name") or "Scene Action").strip()[:60],
                    "target_type": target_type,
                    "target_id": target_id,
                    "step": step,
                    "delay_ms": delay_ms,
                    "hold_ms": hold_ms,
                    "effect": effect,
                })
            base["actions"] = actions
        return base

    def _serve_static(self, path: str) -> None:
        if path == "/":
            path = "/index.html"
        safe = posixpath.normpath(unquote(path)).lstrip("/")
        if safe.startswith(".."):
            return self._error(403, "Forbidden")
        target = (self.ctx.web_dir / safe).resolve()
        try:
            target.relative_to(self.ctx.web_dir)
        except ValueError:
            return self._error(403, "Forbidden")
        if not target.exists() or not target.is_file():
            return self._error(404, "Not found")
        data = target.read_bytes()
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)


def build_server(host: str, port: int, store: ConfigStore, web_dir: Path, memory_logs: MemoryLogHandler) -> HimotheeLightHTTPServer:
    context = HimotheeLightContext(store, web_dir, memory_logs)
    return HimotheeLightHTTPServer((host, port), Handler, context)
