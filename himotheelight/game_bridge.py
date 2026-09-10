from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, Optional

logger = logging.getLogger("himotheelight")


def _int(value: Any, fallback: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _bool(value: Any) -> bool:
    return bool(value)


class AutodartsGameBridge:
    """Receives normalized Autodarts match snapshots from the browser bridge."""

    def __init__(
        self,
        settings_provider: Callable[[], Dict[str, Any]],
        on_base_mode: Callable[[str, str, bool], Any],
        on_event: Callable[[Dict[str, Any]], Any],
    ) -> None:
        self._settings_provider = settings_provider
        self._on_base_mode = on_base_mode
        self._on_event = on_event
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._previous: Optional[Dict[str, Any]] = None
        self._events: Deque[Dict[str, Any]] = deque(maxlen=120)
        self._status: Dict[str, Any] = {
            "extension_connected": False,
            "authoritative": False,
            "route": "unknown",
            "page_url": None,
            "match_id": None,
            "variant": None,
            "match_finished": False,
            "game_finished": False,
            "current_player": None,
            "current_player_index": None,
            "current_player_is_bot": False,
            "current_target": None,
            "last_game_event": None,
            "last_event_at": None,
            "last_heartbeat_at": None,
            "last_heartbeat_monotonic": 0.0,
            "match_active": False,
        }

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._monitor, name="HimotheeLight-GameBridge", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=1.5)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            result = dict(self._status)
            result.pop("last_heartbeat_monotonic", None)
            result["events"] = list(self._events)
            return result

    def is_authoritative(self) -> bool:
        with self._lock:
            return bool(self._status.get("extension_connected") and self._status.get("authoritative"))

    def ingest(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        kind = str(payload.get("type") or "heartbeat").strip().lower()
        page_url = str(payload.get("page_url") or "")[:1000]
        route = str(payload.get("route") or "unknown").strip().lower()
        now = time.monotonic()
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")

        with self._lock:
            self._status["extension_connected"] = True
            self._status["last_heartbeat_monotonic"] = now
            self._status["last_heartbeat_at"] = stamp
            if page_url:
                self._status["page_url"] = page_url
            if route:
                self._status["route"] = route

        if kind in {"heartbeat", "page"}:
            if route != "match":
                self._leave_match("Autodarts match page left")
            return self.snapshot()

        if kind == "page_hidden":
            self._leave_match("Autodarts match page hidden")
            return self.snapshot()

        if kind != "match_state":
            raise ValueError("Unsupported Autodarts bridge message")

        state = payload.get("state")
        if not isinstance(state, dict):
            raise ValueError("Autodarts bridge match_state requires state")
        self._handle_match_state(state)
        return self.snapshot()

    def simulate(self, event_name: str) -> Dict[str, Any]:
        event = str(event_name or "").strip().lower()
        if event == "target_bull":
            self._record_and_dispatch({
                "kind": "game_target", "target": "BULL", "player": "Simulation Player",
                "player_index": 0, "variant": "ATC", "source": "simulation",
            })
            return self.snapshot()
        allowed = {"bust", "gameshot", "matchshot", "turn_start", "game_start", "match_start", "bot_turn"}
        if event not in allowed:
            raise ValueError("Unsupported game-event simulation")
        meta = {
            "kind": "game_event",
            "event": event,
            "player": "Simulation Player",
            "player_index": 0,
            "last_dart": "D20" if event in {"gameshot", "matchshot"} else None,
            "source": "simulation",
        }
        self._record_and_dispatch(meta)
        return self.snapshot()

    def _handle_match_state(self, raw: Dict[str, Any]) -> None:
        state = {
            "match_id": str(raw.get("match_id") or "")[:100],
            "variant": str(raw.get("variant") or "Unknown")[:80],
            "finished": _bool(raw.get("finished", False)),
            "winner": _int(raw.get("winner"), -1),
            "game_finished": _bool(raw.get("game_finished", False)),
            "game_winner": _int(raw.get("game_winner"), -1),
            "player": _int(raw.get("player"), -1),
            "current_player_name": str(raw.get("current_player_name") or "")[:120],
            "current_player_is_bot": _bool(raw.get("current_player_is_bot", False)),
            "current_target": str(raw.get("current_target") or "")[:32],
            "turn_id": str(raw.get("turn_id") or "")[:120],
            "turn_busted": _bool(raw.get("turn_busted", False)),
            "turn_points": _int(raw.get("turn_points"), 0),
            "turn_throw_count": max(0, _int(raw.get("turn_throw_count"), 0)),
            "last_dart": str(raw.get("last_dart") or "")[:32],
            "round": _int(raw.get("round"), 0),
            "leg": _int(raw.get("leg"), 0),
            "set": _int(raw.get("set"), 0),
            "winner_name": str(raw.get("winner_name") or "")[:120],
            "game_winner_name": str(raw.get("game_winner_name") or "")[:120],
        }

        with self._lock:
            previous = dict(self._previous) if self._previous else None
            self._previous = dict(state)
            self._status.update({
                "extension_connected": True,
                "authoritative": True,
                "route": "match",
                "match_id": state["match_id"] or None,
                "variant": state["variant"],
                "match_finished": state["finished"],
                "game_finished": state["game_finished"],
                "current_player": state["current_player_name"] or None,
                "current_player_index": state["player"],
                "current_player_is_bot": state["current_player_is_bot"],
                "current_target": state["current_target"] or None,
                "match_active": not state["finished"],
            })

        if previous is None:
            target_mode = "idle" if state["finished"] else "active"
            self._on_base_mode(target_mode, "Autodarts match state synchronised", False)
            if state["current_target"] and not state["finished"]:
                self._record_and_dispatch(self._target_event(state))
            logger.info("Autodarts game bridge synchronised: %s (%s)", state["match_id"], target_mode)
            return

        if state["match_id"] and previous.get("match_id") and state["match_id"] != previous.get("match_id"):
            self._record_and_dispatch(self._event("match_start", state))

        if previous.get("game_finished") and not state["game_finished"] and not state["finished"]:
            self._record_and_dispatch(self._event("game_start", state))

        turn_changed = bool(state["turn_id"] and state["turn_id"] != previous.get("turn_id"))
        player_changed = state["player"] >= 0 and state["player"] != previous.get("player")
        if (turn_changed or player_changed) and state["turn_throw_count"] == 0 and not state["finished"]:
            self._record_and_dispatch(self._event("turn_start", state))
            self._on_event({
                "kind": "player_turn",
                "player": state["current_player_name"],
                "player_index": state["player"],
                "is_bot": state["current_player_is_bot"],
                "source": "autodarts_game_bridge",
            })
            if state["current_player_is_bot"]:
                self._record_and_dispatch(self._event("bot_turn", state))

        target_changed = bool(state["current_target"] and state["current_target"] != previous.get("current_target"))
        if state["current_target"] and not state["finished"] and (target_changed or turn_changed or player_changed):
            self._record_and_dispatch(self._target_event(state))

        if state["turn_busted"] and not previous.get("turn_busted"):
            self._record_and_dispatch(self._event("bust", state))

        prev_game_winner = _int(previous.get("game_winner"), -1)
        prev_match_winner = _int(previous.get("winner"), -1)
        matchshot = state["winner"] >= 0 and (prev_match_winner < 0 or state["winner"] != prev_match_winner)
        gameshot = state["game_winner"] >= 0 and (prev_game_winner < 0 or state["game_winner"] != prev_game_winner)

        if matchshot:
            self._record_and_dispatch(self._event("matchshot", state, winner=True))
            self._on_base_mode("idle", "Autodarts match finished", True)
        elif gameshot:
            self._record_and_dispatch(self._event("gameshot", state, game_winner=True))
            self._on_base_mode("active", "Autodarts leg finished", True)
        else:
            target_mode = "idle" if state["finished"] else "active"
            self._on_base_mode(target_mode, "Autodarts match active" if target_mode == "active" else "Autodarts match finished", False)

    def _target_event(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "kind": "game_target",
            "target": state.get("current_target"),
            "player": state.get("current_player_name") or "",
            "player_index": state.get("player", -1),
            "variant": state.get("variant"),
            "match_id": state.get("match_id"),
            "source": "autodarts_game_bridge",
        }

    def _event(self, name: str, state: Dict[str, Any], *, winner: bool = False, game_winner: bool = False) -> Dict[str, Any]:
        player = state.get("current_player_name") or ""
        player_index = state.get("player", -1)
        if winner:
            player = state.get("winner_name") or player
            player_index = state.get("winner", player_index)
        elif game_winner:
            player = state.get("game_winner_name") or player
            player_index = state.get("game_winner", player_index)
        return {
            "kind": "game_event",
            "event": name,
            "player": player,
            "player_index": player_index,
            "is_bot": bool(state.get("current_player_is_bot", False)),
            "last_dart": state.get("last_dart") or None,
            "turn_points": state.get("turn_points", 0),
            "variant": state.get("variant"),
            "match_id": state.get("match_id"),
            "source": "autodarts_game_bridge",
        }

    def _record_and_dispatch(self, event: Dict[str, Any]) -> None:
        row = {
            "time": time.strftime("%H:%M:%S"),
            "event": event.get("event") or event.get("kind"),
            "player": event.get("player"),
            "last_dart": event.get("last_dart"),
            "target": event.get("target"),
            "source": event.get("source"),
        }
        with self._lock:
            self._events.append(row)
            self._status["last_game_event"] = row["event"]
            self._status["last_event_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        detail = f" ({row['player']})" if row.get("player") else ""
        if row.get("target"):
            detail += f" target={row['target']}"
        logger.info("Autodarts game event: %s%s", row["event"], detail)
        self._on_event(event)

    def _leave_match(self, reason: str) -> None:
        with self._lock:
            was_authoritative = bool(self._status.get("authoritative"))
            self._previous = None
            self._status["authoritative"] = False
            self._status["match_active"] = False
            self._status["match_id"] = None
            self._status["variant"] = None
            self._status["current_player"] = None
            self._status["current_player_index"] = None
            self._status["current_target"] = None
        if was_authoritative:
            try:
                self._on_base_mode("idle", reason, True)
            except Exception:
                logger.exception("Failed to set Idle while leaving Autodarts match")

    def _monitor(self) -> None:
        while not self._stop.wait(1.0):
            try:
                settings = self._settings_provider()
                timeout = float(settings.get("bridge_timeout_seconds", 10.0))
            except (TypeError, ValueError):
                timeout = 10.0
            timeout = max(5.0, min(60.0, timeout))
            with self._lock:
                last = float(self._status.get("last_heartbeat_monotonic", 0.0) or 0.0)
                connected = bool(self._status.get("extension_connected"))
                age = time.monotonic() - last if last else 999999.0
                if connected and age > timeout:
                    self._status["extension_connected"] = False
                    self._status["authoritative"] = False
                    self._status["match_active"] = False
                    self._status["current_target"] = None
                    self._previous = None
                    logger.warning("Autodarts game bridge heartbeat timed out; Board Manager fallback re-enabled")
