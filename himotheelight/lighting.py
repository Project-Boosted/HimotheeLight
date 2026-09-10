from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, Iterable, List

from .config_store import ConfigStore
from .hue import HueError, apply_light_mode
from .wled import WLEDError, apply_mode

logger = logging.getLogger("himotheelight")


class LightingStateManager:
    """Owns base lighting plus timed/choreographed Autodarts trigger overrides."""

    def __init__(self, store: ConfigStore):
        self.store = store
        self._lock = threading.RLock()
        cfg = store.get()
        app = cfg.get("app", {})
        self._current_mode = str(app.get("current_mode") or app.get("last_manual_mode") or "idle")
        if self._current_mode not in {"idle", "active"}:
            self._current_mode = "idle"
        self._source = str(app.get("mode_source") or "startup")
        self._last_reason = str(app.get("mode_reason") or "Startup")
        self._last_apply_time = app.get("mode_applied_at")
        self._last_results: List[Dict[str, Any]] = []

        self._trigger_generation = 0
        self._trigger_timer: threading.Timer | None = None
        self._trigger_stop_event: threading.Event | None = None
        self._trigger_thread: threading.Thread | None = None
        self._active_trigger: Dict[str, Any] | None = None
        self._trigger_completion_callback: Callable[[], Any] | None = None

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "mode": self._current_mode,
                "source": self._source,
                "reason": self._last_reason,
                "applied_at": self._last_apply_time,
                "results": list(self._last_results),
                "override": dict(self._active_trigger) if self._active_trigger else None,
            }

    def trigger_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            active = dict(self._active_trigger) if self._active_trigger else None
            if active:
                expires = active.get("expires_monotonic")
                if isinstance(expires, (int, float)) and expires > 0:
                    active["remaining_ms"] = max(0, int((expires - time.monotonic()) * 1000))
                else:
                    active["remaining_ms"] = None
                active.pop("expires_monotonic", None)
            return {"active": active, "base_mode": self._current_mode}

    def _apply_mode_to_devices(self, mode_cfg_key: str) -> List[Dict[str, Any]]:
        cfg = self.store.get()
        bridges = {str(b.get("id")): b for b in cfg.get("hue_bridges", []) if isinstance(b, dict) and b.get("enabled", True)}
        jobs: List[tuple[str, Dict[str, Any], Dict[str, Any] | None]] = []
        for device in cfg.get("devices", []):
            if isinstance(device, dict) and device.get("enabled", True):
                jobs.append(("wled", device, None))
        for light in cfg.get("hue_lights", []):
            if not isinstance(light, dict) or not light.get("enabled", True):
                continue
            bridge = bridges.get(str(light.get("bridge_id") or ""))
            if bridge:
                jobs.append(("hue", light, bridge))

        def send(kind: str, endpoint: Dict[str, Any], bridge: Dict[str, Any] | None) -> Dict[str, Any]:
            try:
                mode_cfg = endpoint.get("modes", {}).get(mode_cfg_key)
                if not isinstance(mode_cfg, dict):
                    raise ValueError(f"{mode_cfg_key.title()} mode is not configured")
                if kind == "hue":
                    result = apply_light_mode(bridge or {}, endpoint, mode_cfg)
                else:
                    result = apply_mode(endpoint["host"], mode_cfg)
                return {
                    "device_id": endpoint.get("id"), "name": endpoint.get("name"),
                    "device_type": kind, "ok": True, "result": result,
                }
            except (WLEDError, HueError, KeyError, ValueError) as exc:
                return {
                    "device_id": endpoint.get("id"), "name": endpoint.get("name"),
                    "device_type": kind, "ok": False, "error": str(exc),
                }

        if not jobs:
            return []
        results: List[Dict[str, Any]] = []
        workers = max(1, min(10, len(jobs)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="hl-base") as pool:
            futures = [pool.submit(send, *job) for job in jobs]
            for future in as_completed(futures):
                results.append(future.result())
        results.sort(key=lambda item: (str(item.get("device_type")), str(item.get("name") or "")))
        return results

    def apply_base_mode(self, mode: str, *, source: str, reason: str, force: bool = False, preserve_trigger: bool = False) -> Dict[str, Any]:
        mode = str(mode).lower().strip()
        if mode not in {"idle", "active"}:
            raise ValueError("Base mode must be idle or active")

        with self._lock:
            # Most Idle transitions are hard boundaries. Matchshot is the exception:
            # Game Bridge can move the base to Idle while preserving the celebration,
            # so choreography completion restores Idle instead of cancelling it.
            if mode == "idle" and self._active_trigger and not preserve_trigger:
                self._cancel_trigger_locked("Base mode changed to Idle")

            same_base = self._current_mode == mode and self._source == source
            trigger_active = self._active_trigger is not None

            # While a trigger is active, a deferred base update changes what should
            # be restored later without stomping the temporary effect.
            if trigger_active and (mode == "active" or preserve_trigger) and not force:
                self._set_base_state_locked(mode, source, reason, self._last_results)
                return {
                    "ok": True, "skipped": True, "deferred": True,
                    "mode": mode, "source": source, "reason": reason,
                    "results": list(self._last_results),
                }

            if not force and same_base:
                return {
                    "ok": True,
                    "skipped": True,
                    "mode": mode,
                    "source": source,
                    "reason": reason,
                    "results": list(self._last_results),
                }

            results = self._apply_mode_to_devices(mode)
            self._set_base_state_locked(mode, source, reason, results)
            failures = [item for item in results if not item.get("ok")]
            logger.info(
                "Lighting base mode -> %s (%s: %s) on %d device(s)%s",
                mode.upper(), source, reason, len(results),
                f"; {len(failures)} failed" if failures else "",
            )
            return {
                "ok": not failures,
                "skipped": False,
                "mode": mode,
                "source": source,
                "reason": reason,
                "results": results,
            }

    def _set_base_state_locked(self, mode: str, source: str, reason: str, results: List[Dict[str, Any]]) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self._current_mode = mode
        self._source = source
        self._last_reason = reason
        self._last_apply_time = stamp
        self._last_results = list(results)

        def mutate(current: Dict[str, Any]) -> None:
            app = current.setdefault("app", {})
            app["current_mode"] = mode
            app["mode_source"] = source
            app["mode_reason"] = reason
            app["mode_applied_at"] = stamp
            if source == "manual":
                app["last_manual_mode"] = mode

        self.store.update(mutate)

    def manual_apply(self, mode: str, reason: str = "Manual control") -> Dict[str, Any]:
        with self._lock:
            if self._active_trigger:
                self._cancel_trigger_locked("Manual base-state control")
        return self.apply_base_mode(mode, source="manual", reason=reason, force=True)

    def autodarts_apply(self, mode: str, reason: str) -> Dict[str, Any]:
        cfg = self.store.get()
        settings = cfg.get("autodarts", {})
        if not settings.get("auto_lighting", True):
            return {"ok": True, "skipped": True, "disabled": True, "mode": mode, "reason": reason, "results": []}
        return self.apply_base_mode(mode, source="autodarts", reason=reason, force=False)

    def autodarts_game_apply(self, mode: str, reason: str, preserve_trigger: bool = False) -> Dict[str, Any]:
        cfg = self.store.get()
        settings = cfg.get("autodarts", {})
        if not settings.get("auto_lighting", True):
            return {"ok": True, "skipped": True, "disabled": True, "mode": mode, "reason": reason, "results": []}
        return self.apply_base_mode(
            mode, source="autodarts_game", reason=reason, force=False, preserve_trigger=preserve_trigger
        )

    def set_active_trigger_completion(self, callback: Callable[[], Any], label: str = "Follow-up") -> bool:
        """Replace normal base restoration with a one-shot follow-up action.

        The callback belongs to the currently active trigger generation. If that
        trigger is cancelled or replaced (for example by Game Shot/Match Shot),
        the callback is discarded automatically and can never fire late.
        """
        with self._lock:
            if not self._active_trigger:
                return False
            self._trigger_completion_callback = callback
            self._active_trigger["followup"] = str(label or "Follow-up")[:100]
            return True

    def clear_active_trigger_completion(self) -> None:
        with self._lock:
            self._trigger_completion_callback = None
            if self._active_trigger:
                self._active_trigger.pop("followup", None)

    def takeout_started_yellow(self, device_ids: List[str] | None = None) -> Dict[str, Any]:
        """Hold solid yellow until Takeout Finished or a higher-priority game event.

        This is a persistent system override: it has no timeout. The player can
        take as long as needed to remove the darts, and the board stays yellow.
        """
        yellow_effect = {
            "source": "direct",
            "on": True,
            "brightness": 255,
            "effect_id": 0,
            "palette_id": 0,
            "speed": 128,
            "intensity": 128,
            "segment_id": None,
            "transition_ms": 0,
            "colors": [[255, 255, 0], [0, 0, 0], [0, 0, 0]],
            "preset_id": 1,
        }
        with self._lock:
            active = dict(self._active_trigger) if self._active_trigger else None
            if active:
                label = f"{active.get('name', '')} {active.get('reason', '')}".casefold()
                if "game shot" in label or "match shot" in label:
                    return {
                        "ok": True,
                        "accepted": False,
                        "reason": f"Preserved winning celebration {active.get('name', 'celebration')}",
                        "active": active,
                        "results": [],
                    }
            self._cancel_trigger_locked("Three darts complete — enter Takeout", restore=False)
            cfg = self.store.get()
            results = self._apply_trigger_outputs(cfg, yellow_effect, list(device_ids or []), [])
            self._trigger_generation += 1
            self._active_trigger = {
                "id": "system_takeout_started",
                "name": "Takeout • Yellow",
                "priority": 89,
                "duration_ms": None,
                "reason": "Three darts complete — remove darts",
                "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "expires_monotonic": None,
                "device_ids": list(device_ids or []),
                "scene": None,
                "scene_action_count": 0,
                "choreography": False,
                "persistent": True,
                "results": list(results),
            }
            failures = [x for x in results if not x.get("ok")]
            logger.info("Takeout state -> YELLOW on %d device(s)", len(results))
            return {
                "ok": not failures,
                "accepted": True,
                "trigger": "Takeout • Yellow",
                "priority": 89,
                "duration_ms": None,
                "persistent": True,
                "results": results,
            }

    def takeout_finished_red_flash(self, duration_ms: int = 500, device_ids: List[str] | None = None) -> Dict[str, Any]:
        """Flash solid red after dart removal, unless a win celebration owns priority.

        Game Shot and Match Shot starter priorities are 90/100. A currently active
        override at 90+ is therefore preserved; ordinary dart/visit/takeout effects
        are replaced by the 500 ms acknowledgement before the current base state
        is restored.
        """
        try:
            duration_ms = max(100, min(5000, int(duration_ms)))
        except (TypeError, ValueError):
            duration_ms = 500

        with self._lock:
            active = dict(self._active_trigger) if self._active_trigger else None
            if active:
                label = f"{active.get('name', '')} {active.get('reason', '')}".casefold()
                if "game shot" in label or "match shot" in label:
                    return {
                        "ok": True,
                        "accepted": False,
                        "reason": f"Preserved winning celebration {active.get('name', 'celebration')}",
                        "active": active,
                        "results": [],
                    }

        red_effect = {
            "source": "direct",
            "on": True,
            "brightness": 255,
            "effect_id": 0,
            "palette_id": 0,
            "speed": 128,
            "intensity": 128,
            "segment_id": None,
            "transition_ms": 0,
            "colors": [[255, 0, 0], [0, 0, 0], [0, 0, 0]],
            "preset_id": 1,
        }
        return self.apply_trigger_effect(
            trigger_id="system_takeout_finished",
            trigger_name="Takeout Finished • Red",
            effect=red_effect,
            duration_ms=duration_ms,
            priority=89,
            device_ids=list(device_ids or []),
            reason="Takeout finished — red acknowledgement",
            force=True,
        )

    @staticmethod
    def _scene_is_choreographed(scene_actions: Iterable[Dict[str, Any]], repeat_count: int) -> bool:
        if repeat_count != 1:
            return True
        for action in scene_actions:
            if not isinstance(action, dict):
                continue
            try:
                if int(action.get("step", 1)) > 1:
                    return True
                if int(action.get("delay_ms", 0)) > 0:
                    return True
                if int(action.get("hold_ms", 0)) > 0:
                    return True
            except (TypeError, ValueError):
                return True
        return False

    @staticmethod
    def _choreography_duration_ms(scene_actions: Iterable[Dict[str, Any]], repeat_count: int) -> int | None:
        if repeat_count == 0:
            return None
        grouped: Dict[int, List[Dict[str, Any]]] = {}
        for action in scene_actions:
            if not isinstance(action, dict):
                continue
            try:
                step = max(1, int(action.get("step", 1)))
            except (TypeError, ValueError):
                step = 1
            grouped.setdefault(step, []).append(action)
        single_run = 0
        for step in sorted(grouped):
            step_ms = 0
            for action in grouped[step]:
                try:
                    delay = max(0, int(action.get("delay_ms", 0)))
                except (TypeError, ValueError):
                    delay = 0
                try:
                    hold = max(0, int(action.get("hold_ms", 0)))
                except (TypeError, ValueError):
                    hold = 0
                step_ms = max(step_ms, delay + hold)
            single_run += step_ms
        return single_run * max(1, repeat_count)

    def apply_trigger_effect(
        self,
        *,
        trigger_id: str,
        trigger_name: str,
        effect: Dict[str, Any],
        duration_ms: int,
        priority: int,
        device_ids: List[str],
        reason: str,
        force: bool = False,
        scene_name: str = "",
        scene_actions: List[Dict[str, Any]] | None = None,
        scene_repeat_count: int = 1,
    ) -> Dict[str, Any]:
        actions = list(scene_actions or [])
        try:
            repeat_count = max(0, min(20, int(scene_repeat_count)))
        except (TypeError, ValueError):
            repeat_count = 1
        choreographed = bool(actions) and self._scene_is_choreographed(actions, repeat_count)

        with self._lock:
            current = self._active_trigger
            if current and not force and priority < int(current.get("priority", 0)):
                return {
                    "ok": True,
                    "accepted": False,
                    "reason": f"Lower priority than {current.get('name', 'active trigger')}",
                    "active": self.trigger_snapshot().get("active"),
                    "results": [],
                }

            self._cancel_trigger_locked("Replaced by a new trigger", restore=False)
            self._trigger_generation += 1
            generation = self._trigger_generation

            if choreographed:
                total_ms = self._choreography_duration_ms(actions, repeat_count)
                expires = time.monotonic() + total_ms / 1000.0 if total_ms is not None else None
                stop_event = threading.Event()
                self._trigger_stop_event = stop_event
                self._active_trigger = {
                    "id": trigger_id,
                    "name": trigger_name,
                    "priority": priority,
                    "duration_ms": total_ms,
                    "configured_duration_ms": duration_ms,
                    "reason": reason,
                    "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "expires_monotonic": expires,
                    "device_ids": list(device_ids),
                    "scene": scene_name or None,
                    "scene_action_count": len(actions),
                    "choreography": True,
                    "repeat_count": repeat_count,
                    "step": 0,
                    "repeat": 0,
                    "results": [],
                }
                thread = threading.Thread(
                    target=self._run_choreography,
                    args=(generation, trigger_name, actions, repeat_count, stop_event),
                    daemon=True,
                    name=f"hl-choreo-{trigger_id[:12]}",
                )
                self._trigger_thread = thread
                thread.start()
                return {
                    "ok": True,
                    "accepted": True,
                    "reason": reason,
                    "trigger": trigger_name,
                    "priority": priority,
                    "duration_ms": total_ms,
                    "configured_duration_ms": duration_ms,
                    "choreography": True,
                    "repeat_count": repeat_count,
                    "results": [],
                }

            cfg = self.store.get()
            results = self._apply_trigger_outputs(cfg, effect, device_ids, actions)
            expires = time.monotonic() + duration_ms / 1000.0
            self._active_trigger = {
                "id": trigger_id,
                "name": trigger_name,
                "priority": priority,
                "duration_ms": duration_ms,
                "reason": reason,
                "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "expires_monotonic": expires,
                "device_ids": list(device_ids),
                "scene": scene_name or None,
                "scene_action_count": len(actions),
                "choreography": False,
                "results": list(results),
            }
            timer = threading.Timer(duration_ms / 1000.0, self._expire_trigger, args=(generation, trigger_name))
            timer.daemon = True
            self._trigger_timer = timer
            timer.start()
            failures = [x for x in results if not x.get("ok")]
            return {
                "ok": not failures,
                "accepted": True,
                "reason": reason,
                "trigger": trigger_name,
                "priority": priority,
                "duration_ms": duration_ms,
                "choreography": False,
                "results": results,
            }

    def _run_choreography(
        self,
        generation: int,
        trigger_name: str,
        actions: List[Dict[str, Any]],
        repeat_count: int,
        stop_event: threading.Event,
    ) -> None:
        grouped: Dict[int, List[Dict[str, Any]]] = {}
        for action in actions:
            if not isinstance(action, dict):
                continue
            try:
                step = max(1, min(50, int(action.get("step", 1))))
            except (TypeError, ValueError):
                step = 1
            grouped.setdefault(step, []).append(action)

        ordered_steps = sorted(grouped)
        if not ordered_steps:
            self._finish_choreography(generation, trigger_name)
            return

        repeat_index = 0
        while not stop_event.is_set() and (repeat_count == 0 or repeat_index < repeat_count):
            repeat_index += 1
            for step in ordered_steps:
                if stop_event.is_set() or not self._generation_is_current(generation):
                    return
                step_actions = grouped[step]
                self._update_choreography_runtime(generation, step=step, repeat=repeat_index)

                # Actions with the same delay are resolved together and dispatched
                # in parallel across WLED controllers. Different delay buckets are
                # released relative to the beginning of this step.
                buckets: Dict[int, List[Dict[str, Any]]] = {}
                step_end_ms = 0
                for action in step_actions:
                    try:
                        delay = max(0, min(60000, int(action.get("delay_ms", 0))))
                    except (TypeError, ValueError):
                        delay = 0
                    try:
                        hold = max(0, min(60000, int(action.get("hold_ms", 0))))
                    except (TypeError, ValueError):
                        hold = 0
                    buckets.setdefault(delay, []).append(action)
                    step_end_ms = max(step_end_ms, delay + hold)

                step_started = time.monotonic()
                latest_results: List[Dict[str, Any]] = []
                for delay in sorted(buckets):
                    if not self._wait_until(step_started + delay / 1000.0, stop_event, generation):
                        return
                    cfg = self.store.get()
                    latest_results.extend(self._apply_trigger_outputs(cfg, {}, [], buckets[delay]))
                    self._append_choreography_results(generation, latest_results)

                if not self._wait_until(step_started + step_end_ms / 1000.0, stop_event, generation):
                    return

        if stop_event.is_set() or not self._generation_is_current(generation):
            return
        self._finish_choreography(generation, trigger_name)

    def _wait_until(self, deadline: float, stop_event: threading.Event, generation: int) -> bool:
        while True:
            if stop_event.is_set() or not self._generation_is_current(generation):
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            stop_event.wait(min(0.05, remaining))

    def _generation_is_current(self, generation: int) -> bool:
        with self._lock:
            return generation == self._trigger_generation and self._active_trigger is not None

    def _update_choreography_runtime(self, generation: int, *, step: int, repeat: int) -> None:
        with self._lock:
            if generation != self._trigger_generation or not self._active_trigger:
                return
            self._active_trigger["step"] = step
            self._active_trigger["repeat"] = repeat

    def _append_choreography_results(self, generation: int, results: List[Dict[str, Any]]) -> None:
        with self._lock:
            if generation != self._trigger_generation or not self._active_trigger:
                return
            self._active_trigger["results"] = list(results[-40:])

    def _finish_choreography(self, generation: int, trigger_name: str) -> None:
        with self._lock:
            if generation != self._trigger_generation or not self._active_trigger:
                return
            callback = self._trigger_completion_callback
            self._trigger_completion_callback = None
            self._active_trigger = None
            self._trigger_stop_event = None
            self._trigger_thread = None
            base = self._current_mode
        if callback:
            logger.info("Choreography finished: %s -> running queued follow-up", trigger_name)
            try:
                callback()
            except Exception:
                logger.exception("Queued follow-up failed after choreography %s", trigger_name)
                try:
                    self.apply_base_mode(base, source="restore", reason=f"Scene follow-up failed: {trigger_name}", force=True)
                except Exception:
                    logger.exception("Failed to restore %s after choreography follow-up failure", base)
            return
        logger.info("Choreography finished: %s -> restoring %s", trigger_name, base.upper())
        try:
            self.apply_base_mode(base, source="restore", reason=f"Scene finished: {trigger_name}", force=True)
        except Exception:
            logger.exception("Failed to restore %s after choreography %s", base, trigger_name)

    def _apply_trigger_outputs(
        self,
        cfg: Dict[str, Any],
        fallback_effect: Dict[str, Any],
        device_ids: List[str],
        scene_actions: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        bridges = {
            str(b.get("id")): b
            for b in cfg.get("hue_bridges", [])
            if isinstance(b, dict) and b.get("enabled", True)
        }
        devices: Dict[str, Dict[str, Any]] = {}
        for d in cfg.get("devices", []):
            if isinstance(d, dict) and d.get("enabled", True):
                row = dict(d); row["_kind"] = "wled"; devices[str(d.get("id"))] = row
        for d in cfg.get("hue_lights", []):
            if not isinstance(d, dict) or not d.get("enabled", True):
                continue
            if str(d.get("bridge_id") or "") not in bridges:
                continue
            row = dict(d); row["_kind"] = "hue"; devices[str(d.get("id"))] = row

        groups = {
            str(g.get("id")): set(str(x) for x in g.get("device_ids", []))
            for g in cfg.get("device_groups", [])
            if isinstance(g, dict)
        }
        resolved: Dict[str, Dict[str, Any]] = {}

        if scene_actions:
            for action in scene_actions:
                if not isinstance(action, dict) or not isinstance(action.get("effect"), dict):
                    continue
                target_type = str(action.get("target_type") or "all")
                target_id = str(action.get("target_id") or "")
                if target_type == "device":
                    targets = {target_id}
                elif target_type == "group":
                    targets = groups.get(target_id, set())
                else:
                    targets = set(devices)
                for device_id in targets:
                    if device_id in devices:
                        resolved[device_id] = {
                            "effect": action["effect"],
                            "action": action.get("name") or "Scene Action",
                        }
        else:
            selected = set(str(x) for x in device_ids)
            targets = selected if selected else set(devices)
            for device_id in targets:
                if device_id in devices:
                    resolved[device_id] = {"effect": fallback_effect, "action": "Trigger Effect"}

        def send(device_id: str, row: Dict[str, Any]) -> Dict[str, Any]:
            device = devices[device_id]
            kind = str(device.get("_kind") or "wled")
            try:
                if kind == "hue":
                    bridge = bridges.get(str(device.get("bridge_id") or ""))
                    if not bridge:
                        raise HueError("Hue Bridge is disabled or missing")
                    result = apply_light_mode(bridge, device, row["effect"])
                else:
                    result = apply_mode(device["host"], row["effect"])
                return {
                    "device_id": device_id, "name": device.get("name"),
                    "device_type": kind, "action": row.get("action"), "ok": True, "result": result,
                }
            except (WLEDError, HueError, KeyError, ValueError) as exc:
                return {
                    "device_id": device_id, "name": device.get("name"),
                    "device_type": kind, "action": row.get("action"), "ok": False, "error": str(exc),
                }

        results: List[Dict[str, Any]] = []
        if not resolved:
            return results
        workers = max(1, min(10, len(resolved)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="hl-scene") as pool:
            futures = {pool.submit(send, device_id, row): device_id for device_id, row in resolved.items()}
            for future in as_completed(futures):
                results.append(future.result())
        results.sort(key=lambda item: (str(item.get("device_type")), str(item.get("name") or item.get("device_id") or "")))
        return results

    def _expire_trigger(self, generation: int, trigger_name: str) -> None:
        with self._lock:
            if generation != self._trigger_generation or not self._active_trigger:
                return
            callback = self._trigger_completion_callback
            self._trigger_completion_callback = None
            self._active_trigger = None
            self._trigger_timer = None
            base = self._current_mode
        if callback:
            logger.info("Trigger finished: %s -> running queued follow-up", trigger_name)
            try:
                callback()
            except Exception:
                logger.exception("Queued follow-up failed after trigger %s", trigger_name)
                try:
                    self.apply_base_mode(base, source="restore", reason=f"Trigger follow-up failed: {trigger_name}", force=True)
                except Exception:
                    logger.exception("Failed to restore %s after trigger follow-up failure", base)
            return
        logger.info("Trigger finished: %s -> restoring %s", trigger_name, base.upper())
        try:
            self.apply_base_mode(base, source="restore", reason=f"Trigger finished: {trigger_name}", force=True)
        except Exception:
            logger.exception("Failed to restore %s after trigger %s", base, trigger_name)

    def cancel_trigger(self, reason: str = "Trigger cancelled", *, restore: bool = True) -> Dict[str, Any]:
        with self._lock:
            had = self._active_trigger is not None
            self._cancel_trigger_locked(reason, restore=False)
            base = self._current_mode
        result = None
        if had and restore:
            result = self.apply_base_mode(base, source="restore", reason=reason, force=True)
        return {"ok": True, "cancelled": had, "mode": base, "result": result}

    def _cancel_trigger_locked(self, reason: str, restore: bool = False) -> None:
        self._trigger_completion_callback = None
        timer = self._trigger_timer
        self._trigger_timer = None
        if timer:
            try:
                timer.cancel()
            except Exception:
                pass
        stop_event = self._trigger_stop_event
        self._trigger_stop_event = None
        if stop_event:
            stop_event.set()
        self._trigger_thread = None
        if self._active_trigger:
            logger.info("Trigger cancelled: %s (%s)", self._active_trigger.get("name"), reason)
        self._active_trigger = None
        self._trigger_generation += 1

    def restore_base_mode(self, reason: str = "Restore base state") -> Dict[str, Any]:
        with self._lock:
            mode = self._current_mode
        return self.apply_base_mode(mode, source="restore", reason=reason, force=True)
