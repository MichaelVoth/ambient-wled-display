"""Smooth music-reactive compositing driven by compact audio features."""

from __future__ import annotations

import math

from .color import RGB, clamp, mix, palette_color, saturate, scale
from .config import DeviceConfig, LaneConfig
from .effects import lane_distance_from_top


MUSIC_EFFECTS = {"meter", "pulse", "prism", "spectrum", "lava", "comets", "aurora"}

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
    background_level: float = 0.42,
) -> list[RGB]:
    """Composite a vivid, continuously rendered music layer over the living base."""
    if effect not in MUSIC_EFFECTS:
        effect = "meter"
    bass = clamp(features.get("bass", 0.0))
    mid = clamp(features.get("mid", 0.0))
    treble = clamp(features.get("treble", 0.0))
    energy = clamp(features.get("energy", 0.0))
    beat = clamp(features.get("beat", 0.0))
    phase = float(features.get("phase", 0.0))
    background_level = clamp(background_level, 0.15, 1.0)
    activity = clamp(energy * 0.85 + bass * 0.58 + mid * 0.3 + treble * 0.14)
    output = list(base)

    for lane_number, lane in enumerate(device.lanes if lanes is None else lanes):
        if effect == "meter":
            # Deliberately reserve negative space. The meter is a single,
            # stationary top-down column: sound makes LEDs appear, silence is
            # truly black. It is a reading of volume, not another animation.
            level = clamp(max(energy * 1.12, bass * 0.82 + mid * 0.34 + treble * 0.18))
            lit = max(0.0, level * lane.length)
            for absolute in range(lane.start, lane.start + lane.length):
                vertical = lane_distance_from_top(lane, absolute)
                position = vertical * lane.length
                if position >= lit:
                    output[absolute] = (0, 0, 0)
                    continue
                # A retro level meter: green near the leading edge, moving
                # through amber, then a hot magenta/red peak at the top.
                ratio = position / max(1.0, lit)
                color = (30, 255, 110)
                if ratio < 0.34:
                    color = (255, 46, 135)
                elif ratio < 0.60:
                    color = (255, 156, 16)
                edge = clamp((lit - position) / 2.0)
                output[absolute] = scale(color, 0.48 + edge * 0.52)
            continue
        for absolute in range(lane.start, lane.start + lane.length):
            vertical = lane_distance_from_top(lane, absolute)
            # Music needs a darker stage than everyday ambient light so the
            # note-driven shapes remain readable instead of blending away.
            underlying = saturate(scale(base[absolute], background_level), 1.32)
            stage = scale(underlying, 0.14 + activity * 0.86)

            if effect == "lava":
                broad = math.sin(vertical * math.tau * 0.72 - phase * 0.11) * (0.04 + bass * 0.16)
                medium = math.sin(vertical * math.tau * 2.3 + phase * 0.23) * (0.06 + mid * 0.12)
                fine = math.sin(vertical * math.tau * 7.1 - phase * 0.47) * treble * 0.035
                position = vertical * 0.38 + broad + medium + fine - phase * 0.004
                color = palette_color(MUSIC_PALETTE, position)
                cell = 0.5 + 0.5 * math.sin(vertical * math.tau * (1.1 + bass * 0.7) - phase * 0.3)
                amount = clamp(0.05 + activity * 0.58 + cell * bass * 0.28)
                beat_wave = math.exp(-(((vertical - ((phase * 0.075) % 1.0)) / 0.13) ** 2))
                color = mix(color, (255, 224, 102), beat * beat_wave * 0.86)
                output[absolute] = mix(stage, color, clamp(amount + beat * beat_wave * 0.52))

            elif effect == "comets":
                comet_position = 1.18 - (phase * 0.075 + lane_number * 0.13) % 1.42
                behind = vertical - comet_position
                head = math.exp(-((behind / 0.035) ** 2))
                tail = math.exp(-max(0.0, -behind) / 0.19) if behind <= 0 else 0.0
                spark = max(0.0, math.sin(vertical * 113.0 + phase * 5.1)) ** 15
                comet_color = mix((255, 52, 5), (248, 18, 255), mid)
                color = mix(stage, comet_color, clamp((head + tail * 0.7) * (0.08 + activity * 0.92)))
                output[absolute] = mix(color, (255, 231, 134), clamp(spark * treble + head * beat * 0.9))

            elif effect == "aurora":
                fold = 0.5 + 0.5 * math.sin(vertical * math.tau * 1.35 + phase * 0.19 + math.sin(vertical * 8.0 - phase * 0.08))
                curtain = mix((18, 255, 94), (100, 22, 255), fold)
                curtain = mix(curtain, (255, 24, 202), mid * (0.25 + fold * 0.42))
                star = max(0.0, math.sin(vertical * 149.0 + phase * 4.3)) ** 18
                amount = clamp(0.07 + activity * 0.58 + bass * (1.0 - vertical) * 0.22)
                output[absolute] = mix(mix(stage, curtain, amount), (231, 255, 255), clamp(treble * star + beat * star * 0.55))

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
                output[absolute] = mix(stage, color, amount)

            elif effect == "prism":
                bands = (
                    (255, 32, 8),
                    (255, 126, 0),
                    (54, 255, 38),
                    (18, 216, 255),
                    (74, 34, 255),
                    (232, 24, 255),
                )
                roll = (vertical * 1.8 - phase * 0.11) % 1.0
                index = int(roll * len(bands))
                color = bands[index % len(bands)]
                ripple = 0.5 + 0.5 * math.sin(vertical * math.tau * (2.0 + bass * 2.0) - phase)
                amount = clamp(0.06 + activity * 0.64 + ripple * mid * 0.2 + beat * 0.38)
                output[absolute] = mix(stage, mix(color, (255, 246, 180), beat * 0.5), amount)

            else:
                # A warm bass shockwave carries upward while magenta mids and
                # crisp cyan/green high-frequency sparks move independently.
                bass_center = (1.05 - (phase * 0.19) % 1.25)
                bass_wave = math.exp(-(((vertical - bass_center) / (0.08 + bass * 0.07)) ** 2))
                mid_wave = 0.5 + 0.5 * math.sin(vertical * math.tau * 2.4 + phase * 0.73 + lane_number)
                sparkle = max(0.0, math.sin(vertical * 71.0 + phase * 3.7)) ** 9
                dimmed = stage
                color = mix(dimmed, (255, 52, 8), clamp(bass * (0.42 + bass_wave * 0.58)))
                color = mix(color, (244, 26, 255), clamp(mid * mid_wave * 0.72))
                color = mix(color, (40, 255, 116), clamp(treble * sparkle * 0.9))
                output[absolute] = mix(color, (255, 243, 168), beat * (0.3 + bass_wave * 0.7))

    return output
