"""Adaptive ambient palettes derived from time and Home Assistant context."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .color import RGB, clamp, mix, palette_color


TIME_MOODS: tuple[tuple[float, str, tuple[RGB, ...]], ...] = (
    (0.0, "deep night", ((9, 5, 56), (25, 23, 142), (0, 126, 167), (89, 27, 181), (188, 18, 162), (15, 73, 142), (20, 8, 82))),
    (5.0, "dawn", ((28, 8, 108), (126, 25, 164), (237, 31, 123), (255, 82, 32), (255, 170, 22), (20, 185, 187), (35, 61, 190))),
    (8.5, "morning", ((14, 35, 166), (0, 137, 215), (0, 220, 173), (47, 226, 72), (246, 210, 17), (255, 77, 24), (184, 35, 213))),
    (12.0, "daylight", ((15, 49, 188), (0, 153, 232), (0, 220, 220), (23, 219, 78), (239, 212, 13), (255, 86, 20), (199, 31, 215))),
    (16.5, "golden afternoon", ((29, 28, 169), (29, 112, 223), (0, 205, 146), (236, 188, 13), (255, 77, 17), (232, 27, 112), (129, 27, 203))),
    (19.5, "evening", ((20, 10, 127), (76, 29, 202), (184, 22, 218), (244, 28, 128), (255, 86, 19), (0, 177, 180), (18, 44, 174))),
    (23.0, "night", ((10, 5, 75), (29, 24, 164), (0, 112, 176), (0, 157, 137), (94, 26, 180), (199, 17, 161), (36, 13, 121))),
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

    saturation = 1.42
    brightness = 0.96
    speed = 0.0035
    cloud_scale = 1.75
    wind_strength = 0.0
    weather_label = weather.replace("-", " ") if weather != "unknown" else "settled"
    emotion = "content"
    reasons: list[str] = []

    if weather in {"sunny", "clear", "clear-night"}:
        if weather == "clear-night":
            palette = tuple(mix(color, (52, 75, 139), 0.09 * expression) for color in palette)
            brightness = 0.9
            saturation = 1.4
            emotion = "dreaming"
            reasons.append("a clear night deepens the palette")
        else:
            palette = tuple(mix(color, (244, 178, 63), 0.07 * expression) for color in palette)
            brightness = 1.0
            saturation = 1.5
            emotion = "radiant"
            reasons.append("clear light warms the palette")
    elif weather in {"partlycloudy", "partly-cloudy", "cloudy", "overcast"}:
        palette = tuple(mix(color, (107, 119, 151), 0.10) for color in palette)
        saturation = 1.28
        brightness = 0.91
        cloud_scale = 2.15
        emotion = "contemplative"
        reasons.append("cloud cover softens the colors")
    elif weather in {"rainy", "pouring", "lightning-rainy", "snowy-rainy", "hail"}:
        storm = ((16, 31, 84), (24, 92, 146), (33, 154, 181), (71, 75, 155), (119, 53, 150), (24, 115, 139), (11, 42, 91))
        amount = (0.48 if weather in {"pouring", "lightning-rainy", "hail"} else 0.34) * expression
        palette = _sample_blend(palette, storm, amount)
        speed = 0.0048
        saturation = 1.43
        brightness = 0.9
        emotion = "stormy" if weather in {"pouring", "lightning-rainy", "hail"} else "brooding"
        reasons.append("rain cools and quickens the house")
    elif weather in {"snowy", "snow"}:
        snow = ((30, 48, 105), (80, 142, 190), (181, 217, 226), (132, 116, 195), (75, 133, 174), (214, 221, 228), (65, 77, 137))
        palette = _sample_blend(palette, snow, 0.52)
        saturation = 1.12
        brightness = 0.94
        emotion = "hushed"
        reasons.append("snow quiets the palette")
    elif weather in {"fog", "foggy"}:
        palette = tuple(mix(color, (135, 125, 160), 0.24) for color in palette)
        speed = 0.0022
        saturation = 1.08
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
