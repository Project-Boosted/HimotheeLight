from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict

from .config_store import ConfigStore
from .lighting import LightingStateManager

logger = logging.getLogger("himotheelight")


class AutodartsActivityController:
    """Owns inactivity-idle latching around the normal Autodarts base-state flow.

    A stalled match is intentionally different from a finished match: after the
    configured no-dart timeout the lights move to Idle, but the match is still
    considered active. Subsequent ordinary match-state updates and darts do not
    wake the base lighting. Only a genuine new game/match activation (or a new
    Board Manager running session when the browser bridge is unavailable) clears
    the latch.
    """

    def __init__(self, store: ConfigStore, lighting: LightingStateManager) -> None:
        self.store = store
        self.lighting = lighting
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._game_active = False
        self._inactivity_latched = False
        self._last_dart_monotonic: float | None = None
        self._last_dart_at: str | None = None
        self._active_since_monotonic: float | None = None
        self._active_since_at: str | None = None
        self._latched_at: str | None = None
        self._last_reason = "Waiting for an active game"

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._monitor, name="HimotheeLight-Inactivity", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=1.5)

    def _settings(self) -> Dict[str, Any]:
        settings = self.store.get().get("autodarts", {})
        return settings if isinstance(settings, dict) else {}

    def snapshot(self) -> Dict[str, Any]:
        settings = self._settings()
        enabled = bool(settings.get("inactivity_idle_enabled", True))
        try:
            timeout_seconds = max(10.0, min(3600.0, float(settings.get("inactivity_idle_seconds", 60.0))))
        except (TypeError, ValueError):
            timeout_seconds = 60.0
        with self._lock:
            now = time.monotonic()
            anchor = self._last_dart_monotonic or self._active_since_monotonic
            remaining = None
            if enabled and self._game_active and not self._inactivity_latched and anchor is not None:
                remaining = max(0, int((timeout_seconds - (now - anchor)) * 1000))
            return {
                "enabled": enabled,
                "timeout_seconds": timeout_seconds,
                "game_active": self._game_active,
                "inactivity_latched": self._inactivity_latched,
                "last_dart_at": self._last_dart_at,
                "active_since_at": self._active_since_at,
                "latched_at": self._latched_at,
                "remaining_ms": remaining,
                "reason": self._last_reason,
            }

    def settings_changed(self) -> None:
        """Apply immediate consequences of changing inactivity settings."""
        settings = self._settings()
        enabled = bool(settings.get("inactivity_idle_enabled", True)) and bool(settings.get("auto_lighting", True))
        should_restore = False
        with self._lock:
            if not enabled and self._inactivity_latched:
                self._inactivity_latched = False
                self._latched_at = None
                should_restore = self._game_active
                self._last_reason = "Inactivity Idle disabled"
                if should_restore:
                    self._active_since_monotonic = time.monotonic()
                    self._active_since_at = time.strftime("%Y-%m-%d %H:%M:%S")
                    self._last_dart_monotonic = None
        if should_restore:
            try:
                self.lighting.apply_base_mode(
                    "active", source="inactivity", reason="Inactivity Idle disabled", force=True
                )
            except Exception:
                logger.exception("Failed to restore Active after disabling inactivity Idle")

    def note_dart(self) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            self._last_dart_monotonic = time.monotonic()
            self._last_dart_at = stamp
            if self._inactivity_latched:
                self._last_reason = "Dart received, but inactivity Idle remains latched until a new game"
            else:
                self._last_reason = "Inactivity timer reset by a new dart"

    def note_game_event(self, event_name: str) -> None:
        name = str(event_name or "").strip().lower()
        if name in {"match_start", "game_start"}:
            self.new_game(f"Autodarts {name.replace('_', ' ')}")

    def new_game(self, reason: str) -> None:
        now = time.monotonic()
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            was_latched = self._inactivity_latched
            self._game_active = True
            self._inactivity_latched = False
            self._active_since_monotonic = now
            self._active_since_at = stamp
            self._last_dart_monotonic = None
            self._last_dart_at = None
            self._latched_at = None
            self._last_reason = reason
        if was_latched:
            logger.info("Inactivity Idle cleared: %s", reason)

    def board_base_mode(self, mode: str, reason: str) -> Dict[str, Any]:
        mode = str(mode).lower().strip()
        new_session = mode == "active" and reason in {
            "Autodarts board started",
            "Autodarts state synchronised",
            "Autodarts running state changed",
        }
        return self._apply(mode, reason, source="board", preserve_trigger=False, new_session=new_session)

    def game_base_mode(self, mode: str, reason: str, preserve_trigger: bool = False) -> Dict[str, Any]:
        mode = str(mode).lower().strip()
        # A freshly synchronised match means the browser has entered/re-entered
        # an active match context. Ordinary "match active" snapshots must not
        # clear an inactivity latch.
        new_session = mode == "active" and reason == "Autodarts match state synchronised"
        return self._apply(mode, reason, source="game", preserve_trigger=preserve_trigger, new_session=new_session)

    def _apply(self, mode: str, reason: str, *, source: str, preserve_trigger: bool, new_session: bool) -> Dict[str, Any]:
        if mode not in {"idle", "active"}:
            raise ValueError("Base mode must be idle or active")

        if mode == "active":
            if new_session:
                self.new_game(reason)
            else:
                with self._lock:
                    if not self._game_active:
                        now = time.monotonic()
                        self._game_active = True
                        self._active_since_monotonic = now
                        self._active_since_at = time.strftime("%Y-%m-%d %H:%M:%S")
                        self._last_dart_monotonic = None
                    latched = self._inactivity_latched
                    if not latched:
                        self._last_reason = reason
                if latched:
                    logger.debug("Suppressed Active base update while inactivity Idle is latched: %s", reason)
                    return {
                        "ok": True,
                        "skipped": True,
                        "inactivity_latched": True,
                        "mode": "idle",
                        "requested_mode": "active",
                        "reason": reason,
                        "results": [],
                    }
        else:
            with self._lock:
                self._game_active = False
                self._inactivity_latched = False
                self._active_since_monotonic = None
                self._active_since_at = None
                self._last_dart_monotonic = None
                self._latched_at = None
                self._last_reason = reason

        if source == "game":
            return self.lighting.autodarts_game_apply(mode, reason, preserve_trigger)
        return self.lighting.autodarts_apply(mode, reason)

    def evaluate_now(self) -> bool:
        """Evaluate the inactivity timeout once. Returns True when Idle latched."""
        settings = self._settings()
        if not settings.get("auto_lighting", True) or not settings.get("inactivity_idle_enabled", True):
            return False
        try:
            timeout_seconds = max(10.0, min(3600.0, float(settings.get("inactivity_idle_seconds", 60.0))))
        except (TypeError, ValueError):
            timeout_seconds = 60.0

        should_idle = False
        with self._lock:
            if self._game_active and not self._inactivity_latched:
                anchor = self._last_dart_monotonic or self._active_since_monotonic
                if anchor is not None and time.monotonic() - anchor >= timeout_seconds:
                    self._inactivity_latched = True
                    self._latched_at = time.strftime("%Y-%m-%d %H:%M:%S")
                    self._last_reason = f"No new dart for {int(timeout_seconds)} seconds"
                    should_idle = True

        if should_idle:
            logger.info("No dart for %.0fs during active game -> latching Idle", timeout_seconds)
            try:
                self.lighting.apply_base_mode(
                    "idle",
                    source="inactivity",
                    reason=f"No new dart for {int(timeout_seconds)} seconds",
                    force=True,
                )
            except Exception:
                logger.exception("Failed to apply inactivity Idle")
            return True
        return False

    def _monitor(self) -> None:
        while not self._stop.wait(0.5):
            self.evaluate_now()
