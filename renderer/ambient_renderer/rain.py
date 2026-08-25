"""Stateful window-rain simulation with merging, accelerating droplets."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from .color import RGB, mix
from .config import DeviceConfig, LaneConfig
from .effects import lane_distance_from_top


@dataclass
class Drop:
    position: float
    velocity: float
    volume: float
    tint: float


class RainField:
    def __init__(self, seed: int = 1) -> None:
        self.random = random.Random(seed)
        self.drops: dict[str, list[Drop]] = {}
        self.spawn_carry: dict[str, float] = {}
        self.last_at: float | None = None
        self.spawned = 0
        self.merged = 0

    def reset(self) -> None:
        self.drops.clear()
        self.spawn_carry.clear()
        self.last_at = None

    def update(self, lanes: tuple[LaneConfig, ...], now: float, intensity: float = 1.0) -> None:
        dt = 1.0 / 30.0 if self.last_at is None else max(0.0, min(0.12, now - self.last_at))
        self.last_at = now
        lane_ids = {lane.id for lane in lanes}
        for lane_id in set(self.drops) - lane_ids:
            self.drops[lane_id] = []

        for lane in lanes:
            drops = self.drops.setdefault(lane.id, [])
            for drop in drops:
                drop.velocity += (0.010 + 0.008 * drop.volume) * dt
                drop.position += drop.velocity * dt
            drops[:] = [drop for drop in drops if drop.position < 1.12]

            drops.sort(key=lambda drop: drop.position)
            merged: list[Drop] = []
            for drop in drops:
                if merged and drop.position - merged[-1].position < 0.014 + 0.006 * min(drop.volume, merged[-1].volume):
                    previous = merged[-1]
                    total = previous.volume + drop.volume
                    previous.position = (previous.position * previous.volume + drop.position * drop.volume) / total
                    previous.velocity = max(previous.velocity, drop.velocity) + 0.018 * min(2.5, total)
                    previous.tint = (previous.tint * previous.volume + drop.tint * drop.volume) / total
                    previous.volume = min(3.5, total)
                    self.merged += 1
                else:
                    merged.append(drop)
            self.drops[lane.id] = merged

            rate = 1.1 + 2.1 * max(0.25, intensity)
            carry = self.spawn_carry.get(lane.id, 0.0) + rate * dt
            count = int(carry)
            carry -= count
            if self.random.random() < carry * 0.10:
                count += 1
                carry = max(0.0, carry - 1.0)
            self.spawn_carry[lane.id] = carry
            for _ in range(count):
                volume = self.random.uniform(0.45, 1.25)
                position = self.random.uniform(-0.04, 0.55 if self.random.random() < 0.38 else 0.08)
                velocity = self.random.uniform(0.035, 0.115) * (0.78 + volume * 0.38)
                merged.append(Drop(position, velocity, volume, self.random.random()))
                self.spawned += 1

    def render(
        self,
        base: list[RGB],
        device: DeviceConfig,
        now: float,
        lanes: tuple[LaneConfig, ...],
        intensity: float = 1.0,
    ) -> list[RGB]:
        self.update(lanes, now, intensity)
        output = list(base)
        for lane in lanes:
            drops = self.drops.get(lane.id, [])
            for absolute in range(lane.start, lane.start + lane.length):
                vertical = lane_distance_from_top(lane, absolute)
                color = mix(base[absolute], (10, 67, 132), 0.24 + 0.08 * min(1.5, intensity))
                for drop in drops:
                    distance = vertical - drop.position
                    head_width = 0.007 + 0.004 * math.sqrt(drop.volume)
                    head = math.exp(-((distance / head_width) ** 2))
                    trail_length = 0.026 + 0.032 * min(2.5, drop.volume)
                    trail = 0.0
                    if -trail_length < distance < 0.0:
                        trail = (1.0 + distance / trail_length) * 0.42
                    strength = min(0.92, head * 0.82 + trail)
                    if strength > 0.01:
                        tint = mix((48, 148, 224), (91, 205, 224), drop.tint)
                        color = mix(color, tint, strength)
                output[absolute] = color
        return output

    def status(self) -> dict[str, int]:
        return {
            "active_drops": sum(len(drops) for drops in self.drops.values()),
            "spawned": self.spawned,
            "merged": self.merged,
        }
