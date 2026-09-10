from __future__ import annotations

import colorsys
import json
import socket
import ssl
import urllib.error
import urllib.request
from typing import Any, Dict, List
from urllib.parse import urlparse


class HueError(RuntimeError):
    pass


def normalize_bridge_host(host: str) -> str:
    value = str(host or "").strip().rstrip("/")
    if not value:
        raise HueError("Hue Bridge IP/hostname is required")
    if "://" not in value:
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise HueError("Enter a valid Hue Bridge IP/hostname, for example 192.168.1.20")
    return f"{parsed.scheme}://{parsed.netloc}"


def _local_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _request_json(base_url: str, path: str, *, method: str = "GET", payload: Dict[str, Any] | None = None,
                  application_key: str | None = None, timeout: float = 3.0) -> Any:
    base = normalize_bridge_host(base_url)
    url = base + path
    headers = {"Accept": "application/json", "User-Agent": "HimotheeLight/0.8.0"}
    if application_key:
        headers["hue-application-key"] = str(application_key)
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    kwargs: Dict[str, Any] = {"timeout": timeout}
    if base.startswith("https://"):
        kwargs["context"] = _local_ssl_context()
    try:
        with urllib.request.urlopen(request, **kwargs) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        raise HueError(f"Hue Bridge returned HTTP {exc.code}{': ' + detail if detail else ''}") from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise HueError(f"Could not reach Hue Bridge at {base}: {reason}") from exc
    except json.JSONDecodeError as exc:
        raise HueError("Hue Bridge returned invalid JSON") from exc


