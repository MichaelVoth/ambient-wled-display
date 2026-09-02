"""Minimal Distributed Display Protocol output for WLED realtime frames."""

from __future__ import annotations

import socket
import struct

from .color import RGB


DDP_VERSION1 = 0x40
DDP_PUSH = 0x01
DDP_RGB24 = 0x0B
DDP_DISPLAY = 0x01
DDP_MAX_PAYLOAD = 1440


def encode_packet(frame: list[RGB], sequence: int = 1, offset: int = 0, push: bool = True) -> bytes:
    payload = bytes(channel for pixel in frame for channel in pixel)
    flags = DDP_VERSION1 | (DDP_PUSH if push else 0)
    header = struct.pack(
        ">BBBBIH",
        flags,
        sequence & 0x0F,
        DDP_RGB24,
        DDP_DISPLAY,
        offset,
        len(payload),
    )
    return header + payload


def encode_packets(
    frame: list[RGB],
    sequence: int = 1,
    max_payload: int = DDP_MAX_PAYLOAD,
) -> list[bytes]:
    """Encode a complete RGB frame, splitting large devices on pixel boundaries."""
    if max_payload < 3:
        raise ValueError("max_payload must fit at least one RGB pixel")
    chunk_size = max_payload - (max_payload % 3)
    pixels_per_chunk = chunk_size // 3
    packets = []
    for start in range(0, len(frame), pixels_per_chunk):
        chunk = frame[start:start + pixels_per_chunk]
        is_last = start + pixels_per_chunk >= len(frame)
        packets.append(encode_packet(
            chunk,
            sequence=sequence,
            offset=start * 3,
            push=is_last,
        ))
        sequence = 1 if sequence >= 15 else sequence + 1
    return packets


class DDPOutput:
    name = "ddp"

    def __init__(self, host: str, port: int = 4048) -> None:
        self.host = host
        self.port = port
        self.sequence = 1
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, frame: list[RGB]) -> int:
        packets = encode_packets(frame, self.sequence)
        sent = 0
        for packet in packets:
            sent += self.socket.sendto(packet, (self.host, self.port))
        for _ in packets:
            self.sequence = 1 if self.sequence >= 15 else self.sequence + 1
        return sent

    def close(self) -> None:
        self.socket.close()
