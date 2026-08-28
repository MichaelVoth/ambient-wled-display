"""Sparse, legible music-reactive effects with intentional negative space."""

from __future__ import annotations

import math

from .color import RGB, clamp, mix, scale
from .config import DeviceConfig, LaneConfig
from .effects import lane_distance_from_top


MUSIC_EFFECTS = {"meter", "chunks", "firefly"}


def _noise(value: float) -> float:
    """Stable pseudo randomness without an additional state machine."""
    return (math.sin(value * 12.9898) * 43758.5453) % 1.0


def _chunk_color(seed: float) -> RGB:
    colors: tuple[RGB, ...] = ((255, 43, 138), (255, 149, 18), (34, 255, 117), (112, 43, 255))
    return colors[int(_noise(seed + 4.1) * len(colors)) % len(colors)]


def render_music(
    base: list[RGB],
    device: DeviceConfig,
    now: float,
    features: dict[str, float],
    effect: str,
    lanes: tuple[LaneConfig, ...] | None = None,
    background_level: float = 0.42,
    sensitivity: float = 0.22,
) -> list[RGB]:
    """Render one simple music reading over a fully black field.

    Sensitivity is deliberately a maximum reach, not a hidden gain. At the
    default .22, a full-scale signal can only use about 30 LEDs of a 139-pixel
    wall strip.
    """
    del base, now, background_level
    if effect not in MUSIC_EFFECTS:
        effect = "meter"
    bass = clamp(features.get("bass", 0.0))
    treble = clamp(features.get("treble", 0.0))
    energy = clamp(features.get("energy", 0.0))
    beat = clamp(features.get("beat", 0.0))
    phase = float(features.get("phase", 0.0))
    sensitivity = clamp(sensitivity, 0.05, 1.0)
    output: list[RGB] = [(0, 0, 0)] * device.pixel_count

    for lane_number, lane in enumerate(device.lanes if lanes is None else lanes):
        if effect == "meter":
            # A classic volume bar begins at the physical top and grows only
            # downward. There is no base illumination or movement to obscure it.
            reach = clamp(energy * sensitivity) * lane.length
            for absolute in range(lane.start, lane.start + lane.length):
                position = lane_distance_from_top(lane, absolute) * lane.length
                if position >= reach:
                    continue
                ratio = position / max(1.0, reach)
                color: RGB = (30, 255, 110)
                if ratio < 0.26:
                    color = (255, 44, 138)
                elif ratio < 0.53:
                    color = (255, 155, 18)
                output[absolute] = scale(color, 0.56 + clamp((reach - position) / 2.0) * 0.44)

        elif effect == "chunks":
            # Irregular, short islands react to music; the majority of the
            # wall remains black. Positions change only as music changes.
            activity = clamp(energy * sensitivity * 3.0)
            count = 1 + int(clamp(activity * 4.0 + beat * 1.5, 0.0, 4.0))
            epoch = math.floor(phase * 0.16)
            for chunk in range(count):
                seed = epoch * 11.0 + lane_number * 31.0 + chunk * 7.0
                center = _noise(seed) * 0.92 + 0.04
                half_width = (1.0 + _noise(seed + 2.0) * (2.0 + activity * 9.0)) / lane.length
                color = _chunk_color(seed)
                brightness = clamp(0.58 + activity * 0.42 + (beat if chunk == 0 else 0.0) * 0.36)
                for absolute in range(lane.start, lane.start + lane.length):
                    vertical = lane_distance_from_top(lane, absolute)
                    amount = clamp(1.0 - abs(vertical - center) / half_width)
                    if amount:
                        output[absolute] = mix(output[absolute], scale(color, brightness), amount)

        else:  # firefly
            # One visible pixel, moving organically up and down. A beat gives
            # it a quick bright flash but never creates a second pattern.
            position = 0.5 + 0.46 * math.sin(phase * (0.42 + energy * 0.9) + lane_number * 1.9)
            index = min(lane.length - 1, max(0, int(position * lane.length)))
            color = mix((38, 255, 123), (255, 42, 164), clamp(beat + treble * 0.35))
            brightness = clamp(0.28 + energy * sensitivity * 2.2 + beat * 0.42)
            absolute = lane.start + int((1.0 - index / max(1, lane.length - 1)) * (lane.length - 1))
            output[absolute] = scale(color, brightness)

    return output