def discover_bridges(timeout: float = 4.0) -> List[Dict[str, Any]]:
    req = urllib.request.Request(
        "https://discovery.meethue.com/",
        headers={"Accept": "application/json", "User-Agent": "HimotheeLight/0.8.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise HueError(f"Hue Bridge discovery failed: {exc}") from exc
    if not isinstance(raw, list):
        raise HueError("Hue discovery returned an unexpected response")
    result = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        ip = str(row.get("internalipaddress") or "").strip()
        if not ip:
            continue
        result.append({
            "bridge_id": str(row.get("id") or ""),
            "host": normalize_bridge_host(ip),
            "ip": ip,
            "port": row.get("port"),
        })
    return result


def pair_bridge(base_url: str, device_name: str = "HimotheeLight") -> Dict[str, Any]:
    payload = {"devicetype": f"HimotheeLight#{str(device_name or 'Windows PC')[:24]}", "generateclientkey": True}
    raw = _request_json(base_url, "/api", method="POST", payload=payload, timeout=5.0)
    if not isinstance(raw, list) or not raw:
        raise HueError("Hue Bridge returned an unexpected pairing response")
    for row in raw:
        if not isinstance(row, dict):
            continue
        success = row.get("success")
        if isinstance(success, dict) and success.get("username"):
            return {"application_key": str(success["username"]), "client_key": str(success.get("clientkey") or "")}
        error = row.get("error")
        if isinstance(error, dict):
            if int(error.get("type", 0) or 0) == 101:
                raise HueError("Press the physical button on the Hue Bridge, then click Pair again within 30 seconds")
            raise HueError(str(error.get("description") or "Hue Bridge pairing failed"))
    raise HueError("Hue Bridge pairing failed")


def _v2_data(raw: Any, what: str) -> List[Dict[str, Any]]:
    if not isinstance(raw, dict):
        raise HueError(f"Unexpected Hue {what} response")
    errors = raw.get("errors")
    if isinstance(errors, list) and errors:
        description = errors[0].get("description") if isinstance(errors[0], dict) else str(errors[0])
        raise HueError(f"Hue API error: {description}")
    data = raw.get("data")
    if not isinstance(data, list):
        raise HueError(f"Unexpected Hue {what} data")
    return [x for x in data if isinstance(x, dict)]


def probe_bridge(base_url: str, application_key: str) -> Dict[str, Any]:
    bridge_rows = _v2_data(_request_json(base_url, "/clip/v2/resource/bridge", application_key=application_key), "bridge")
    devices = _v2_data(_request_json(base_url, "/clip/v2/resource/device", application_key=application_key), "device")
    lights = list_lights(base_url, application_key)
    bridge = bridge_rows[0] if bridge_rows else {}
    metadata = bridge.get("metadata") if isinstance(bridge.get("metadata"), dict) else {}
    product_data = bridge.get("product_data") if isinstance(bridge.get("product_data"), dict) else {}
    return {
        "base_url": normalize_bridge_host(base_url),
        "bridge_resource_id": bridge.get("id"),
        "name": metadata.get("name") or product_data.get("product_name") or "Philips Hue Bridge",
        "model": product_data.get("model_id") or product_data.get("product_name") or "Hue Bridge",
        "device_count": len(devices),
        "light_count": len(lights),
        "lights": lights,
    }


def list_lights(base_url: str, application_key: str) -> List[Dict[str, Any]]:
    rows = _v2_data(_request_json(base_url, "/clip/v2/resource/light", application_key=application_key), "lights")
    result = []
    for light in rows:
        metadata = light.get("metadata") if isinstance(light.get("metadata"), dict) else {}
        owner = light.get("owner") if isinstance(light.get("owner"), dict) else {}
        result.append({
            "resource_id": str(light.get("id") or ""),
            "owner_id": str(owner.get("rid") or ""),
            "name": str(metadata.get("name") or light.get("id") or "Hue Light"),
            "archetype": str(metadata.get("archetype") or "light"),
            "supports_color": isinstance(light.get("color"), dict),
            "supports_color_temperature": isinstance(light.get("color_temperature"), dict),
            "supports_dimming": isinstance(light.get("dimming"), dict),
            "on": bool((light.get("on") or {}).get("on", False)) if isinstance(light.get("on"), dict) else None,
            "brightness": (light.get("dimming") or {}).get("brightness") if isinstance(light.get("dimming"), dict) else None,
        })
    return result


def _clean_rgb(value: Any) -> tuple[int, int, int]:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return 138, 43, 226
    vals = []
    for i in range(3):
        try:
            vals.append(max(0, min(255, int(value[i]))))
        except (TypeError, ValueError):
            vals.append((138, 43, 226)[i])
    return vals[0], vals[1], vals[2]


def rgb_to_xy(rgb: Any) -> Dict[str, float]:
    r8, g8, b8 = _clean_rgb(rgb)
    channels = []
    for c in (r8 / 255.0, g8 / 255.0, b8 / 255.0):
        channels.append(((c + 0.055) / 1.055) ** 2.4 if c > 0.04045 else c / 12.92)
    r, g, b = channels
    x = r * 0.664511 + g * 0.154324 + b * 0.162028
    y = r * 0.283881 + g * 0.668433 + b * 0.047685
    z = r * 0.000088 + g * 0.072310 + b * 0.986039
    total = x + y + z
    if total <= 0:
        return {"x": 0.3227, "y": 0.3290}
    return {"x": round(x / total, 5), "y": round(y / total, 5)}


def build_light_payload(mode: Dict[str, Any], light: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(mode, dict):
        raise HueError("Hue light mode configuration is invalid")
    on = bool(mode.get("on", True))
    payload: Dict[str, Any] = {"on": {"on": on}}
    try:
        transition_ms = max(0, min(600000, int(mode.get("transition_ms", 300))))
    except (TypeError, ValueError):
        transition_ms = 300
    payload["dynamics"] = {"duration": transition_ms}
    if not on:
        return payload
    if light.get("supports_dimming", True):
        try:
            bri = max(1, min(255, int(mode.get("brightness", 128))))
        except (TypeError, ValueError):
            bri = 128
        payload["dimming"] = {"brightness": round(bri / 255.0 * 100.0, 2)}
    if light.get("supports_color", False):
        colors = mode.get("colors") if isinstance(mode.get("colors"), list) else []
        color1 = colors[0] if colors else [138, 43, 226]
        payload["color"] = {"xy": rgb_to_xy(color1)}
    return payload


def apply_light_mode(bridge: Dict[str, Any], light: Dict[str, Any], mode: Dict[str, Any]) -> Dict[str, Any]:
    host = str(bridge.get("host") or "")
    key = str(bridge.get("application_key") or "")
    rid = str(light.get("resource_id") or "")
    if not host or not key or not rid:
        raise HueError("Hue Bridge/light configuration is incomplete")
    payload = build_light_payload(mode, light)
    raw = _request_json(host, f"/clip/v2/resource/light/{rid}", method="PUT", payload=payload, application_key=key)
    _v2_data(raw, "light update")
    return raw if isinstance(raw, dict) else {"ok": True}
