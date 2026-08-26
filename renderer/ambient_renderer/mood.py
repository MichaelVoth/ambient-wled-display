"""Adaptive ambient palettes derived from time and Home Assistant context."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .color import RGB, clamp, mix, palette_color


TIME_MOODS: tuple[tuple[float, str, tuple[RGB, ...]], ...] = (
    (0.0, "deep night", ((7, 7, 30), (22, 25, 75), (24, 88, 112), (53, 38, 105), (105, 38, 104), (20, 55, 96), (12, 16, 48))),
    (5.0, "dawn", ((15, 14, 55), (92, 44, 105), (190, 75, 105), (238, 135, 82), (238, 193, 104), (84, 149, 171), (43, 74, 137))),
    (8.5, "morning", ((16, 30, 82), (24, 103, 153), (39, 175, 163), (116, 195, 104), (226, 192, 68), (226, 105, 72), (126, 65, 158))),
    (12.0, "daylight", ((18, 38, 105), (27, 127, 184), (35, 190, 192), (56, 180, 116), (212, 199, 65), (230, 118, 68), (139, 67, 170))),
    (16.5, "golden afternoon", ((29, 37, 99), (53, 109, 168), (49, 171, 154), (210, 173, 61), (235, 116, 64), (188, 66, 117), (91, 58, 151))),
    (19.5, "evening", ((15, 20, 66), (58, 46, 133), (142, 53, 154), (215, 79, 112), (233, 139, 69), (43, 133, 144), (25, 54, 112))),
    (23.0, "night", ((8, 10, 40), (25, 31, 91), (35, 85, 128), (38, 111, 114), (74, 47, 126), (126, 42, 116), (29, 29, 78))),
)


def _sample_blend(first: tuple[RGB, ...], second: tuple[RGB, ...], amount: float) -> tuple[RGB, ...]:
    count = max(len(first), len(second), 7)
    return tuple(
        mix(palette_color(first, index / count), palette_color(second, index / count), amount)
        for index in range(count)
    )


def _time_palette(hour: float) -> tuple[tuple[RGB, ...], str]:
    anchors = TIME_MOODS + ((24.0 + TIME_MOODS[0][0], TIME_MOODS[0][1], TIME_MOODS[0][2]),)
    for index, (start, label, palette) in enumerate(anchors[:-1]):
        end, next_label, next_palette = anchors[index + 1]
        if start <= hour < end:
            progress = (hour - start) / max(0.001, end - start)
            return _sample_blend(palette, next_palette, progress), label
    return TIME_MOODS[0][2], TIME_MOODS[0][1]


def adaptive_ambient(
    timestamp: float,
    context: dict[str, Any],
    expression: float = 1.0,
) -> dict[str, Any]:
    try:
        timezone = ZoneInfo(str(context.get("timezone") or "UTC"))
    except ZoneInfoNotFoundError:
        timezone = ZoneInfo("UTC")
    local = datetime.fromtimestamp(timestamp, timezone)
    hour = local.hour + local.minute / 60.0 + local.second / 3600.0
    palette, time_label = _time_palette(hour)
    weather = str(context.get("weather") or "unknown").lower()
    sun_elevation_value = context.get("sun_elevation")
    if (
        sun_elevation_value is not None
        and float(sun_elevation_value) < -3.0
        and weather in {"sunny", "clear"}
    ):
        weather = "clear-night"
    expression = max(0.5, min(1.5, float(expression)))
    temperature = context.get("temperature")
    unit = str(context.get("temperature_unit") or "°F")
    if temperature is not None:
        temperature = float(temperature)
        fahrenheit = temperature * 9.0 / 5.0 + 32.0 if "c" in unit.lower() else temperature
    else:
        fahrenheit = None

    saturation = 1.18
    brightness = 0.88
    speed = 0.0035
    cloud_scale = 1.75
    wind_strength = 0.0
    weather_label = weather.replace("-", " ") if weather != "unknown" else "settled"
    emotion = "content"
    reasons: list[str] = []

    if weather in {"sunny", "clear", "clear-night"}:
        if weather == "clear-night":
            palette = tuple(mix(color, (52, 75, 139), 0.09 * expression) for color in palette)
            brightness = 0.78
            saturation = 1.16
            emotion = "dreaming"
            reasons.append("a clear night deepens the palette")
        else:
            palette = tuple(mix(color, (244, 178, 63), 0.07 * expression) for color in palette)
            brightness = 0.94
            saturation = 1.25
            emotion = "radiant"
            reasons.append("clear light warms the palette")
    elif weather in {"partlycloudy", "partly-cloudy", "cloudy", "overcast"}:
        palette = tuple(mix(color, (107, 119, 151), 0.10) for color in palette)
        saturation = 1.05
        brightness = 0.82
        cloud_scale = 2.15
        emotion = "contemplative"
        reasons.append("cloud cover softens the colors")
    elif weather in {"rainy", "pouring", "lightning-rainy", "snowy-rainy", "hail"}:
        storm = ((16, 31, 84), (24, 92, 146), (33, 154, 181), (71, 75, 155), (119, 53, 150), (24, 115, 139), (11, 42, 91))
        amount = (0.48 if weather in {"pouring", "lightning-rainy", "hail"} else 0.34) * expression
        palette = _sample_blend(palette, storm, amount)
        speed = 0.0048
        saturation = 1.22
        brightness = 0.78
        emotion = "stormy" if weather in {"pouring", "lightning-rainy", "hail"} else "brooding"
        reasons.append("rain cools and quickens the house")
    elif weather in {"snowy", "snow"}:
        snow = ((30, 48, 105), (80, 142, 190), (181, 217, 226), (132, 116, 195), (75, 133, 174), (214, 221, 228), (65, 77, 137))
        palette = _sample_blend(palette, snow, 0.52)
        saturation = 0.92
        brightness = 0.88
        emotion = "hushed"
        reasons.append("snow quiets the palette")
    elif weather in {"fog", "foggy"}:
        palette = tuple(mix(color, (135, 125, 160), 0.24) for color in palette)
        speed = 0.0022
        saturation = 0.82
        cloud_scale = 2.5
        emotion = "watchful"
        reasons.append("fog slows and mutes the motion")

    cloud_coverage = context.get("cloud_coverage")
    if cloud_coverage is not None:
        cloud = clamp(float(cloud_coverage) / 100.0)
        if cloud > 0.2:
            palette = tuple(mix(color, (91, 102, 139), cloud * 0.08 * expression) for color in palette)
            cloud_scale += cloud * 0.28
            brightness -= cloud * 0.06
            reasons.append(f"{round(cloud * 100):g}% cloud cover adds depth")

    humidity = context.get("humidity")
    if humidity is not None:
        damp = clamp((float(humidity) - 55.0) / 45.0)
        if damp:
            palette = tuple(mix(color, (45, 142, 154), damp * 0.07 * expression) for color in palette)
            cloud_scale += damp * 0.16

    sun_elevation = sun_elevation_value
    if sun_elevation is not None:
        elevation = float(sun_elevation)
        if -6.0 <= elevation <= 8.0:
            horizon = 1.0 - min(1.0, abs(elevation - 1.0) / 8.0)
            palette = tuple(mix(color, (241, 108, 76), horizon * 0.12 * expression) for color in palette)
            reasons.append("low sun warms the horizon")
        elif elevation < -12.0:
            brightness *= 0.9

    presence = str(context.get("presence") or "unknown").lower()
    if presence == "home":
        reasons.append("the house knows it is inhabited")

    if fahrenheit is not None:
        cold = clamp((55.0 - fahrenheit) / 28.0)
        heat = clamp((fahrenheit - 75.0) / 25.0)
        if cold:
            palette = tuple(mix(color, (68, 150, 239), cold * 0.20) for color in palette)
            reasons.append("cool air pulls in blue")
        if heat:
            palette = tuple(mix(color, (255, 87, 40), heat * 0.24) for color in palette)
            speed += heat * 0.0015
            saturation += heat * 0.12
            emotion = "restless" if heat > 0.55 else emotion
            reasons.append("warm air adds coral energy")

    wind = context.get("wind_speed")
    if wind is not None:
        wind_amount = clamp(float(wind) / 35.0)
        wind_strength = wind_amount
        speed += wind_amount * 0.0012 * expression
        if wind_amount > 0.45 and emotion not in {"stormy", "brooding"}:
            emotion = "restless"
        if wind_amount > 0.45:
            reasons.append("wind speeds up the drift")

    if time_label in {"deep night", "night"} and emotion == "content":
        emotion = "dreaming"
    elif time_label == "evening" and emotion == "content":
        emotion = "cozy"

    saturation = 1.0 + (saturation - 1.0) * expression
    speed *= 0.72 + expression * 0.28
    breath_depth = 0.035 + 0.035 * expression
    breath_rate = 0.16 + min(0.16, max(0.0, speed - 0.003) * 32.0)
    life_activity = 0.12
    if time_label in {"evening", "golden afternoon"} and weather in {"sunny", "clear", "partlycloudy", "partly-cloudy"}:
        life_activity = 0.48
    elif time_label in {"night", "deep night"}:
        life_activity = 0.2
    if presence == "home":
        life_activity += 0.1
    if weather in {"rainy", "pouring", "lightning-rainy", "hail"}:
        life_activity = 0.04
    life_activity = clamp(life_activity * expression)
    life_color = (255, 183, 82) if time_label in {"golden afternoon", "evening", "night"} else (86, 220, 205)

    temperature_label = ""
    if temperature is not None:
        temperature_label = f" · {temperature:g}{unit}"
    return {
        "palette": palette,
        "speed": speed,
        "cloud_scale": cloud_scale,
        "saturation": saturation,
        "brightness": brightness,
        "mood": f"{weather_label} {time_label}{temperature_label}",
        "time_mood": time_label,
        "emotion": emotion,
        "reason": " · ".join(dict.fromkeys(reasons)) or "time of day guides a calm living palette",
        "breath_depth": breath_depth,
        "breath_rate": breath_rate,
        "expression": expression,
        "wind_strength": wind_strength,
        "life_activity": life_activity,
        "life_color": life_color,
    }
