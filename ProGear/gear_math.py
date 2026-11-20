"""Gear geometry in millimetres and radians, independent of Fusion."""

from dataclasses import dataclass
from math import acos, atan, cos, hypot, pi, sin, sqrt, tan

ISO54_FIRST = (1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 7, 8, 10, 12, 16, 20, 25, 32, 40, 50)
ISO54_SECOND = (1.125, 1.375, 1.75, 2.25, 2.75, 3.5, 4.5, 5.5, 6.5, 9, 11, 14, 18, 22, 28, 36, 45)
DIN_FINE = (0.05, 0.06, 0.08, 0.1, 0.12, 0.16, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
ISO678_FIRST = tuple(m for m in ISO54_FIRST if m != 7)
ISO678_SECOND = tuple(sorted((*ISO54_SECOND, 7)))


@dataclass(frozen=True)
class Gear:
    module: float
    teeth: int
    pressure: float
    helix: float = 0.0
    normal: bool = True
    addendum: float = 1.0
    dedendum: float = 1.25
    shift: float = 0.0
    backlash: float = 0.0
    internal: bool = False
    inverted: bool = False
    cone: float = 0.0

    @property
    def transverse_module(self):
        return transverse_module(self.module, self.helix, self.normal)

    @property
    def transverse_pressure(self):
        return atan(tan(self.pressure) / cos(self.helix)) if self.normal else self.pressure

    @property
    def pitch(self):
        return self.transverse_module * self.teeth / 2

    @property
    def tip(self):
        delta = self.addendum + self.shift
        if self.internal:
            return self.pitch - self.module * self.addendum
        return self.pitch + self.module * delta * cos(self.cone)

    @property
    def root(self):
        if self.internal:
            return self.pitch + self.module * self.dedendum
        return self.pitch - self.module * (self.dedendum - self.shift) * cos(self.cone)

    @property
    def twist_per_mm(self):
        return tan(self.helix) / self.pitch

    def sector(self, samples):
        """Three curves for one tooth (or one space in a conventional ring gear)."""
        pitch_angle = 2 * pi / self.teeth
        pressure = self.transverse_pressure
        pitch_half = pitch_angle / 4 + (
            self.shift * self.module * tan(self.pressure)
            + (1 if self.internal and not self.inverted else -1) * self.backlash / 2
        ) / self.pitch
        base = self.pitch * cos(pressure)
        low, high = sorted((self.root, self.tip))
        if low <= 0 or high <= low or high <= base:
            raise ValueError("Tooth heights leave no usable involute.")

        def inv_at(radius):
            return involute(acos(min(1.0, base / radius)))

        def half_angle(radius):
            return pitch_half + (involute(pressure) - inv_at(radius)) / cos(self.cone)

        def check_angles(angles):
            if any(not 0 < angle < pitch_angle / 2 for angle in angles):
                raise ValueError(
                    "Teeth cross or vanish. Adjust tooth heights, pressure angle, shift or backlash."
                )

        if self.inverted:
            # Reflect the external tooth in the radial direction, then use its
            # complementary angular width to draw the space in the ring.
            if high >= 2 * self.pitch:
                raise ValueError("The inverted profile extends beyond the reference circle.")
            flank = []
            for index in range(samples):
                radius = low + (high - low) * index / (samples - 1)
                external_radius = 2 * self.pitch - radius
                material_half = half_angle(external_radius) * external_radius / radius
                flank.append((radius, pitch_angle / 2 - material_half))
            check_angles(angle for _, angle in flank)
            return [], flank, flank[-1][1]

        low_half, high_half = half_angle(low), half_angle(high)
        check_angles((low_half, high_half))
        start = max(low, base)
        start_roll = sqrt((start / base) ** 2 - 1)
        end_roll = sqrt((high / base) ** 2 - 1)
        flank = []
        for index in range(samples):
            roll = start_roll + (end_roll - start_roll) * index / (samples - 1)
            radius = base * hypot(1, roll)
            flank.append((radius, half_angle(radius)))
        # Below the base circle the flank is continued radially.
        if low < start:
            flank_start = [(low, low_half), flank[0]]
        else:
            flank_start = []
        return flank_start, flank, high_half


def involute(angle):
    return tan(angle) - angle


def transverse_module(module, helix, normal):
    return module / cos(helix) if normal else module


def module_for_diameter(diameter, teeth, mode, helix=0, normal=True,
                        addendum=1, shift=0, internal=False, cone=0):
    factor = teeth / cos(helix) if normal else teeth
    if mode == "Tip diameter":
        factor += -2 * addendum if internal else 2 * (addendum + shift) * cos(cone)
    if factor <= 0:
        raise ValueError("Diameter and tooth heights do not define a positive module.")
    return diameter / factor


def bevel_angle(teeth, mate_teeth):
    return atan(teeth / mate_teeth)


def rack_outline(teeth, module, pressure, backing, addendum, dedendum, backlash):
    pitch = pi * module
    half = (pitch / 2 - backlash) / 2
    top, root = half - addendum * tan(pressure), half + dedendum * tan(pressure)
    if not 0 < top < root < pitch / 2:
        raise ValueError("Rack teeth cross or vanish. Adjust heights, pressure angle or backlash.")
    end = teeth * pitch
    result = [(0, -dedendum - backing), (end, -dedendum - backing), (end, -dedendum)]
    for index in reversed(range(teeth)):
        x = (index + 0.5) * pitch
        result.extend(((x + root, -dedendum), (x + top, addendum),
                       (x - top, addendum), (x - root, -dedendum)))
    result.append((0, -dedendum))
    return result


def polar(radius, angle):
    return radius * cos(angle), radius * sin(angle)
