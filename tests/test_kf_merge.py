"""Checks for animation curve conversion used by KF merging."""

import math
from types import SimpleNamespace
import unittest

import struct

from tes3x.kf_merge import (DODGE_GROUPS, KFMergeError, _interpolate, _xyz_quaternions,
                            dodge_clip_ranges)


def _kf(keys):
    block = b"NiTextKeyExtraData"
    data = b"NetImmerse File Format, Version 4.0.0.2" + bytes([10, 2, 0, 0, 4])
    data += struct.pack("<I", len(block)) + block + struct.pack("<III", 1, 0xFFFFFFFF, len(keys))
    for time, text in keys:
        data += struct.pack("<fI", time, len(text)) + text.encode("ascii")
    return data


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

    def test_dodge_clip_ranges_follow_group_order(self):
        keys = []
        for i, name in enumerate(reversed(DODGE_GROUPS)):
            keys += [(i + 0.0, name + ": Start"), (i + 0.5, name + ": Stop")]
        ranges = dodge_clip_ranges(_kf(keys))
        self.assertEqual(len(ranges), 11)
        self.assertEqual(ranges[-1], (0.0, 0.5))
        self.assertEqual(ranges[0], (10.0, 10.5))

    def test_dodge_clip_ranges_name_the_missing_group(self):
        keys = [(0.0, "missdr: start"), (1.0, "missdr: stop")]
        with self.assertRaisesRegex(KFMergeError, "missdl"):
            dodge_clip_ranges(_kf(keys))


if __name__ == "__main__":
    unittest.main()
