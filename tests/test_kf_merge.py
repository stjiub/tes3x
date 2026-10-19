"""Checks for animation curve conversion used by KF merging."""

import math
from types import SimpleNamespace
import unittest

from tes3x.kf_merge import _interpolate, _xyz_quaternions


class KFMergeTests(unittest.TestCase):
    def test_quadratic_tangents_shape_the_curve(self):
        left = SimpleNamespace(value=0.0, forward=0.0)
        right = SimpleNamespace(value=1.0, backward=0.0)
        self.assertAlmostEqual(_interpolate(left, right, 0.25, 2), 0.15625)

    def test_xyz_rotation_multiplies_axes_in_order(self):
        def axis(value):
            return SimpleNamespace(keys=[SimpleNamespace(time=0.0, value=value)])

        data = SimpleNamespace(xyz_rotations=[axis(math.pi / 2), axis(math.pi / 2), axis(0)])
        time, quaternion = next(_xyz_quaternions(data))
        self.assertEqual(time, 0)
        for component in quaternion:
            self.assertAlmostEqual(component, 0.5)


if __name__ == "__main__":
    unittest.main()
