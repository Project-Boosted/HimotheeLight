from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Dict, List

from .config_store import ConfigStore
from .lighting import LightingStateManager

logger = logging.getLogger("himotheelight")


def normalize_dart_name(value: str) -> str:
    value = str(value or "").strip().upper().replace(" ", "")
    aliases = {
        "DB": "BULL", "D25": "BULL", "BULLSEYE": "BULL", "INNERBULL": "BULL",
        "SB": "25", "S25": "25", "OUTERBULL": "25",
        "MISS": "MISS", "M": "MISS", "OUTSIDE": "MISS",
    }
    return aliases.get(value, value)


def normalize_target(value: str) -> str:
    raw = str(value or "").strip().upper().replace(" ", "")
    aliases = {
        "DB": "BULL", "D25": "BULL", "BULLSEYE": "BULL", "INNERBULL": "BULL",
        "SB": "25", "S25": "25", "OUTERBULL": "25",
    }
    return aliases.get(raw, raw)


def parse_combination(value: str) -> List[str]:
    parts = [p for p in re.split(r"[_\s,>+]+", str(value or "").strip()) if p]
    return [normalize_dart_name(p) for p in parts]


def _safe_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def trigger_matches(trigger: Dict[str, Any], event: Dict[str, Any]) -> bool:
    kind = str(trigger.get("trigger_type") or "").lower()
    if kind == "dart":
        if event.get("kind") != "dart":
            return False
        return normalize_dart_name(trigger.get("value", "")) == normalize_dart_name(event.get("dart", ""))
    if kind == "visit_exact":
        if event.get("kind") != "visit":
            return False
        expected = _safe_int(trigger.get("value"), _safe_int(trigger.get("minimum")))
        return _safe_int(event.get("score")) == expected
    if kind == "visit_range":
        if event.get("kind") != "visit":
            return False
        score = _safe_int(event.get("score"))
        lo = max(0, min(180, _safe_int(trigger.get("minimum"), 0)))
        hi = max(lo, min(180, _safe_int(trigger.get("maximum"), 180)))
        return lo <= score <= hi
    if kind == "combination":
        if event.get("kind") != "visit":
            return False
        expected = parse_combination(str(trigger.get("value") or ""))
        actual = [normalize_dart_name(x) for x in event.get("darts") or []]
        return bool(expected) and expected == actual
    if kind == "target":
        if event.get("kind") != "game_target":
            return False
        return normalize_target(trigger.get("value", "")) == normalize_target(event.get("target", ""))
    if kind == "board_event":
        if event.get("kind") != "board_event":
            return False
        return str(trigger.get("value") or "").strip().lower() == str(event.get("event") or "").strip().lower()
    if kind == "game_event":
        if event.get("kind") != "game_event":
            return False
        return str(trigger.get("value") or "").strip().lower() == str(event.get("event") or "").strip().lower()
    if kind == "player_turn":
        if event.get("kind") != "player_turn":
            return False
        expected = str(trigger.get("value") or "").strip().casefold()
        player = str(event.get("player") or "").strip().casefold()
        return bool(expected) and expected == player
    return False


