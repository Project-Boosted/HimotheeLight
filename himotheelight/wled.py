from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any, Dict, List
from urllib.parse import urlparse


class WLEDError(RuntimeError):
    pass


def normalize_host(host: str) -> str:
    value = (host or "").strip().rstrip("/")
    if not value:
        raise WLEDError("WLED host/IP is required")
    if "://" not in value:
        value = "http://" + value
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise WLEDError("Enter a valid WLED IP/hostname, for example 192.168.1.50")
    return f"{parsed.scheme}://{parsed.netloc}"


def _request_json(base_url: str, path: str, *, method: str = "GET", payload: Dict[str, Any] | None = None,
                  timeout: float = 2.5) -> Any:
    url = normalize_host(base_url) + path
    data = None
    headers = {"Accept": "application/json", "User-Agent": "HimotheeLight/0.8.0"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            if not raw.strip():
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        raise WLEDError(f"WLED returned HTTP {exc.code}{': ' + detail if detail else ''}") from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise WLEDError(f"Could not reach WLED at {normalize_host(base_url)}: {reason}") from exc
    except json.JSONDecodeError as exc:
        raise WLEDError("WLED returned an invalid JSON response") from exc


def _parse_presets(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return []
    presets = []
    for key, value in raw.items():
        try:
            preset_id = int(key)
        except (TypeError, ValueError):
            continue
        if preset_id <= 0 or not isinstance(value, dict):
            continue
        name = str(value.get("n") or f"Preset {preset_id}")
        presets.append({"id": preset_id, "name": name})
    presets.sort(key=lambda item: item["id"])
    return presets


def _segments_from_state(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    result = []
    segments = state.get("seg") if isinstance(state, dict) else None
    if not isinstance(segments, list):
        return result
    for seg in segments:
        if not isinstance(seg, dict) or "id" not in seg:
            continue
        result.append({
            "id": int(seg.get("id", 0)),
            "name": seg.get("n") or f"Segment {seg.get('id', 0)}",
            "start": int(seg.get("start", 0)),
            "stop": int(seg.get("stop", 0)),
            "on": bool(seg.get("on", True)),
            "effect_id": int(seg.get("fx", 0)),
            "palette_id": int(seg.get("pal", 0)),
        })
    return result


def probe(base_url: str) -> Dict[str, Any]:
    base = normalize_host(base_url)
    root = _request_json(base, "/json")
    if not isinstance(root, dict):
        raise WLEDError("Unexpected response from WLED /json endpoint")
    state = root.get("state") if isinstance(root.get("state"), dict) else {}
    info = root.get("info") if isinstance(root.get("info"), dict) else {}
    effects_raw = root.get("effects") if isinstance(root.get("effects"), list) else []
    palettes_raw = root.get("palettes") if isinstance(root.get("palettes"), list) else []

    effects = []
    for idx, name in enumerate(effects_raw):
        label = str(name or "").strip()
        if not label or label in {"-", "RSVD"}:
            continue
        effects.append({"id": idx, "name": label})
    palettes = [{"id": idx, "name": str(name or f"Palette {idx}")} for idx, name in enumerate(palettes_raw)]

    try:
        presets_raw = _request_json(base, "/presets.json")
        presets = _parse_presets(presets_raw)
    except WLEDError:
        presets = []

    leds = info.get("leds") if isinstance(info.get("leds"), dict) else {}
    return {
        "base_url": base,
        "name": info.get("name") or info.get("brand") or "WLED",
        "version": info.get("ver") or "unknown",
        "architecture": info.get("arch") or "unknown",
        "ip": info.get("ip") or urlparse(base).hostname,
        "led_count": leds.get("count") or leds.get("lc") or None,
        "max_segments": leds.get("maxseg") or None,
        "brightness": state.get("bri"),
        "on": state.get("on"),
        "segments": _segments_from_state(state),
        "effects": effects,
        "palettes": palettes,
        "presets": presets,
        "state": state,
    }


def get_state(base_url: str) -> Dict[str, Any]:
    state = _request_json(normalize_host(base_url), "/json/state")
    if not isinstance(state, dict):
        raise WLEDError("Unexpected WLED state response")
    return state


def send_state(base_url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise WLEDError("WLED state payload must be an object")
    result = _request_json(normalize_host(base_url), "/json/state", method="POST", payload=payload)
    return result if isinstance(result, dict) else {"success": True}


def _clamp_int(value: Any, minimum: int, maximum: int, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(maximum, parsed))


def _clean_color(value: Any, fallback: List[int]) -> List[int]:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return list(fallback)
    return [_clamp_int(value[i], 0, 255, fallback[i]) for i in range(3)]


def build_mode_payload(mode: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(mode, dict):
        raise WLEDError("Mode configuration is invalid")

    on = bool(mode.get("on", True))
    transition_ms = _clamp_int(mode.get("transition_ms"), 0, 65000, 300)
    payload: Dict[str, Any] = {
        "on": on,
        "tt": min(65535, round(transition_ms / 100)),
    }
    if not on:
        return payload

    payload["bri"] = _clamp_int(mode.get("brightness"), 1, 255, 128)
    source = str(mode.get("source") or "direct").lower()
    if source == "preset":
        preset_id = _clamp_int(mode.get("preset_id"), 1, 250, 1)
        payload["ps"] = preset_id
        return payload

    colors = mode.get("colors") if isinstance(mode.get("colors"), list) else []
    fallbacks = [[138, 43, 226], [255, 255, 255], [0, 0, 0]]
    clean_colors = [
        _clean_color(colors[i] if i < len(colors) else None, fallbacks[i]) for i in range(3)
    ]
    segment: Dict[str, Any] = {
        "fx": _clamp_int(mode.get("effect_id"), 0, 255, 0),
        "pal": _clamp_int(mode.get("palette_id"), 0, 255, 0),
        "sx": _clamp_int(mode.get("speed"), 0, 255, 128),
        "ix": _clamp_int(mode.get("intensity"), 0, 255, 128),
        "col": clean_colors,
    }
    seg_id = mode.get("segment_id")
    if seg_id is None or seg_id == "" or str(seg_id).lower() == "main":
        payload["seg"] = segment
    else:
        segment["id"] = _clamp_int(seg_id, 0, 255, 0)
        payload["seg"] = [segment]
    return payload


def apply_mode(base_url: str, mode: Dict[str, Any]) -> Dict[str, Any]:
    return send_state(base_url, build_mode_payload(mode))
