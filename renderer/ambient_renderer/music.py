"""Sparse, legible music-reactive effects with intentional negative space."""

from __future__ import annotations

import math
import colorsys

from .color import RGB, clamp, mix, scale
from .config import DeviceConfig, LaneConfig
from .effects import lane_distance_from_top


MUSIC_EFFECTS = {"meter", "chunks", "firefly"}


def _noise(value: float) -> float:
    """Stable pseudo randomness without an additional state machine."""
    return (math.sin(value * 12.9898) * 43758.5453) % 1.0


def _hue_color(hue: float) -> RGB:
    red, green, blue = colorsys.hsv_to_rgb(hue % 1.0, 0.96, 1.0)
    return round(red * 255), round(green * 255), round(blue * 255)


def render_music(
    base: list[RGB],
    device: DeviceConfig,
    now: float,
    features: dict[str, float],
    effect: str,
    lanes: tuple[LaneConfig, ...] | None = None,
    background_level: float = 0.42,
    sensitivity: float = 0.22,
    color_mode: str = "cycle",
    primary: RGB = (0, 190, 255),
    accent: RGB = (255, 32, 170),
    motion_speed: float = 0.75,
) -> list[RGB]:
    """Render one simple music reading over a fully black field.

    Sensitivity is deliberately a maximum reach, not a hidden gain. At the
    default .22, a full-scale signal can only use about 30 LEDs of a 139-pixel
    wall strip.
    """
    del base, background_level
    if effect not in MUSIC_EFFECTS:
        effect = "meter"
    treble = clamp(features.get("treble", 0.0))
    energy = clamp(features.get("energy", 0.0))
    beat = clamp(features.get("beat", 0.0))
    phase = float(features.get("phase", 0.0))
    sensitivity = clamp(sensitivity, 0.05, 1.0)
    motion_speed = clamp(motion_speed, 0.1, 3.0)
    if color_mode == "cycle":
        # Traverse the entire hue wheel slowly. Each frame still has one clear
        # primary color rather than several competing palette bands.
        hue = now * (0.004 + motion_speed * 0.004)
        primary = _hue_color(hue)
        accent = _hue_color(hue + 0.38)
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
                edge = clamp(reach - position)
                output[absolute] = scale(primary, 0.48 + edge * 0.42 + beat * 0.10)

        elif effect == "chunks":
            # Irregular, short islands react to music; the majority of the
            # wall remains black. Positions change only as music changes.
            activity = clamp(energy * sensitivity * 2.6)
            count = 1 + int(sensitivity > 0.35) + int(sensitivity > 0.72)
            for chunk in range(count):
                seed = lane_number * 31.0 + chunk * 7.0
                center = 0.5 + 0.43 * math.sin(
                    now * motion_speed * (0.11 + _noise(seed) * 0.16)
                    + phase * (0.32 + _noise(seed + 8.0) * 0.42)
                    + seed
                )
                half_width = (1.0 + _noise(seed + 2.0) * (2.0 + activity * 9.0)) / lane.length
                color = primary if chunk % 2 == 0 else accent
                brightness = clamp(activity * (0.68 + _noise(seed + 5.0) * 0.32) + beat * 0.72)
                for absolute in range(lane.start, lane.start + lane.length):
                    vertical = lane_distance_from_top(lane, absolute)
                    amount = clamp(1.0 - abs(vertical - center) / half_width)
                    if amount:
                        output[absolute] = mix(output[absolute], scale(color, brightness), amount)

        else:  # firefly
            # One visible pixel, moving organically up and down. A beat gives
            # it a quick bright flash but never creates a second pattern.
            position = 0.5 + 0.46 * math.sin(
                now * motion_speed * 0.52 + phase * 2.4 + lane_number * 1.9
            )
            index = min(lane.length - 1, max(0, int(position * lane.length)))
            color = mix(primary, accent, clamp(beat * 0.7 + treble * 0.18))
            brightness = clamp(0.28 + energy * sensitivity * 2.2 + beat * 0.42)
            absolute = lane.start + int((1.0 - index / max(1, lane.length - 1)) * (lane.length - 1))
            output[absolute] = scale(color, brightness)

    return output