class TriggerEngine:
    """Matches normalized Autodarts events to timed WLED trigger effects."""

    def __init__(self, store: ConfigStore, lighting: LightingStateManager):
        self.store = store
        self.lighting = lighting
        self._lock = threading.RLock()
        self._last_visit_throws: List[Dict[str, Any]] = []
        self._visit_fired = False
        self._takeout_started_for_visit = False
        self._takeout_pending_after_score = False
        self._takeout_pending_trigger_id: str | None = None
        self._takeout_removed_while_pending = False
        self._takeout_pending_device_ids: List[str] = []
        self._takeout_pending_finish_ms = 500
        self._history: List[Dict[str, Any]] = []

    def snapshot(self) -> Dict[str, Any]:
        cfg = self.store.get()
        return {
            "triggers": cfg.get("triggers", []),
            "runtime": self.lighting.trigger_snapshot(),
            "history": list(self._history[-80:]),
        }

    def reset_visit(self) -> None:
        with self._lock:
            self._last_visit_throws = []
            self._visit_fired = False
            self._takeout_started_for_visit = False

    @staticmethod
    def _dispatch_accepted(result: Dict[str, Any] | None) -> bool:
        if not isinstance(result, dict):
            return False
        return any(bool(row.get("accepted")) for row in result.get("results", []) if isinstance(row, dict))

    def _clear_takeout_pending(self) -> None:
        with self._lock:
            self._takeout_pending_after_score = False
            self._takeout_pending_trigger_id = None
            self._takeout_removed_while_pending = False
            self._takeout_pending_device_ids = []
            self._takeout_pending_finish_ms = 500

    def _record_takeout_red(self, result: Dict[str, Any], finish_flash_ms: int) -> None:
        row = {
            "time": time.strftime("%H:%M:%S"),
            "trigger_id": "system_takeout_finished",
            "trigger": "Takeout • Red",
            "priority": 89,
            "duration_ms": finish_flash_ms,
            "accepted": bool(result.get("accepted")),
            "reason": result.get("reason") or "Darts removed — red acknowledgement",
            "event": {"kind": "board_event", "event": "Takeout finished", "source": "himotheelight_takeout_flow"},
        }
        with self._lock:
            self._history.append(row)
            if len(self._history) > 120:
                self._history = self._history[-120:]

    def _complete_deferred_takeout(self) -> None:
        """Run after the dart/visit score effect has completed naturally."""
        with self._lock:
            if not self._takeout_pending_after_score:
                return
            removed = self._takeout_removed_while_pending
            device_ids = list(self._takeout_pending_device_ids)
            finish_ms = self._takeout_pending_finish_ms
            self._takeout_pending_after_score = False
            self._takeout_pending_trigger_id = None
            self._takeout_removed_while_pending = False
            self._takeout_pending_device_ids = []
            self._takeout_pending_finish_ms = 500
        if removed:
            result = self.lighting.takeout_finished_red_flash(finish_ms, device_ids)
            self._record_takeout_red(result, finish_ms)
            logger.info("Score effect finished after early dart removal -> RED takeout acknowledgement")
        else:
            self.lighting.takeout_started_yellow(device_ids)
            logger.info("Score effect finished -> YELLOW takeout waiting")

    def _defer_takeout_until_score_finishes(self, device_ids: List[str], finish_flash_ms: int) -> bool:
        active = self.lighting.trigger_snapshot().get("active")
        if not active:
            return False
        active_id = str(active.get("id") or "")
        if active_id.startswith("system_takeout_"):
            return False
        label = f"{active.get('name', '')} {active.get('reason', '')}".casefold()
        if "game shot" in label or "match shot" in label:
            return False
        with self._lock:
            self._takeout_pending_after_score = True
            self._takeout_pending_trigger_id = active_id
            self._takeout_removed_while_pending = False
            self._takeout_pending_device_ids = list(device_ids)
            self._takeout_pending_finish_ms = finish_flash_ms
        queued = self.lighting.set_active_trigger_completion(self._complete_deferred_takeout, "Takeout -> Yellow")
        if not queued:
            self._clear_takeout_pending()
        return queued

    def handle_autodarts_state(self, event: Dict[str, Any]) -> None:
        event_name = str(event.get("event") or "")
        throws = [x for x in (event.get("throws") or []) if isinstance(x, dict)]
        num_throws = _safe_int(event.get("num_throws"), len(throws))
        previous_num = _safe_int(event.get("previous_num_throws"), 0)
        settings = self.store.get().get("autodarts", {})
        flow_enabled = bool(settings.get("takeout_flow_enabled", True))
        after_darts = 3
        finish_flash_ms = max(100, min(5000, _safe_int(settings.get("takeout_finish_flash_ms"), 500)))

        dart_dispatch: Dict[str, Any] | None = None
        visit_dispatch: Dict[str, Any] | None = None
        if event_name == "Throw detected" and event.get("last_throw"):
            dart = event["last_throw"]
            dart_dispatch = self.dispatch({
                "kind": "dart",
                "dart": dart.get("name"),
                "score": dart.get("score"),
                "source": "autodarts",
            })

        with self._lock:
            if throws:
                self._last_visit_throws = [dict(x) for x in throws]
            complete_throws = list(self._last_visit_throws)
            should_complete = False
            if not self._visit_fired:
                if event_name == "Throw detected" and num_throws >= 3:
                    should_complete = True
                elif event_name == "Takeout started" and complete_throws:
                    should_complete = True
                elif previous_num > 0 and num_throws == 0 and complete_throws:
                    should_complete = True

            if should_complete:
                self._visit_fired = True
                total = sum(_safe_int(x.get("score")) for x in complete_throws)
            else:
                total = None

            synthetic_takeout = bool(
                flow_enabled
                and not self._takeout_started_for_visit
                and event_name == "Throw detected"
                and num_throws >= after_darts
                and len(complete_throws) >= after_darts
            )
            actual_takeout = bool(
                flow_enabled
                and not self._takeout_started_for_visit
                and event_name == "Takeout started"
                and len(complete_throws) >= after_darts
            )
            if synthetic_takeout or actual_takeout:
                self._takeout_started_for_visit = True
            reset_after = num_throws == 0 and previous_num > 0

        if total is not None:
            visit_dispatch = self.dispatch({
                "kind": "visit",
                "score": total,
                "darts": [x.get("name") for x in complete_throws],
                "source": "autodarts",
            })

        takeout_device_ids: List[str] = []
        cfg = self.store.get()
        takeout_rule = next((
            t for t in cfg.get("triggers", [])
            if isinstance(t, dict) and t.get("enabled", False)
            and str(t.get("trigger_type") or "") == "board_event"
            and str(t.get("value") or "").strip().lower() == "takeout started"
         ), None)
        if takeout_rule and isinstance(takeout_rule.get("device_ids"), list):
            takeout_device_ids = [str(x) for x in takeout_rule.get("device_ids", [])]

        if synthetic_takeout or actual_takeout:
            # Dart/visit effects triggered by the third dart get to finish first.
            # This includes T20, 100+, 140+, 180 and any custom scoring rule.
            score_effect_accepted = self._dispatch_accepted(dart_dispatch) or self._dispatch_accepted(visit_dispatch)
            if not (score_effect_accepted and self._defer_takeout_until_score_finishes(takeout_device_ids, finish_flash_ms)):
                self.lighting.takeout_started_yellow(takeout_device_ids)

        if flow_enabled and event_name == "Takeout finished":
            deferred = False
            with self._lock:
                pending = self._takeout_pending_after_score
                pending_id = self._takeout_pending_trigger_id
            if pending:
                active = self.lighting.trigger_snapshot().get("active")
                if active and str(active.get("id") or "") == str(pending_id or ""):
                    with self._lock:
                        self._takeout_removed_while_pending = True
                    deferred = self.lighting.set_active_trigger_completion(
                        self._complete_deferred_takeout, "Takeout removed -> Red"
                    )
                    if deferred:
                        logger.info("Darts removed while score effect is running -> defer RED until score effect finishes")
                if not deferred:
                    self._clear_takeout_pending()
            if not deferred:
                result = self.lighting.takeout_finished_red_flash(finish_flash_ms, takeout_device_ids)
                self._record_takeout_red(result, finish_flash_ms)

        # Every concrete Board Manager event remains available to explicit rules,
        # except the two automatic takeout-flow events while that flow is enabled.
        # This prevents a second Takeout rule from immediately replacing the
        # guaranteed waiting/red acknowledgement sequence.
        if event_name not in {"State", ""}:
            skip_generic = flow_enabled and event_name in {"Takeout started", "Takeout finished"}
            if not skip_generic:
                self.dispatch({"kind": "board_event", "event": event_name, "source": "autodarts"})

        if reset_after or event_name in {"Takeout finished", "Manual reset", "Stopped"}:
            self.reset_visit()
        if event_name in {"Manual reset", "Stopped"}:
            self._clear_takeout_pending()
            self.lighting.clear_active_trigger_completion()

    def dispatch(self, event: Dict[str, Any], force: bool = False) -> Dict[str, Any]:
        cfg = self.store.get()
        matched = []
        for trigger in cfg.get("triggers", []):
            if not isinstance(trigger, dict) or not trigger.get("enabled", False):
                continue
            if trigger_matches(trigger, event):
                matched.append(trigger)

        matched.sort(key=lambda t: _safe_int(t.get("priority"), 0), reverse=True)
        results = []
        for trigger in matched:
            result = self._fire(trigger, event, force=force)
            results.append(result)
            if result.get("accepted"):
                break
        return {"ok": True, "event": event, "matched": len(matched), "results": results}

    def fire_by_id(self, trigger_id: str, *, simulated: bool = True) -> Dict[str, Any]:
        cfg = self.store.get()
        trigger = next((t for t in cfg.get("triggers", []) if t.get("id") == trigger_id), None)
        if not trigger:
            raise ValueError("Trigger not found")
        event = {"kind": "test", "source": "simulation" if simulated else "manual"}
        return self._fire(trigger, event, force=True)

    def _fire(self, trigger: Dict[str, Any], event: Dict[str, Any], force: bool = False) -> Dict[str, Any]:
        priority = max(0, min(100, _safe_int(trigger.get("priority"), 20)))
        duration_ms = max(100, min(60_000, _safe_int(trigger.get("duration_ms"), 1500)))
        effect = trigger.get("effect") if isinstance(trigger.get("effect"), dict) else {}
        device_ids = trigger.get("device_ids") if isinstance(trigger.get("device_ids"), list) else []
        cfg = self.store.get()
        scene_id = str(trigger.get("scene_id") or "")
        scene = None
        if scene_id:
            scene = next((x for x in cfg.get("scenes", []) if isinstance(x, dict) and str(x.get("id")) == scene_id), None)
        result = self.lighting.apply_trigger_effect(
            trigger_id=str(trigger.get("id") or ""),
            trigger_name=str(trigger.get("name") or "Trigger"),
            effect=effect,
            duration_ms=duration_ms,
            priority=priority,
            device_ids=[str(x) for x in device_ids],
            reason=self._event_reason(event),
            force=force,
            scene_name=str(scene.get("name") or "") if scene else "",
            scene_actions=list(scene.get("actions") or []) if scene else None,
            scene_repeat_count=int(scene.get("repeat_count", 1)) if scene else 1,
        )
        row = {
            "time": time.strftime("%H:%M:%S"),
            "trigger_id": trigger.get("id"),
            "trigger": trigger.get("name"),
            "priority": priority,
            "duration_ms": duration_ms,
            "accepted": bool(result.get("accepted")),
            "reason": result.get("reason") or self._event_reason(event),
            "event": dict(event),
        }
        with self._lock:
            self._history.append(row)
            if len(self._history) > 120:
                self._history = self._history[-120:]
        if result.get("accepted"):
            effective = result.get("duration_ms")
            if effective is None:
                logger.info("Trigger fired: %s (looping choreography)", trigger.get("name"))
            else:
                logger.info("Trigger fired: %s for %.2fs", trigger.get("name"), float(effective) / 1000.0)
        else:
            logger.info("Trigger ignored: %s (%s)", trigger.get("name"), result.get("reason", "lower priority"))
        return result

    @staticmethod
    def _event_reason(event: Dict[str, Any]) -> str:
        kind = event.get("kind")
        if kind == "dart":
            return f"Dart {event.get('dart')}"
        if kind == "visit":
            darts = ", ".join(str(x) for x in event.get("darts") or [])
            return f"Visit {event.get('score')}{f' ({darts})' if darts else ''}"
        if kind == "game_target":
            player = event.get("player")
            return f"Target {event.get('target')}{f' — {player}' if player else ''}"
        if kind == "board_event":
            return f"Autodarts {event.get('event')}"
        if kind == "game_event":
            label = str(event.get("event") or "Game event").replace("_", " ").title()
            player = event.get("player")
            dart = event.get("last_dart")
            detail = f" — {player}" if player else ""
            if dart:
                detail += f" ({dart})"
            return f"{label}{detail}"
        if kind == "player_turn":
            return f"Player turn — {event.get('player') or 'Unknown'}"
        return "Trigger test"
