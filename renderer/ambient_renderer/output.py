"""Realtime LED transports supported by stock WLED."""

from __future__ import annotations

import socket

from .color import RGB
from .ddp import DDPOutput


DRGB_PROTOCOL = 2
DRGB_MAX_PIXELS = 490


def encode_drgb(frame: list[RGB], timeout: int = 2) -> bytes:
    """Encode WLED's compact UDP realtime DRGB format."""
    if len(frame) > DRGB_MAX_PIXELS:
        raise ValueError(f"DRGB supports at most {DRGB_MAX_PIXELS} pixels")
    if not 1 <= timeout <= 255:
        raise ValueError("realtime timeout must be between 1 and 255 seconds")
    return bytes((DRGB_PROTOCOL, timeout)) + bytes(
        channel for pixel in frame for channel in pixel
    )


class UDPRealtimeOutput:
    name = "udp_realtime"

    def __init__(self, host: str, port: int = 21324, timeout: int = 2) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, frame: list[RGB]) -> int:
        packet = encode_drgb(frame, self.timeout)
        return self.socket.sendto(packet, (self.host, self.port))

    def close(self) -> None:
        self.socket.close()


def create_output(device: object) -> UDPRealtimeOutput | DDPOutput:
    transport = getattr(device, "transport")
    if transport == "udp_realtime":
        return UDPRealtimeOutput(
            getattr(device, "host"),
            getattr(device, "realtime_port"),
            getattr(device, "realtime_timeout"),
        )
    if transport == "ddp":
        return DDPOutput(getattr(device, "host"), getattr(device, "ddp_port"))
    raise ValueError(f"unsupported transport {transport!r}")
