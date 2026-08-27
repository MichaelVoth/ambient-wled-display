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
    adhesion: float = 0.35
    previous_position: float = 0.0


class RainField:
    def __init__(self, seed: int = 1) -> None:
        self.random = random.Random(seed)
        self.drops: dict[str, list[Drop]] = {}
        self.spawn_carry: dict[str, float] = {}
        self.wetness: dict[str, list[float]] = {}
        self.last_at: float | None = None
        self.spawned = 0
        self.merged = 0

    def reset(self) -> None:
        self.drops.clear()
        self.spawn_carry.clear()
        self.wetness.clear()
        self.last_at = None

    def update(self, lanes: tuple[LaneConfig, ...], now: float, intensity: float = 1.0) -> None:
        dt = 1.0 / 30.0 if self.last_at is None else max(0.0, min(0.12, now - self.last_at))
        self.last_at = now
        lane_ids = {lane.id for lane in lanes}
        for lane_id in set(self.drops) - lane_ids:
            self.drops[lane_id] = []

        for lane in lanes:
            drops = self.drops.setdefault(lane.id, [])
            wetness = self.wetness.setdefault(lane.id, [0.0] * lane.length)
            if len(wetness) != lane.length:
                wetness[:] = [0.0] * lane.length
            decay = math.exp(-dt / 7.5)
            for index in range(len(wetness)):
                wetness[index] *= decay
            for drop in drops:
                drop.previous_position = drop.position
                # Small drops cling and stutter. Heavy drops overcome surface
                # tension, accelerate hard, and become the catch-up rivulets.
                gravity = (0.007 + 0.055 * drop.volume) * (1.0 - drop.adhesion * 0.55)
                drop.velocity += gravity * dt
                if drop.velocity < 0.09 and self.random.random() < drop.adhesion * dt * 0.9:
                    drop.velocity *= self.random.uniform(0.12, 0.45)
                drop.velocity = min(1.15, drop.velocity)
                drop.position += drop.velocity * dt
                if 0.0 <= drop.position <= 1.0:
                    head = min(lane.length - 1, max(0, int(drop.position * lane.length)))
                    trail_pixels = max(1, int(1 + drop.volume * 1.6 + drop.velocity * 7.0))
                    for offset in range(trail_pixels):
                        wet_index = max(0, head - offset)
                        wetness[wet_index] = max(
                            wetness[wet_index],
                            min(1.0, (0.16 + drop.volume * 0.18) * (1.0 - offset / (trail_pixels + 1))),
                        )
            drops[:] = [drop for drop in drops if drop.position < 1.12]

            drops.sort(key=lambda drop: drop.position)
            merged: list[Drop] = []
            for drop in drops:
                catch_distance = 0.016 + 0.007 * min(drop.volume, merged[-1].volume) if merged else 0.0
                if merged:
                    catch_distance += abs(drop.velocity - merged[-1].velocity) * dt * 1.35
                crossed = bool(
                    merged
                    and drop.previous_position <= merged[-1].previous_position
                    and drop.position >= merged[-1].position
                )
                if merged and (drop.position - merged[-1].position < catch_distance or crossed):
                    previous = merged[-1]
                    total = previous.volume + drop.volume
                    previous.position = (previous.position * previous.volume + drop.position * drop.volume) / total
                    previous.velocity = min(
                        1.15,
                        max(previous.velocity, drop.velocity) + 0.045 * min(3.5, total),
                    )
                    previous.tint = (previous.tint * previous.volume + drop.tint * drop.volume) / total
                    previous.volume = min(3.5, total)
                    previous.adhesion = min(previous.adhesion, drop.adhesion) * 0.72
                    self.merged += 1
                else:
                    merged.append(drop)
            self.drops[lane.id] = merged

            rate = 0.0 if intensity <= 0 else 0.8 + 2.35 * intensity
            count = 1 if self.random.random() < 1.0 - math.exp(-rate * dt) else 0
            # Occasional clusters make the window feel driven by weather,
            # rather than by a metronome.
            if count and self.random.random() < 0.14 * min(1.8, intensity):
                count += self.random.randint(1, 3)
            for _ in range(count):
                kind = self.random.random()
                if kind < 0.52:
                    volume = self.random.uniform(0.28, 0.82)
                    velocity = self.random.uniform(0.004, 0.032)
                    adhesion = self.random.uniform(0.62, 0.96)
                elif kind < 0.88:
                    volume = self.random.uniform(0.7, 1.55)
                    velocity = self.random.uniform(0.055, 0.19)
                    adhesion = self.random.uniform(0.22, 0.62)
                else:
                    volume = self.random.uniform(1.5, 2.9)
                    velocity = self.random.uniform(0.38, 0.78)
                    adhesion = self.random.uniform(0.02, 0.2)
                position = self.random.uniform(-0.04, 0.55 if self.random.random() < 0.38 else 0.08)
                merged.append(
                    Drop(position, velocity, volume, self.random.random(), adhesion, position)
                )
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
                cooling = 0.24 + 0.08 * min(1.5, intensity) if intensity > 0 else 0.0
                color = mix(base[absolute], (4, 54, 185), cooling)
                wet_index = min(lane.length - 1, max(0, int(vertical * lane.length)))
                wet = self.wetness.get(lane.id, [0.0] * lane.length)[wet_index]
                if wet > 0.01:
                    color = mix(color, (0, 149, 238), min(0.34, wet * 0.36))
                for drop in drops:
                    distance = vertical - drop.position
                    head_width = 0.007 + 0.004 * math.sqrt(drop.volume)
                    head = math.exp(-((distance / head_width) ** 2))
                    trail_length = 0.018 + 0.032 * min(2.5, drop.volume) + 0.11 * drop.velocity
                    trail = 0.0
                    if -trail_length < distance < 0.0:
                        trail = (1.0 + distance / trail_length) * 0.42
                    strength = min(0.92, head * 0.82 + trail)
                    if strength > 0.01:
                        tint = mix((0, 132, 255), (42, 255, 220), drop.tint)
                        color = mix(color, tint, min(1.0, strength * 1.18))
                output[absolute] = color
        return output

    def has_residue(self) -> bool:
        return any(self.drops.values()) or any(
            value > 0.025 for lane in self.wetness.values() for value in lane
        )

    def status(self) -> dict[str, int]:
        return {
            "active_drops": sum(len(drops) for drops in self.drops.values()),
            "spawned": self.spawned,
            "merged": self.merged,
            "wet_pixels": sum(sum(value > 0.025 for value in lane) for lane in self.wetness.values()),
        }
