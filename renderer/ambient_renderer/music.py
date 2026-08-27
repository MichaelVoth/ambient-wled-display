"""Smooth music-reactive compositing driven by compact audio features."""

from __future__ import annotations

import math

from .color import RGB, clamp, mix, palette_color, saturate, scale
from .config import DeviceConfig, LaneConfig
from .effects import lane_distance_from_top


MUSIC_EFFECTS = {"pulse", "prism", "spectrum", "lava", "comets", "aurora"}

MUSIC_PALETTE: tuple[RGB, ...] = (
    (255, 24, 10), (255, 118, 0), (58, 255, 28),
    (8, 215, 255), (70, 28, 255), (242, 18, 255),
)


def render_music(
    base: list[RGB],
    device: DeviceConfig,
    now: float,
    features: dict[str, float],
    effect: str,
    lanes: tuple[LaneConfig, ...] | None = None,
) -> list[RGB]:
    """Composite a vivid, continuously rendered music layer over the living base."""
    if effect not in MUSIC_EFFECTS:
        effect = "pulse"
    bass = clamp(features.get("bass", 0.0))
    mid = clamp(features.get("mid", 0.0))
    treble = clamp(features.get("treble", 0.0))
    energy = clamp(features.get("energy", 0.0))
    beat = clamp(features.get("beat", 0.0))
    phase = float(features.get("phase", 0.0))
    output = list(base)

    for lane_number, lane in enumerate(device.lanes if lanes is None else lanes):
        for absolute in range(lane.start, lane.start + lane.length):
            vertical = lane_distance_from_top(lane, absolute)
            underlying = saturate(base[absolute], 1.32)

            if effect == "lava":
                broad = math.sin(vertical * math.tau * 0.72 - phase * 0.11) * 0.18
                medium = math.sin(vertical * math.tau * 2.3 + phase * 0.23) * (0.06 + mid * 0.12)
                fine = math.sin(vertical * math.tau * 7.1 - phase * 0.47) * treble * 0.035
                position = vertical * 0.38 + broad + medium + fine - now * 0.018
                color = palette_color(MUSIC_PALETTE, position)
                cell = 0.5 + 0.5 * math.sin(vertical * math.tau * (1.1 + bass * 0.7) - phase * 0.3)
                amount = clamp(0.34 + energy * 0.38 + cell * bass * 0.3 + beat * 0.25)
                output[absolute] = mix(scale(underlying, 0.48 + mid * 0.16), color, amount)

            elif effect == "comets":
                comet_position = 1.18 - (phase * 0.075 + lane_number * 0.13) % 1.42
                behind = vertical - comet_position
                head = math.exp(-((behind / 0.035) ** 2))
                tail = math.exp(-max(0.0, -behind) / 0.19) if behind <= 0 else 0.0
                spark = max(0.0, math.sin(vertical * 113.0 + phase * 5.1)) ** 15
                comet_color = mix((255, 52, 5), (248, 18, 255), mid)
                color = mix(scale(underlying, 0.38 + energy * 0.24), comet_color, clamp((head + tail * 0.7) * (0.35 + bass * 0.65)))
                output[absolute] = mix(color, (24, 255, 146), clamp(spark * treble + head * beat * 0.55))

            elif effect == "aurora":
                fold = 0.5 + 0.5 * math.sin(vertical * math.tau * 1.35 + phase * 0.19 + math.sin(vertical * 8.0 - phase * 0.08))
                curtain = mix((18, 255, 94), (100, 22, 255), fold)
                curtain = mix(curtain, (255, 24, 202), mid * (0.25 + fold * 0.42))
                star = max(0.0, math.sin(vertical * 149.0 + phase * 4.3)) ** 18
                amount = clamp(0.25 + energy * 0.36 + bass * (1.0 - vertical) * 0.22)
                output[absolute] = mix(mix(scale(underlying, 0.52), curtain, amount), (90, 245, 255), treble * star)

            elif effect == "spectrum":
                # Bass owns the bottom, voice/instruments the middle, and
                # cymbals/high harmonics the visible top.
                low = math.exp(-(((vertical - 0.82) / 0.24) ** 2)) * bass
                middle = math.exp(-(((vertical - 0.48) / 0.23) ** 2)) * mid
                high = math.exp(-(((vertical - 0.14) / 0.18) ** 2)) * treble
                color = (255, 48, 12)
                color = mix(color, (35, 255, 74), middle / max(0.001, low + middle))
                color = mix(color, (220, 24, 255), high / max(0.001, low + middle + high))
                amount = clamp((low + middle + high) * 0.9 + beat * 0.2)
                output[absolute] = mix(scale(underlying, 0.42 + energy * 0.3), color, amount)

            elif effect == "prism":
                bands = (
                    (255, 32, 8),
                    (255, 126, 0),
                    (54, 255, 38),
                    (18, 216, 255),
                    (74, 34, 255),
                    (232, 24, 255),
                )
                roll = (vertical * 1.8 - phase * 0.11 - now * (0.035 + energy * 0.08)) % 1.0
                index = int(roll * len(bands))
                color = bands[index % len(bands)]
                ripple = 0.5 + 0.5 * math.sin(vertical * math.tau * (2.0 + bass * 2.0) - phase)
                amount = clamp(0.24 + energy * 0.48 + ripple * mid * 0.28 + beat * 0.32)
                output[absolute] = mix(scale(underlying, 0.48), color, amount)

            else:
                # A warm bass shockwave carries upward while magenta mids and
                # crisp cyan/green high-frequency sparks move independently.
                bass_center = (1.05 - (phase * 0.19) % 1.25)
                bass_wave = math.exp(-(((vertical - bass_center) / (0.08 + bass * 0.07)) ** 2))
                mid_wave = 0.5 + 0.5 * math.sin(vertical * math.tau * 2.4 + phase * 0.73 + lane_number)
                sparkle = max(0.0, math.sin(vertical * 71.0 + phase * 3.7)) ** 9
                dimmed = scale(underlying, 0.5 + energy * 0.32)
                color = mix(dimmed, (255, 52, 8), clamp(bass * (0.42 + bass_wave * 0.58)))
                color = mix(color, (244, 26, 255), clamp(mid * mid_wave * 0.72))
                color = mix(color, (40, 255, 116), clamp(treble * sparkle * 0.9))
                output[absolute] = mix(color, (255, 188, 24), beat * bass_wave * 0.72)

    return output
