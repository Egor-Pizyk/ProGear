import math
import unittest
from dataclasses import replace

from ProGear.gear_math import (
    ISO54_FIRST, ISO54_SECOND, ISO678_FIRST, ISO678_SECOND,
    Gear, bevel_angle, module_for_diameter, rack_outline,
)


class GearMathTests(unittest.TestCase):
    def setUp(self):
        self.gear = Gear(2, 24, math.radians(20))

    def test_cylindrical_reference_dimensions(self):
        gear = self.gear
        self.assertEqual((2 * gear.pitch, 2 * gear.tip, 2 * gear.root), (48, 52, 43))
        ring = replace(gear, internal=True)
        self.assertEqual((2 * ring.pitch, 2 * ring.tip, 2 * ring.root), (48, 44, 53))

    def test_diameter_inversion(self):
        for gear in (
            self.gear,
            replace(self.gear, helix=math.radians(33), addendum=1.2, shift=0.3),
            replace(self.gear, helix=math.radians(33), normal=False, internal=True),
            replace(self.gear, cone=bevel_angle(24, 18), addendum=0.8),
        ):
            for mode, diameter in (("Pitch diameter", 2 * gear.pitch), ("Tip diameter", 2 * gear.tip)):
                with self.subTest(gear=gear, mode=mode):
                    module = module_for_diameter(
                        diameter, gear.teeth, mode, gear.helix, gear.normal,
                        gear.addendum, gear.shift, gear.internal, gear.cone,
                    )
                    self.assertAlmostEqual(module, gear.module)

    def test_gear_tooth_thinning_and_ring_space(self):
        gear = self.gear
        ring = replace(gear, internal=True, teeth=48)
        self.assertLess(replace(gear, backlash=0.15).sector(12)[2], gear.sector(12)[2])
        self.assertGreater(replace(ring, backlash=0.15).sector(12)[2], ring.sector(12)[2])

    def test_configurations_that_make_teeth_disappear_are_rejected(self):
        with self.assertRaises(ValueError):
            replace(self.gear, addendum=12).sector(12)
        with self.assertRaises(ValueError):
            rack_outline(24, 1, math.radians(30), 5, 2, 1.25, 0)

    def test_rack_pitch_and_total_depth(self):
        vertices = rack_outline(24, 2, math.radians(20), 5, 2, 2.5, 0)
        self.assertAlmostEqual(max(x for x, _ in vertices), 48 * math.pi)
        self.assertAlmostEqual(max(y for _, y in vertices) - min(y for _, y in vertices), 9.5)

    def test_bevel_pitch_cones_are_complementary(self):
        self.assertAlmostEqual(bevel_angle(24, 18) + bevel_angle(18, 24), math.pi / 2)
        self.assertIn(7, ISO54_FIRST)
        self.assertNotIn(7, ISO54_SECOND)
        self.assertIn(7, ISO678_SECOND)
        self.assertNotIn(7, ISO678_FIRST)


if __name__ == "__main__":
    unittest.main()
