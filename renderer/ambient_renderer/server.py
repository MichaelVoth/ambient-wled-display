"""Local HTTP API and browser simulator for the ambient renderer."""

from __future__ import annotations

import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .color import parse_hex
from .engine import RendererEngine
from .engine import AMBIENT_PRESETS


LOGGER = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parents[1] / "static"


class RendererHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], engine: RendererEngine) -> None:
        super().__init__(address, RendererHandler)
        self.engine = engine


class RendererHandler(BaseHTTPRequestHandler):
    server: RendererHTTPServer

    def log_message(self, message: str, *args: Any) -> None:
        LOGGER.debug("%s - %s", self.address_string(), message % args)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            self._file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        elif path == "/api/status":
            self._json(self.server.engine.status())
        elif path == "/api/frame":
            self._json({"frames": self.server.engine.frame_snapshot()})
        elif path == "/api/config":
            status = self.server.engine.status()
            self._json({
                "devices": status["devices"],
                "fps": status["fps_target"],
                "ambient_presets": AMBIENT_PRESETS,
            })
        elif path == "/api/ambient":
            self._json(self.server.engine.status()["ambient"])
        elif path == "/health":
            status = self.server.engine.status()
            self._json({"ok": status["ok"], "mode": status["mode"]})
        else:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            body = self._body()
            if path == "/api/events/hour":
                hour = int(body.get("hour", 0))
                if not 0 <= hour <= 23:
                    raise ValueError("hour must be between 0 and 23")
                take_output = body.get("take_output", False)
                if not isinstance(take_output, bool):
                    raise ValueError("take_output must be true or false")
                self._json(
                    self.server.engine.trigger_hour(
                        hour,
                        take_output=take_output,
                        targets=self._targets(body),
                    ),
                    HTTPStatus.ACCEPTED,
                )
            elif path == "/api/events/alert":
                color = parse_hex(str(body.get("color", "#ff280f")))
                duration = float(body.get("duration", 6))
                self._json(
                    self.server.engine.trigger_alert(color, duration, self._targets(body)),
                    HTTPStatus.ACCEPTED,
                )
            elif path == "/api/events/signal":
                signal = str(body.get("signal", ""))
                duration = body.get("duration")
                duration = float(duration) if duration is not None else None
                take_output = body.get("take_output", False)
                if not isinstance(take_output, bool):
                    raise ValueError("take_output must be true or false")
                self._json(
                    self.server.engine.trigger_signal(
                        signal,
                        duration=duration,
                        take_output=take_output,
                        targets=self._targets(body),
                    ),
                    HTTPStatus.ACCEPTED,
                )
            elif path == "/api/events/cancel":
                self.server.engine.cancel_event()
                self._json({"ok": True})
            elif path.startswith("/api/layers/"):
                name = path.rsplit("/", 1)[-1]
                enabled = body.get("enabled", False)
                if not isinstance(enabled, bool):
                    raise ValueError("enabled must be true or false")
                self.server.engine.set_layer(name, enabled, self._targets(body))
                self._json({"ok": True, "layer": name, "enabled": enabled})
            elif path == "/api/mode":
                self.server.engine.set_mode(str(body.get("mode", "preview")))
                self._json({"ok": True, "mode": self.server.engine.status()["mode"]})
            elif path == "/api/ambient":
                self._json({"ok": True, "ambient": self.server.engine.set_ambient(body)})
            else:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 65536:
            raise ValueError("request body is too large")
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    @staticmethod
    def _targets(body: dict[str, Any]) -> list[str] | None:
        targets = body.get("targets")
        if targets is None:
            return None
        if not isinstance(targets, list) or not all(isinstance(item, str) for item in targets):
            raise ValueError("targets must be a list of device or lane IDs")
        return targets

    def _json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _file(self, path: Path, content_type: str) -> None:
        try:
            payload = path.read_bytes()
        except OSError:
            self._json({"error": "simulator asset not found"}, HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(payload)
