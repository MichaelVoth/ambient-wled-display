"""Small RGB color and compositing helpers."""

from __future__ import annotations

import math
from typing import Iterable


RGB = tuple[int, int, int]
BLACK: RGB = (0, 0, 0)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def smoothstep(edge0: float, edge1: float, value: float) -> float:
    if edge0 == edge1:
        return 1.0 if value >= edge1 else 0.0
    x = clamp((value - edge0) / (edge1 - edge0))
    return x * x * (3.0 - 2.0 * x)


def parse_hex(value: str) -> RGB:
    text = value.strip().lstrip("#")
    if len(text) != 6:
        raise ValueError(f"invalid RGB color {value!r}")
    return tuple(int(text[index:index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


def mix(first: RGB, second: RGB, amount: float) -> RGB:
    amount = clamp(amount)
    return tuple(round(a + (b - a) * amount) for a, b in zip(first, second))  # type: ignore[return-value]


def scale(color: RGB, amount: float) -> RGB:
    amount = max(0.0, amount)
    return tuple(min(255, round(channel * amount)) for channel in color)  # type: ignore[return-value]


def palette_color(palette: tuple[RGB, ...], position: float) -> RGB:
    position %= 1.0
    scaled = position * len(palette)
    index = int(math.floor(scaled))
    return mix(palette[index % len(palette)], palette[(index + 1) % len(palette)], scaled - index)


def average(colors: Iterable[RGB]) -> RGB:
    values = list(colors)
    if not values:
        return BLACK
    return tuple(round(sum(color[channel] for color in values) / len(values)) for channel in range(3))  # type: ignore[return-value]
