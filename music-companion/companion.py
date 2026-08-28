#!/usr/bin/env python3
"""Mac companion that routes system audio and feeds musical features to the Pi."""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import socket
import struct
import subprocess
import threading
import time
import urllib.request
from collections import deque
from urllib.parse import urlparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import numpy as np


class MusicCompanion:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.feature_ready = threading.Condition(self.lock)
        self.shutdown_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.sender_thread: threading.Thread | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self.capture_socket: socket.socket | None = None
        self.active_route: str | None = None
        self.effect = "meter"
        self.last_error: str | None = None
        self.last_frame_at = 0.0
        self.frames_sent = 0
        self.frames_analyzed = 0
        self.pending_features: dict[str, float] | None = None
        self.analysis_times: deque[float] = deque(maxlen=120)
        self.send_times: deque[float] = deque(maxlen=120)

    def start_registration(self) -> None:
        threading.Thread(target=self._registration_loop, name="renderer-registration", daemon=True).start()

    def _registration_loop(self) -> None:
        while not self.shutdown_event.is_set():
            try:
                request = urllib.request.Request(
                    self.config["renderer_url"].rstrip("/") + "/api/music/register",
                    data=json.dumps({"port": int(self.config.get("port", 8091))}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=2.0) as response:
                    response.read()
            except OSError:
                pass
            self.shutdown_event.wait(30.0)

    def _devices(self) -> list[dict[str, Any]]:
        command = [self.config["audio_route_tool"], "list"]
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=4)
        devices = []
        for line in result.stdout.splitlines():
            identifier, selected, name = line.split("\t", 2)
            devices.append({"id": int(identifier), "name": name, "isDefault": selected == "1"})
        return devices

    def _route_health(self, route: dict[str, Any]) -> tuple[bool, str | None]:
        """Reject a saved Multi-Output Device when one of its speakers is gone."""
        try:
            result = subprocess.run(
                [self.config["audio_route_tool"], "inspect", str(route["device"])],
                check=False,
                capture_output=True,
                text=True,
                timeout=4,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, str(exc)
        if result.returncode != 0:
            return False, "could not inspect this speaker route"
        if "missing\t" in result.stdout:
            return False, "the speaker is not connected to this saved route"
        return True, None

    def _repair_route(self, route: dict[str, Any]) -> tuple[bool, str | None]:
        """Rebuild a stale Multi-Output Device using a currently connected speaker."""
        try:
            devices = self._devices()
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            return False, str(exc)
        matches = tuple(str(value).lower() for value in route.get("speaker_matches", ()))
        speaker = next(
            (
                device["name"] for device in devices
                if device["name"] not in {route["device"], "BlackHole 2ch"}
                and any(match in device["name"].lower() for match in matches)
            ),
            None,
        )
        if not speaker:
            friendly = " or ".join(route.get("speaker_matches", ("speaker",)))
            return False, f"connect {friendly} first, then try again"
        try:
            subprocess.run(
                [self.config["audio_route_tool"], "repair", str(route["device"]), speaker],
                check=True,
                capture_output=True,
                text=True,
                timeout=8,
            )
        except subprocess.CalledProcessError as exc:
            return False, (exc.stderr or exc.stdout or "could not rebuild the speaker route").strip()
        except (OSError, subprocess.SubprocessError) as exc:
            return False, str(exc)
        healthy, reason = self._route_health(route)
        return healthy, reason

    def status(self) -> dict[str, Any]:
        try:
            available = {device["name"]: device for device in self._devices()}
            device_error = None
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            available = {}
            device_error = str(exc)
        routes = []
        for route in self.config["routes"]:
            present = route["device"] in available
            healthy, reason = self._route_health(route) if present else (False, "this route is not available on the Mac")
            routes.append({
                **route,
                "available": present and healthy,
                "selected": bool(available.get(route["device"], {}).get("isDefault")),
                "reason": reason,
            })
        with self.lock:
            running = bool(self.thread and self.thread.is_alive())
            now = time.monotonic()
            analysis_fps = self._recent_rate(self.analysis_times, now)
            feature_fps = self._recent_rate(self.send_times, now)
            return {
                "available": device_error is None,
                "running": running,
                "route": self.active_route,
                "effect": self.effect,
                "receiving_audio": running and time.monotonic() - self.last_frame_at < 1.2,
                "frames_sent": self.frames_sent,
                "frames_analyzed": self.frames_analyzed,
                "analysis_fps": analysis_fps,
                "feature_fps": feature_fps,
                "last_error": self.last_error or device_error,
                "routes": routes,
            }

    def start(self, route_id: str, effect: str) -> dict[str, Any]:
        route = next((item for item in self.config["routes"] if item["id"] == route_id), None)
        if route is None:
            raise ValueError("unknown speaker route")
        if effect not in {"meter", "chunks", "firefly"}:
            raise ValueError("unknown music effect")
        healthy, reason = self._route_health(route)
        if not healthy:
            healthy, reason = self._repair_route(route)
        if not healthy:
            raise ValueError(f"{route['label']} is unavailable: {reason}")
        subprocess.run(
            [self.config["audio_route_tool"], "set", route["device"]],
            check=True,
            capture_output=True,
            text=True,
            timeout=4,
        )
        self.stop()
        with self.lock:
            self.active_route = route_id
            self.effect = effect
            self.last_error = None
            self.stop_event.clear()
            self.pending_features = None
            self.analysis_times.clear()
            self.send_times.clear()
            self.sender_thread = threading.Thread(target=self._sender_loop, name="music-feature-sender", daemon=True)
            self.thread = threading.Thread(target=self._analyze, name="music-analysis", daemon=True)
            sender_thread = self.sender_thread
            analysis_thread = self.thread
        sender_thread.start()
        analysis_thread.start()
        return self.status()

    def repair_routes(self) -> dict[str, Any]:
        """Repair every stale saved route whose real speaker is available."""
        results: list[dict[str, Any]] = []
        for route in self.config["routes"]:
            healthy, reason = self._route_health(route)
            if not healthy:
                healthy, reason = self._repair_route(route)
            results.append({"id": route["id"], "label": route["label"], "available": healthy, "reason": reason})
        return {"routes": results, "status": self.status()}

    def stop(self) -> dict[str, Any]:
        self.stop_event.set()
        with self.feature_ready:
            self.feature_ready.notify_all()
        with self.lock:
            process = self.process
            thread = self.thread
            sender_thread = self.sender_thread
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        with self.lock:
            capture_socket = self.capture_socket
        if capture_socket:
            try:
                capture_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            capture_socket.close()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2)
        if sender_thread and sender_thread is not threading.current_thread():
            sender_thread.join(timeout=2)
        with self.lock:
            self.process = None
            self.capture_socket = None
            self.thread = None
            self.sender_thread = None
            self.pending_features = None
            self.active_route = None
        if self.config.get("capture_backend", "ledfx") == "ledfx":
            try:
                self._ledfx_request(
                    "/api/virtuals/house-audio-capture/effects/delete",
                    {"type": "energy"},
                )
            except OSError:
                pass
        return self.status()

    @staticmethod
    def _recent_rate(times: deque[float], now: float) -> float:
        recent = [value for value in times if now - value <= 2.0]
        if len(recent) < 2:
            return 0.0
        return round((len(recent) - 1) / max(0.001, recent[-1] - recent[0]), 1)

    def _queue_features(self, features: dict[str, float]) -> None:
        now = time.monotonic()
        with self.feature_ready:
            self.pending_features = features
            self.frames_analyzed += 1
            self.analysis_times.append(now)
            self.feature_ready.notify()

    def _sender_loop(self) -> None:
        parsed = urlparse(self.config["renderer_url"])
        try:
            destination = (socket.gethostbyname(parsed.hostname or "raspberrypi.local"), int(self.config.get("audio_udp_port", 8092)))
        except OSError:
            with self.lock:
                self.last_error = "Could not resolve the house renderer"
            return
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as feature_socket:
            while not self.stop_event.is_set():
                with self.feature_ready:
                    self.feature_ready.wait_for(
                        lambda: self.pending_features is not None or self.stop_event.is_set(),
                        timeout=0.25,
                    )
                    if self.stop_event.is_set():
                        break
                    payload = self.pending_features
                    self.pending_features = None
                if payload is None:
                    continue
                with self.lock:
                    payload = {**payload, "source_fps": self._recent_rate(self.analysis_times, time.monotonic())}
                try:
                    feature_socket.sendto(json.dumps(payload, separators=(",", ":")).encode("utf-8"), destination)
                    now = time.monotonic()
                    with self.lock:
                        self.last_error = None
                        self.last_frame_at = now
                        self.frames_sent += 1
                        self.send_times.append(now)
                except OSError:
                    with self.lock:
                        self.last_error = "Waiting for the house renderer to reconnect"

    def _post_features(self, features: dict[str, float]) -> bool:
        request = urllib.request.Request(
            self.config["renderer_url"].rstrip("/") + "/api/audio",
            data=json.dumps(features).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=0.8) as response:
                response.read()
            with self.lock:
                self.last_error = None
            return True
        except OSError:
            # The Pi may be rebuilding, rebooting, or changing Wi-Fi. Audio
            # analysis should survive that interruption and resume on its own.
            with self.lock:
                self.last_error = "Waiting for the house renderer to reconnect"
            return False

    def _analyze(self) -> None:
        if self.config.get("capture_backend", "ledfx") == "ledfx":
            self._analyze_ledfx()
            return
        self._analyze_ffmpeg()

    def _analyze_ffmpeg(self) -> None:
        sample_rate = 44100
        # Smaller windows keep CoreAudio/ffmpeg packet batching from reducing
        # the feature stream to roughly 10 FPS. 384 samples is ~8.7 ms and
        # still provides enough frequency resolution for three broad bands.
        block_size = 384
        frame_seconds = block_size / sample_rate
        command = [
            self.config["ffmpeg"], "-hide_banner", "-loglevel", "error",
            "-f", "avfoundation", "-i", f":{self.config['audio_input']}",
            "-ac", "1", "-ar", str(sample_rate), "-f", "f32le", "pipe:1",
        ]
        smoothed = {"bass": 0.0, "mid": 0.0, "treble": 0.0, "energy": 0.0, "beat": 0.0}
        bass_fast = 0.0
        bass_slow = 0.0
        last_beat_at = 0.0
        phase = 0.0
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            with self.lock:
                self.process = process
            assert process.stdout is not None
            window = np.hanning(block_size)
            frequencies = np.fft.rfftfreq(block_size, 1.0 / sample_rate)
            masks = {
                "bass": (frequencies >= 35) & (frequencies < 190),
                "mid": (frequencies >= 190) & (frequencies < 2400),
                "treble": (frequencies >= 2400) & (frequencies < 12000),
            }
            while not self.stop_event.is_set():
                raw = process.stdout.read(block_size * 4)
                if len(raw) != block_size * 4:
                    break
                samples = np.frombuffer(raw, dtype=np.float32)
                rms = float(np.sqrt(np.mean(samples * samples)))
                # Use a useful musical dynamic range instead of mapping normal
                # mastered audio straight to 100%. -42 dB is effectively dark;
                # only a near-full-scale signal approaches 1.0.
                rms_db = 20.0 * math.log10(max(rms, 1e-7))
                loudness = max(0.0, min(1.0, (rms_db + 42.0) / 36.0))
                loudness = loudness * loudness * (3.0 - 2.0 * loudness)
                energy = loudness ** 2.2
                spectrum = np.abs(np.fft.rfft(samples * window))
                total = float(np.sum(spectrum)) + 1e-9
                shares = {
                    name: float(np.sum(spectrum[mask]) / total)
                    for name, mask in masks.items()
                }
                values = {
                    "bass": min(1.0, math.sqrt(shares["bass"]) * energy * 1.65),
                    "mid": min(1.0, math.sqrt(shares["mid"]) * energy * 0.95),
                    "treble": min(1.0, math.sqrt(shares["treble"]) * energy * 1.15),
                }
                # A beat is a short bass onset relative to its own recent
                # baseline, with a refractory window to prevent double hits.
                bass_fast += (values["bass"] - bass_fast) * (1.0 - math.exp(-frame_seconds / 0.035))
                bass_slow += (values["bass"] - bass_slow) * (1.0 - math.exp(-frame_seconds / 0.6))
                onset = max(0.0, bass_fast - bass_slow)
                now = time.monotonic()
                beat = 1.0 if energy > 0.08 and onset > 0.07 and now - last_beat_at > 0.17 else 0.0
                if beat:
                    last_beat_at = now
                values.update({"energy": energy, "beat": beat})
                for name, value in values.items():
                    if name == "beat":
                        continue
                    time_constant = 0.045 if value > smoothed[name] else 0.18
                    rate = 1.0 - math.exp(-frame_seconds / time_constant)
                    smoothed[name] += (value - smoothed[name]) * rate
                smoothed["beat"] = max(beat, smoothed["beat"] * math.exp(-frame_seconds / 0.1))
                phase += (0.35 + smoothed["energy"] * 1.4) * frame_seconds
                payload = {**smoothed, "phase": phase}
                self._queue_features(payload)
            if process.poll() not in {None, 0, -15} and not self.stop_event.is_set():
                assert process.stderr is not None
                raise RuntimeError(process.stderr.read().decode("utf-8", errors="replace").strip())
        except Exception as exc:
            with self.lock:
                self.last_error = str(exc) or type(exc).__name__
        finally:
            with self.lock:
                self.process = None

    def _ledfx_request(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        method: str | None = None,
    ) -> dict[str, Any]:
        payload = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.config.get("ledfx_url", "http://127.0.0.1:8889").rstrip("/") + path,
            data=payload,
            headers={"Content-Type": "application/json"} if payload else {},
            method=method or ("POST" if payload is not None else "GET"),
        )
        with urllib.request.urlopen(request, timeout=3.0) as response:
            value = json.loads(response.read().decode("utf-8"))
        result = value if isinstance(value, dict) else {}
        if result.get("status") == "failed":
            payload = result.get("payload", {})
            raise RuntimeError(str(payload.get("reason", "audio engine request failed")))
        return result

    def _ensure_ledfx(self) -> None:
        try:
            self._ledfx_request("/api/info")
        except OSError:
            app = self.config.get("ledfx_app", "/Applications/LedFx-2.1.5.app")
            config_dir = os.path.expanduser(self.config.get("ledfx_config_dir", "~/.ledfx"))
            subprocess.run(
                [
                    "/usr/bin/open", "-n", "-g", app, "--args", "--no-tray", "--offline",
                    "-c", config_dir, "-p", "8889",
                ],
                check=True,
                timeout=5,
            )
            for _ in range(40):
                try:
                    self._ledfx_request("/api/info")
                    break
                except OSError:
                    time.sleep(0.25)
            else:
                raise RuntimeError("the authorized audio engine did not start")

        devices = self._ledfx_request("/api/audio/devices").get("devices", {})
        match = self.config.get("audio_input", "BlackHole 2ch").lower()
        audio_id = next((key for key, name in devices.items() if match in str(name).lower()), None)
        if audio_id is None:
            raise RuntimeError("BlackHole 2ch is not available to the audio engine")
        self._ledfx_request("/api/audio/devices", {"audio_device": int(audio_id)}, method="PUT")

        devices_state = self._ledfx_request("/api/devices").get("devices", {})
        if "house-audio-capture" not in devices_state:
            created = self._ledfx_request(
                "/api/devices",
                {"type": "dummy", "config": {"name": "House Audio Capture", "pixel_count": 60}},
            )
            if str(created.get("device", {}).get("id", "")) != "house-audio-capture":
                raise RuntimeError("could not create the private audio-analysis channel")
        self._ledfx_request(
            "/api/virtuals/house-audio-capture/effects",
            {"type": "energy", "config": {"brightness": 1.0, "sensitivity": 0.9}},
        )

    @staticmethod
    def _read_exact(connection: socket.socket, count: int) -> bytes:
        data = b""
        while len(data) < count:
            part = connection.recv(count - len(data))
            if not part:
                raise ConnectionError("audio graph connection closed")
            data += part
        return data

    @staticmethod
    def _send_websocket(connection: socket.socket, payload: bytes, opcode: int = 1) -> None:
        mask = os.urandom(4)
        length = len(payload)
        header = bytes([0x80 | opcode])
        if length < 126:
            header += bytes([0x80 | length])
        elif length <= 65535:
            header += bytes([0x80 | 126]) + struct.pack("!H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", length)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        connection.sendall(header + mask + masked)

    @classmethod
    def _read_websocket_json(cls, connection: socket.socket) -> dict[str, Any]:
        while True:
            first, second = cls._read_exact(connection, 2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", cls._read_exact(connection, 2))[0]
            elif length == 127:
                length = struct.unpack("!Q", cls._read_exact(connection, 8))[0]
            mask = cls._read_exact(connection, 4) if second & 0x80 else b""
            payload = cls._read_exact(connection, length)
            if mask:
                payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
            if opcode == 8:
                raise ConnectionError("audio graph connection closed")
            if opcode == 9:
                cls._send_websocket(connection, payload, opcode=10)
                continue
            if opcode == 1:
                return json.loads(payload.decode("utf-8"))

    def _open_ledfx_graph(self) -> socket.socket:
        port = int(self.config.get("ledfx_url", "http://127.0.0.1:8889").rsplit(":", 1)[-1])
        connection = socket.create_connection(("127.0.0.1", port), timeout=4)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            "GET /api/websocket HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        connection.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            # Do not read past the HTTP header: LedFx often places its first
            # WebSocket frame in the same packet and discarding those bytes
            # would lose the client-id greeting, leaving us waiting forever.
            response += self._read_exact(connection, 1)
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise ConnectionError("audio graph websocket upgrade failed")
        connection.settimeout(4)
        self._read_websocket_json(connection)
        self._send_websocket(
            connection,
            json.dumps({
                "id": 1,
                "type": "subscribe_event",
                "event_type": "graph_update",
                "event_filter": {"graph_id": "melbank_2"},
            }).encode("utf-8"),
        )
        return connection

    def _analyze_ledfx(self) -> None:
        smoothed = {"bass": 0.0, "mid": 0.0, "treble": 0.0, "energy": 0.0, "beat": 0.0}
        bass_floor = 0.02
        phase = 0.0
        last_sent = 0.0
        try:
            self._ensure_ledfx()
            connection = self._open_ledfx_graph()
            with self.lock:
                self.capture_socket = connection
            while not self.stop_event.is_set():
                message = self._read_websocket_json(connection)
                if message.get("event_type") != "graph_update" or message.get("graph_id") != "melbank_2":
                    continue
                now = time.monotonic()
                if now - last_sent < 1.0 / 30.0:
                    continue
                melbank = np.asarray(message.get("melbank", []), dtype=float)
                frequencies = np.asarray(message.get("frequencies", []), dtype=float)
                if not len(melbank) or len(melbank) != len(frequencies):
                    continue

                def band(low: float, high: float) -> float:
                    values = melbank[(frequencies >= low) & (frequencies < high)]
                    return min(1.0, float(np.quantile(values, 0.82)) if len(values) else 0.0)

                values = {
                    "bass": band(35, 190),
                    "mid": band(190, 2400),
                    "treble": band(2400, 15000),
                    "energy": min(1.0, float(np.quantile(melbank, 0.78))),
                }
                bass_floor = bass_floor * 0.985 + values["bass"] * 0.015
                raw_beat = 1.0 if values["energy"] > 0.018 and values["bass"] > max(0.02, bass_floor * 1.18) else 0.0
                values["beat"] = raw_beat
                for name, value in values.items():
                    if name == "beat":
                        continue
                    rate = 0.62 if value > smoothed[name] else 0.2
                    smoothed[name] += (value - smoothed[name]) * rate
                smoothed["beat"] = max(raw_beat, smoothed["beat"] * 0.72)
                phase += 0.015 + smoothed["bass"] * 0.22 + smoothed["energy"] * 0.28
                self._queue_features({**smoothed, "phase": phase})
                last_sent = now
        except Exception as exc:
            if not self.stop_event.is_set():
                with self.lock:
                    self.last_error = str(exc) or type(exc).__name__
        finally:
            with self.lock:
                self.capture_socket = None


class Handler(BaseHTTPRequestHandler):
    server: "Server"

    def log_message(self, *_args: Any) -> None:
        return

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/status":
            self._json(self.server.companion.status())
        elif self.path == "/health":
            self._json({"ok": True})
        else:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            if self.path == "/api/start":
                result = self.server.companion.start(str(body.get("route", "")), str(body.get("effect", "meter")))
            elif self.path == "/api/repair":
                result = self.server.companion.repair_routes()
            elif self.path == "/api/stop":
                result = self.server.companion.stop()
            else:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            self._json(result)
        except (ValueError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) and exc.stderr else str(exc)
            self._json({"error": detail}, HTTPStatus.BAD_REQUEST)

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(payload)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], companion: MusicCompanion) -> None:
        super().__init__(address, Handler)
        self.companion = companion


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    path = Path(args.config).resolve()
    config = json.loads(path.read_text(encoding="utf-8"))
    route_tool = Path(config.get("audio_route_tool", "./audio-route"))
    if not route_tool.is_absolute():
        config["audio_route_tool"] = str((path.parent / route_tool).resolve())
    companion = MusicCompanion(config)
    server = Server((str(config.get("bind", "0.0.0.0")), int(config.get("port", 8091))), companion)
    companion.start_registration()
    try:
        server.serve_forever(0.5)
    finally:
        companion.shutdown_event.set()
        companion.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
