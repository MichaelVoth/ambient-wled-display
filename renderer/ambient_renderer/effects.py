"""Frame effects and semantic layers used by the renderer."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .color import BLACK, RGB, mix, palette_color, saturate, scale, smoothstep
from .config import DeviceConfig, LaneConfig


@dataclass(frozen=True)
class HourTiming:
    sweep: float = 8.0
    blackout: float = 0.6
    toll_interval: float = 1.0
    hold: float = 5.0
    restore: float = 3.0
    feather_fraction: float = 0.065
    dot_fade: float = 0.18
    top_offset: int = 5
    dot_gap: int = 3


@dataclass
class HourEvent:
    hour: int
    started_at: float
    timing: HourTiming = HourTiming()
    priority: int = 50
    kind: str = "hour"
    targets: tuple[str, ...] | None = None

    @property
    def count(self) -> int:
        return self.hour % 12 or 12

    @property
    def last_toll_at(self) -> float:
        return max(0, self.count - 1) * self.timing.toll_interval

    @property
    def duration(self) -> float:
        return (
            self.timing.sweep
            + self.timing.blackout
            + self.last_toll_at
            + self.timing.hold
            + self.timing.restore
        )

    def phase(self, now: float) -> tuple[str, float]:
        elapsed = max(0.0, now - self.started_at)
        if elapsed < self.timing.sweep:
            return "sweep", elapsed / self.timing.sweep
        elapsed -= self.timing.sweep
        if elapsed < self.timing.blackout:
            return "blackout", elapsed / self.timing.blackout
        elapsed -= self.timing.blackout
        if elapsed < self.last_toll_at:
            return "toll", elapsed
        elapsed -= self.last_toll_at
        if elapsed < self.timing.hold:
            return "hold", elapsed / self.timing.hold
        elapsed -= self.timing.hold
        if elapsed < self.timing.restore:
            return "restore", elapsed / self.timing.restore
        return "complete", 1.0

    def is_complete(self, now: float) -> bool:
        return now - self.started_at >= self.duration


@dataclass
class AlertEvent:
    started_at: float
    color: RGB = (255, 40, 15)
    duration: float = 6.0
    priority: int = 80
    kind: str = "alert"
    targets: tuple[str, ...] | None = None

    def is_complete(self, now: float) -> bool:
        return now - self.started_at >= self.duration

    def phase(self, now: float) -> tuple[str, float]:
        return ("alert", min(1.0, max(0.0, (now - self.started_at) / self.duration)))


SIGNAL_DEFAULTS = {
    "welcome": (8.0, 58),
    "comfort": (10.0, 52),
    "curious": (7.0, 54),
    "goodbye": (8.0, 57),
    "storm": (6.0, 70),
    "reminder": (8.0, 55),
    "success": (5.0, 60),
    "celebration": (12.0, 65),
    "warning": (8.0, 75),
}


@dataclass
class SignalEvent:
    signal: str
    started_at: float
    duration: float | None = None
    priority: int | None = None
    targets: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.signal not in SIGNAL_DEFAULTS:
            raise ValueError(f"unknown signal {self.signal!r}")
        default_duration, default_priority = SIGNAL_DEFAULTS[self.signal]
        if self.duration is None:
            self.duration = default_duration
        if self.priority is None:
            self.priority = default_priority

    @property
    def kind(self) -> str:
        return self.signal

    def is_complete(self, now: float) -> bool:
        return now - self.started_at >= float(self.duration)

    def phase(self, now: float) -> tuple[str, float]:
        elapsed = max(0.0, now - self.started_at)
        duration = float(self.duration)
        if elapsed < 0.6:
            return "intro", elapsed / 0.6
        if elapsed < duration - 1.5:
            return "display", (elapsed - 0.6) / max(0.001, duration - 2.1)
        if elapsed < duration:
            return "restore", (elapsed - (duration - 1.5)) / 1.5
        return "complete", 1.0


def lane_local_index(lane: LaneConfig, distance_from_top: int) -> int:
    if lane.top_at_high_index:
        return lane.start + lane.length - 1 - distance_from_top
    return lane.start + distance_from_top


def lane_distance_from_top(lane: LaneConfig, absolute_index: int) -> float:
    local = absolute_index - lane.start
    distance = lane.length - 1 - local if lane.top_at_high_index else local
    return distance / max(1, lane.length - 1)


def lava_color(
    palette: tuple[RGB, ...],
    vertical: float,
    now: float,
    speed: float,
    scale_factor: float,
    lane_number: int,
    wind_strength: float,
) -> RGB:
    """Return a multi-scale flowing color field with unequal organic regions."""
    scale_factor = max(0.3, scale_factor)
    drift = now * speed
    # One body can wash across almost the entire strip while smaller ones bud,
    # merge, and travel independently instead of forming repeating bands.
    radii = (0.88, 0.46, 0.27, 0.15, 0.075, 0.035)
    weighted = [0.0, 0.0, 0.0]
    total = 0.0
    for blob, radius in enumerate(radii):
        seed = ((blob * 47 + lane_number * 29 + 13) % 97) / 97.0
        direction = -1.0 if blob % 2 else 1.0
        travel = drift * direction * (0.12 + blob * 0.047)
        wobble = math.sin(now * speed * (0.7 + blob * 0.21) + seed * math.tau) * (0.04 + blob * 0.007)
        center = (seed + travel + wobble + wind_strength * now * 0.004) % 1.0
        distance = min(abs(vertical - center), 1.0 - abs(vertical - center))
        changing_radius = radius * scale_factor * (
            0.82 + 0.24 * math.sin(now * speed * (0.31 + blob * 0.09) + seed * 11.0)
        )
        weight = math.exp(-0.5 * (distance / max(0.012, changing_radius)) ** 2)
        color_position = seed + drift * (0.035 + blob * 0.017) + blob * 0.137
        blob_color = palette_color(palette, color_position)
        weight *= 1.18 if blob < 2 else 0.82
        for channel in range(3):
            weighted[channel] += blob_color[channel] * weight
        total += weight

    background = palette_color(palette, drift * 0.028 + lane_number * 0.07)
    background_weight = 0.12
    return tuple(
        round((weighted[channel] + background[channel] * background_weight) / (total + background_weight))
        for channel in range(3)
    )  # type: ignore[return-value]


def render_base(
    device: DeviceConfig,
    now: float,
    palette: tuple[RGB, ...],
    palette_speed: float,
    rain: bool,
    focus: bool,
    rain_lanes: tuple[LaneConfig, ...] | None = None,
    focus_lanes: tuple[LaneConfig, ...] | None = None,
    cloud_scale: float = 1.0,
    saturation: float = 1.0,
    ambient_brightness: float = 1.0,
    breath_rate: float = 0.21,
    breath_depth: float = 0.06,
    wind_strength: float = 0.0,
    life_activity: float = 0.0,
    life_color: RGB = (86, 220, 205),
) -> list[RGB]:
    frame = [BLACK] * device.pixel_count
    rain_lane_ids = None if rain_lanes is None else {lane.id for lane in rain_lanes}
    focus_lane_ids = None if focus_lanes is None else {lane.id for lane in focus_lanes}
    for lane_number, lane in enumerate(device.lanes):
        # Organic bodies are spatially smooth, so evaluate their expensive
        # multi-blob field every three LEDs and interpolate between control
        # points. This preserves small pockets while leaving frame time for
        # music, rain, and semantic layers on a Raspberry Pi.
        sample_count = max(2, math.ceil(lane.length / 3) + 1)
        lava_samples = [
            lava_color(
                palette, sample / (sample_count - 1), now, palette_speed,
                cloud_scale, lane_number, wind_strength,
            )
            for sample in range(sample_count)
        ]
        for absolute in range(lane.start, lane.start + lane.length):
            vertical = lane_distance_from_top(lane, absolute)
            sample_position = vertical * (sample_count - 1)
            sample_index = min(sample_count - 2, int(sample_position))
            color = mix(
                lava_samples[sample_index], lava_samples[sample_index + 1],
                sample_position - sample_index,
            )
            breath = (
                1.0
                - breath_depth
                + breath_depth * math.sin(now * breath_rate + vertical * math.tau * 0.8)
                + 0.012 * math.sin(now * 0.037 + lane_number * 1.7)
            )
            color = saturate(color, saturation)
            color = scale(color, breath * device.brightness * ambient_brightness)
            if life_activity > 0.01:
                cell = min(11, int(vertical * 12.0))
                cell_start = cell / 12.0
                seed = ((cell * 73 + lane_number * 41 + 19) % 101) / 101.0
                center = cell_start + (0.15 + seed * 0.7) / 12.0
                point = math.exp(-(((vertical - center) / 0.012) ** 2))
                period = 3.7 + (cell * 17 % 23) / 5.0
                pulse = max(0.0, math.sin(now * math.tau / period + seed * math.tau)) ** 5
                color = mix(color, life_color, min(0.34, life_activity * point * pulse * 0.42))
            if rain and (rain_lane_ids is None or lane.id in rain_lane_ids):
                # Cool the complete palette, then add narrow downward-moving
                # cyan drops. The motion is deterministic and continuous, so
                # it remains smooth without storing per-frame particle state.
                color = mix(color, (8, 66, 145), 0.34)
                drop_phase = (vertical - now * 0.22 - lane_number * 0.07) % 0.29
                drop = math.exp(-((drop_phase / 0.027) ** 2))
                color = mix(color, (72, 190, 255), 0.52 * drop)
            if focus and (focus_lane_ids is None or lane.id in focus_lane_ids):
                color = scale(color, 0.62)
            frame[absolute] = color
    return frame


def render_hour(
    base: list[RGB],
    device: DeviceConfig,
    event: HourEvent,
    now: float,
    lanes: tuple[LaneConfig, ...] | None = None,
) -> list[RGB]:
    selected_lanes = device.lanes if lanes is None else lanes
    phase, value = event.phase(now)
    if phase == "complete":
        return list(base)
    if phase == "sweep":
        output = list(base)
        feather = event.timing.feather_fraction
        edge = value * (1.0 + feather)
        for lane in selected_lanes:
            for absolute in range(lane.start, lane.start + lane.length):
                distance = lane_distance_from_top(lane, absolute)
                keep = smoothstep(edge - feather, edge, distance)
                output[absolute] = scale(base[absolute], keep)
        return output
    if phase == "blackout":
        output = list(base)
        for lane in selected_lanes:
            output[lane.start:lane.start + lane.length] = [BLACK] * lane.length
        return output

    dots = list(base)
    for lane in selected_lanes:
        dots[lane.start:lane.start + lane.length] = [BLACK] * lane.length
    toll_elapsed = 0.0
    if phase == "toll":
        toll_elapsed = value
    elif phase == "hold":
        toll_elapsed = event.last_toll_at + min(
            event.timing.dot_fade,
            value * event.timing.hold,
        )
    elif phase == "restore":
        toll_elapsed = event.last_toll_at + event.timing.dot_fade
    visible = min(event.count, int(toll_elapsed / event.timing.toll_interval) + 1)
    for lane in selected_lanes:
        for number in range(visible):
            distance = event.timing.top_offset + number * (event.timing.dot_gap + 1)
            if distance >= lane.length:
                continue
            appeared_at = number * event.timing.toll_interval
            alpha = smoothstep(0.0, event.timing.dot_fade, toll_elapsed - appeared_at)
            absolute = lane_local_index(lane, distance)
            dots[absolute] = scale(base[absolute], max(0.35, alpha))

    if phase == "restore":
        return [mix(dot, underlying, smoothstep(0.0, 1.0, value)) for dot, underlying in zip(dots, base)]
    return dots


def render_alert(
    base: list[RGB],
    device: DeviceConfig,
    event: AlertEvent,
    now: float,
    lanes: tuple[LaneConfig, ...] | None = None,
) -> list[RGB]:
    elapsed = max(0.0, now - event.started_at)
    envelope = min(1.0, elapsed / 0.25, max(0.0, (event.duration - elapsed) / 0.8))
    pulse = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(elapsed * math.tau * 2.0))
    output = list(base)
    for lane in device.lanes if lanes is None else lanes:
        for index in range(lane.start, lane.start + lane.length):
            output[index] = mix(base[index], event.color, envelope * pulse)
    return output


def render_signal(
    base: list[RGB],
    device: DeviceConfig,
    event: SignalEvent,
    now: float,
    lanes: tuple[LaneConfig, ...] | None = None,
) -> list[RGB]:
    elapsed = max(0.0, now - event.started_at)
    duration = float(event.duration)
    envelope = smoothstep(0.0, 0.6, elapsed) * (
        1.0 - smoothstep(duration - 1.5, duration, elapsed)
    )
    output = list(base)
    selected_lanes = device.lanes if lanes is None else lanes

    for lane in selected_lanes:
        for absolute in range(lane.start, lane.start + lane.length):
            vertical = lane_distance_from_top(lane, absolute)
            underlying = base[absolute]

            if event.signal == "reminder":
                # A calm amber beacon in the visible top third.
                region = 1.0 - smoothstep(0.18, 0.36, vertical)
                breath = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(elapsed * math.tau / 2.2))
                amount = envelope * region * (0.28 + 0.56 * breath)
                output[absolute] = mix(underlying, (255, 142, 0), min(1.0, amount * 1.12))

            elif event.signal == "welcome":
                # Warmth rises into the room, then glints near the visible top.
                center = 1.08 - min(1.0, elapsed / 2.8) * 1.16
                wave = math.exp(-(((vertical - center) / 0.09) ** 2))
                top_glow = (1.0 - smoothstep(0.12, 0.48, vertical)) * smoothstep(1.0, 3.2, elapsed)
                sparkle = 1.0 if ((absolute * 19 + int(elapsed * 9)) % 67) < 2 else 0.0
                warmth = mix((255, 112, 10), (255, 31, 158), vertical * 0.48)
                amount = envelope * min(0.98, wave * 0.9 + top_glow * 0.4 + sparkle * top_glow * 0.4)
                output[absolute] = mix(underlying, warmth, amount)

            elif event.signal == "comfort":
                # Two soft fronts meet like an embrace and settle into a slow glow.
                approach = smoothstep(0.0, 3.2, elapsed) * 0.5
                first = math.exp(-(((vertical - approach) / 0.13) ** 2))
                second = math.exp(-(((vertical - (1.0 - approach)) / 0.13) ** 2))
                held = 0.22 * smoothstep(2.4, 4.0, elapsed)
                pulse = 0.72 + 0.28 * math.sin(elapsed * math.tau / 3.4)
                amount = envelope * min(0.94, (max(first, second) * 0.74 + held) * pulse)
                output[absolute] = mix(underlying, (255, 64, 43), amount)

            elif event.signal == "curious":
                # Independent points investigate the lane without reading as an alarm.
                first_center = 0.5 + 0.39 * math.sin(elapsed * 1.45)
                second_center = 0.5 + 0.34 * math.sin(elapsed * 0.93 + 2.1)
                first = math.exp(-(((vertical - first_center) / 0.045) ** 2))
                second = math.exp(-(((vertical - second_center) / 0.065) ** 2))
                cyan = mix(underlying, (0, 241, 224), envelope * first * 0.96)
                output[absolute] = mix(cyan, (237, 20, 255), envelope * second * 0.9)

            elif event.signal == "goodbye":
                # A cool veil moves down, leaving a small warm memory at the top.
                edge = min(1.12, elapsed / 4.5)
                veil = 1.0 - smoothstep(edge - 0.12, edge + 0.03, vertical)
                memory = (1.0 - smoothstep(0.04, 0.2, vertical)) * max(0.0, 1.0 - elapsed / 6.5)
                cooled = mix(scale(underlying, 0.32), (35, 18, 169), 0.62)
                output[absolute] = mix(underlying, cooled, envelope * veil * 0.82)
                output[absolute] = mix(output[absolute], (255, 92, 12), envelope * memory * 0.48)

            elif event.signal == "storm":
                # Deterministic clusters imitate irregular lightning without
                # relying on frame-rate-dependent randomness.
                flashes = (0.32, 0.49, 1.72, 3.08, 3.24, 4.86)
                flash = max(
                    math.exp(-(((elapsed - moment) / (0.045 if index % 2 else 0.075)) ** 2))
                    for index, moment in enumerate(flashes)
                )
                branch = 0.72 + 0.28 * math.sin(vertical * math.tau * 5.7 + elapsed * 17.0)
                charged = mix(underlying, scale(underlying, 0.55), envelope * 0.18)
                output[absolute] = mix(charged, (220, 228, 255), envelope * flash * branch)

            elif event.signal == "success":
                # A green wave rises from bottom to top, leaving a soft glow.
                center = 1.08 - min(1.0, elapsed / 2.7) * 1.16
                wave = math.exp(-(((vertical - center) / 0.075) ** 2))
                glow = 0.16 * smoothstep(0.0, 1.4, elapsed)
                output[absolute] = mix(
                    underlying,
                    (28, 255, 70),
                    envelope * min(1.0, glow + 0.88 * wave),
                )

            elif event.signal == "warning":
                # Amber means attention. Red remains reserved for urgent alerts.
                pulse = 0.5 + 0.5 * math.sin(elapsed * math.tau * 1.35)
                output[absolute] = mix(
                    underlying,
                    (255, 116, 24),
                    envelope * (0.28 + 0.72 * pulse),
                )

            elif event.signal == "celebration":
                # Repeating launches and expanding colored rings make a
                # readable one-dimensional firework without random flicker.
                colors = (
                    (255, 28, 91),
                    (255, 174, 0),
                    (0, 220, 255),
                    (131, 42, 255),
                    (27, 255, 91),
                )
                cycle_length = 1.35
                cycle_index = int(elapsed / cycle_length)
                cycle = elapsed % cycle_length
                burst_color = colors[cycle_index % len(colors)]
                dimmed = mix(underlying, scale(underlying, 0.5), envelope)
                if cycle < 0.52:
                    center = 1.03 - (cycle / 0.52) * (0.78 + 0.08 * (cycle_index % 3))
                    launch = math.exp(-(((vertical - center) / 0.035) ** 2))
                    amount = envelope * launch
                else:
                    age = cycle - 0.52
                    center = 0.16 + 0.08 * (cycle_index % 4)
                    radius = age * 0.42
                    ring = max(
                        math.exp(-(((vertical - center - radius) / 0.042) ** 2)),
                        math.exp(-(((vertical - center + radius) / 0.042) ** 2)),
                    )
                    decay = max(0.0, 1.0 - age / 0.83)
                    sparkle = 1.0 if ((absolute * 17 + cycle_index * 29) % 53) < 3 else 0.0
                    amount = envelope * decay * min(1.0, ring + sparkle * 0.38)
                output[absolute] = mix(dimmed, burst_color, amount)

    return output
