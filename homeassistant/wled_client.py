"""Small standard-library client for state-safe WLED automations."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


STATE_KEYS = ("on", "bri", "transition", "mainseg")
SEGMENT_KEYS = (
    "id", "start", "stop", "len", "grp", "spc", "of", "on", "frz", "bri",
    "cct", "set", "col", "fx", "sx", "ix", "pal", "sel", "rev", "mi",
    "o1", "o2", "o3", "si", "m12",
)


class WLEDError(RuntimeError):
    """Raised when WLED cannot complete an API request."""


def _url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def request_json(
    base_url: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        _url(base_url, path),
        data=body,
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise WLEDError(f"WLED request failed for {request.full_url}: {exc}") from exc


def get_state(base_url: str) -> dict[str, Any]:
    return request_json(base_url, "/json/state")


def get_info(base_url: str) -> dict[str, Any]:
    return request_json(base_url, "/json/info")


def realtime_active(info: dict[str, Any]) -> bool:
    return bool(info.get("live"))


def clean_segment(segment: dict[str, Any]) -> dict[str, Any]:
    return {key: segment[key] for key in SEGMENT_KEYS if key in segment}


def clean_state(state: dict[str, Any]) -> dict[str, Any]:
    clean = {key: state[key] for key in STATE_KEYS if key in state}
    clean["seg"] = [clean_segment(item) for item in state.get("seg", [])]
    used = {int(item.get("id", 0)) for item in clean["seg"]}
    clean["seg"].extend({"id": segment_id, "stop": 0} for segment_id in range(32) if segment_id not in used)
    return clean


def restore_state(base_url: str, state: dict[str, Any]) -> None:
    """Restore segment data first, then recover the preset identity if present."""
    request_json(base_url, "/json/state", clean_state(state))
    preset = state.get("ps")
    if isinstance(preset, int) and preset > 0:
        request_json(base_url, "/json/state", {"ps": preset})
