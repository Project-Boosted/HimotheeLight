from __future__ import annotations

import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from typing import Any, Callable, Deque, Dict, Optional

logger = logging.getLogger("himotheelight")

try:
    import websocket  # type: ignore
except Exception:  # pragma: no cover - exercised on machines missing dependency
    websocket = None


class AutodartsError(RuntimeError):
    pass


def normalize_board_host(value: str) -> str:
    host = (value or "127.0.0.1").strip()
    host = host.removeprefix("http://").removeprefix("https://").removeprefix("ws://").removeprefix("wss://")
    host = host.split("/", 1)[0].strip()
    if not host:
        raise AutodartsError("Autodarts Board Manager host is required")
    # Strip an accidentally supplied port; HimotheeLight stores it separately.
    if host.startswith("[") and "]" in host:
        return host
    if host.count(":") == 1:
        maybe_host, maybe_port = host.rsplit(":", 1)
        if maybe_port.isdigit():
            host = maybe_host
    return host


def _port(value: Any) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise AutodartsError("Autodarts port must be a number") from exc
    if not 1 <= port <= 65535:
        raise AutodartsError("Autodarts port must be between 1 and 65535")
    return port


def _http_state(host: str, port: int, timeout: float = 2.0) -> Dict[str, Any]:
    url = f"http://{normalize_board_host(host)}:{_port(port)}/api/state"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "HimotheeLight/0.8.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        raise AutodartsError(f"Board Manager returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise AutodartsError(f"Could not reach Autodarts Board Manager at {host}:{port}: {reason}") from exc
    except json.JSONDecodeError as exc:
        raise AutodartsError("Board Manager returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise AutodartsError("Board Manager returned an unexpected state response")
    return data


def _clean_throw(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    segment = raw.get("segment")
    if not isinstance(segment, dict):
        return None
    try:
        multiplier = int(segment.get("multiplier", 0))
        number = int(segment.get("number", 0))
    except (TypeError, ValueError):
        multiplier, number = 0, 0
    name = str(segment.get("name") or "MISS")
    return {
        "name": name,
        "score": multiplier * number,
        "multiplier": multiplier,
        "number": number,
        "bed": segment.get("bed"),
    }


class AutodartsClient:
    """Background client for the local Autodarts Board Manager event stream."""

    def __init__(
        self,
        settings_provider: Callable[[], Dict[str, Any]],
        on_base_mode: Callable[[str, str], Any],
        on_event: Callable[[Dict[str, Any]], Any] | None = None,
        allow_base_mode: Callable[[], bool] | None = None,
    ):
        self._settings_provider = settings_provider
        self._on_base_mode = on_base_mode
        self._on_event = on_event
        self._allow_base_mode = allow_base_mode or (lambda: True)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._desired_connected = False
        self._thread: Optional[threading.Thread] = None
        self._ws = None
        self._events: Deque[Dict[str, Any]] = deque(maxlen=120)
        self._first_state_after_connect = True
        self._status: Dict[str, Any] = {
            "transport_connected": False,
            "connecting": False,
            "board_connected": False,
            "running": False,
            "status": "Unknown",
            "event": None,
            "num_throws": 0,
            "throws": [],
            "last_throw": None,
            "last_message_at": None,
            "connected_at": None,
            "error": None,
            "reconnects": 0,
            "derived_mode": "idle",
            "dependency_ok": websocket is not None,
        }

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            settings = self._settings_provider()
            self._desired_connected = bool(settings.get("enabled", True) and settings.get("auto_connect", True))
            self._stop.clear()
            self._thread = threading.Thread(target=self._run_loop, name="HimotheeLight-Autodarts", daemon=True)
            self._thread.start()
            self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._desired_connected = False
        self._wake.set()
        self._close_ws()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)

    def connect(self) -> None:
        with self._lock:
            self._desired_connected = True
            self._status["error"] = None
        self._wake.set()

    def disconnect(self, *, manual: bool = True) -> None:
        with self._lock:
            self._desired_connected = False
            self._status["connecting"] = False
            if manual:
                self._status["error"] = None
        self._close_ws()
        self._wake.set()

    def restart(self) -> None:
        with self._lock:
            desired = self._desired_connected
        self._close_ws()
        if desired:
            self._wake.set()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            result = dict(self._status)
            result["throws"] = list(self._status.get("throws") or [])
            result["desired_connected"] = self._desired_connected
            result["events"] = list(self._events)
            return result

    def events(self, limit: int = 60) -> list[Dict[str, Any]]:
        limit = max(1, min(120, int(limit)))
        with self._lock:
            return list(self._events)[-limit:]

    def test_connection(self, host: str | None = None, port: int | None = None) -> Dict[str, Any]:
        settings = self._settings_provider()
        test_host = normalize_board_host(host if host is not None else settings.get("host", "127.0.0.1"))
        test_port = _port(port if port is not None else settings.get("port", 3180))
        state = _http_state(test_host, test_port)
        data = state.get("data") if state.get("type") == "state" and isinstance(state.get("data"), dict) else state
        if not isinstance(data, dict):
            data = state
        return {
            "ok": True,
            "host": test_host,
            "port": test_port,
            "connected": bool(data.get("connected", False)),
            "running": bool(data.get("running", False)),
            "status": data.get("status") or "Unknown",
            "event": data.get("event"),
            "num_throws": int(data.get("numThrows", 0) or 0),
        }

    def simulate(self, event_name: str) -> Dict[str, Any]:
        name = str(event_name or "").strip().lower()
        if name in {"started", "start", "active"}:
            data = {"connected": True, "running": True, "status": "Throw", "event": "Started", "numThrows": 0}
        elif name in {"stopped", "stop", "idle"}:
            data = {"connected": True, "running": False, "status": "Stopped", "event": "Stopped", "numThrows": 0}
        elif name in {"throw", "dart", "t20", "throw_detected"}:
            data = {
                "connected": True,
                "running": True,
                "status": "Throw",
                "event": "Throw detected",
                "numThrows": 1,
                "throws": [{"segment": {"bed": "Triple", "multiplier": 3, "number": 20, "name": "T20"}}],
            }
        elif name in {"three_darts", "3_darts", "third_dart"}:
            data = {
                "connected": True,
                "running": True,
                "status": "Takeout",
                "event": "Throw detected",
                "numThrows": 3,
                "throws": [
                    {"segment": {"bed": "SingleOuter", "multiplier": 1, "number": 20, "name": "S20"}},
                    {"segment": {"bed": "SingleOuter", "multiplier": 1, "number": 20, "name": "S20"}},
                    {"segment": {"bed": "Triple", "multiplier": 3, "number": 20, "name": "T20"}},
                ],
            }
        elif name in {"starting"}:
            data = {"connected": True, "running": False, "status": "Starting", "event": "Starting", "numThrows": 0}
        elif name in {"stopping"}:
            data = {"connected": True, "running": True, "status": "Stopping", "event": "Stopping", "numThrows": 0}
        elif name in {"takeout", "takeout_started", "takeout started"}:
            data = {"connected": True, "running": True, "status": "Takeout in progress", "event": "Takeout started", "numThrows": 0}
        elif name in {"takeout_finished", "takeout finished"}:
            data = {"connected": True, "running": True, "status": "Throw", "event": "Takeout finished", "numThrows": 0}
        elif name in {"manual_reset", "manual reset", "reset"}:
            data = {"connected": True, "running": True, "status": "Throw", "event": "Manual reset", "numThrows": 0}
        elif name in {"calibration_started", "calibration started"}:
            data = {"connected": True, "running": True, "status": "Calibrating", "event": "Calibration started", "numThrows": 0}
        elif name in {"calibration_finished", "calibration finished"}:
            data = {"connected": True, "running": True, "status": "Throw", "event": "Calibration finished", "numThrows": 0}
        elif name in {"calibration_failed", "calibration failed"}:
            data = {"connected": True, "running": True, "status": "Throw", "event": "Calibration failed", "numThrows": 0}
        else:
            raise AutodartsError("Unknown Board Manager simulation event")
        self._handle_state(data, simulated=True)
        return self.snapshot()

    def _settings(self) -> Dict[str, Any]:
        settings = self._settings_provider()
        settings.setdefault("host", "127.0.0.1")
        settings.setdefault("port", 3180)
        settings.setdefault("enabled", True)
        settings.setdefault("auto_connect", True)
        settings.setdefault("auto_lighting", True)
        settings.setdefault("idle_on_disconnect", True)
        settings.setdefault("reconnect_seconds", 2.0)
        return settings

    def _ws_url(self) -> str:
        settings = self._settings()
        host = normalize_board_host(str(settings.get("host") or "127.0.0.1"))
        port = _port(settings.get("port", 3180))
        return f"ws://{host}:{port}/api/events?type=state"

    def _close_ws(self) -> None:
        ws = None
        with self._lock:
            ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def _run_loop(self) -> None:
        if websocket is None:
            with self._lock:
                self._status["error"] = "websocket-client is not installed. Run Setup HimotheeLight.bat again."
            logger.error(self._status["error"])

        while not self._stop.is_set():
            with self._lock:
                desired = self._desired_connected
            settings = self._settings()
            if not desired or not settings.get("enabled", True):
                self._wake.wait(0.5)
                self._wake.clear()
                continue
            if websocket is None:
                self._wake.wait(2.0)
                self._wake.clear()
                continue

            url = self._ws_url()
            with self._lock:
                self._status["connecting"] = True
                self._status["error"] = None
                self._first_state_after_connect = True
            logger.info("Connecting to Autodarts Board Manager at %s", url)

            def on_open(ws_app):
                with self._lock:
                    self._status["transport_connected"] = True
                    self._status["connecting"] = False
                    self._status["connected_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    self._status["error"] = None
                logger.info("Autodarts event stream connected")

            def on_message(ws_app, message):
                try:
                    parsed = json.loads(message)
                    if not isinstance(parsed, dict) or parsed.get("type") != "state":
                        return
                    data = parsed.get("data")
                    if isinstance(data, dict):
                        self._handle_state(data)
                except Exception as exc:
                    logger.warning("Could not parse Autodarts event: %s", exc)

            def on_error(ws_app, error):
                message = str(error)
                with self._lock:
                    self._status["error"] = message
                if not self._stop.is_set() and self._desired_connected:
                    logger.warning("Autodarts WebSocket error: %s", message)

            def on_close(ws_app, code, message):
                was_connected = False
                with self._lock:
                    was_connected = bool(self._status.get("transport_connected"))
                    self._status["transport_connected"] = False
                    self._status["connecting"] = False
                    self._status["board_connected"] = False
                if was_connected:
                    logger.info("Autodarts event stream disconnected%s", f" ({code}: {message})" if code or message else "")
                if self._desired_connected and self._settings().get("idle_on_disconnect", True) and self._allow_base_mode():
                    try:
                        self._on_base_mode("idle", "Autodarts disconnected")
                    except Exception:
                        logger.exception("Failed to apply Idle after Autodarts disconnect")

            ws_app = websocket.WebSocketApp(url, on_open=on_open, on_message=on_message, on_error=on_error, on_close=on_close)
            with self._lock:
                self._ws = ws_app
            try:
                # Board Manager is local/LAN traffic; avoid inheriting a desktop proxy for it.
                ws_app.run_forever(ping_interval=20, ping_timeout=8, http_proxy_host=None)
            except Exception as exc:
                with self._lock:
                    self._status["error"] = str(exc)
                    self._status["transport_connected"] = False
                    self._status["connecting"] = False
                logger.warning("Autodarts connection ended: %s", exc)
            finally:
                with self._lock:
                    if self._ws is ws_app:
                        self._ws = None
                    should_reconnect = self._desired_connected and not self._stop.is_set()
                    if should_reconnect:
                        self._status["reconnects"] = int(self._status.get("reconnects", 0)) + 1

            if should_reconnect:
                try:
                    wait = float(settings.get("reconnect_seconds", 2.0))
                except (TypeError, ValueError):
                    wait = 2.0
                self._wake.wait(max(0.5, min(30.0, wait)))
                self._wake.clear()

    def _handle_state(self, data: Dict[str, Any], simulated: bool = False) -> None:
        event = str(data.get("event") or "State")
        status = str(data.get("status") or "Unknown")
        running = bool(data.get("running", False))
        board_connected = bool(data.get("connected", False))
        try:
            num_throws = int(data.get("numThrows", 0) or 0)
        except (TypeError, ValueError):
            num_throws = 0
        throws = []
        for raw in data.get("throws") or []:
            throw = _clean_throw(raw)
            if throw:
                throws.append(throw)
        event_throw = throws[-1] if throws else None
        stamp = time.strftime("%H:%M:%S")

        event_row = {
            "time": stamp,
            "event": event,
            "status": status,
            "running": running,
            "num_throws": num_throws,
            "dart": event_throw.get("name") if event_throw and event == "Throw detected" else None,
            "score": event_throw.get("score") if event_throw and event == "Throw detected" else None,
            "simulated": bool(simulated),
        }

        with self._lock:
            first_state = self._first_state_after_connect
            self._first_state_after_connect = False
            previous_num_throws = int(self._status.get("num_throws", 0) or 0)
            persistent_last_throw = event_throw or self._status.get("last_throw")
            self._status.update({
                "board_connected": board_connected,
                "running": running,
                "status": status,
                "event": event,
                "num_throws": num_throws,
                "throws": throws,
                "last_throw": persistent_last_throw,
                "last_message_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "derived_mode": "active" if running else "idle",
            })
            self._events.append(event_row)

        if event == "Throw detected" and event_throw:
            logger.info("Autodarts: %s (%s) — throw %d", event_throw["name"], event_throw["score"], num_throws)
        elif event not in {"State", ""}:
            logger.info("Autodarts event: %s (%s)", event, status)

        # Base-state transitions. Explicit Started/Stopped messages win; the first
        # state after connection also synchronises us if HimotheeLight starts mid-game.
        target: Optional[str] = None
        reason: Optional[str] = None
        if event == "Started":
            target, reason = "active", "Autodarts board started"
        elif event == "Stopped":
            target, reason = "idle", "Autodarts board stopped"
        elif first_state:
            target = "active" if running else "idle"
            reason = "Autodarts state synchronised"
        else:
            # Some Board Manager builds update running without emitting a Started/
            # Stopped frame. Keep the lighting consistent with that state.
            current_derived = "active" if running else "idle"
            previous = None
            with self._lock:
                # The prior event is the second-last entry if available.
                if len(self._events) >= 2:
                    previous = "active" if self._events[-2].get("running") else "idle"
            if previous and previous != current_derived:
                target, reason = current_derived, "Autodarts running state changed"

        if target and reason and self._allow_base_mode():
            try:
                self._on_base_mode(target, reason)
            except Exception:
                logger.exception("Failed to apply %s mode from Autodarts", target)

        # Trigger matching runs after base-state synchronisation. This matters on
        # the first frame after reconnect: a mid-visit throw should not be
        # immediately overwritten by the initial Active-state sync.
        if self._on_event is not None:
            try:
                self._on_event({
                    "event": event,
                    "status": status,
                    "running": running,
                    "board_connected": board_connected,
                    "num_throws": num_throws,
                    "previous_num_throws": previous_num_throws,
                    "throws": throws,
                    "last_throw": event_throw if event == "Throw detected" else None,
                    "simulated": bool(simulated),
                })
            except Exception:
                logger.exception("Trigger engine failed while handling Autodarts event")
