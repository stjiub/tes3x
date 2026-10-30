"""Checks for animation curve conversion used by KF merging."""

import math
from types import SimpleNamespace
import unittest

import struct

from tes3x.kf_merge import (DODGE_GROUPS, KFMergeError, _blend, _interpolate, _window_keys,
                            _xyz_quaternions, dodge_clip_ranges, group_windows)


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
        for component, expected in zip(quaternion, (0.5, 0.5, 0.5, -0.5)):
            self.assertAlmostEqual(component, expected)

    def test_xyz_rotation_applies_x_before_z(self):
        def axis(value):
            return SimpleNamespace(keys=[SimpleNamespace(time=0.0, value=value)])

        data = SimpleNamespace(xyz_rotations=[axis(math.pi / 2), axis(0), axis(math.pi / 2)])
        _, quaternion = next(_xyz_quaternions(data))
        # Rz(90) * Rx(90) sends +X to +Y and +Y to +Z
        for component, expected in zip(quaternion, (0.5, 0.5, 0.5, 0.5)):
            self.assertAlmostEqual(component, expected)

    def test_group_windows_pair_start_and_stop(self):
        text = SimpleNamespace(text_keys=[
            SimpleNamespace(time=1.0, value=b"Idle: Start\r\n"),
            SimpleNamespace(time=2.0, value=b"Idle: Loop Start\r\nIdle2: Start\r\n"),
            SimpleNamespace(time=3.0, value=b"Idle: Stop\r\n")])
        self.assertEqual(group_windows(text), {"idle": (1.0, 3.0)})

    def test_window_keys_add_boundary_keys(self):
        keys = [(0.0, (0.0,)), (2.0, (2.0,)), (4.0, (4.0,))]
        self.assertEqual(_window_keys(keys, 1.0, 3.0), [(1.0, (1.0,)), (2.0, (2.0,)), (3.0, (3.0,))])
        self.assertEqual(_window_keys(keys, 2.0, 4.0), [(2.0, (2.0,)), (4.0, (4.0,))])

    def test_blend_keeps_quaternions_unit_length(self):
        value = _blend([(0.0, (1.0, 0.0, 0.0, 0.0)), (1.0, (0.0, 1.0, 0.0, 0.0))], 0.5)
        self.assertAlmostEqual(math.sqrt(sum(x * x for x in value)), 1.0)

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

    def test_euler_base_rotations_become_quaternion_keys_before_appending(self):
        try:
            from tes3x.kf_merge import _append_rotations, _nif_format
            nif = _nif_format()
        except KFMergeError:
            self.skipTest("PyFFI is not installed")
        dst = nif.NiKeyframeData()
        dst.rotation_type, dst.num_rotation_keys = 4, 1
        for group in dst.xyz_rotations:
            group.num_keys, group.interpolation = 2, 1
            group.keys.update_size()
            group.keys[1].time, group.keys[1].value = 1.0, math.pi / 2
        src = nif.NiKeyframeData()
        src.rotation_type, src.num_rotation_keys = 1, 1
        src.quaternion_keys.update_size()
        src.quaternion_keys[0].value.w = 1.0
        _append_rotations(dst, src, 2.0)
        self.assertEqual((dst.rotation_type, dst.num_rotation_keys), (1, 3))
        self.assertEqual([key.time for key in dst.quaternion_keys], [0.0, 1.0, 2.0])


if __name__ == "__main__":
    unittest.main()
