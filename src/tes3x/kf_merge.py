"""Append NetImmerse 4.0.0.2 animation clips to a base KF."""

import argparse
import math
from pathlib import Path
import struct
import sys
import time
from types import SimpleNamespace

from tes3x.bsa import Bsa, tes3_hash


class KFMergeError(ValueError):
    pass


MAX_KEY_TURN = math.pi / 3
KEYFRAME_TAG = struct.pack("<I", len(b"NiKeyframeData")) + b"NiKeyframeData"
XYZ_ROTATION = 4
# Bytes per key by interpolation: linear, quadratic, TBC.
FLOAT_KEY_BYTES = {1: 8, 2: 16, 3: 20}
VECTOR_KEY_BYTES = {1: 16, 2: 40, 3: 28}


def _nif_format():
    # PyFFI 2.2.3 still calls time.clock and imports the setuptools distutils shim.
    if not hasattr(time, "clock"):
        time.clock = time.perf_counter
    try:
        import setuptools  # noqa: F401
        from pyffi.formats.nif import NifFormat
    except ImportError as exc:
        raise KFMergeError(
            "KF merging needs PyFFI 2.2.3, which is not installed for this Python. "
            f'Install it with: "{sys.executable}" -m pip install "tes3x[kf]"'
        ) from exc
    return NifFormat


def _read(path, nif):
    data = nif.Data()
    try:
        with Path(path).open("rb") as stream:
            data.read(stream)
    except (ValueError, EOFError, IndexError) as exc:
        raise KFMergeError(f"{path}: invalid animation KF: {exc}") from exc
    if data.version != 0x04000002 or len(data.roots) != 1:
        raise KFMergeError(f"{path}: expected one NetImmerse 4.0.0.2 sequence")
    root = data.roots[0]
    if not isinstance(root, nif.NiSequenceStreamHelper) or not isinstance(
            root.extra_data, nif.NiTextKeyExtraData):
        raise KFMergeError(f"{path}: expected an animation sequence with text keys")
    names = []
    labels = {}
    extra = root.extra_data.next_extra_data
    while extra:
        if not isinstance(extra, nif.NiStringExtraData):
            raise KFMergeError(f"{path}: unsupported extra-data block")
        label = bytes(extra.string_data)
        name = label.lower()
        if name in names:
            raise KFMergeError(f"{path}: duplicate bone {name!r}")
        names.append(name)
        labels[name] = label
        extra = extra.next_extra_data
    controls = []
    control = root.controller
    while control:
        if not isinstance(control, nif.NiKeyframeController) or not isinstance(
                control.data, nif.NiKeyframeData):
            raise KFMergeError(f"{path}: unsupported controller")
        controls.append(control)
        control = control.next_controller
    if len(names) != len(controls):
        raise KFMergeError(f"{path}: bone and controller counts differ")
    return data, dict(zip(names, controls)), names, labels


def _components(value):
    if hasattr(value, "x"):
        return (value.x, value.y, value.z)
    return value


def _set_components(target, value):
    if isinstance(value, tuple):
        target.x, target.y, target.z = value
    else:
        return value
    return target


def _interpolate(left, right, fraction, kind):
    a, b = _components(left.value), _components(right.value)
    if kind == 1:
        weights = (1 - fraction, fraction, 0, 0)
    elif kind == 2:
        t = fraction
        weights = (2*t*t*t - 3*t*t + 1, -2*t*t*t + 3*t*t,
                   t*t*t - 2*t*t + t, t*t*t - t*t)
    else:
        raise KFMergeError(f"unsupported key interpolation {kind}")
    outward = _components(left.forward) if kind == 2 else 0
    inward = _components(right.backward) if kind == 2 else 0
    if isinstance(a, tuple):
        if kind == 1:
            outward = inward = (0, 0, 0)
        return tuple(sum(values[i] * weight for values, weight in
                         zip((a, b, outward, inward), weights)) for i in range(3))
    return sum(value * weight for value, weight in
               zip((a, b, outward, inward), weights))


def _sample_keys(group, dst_type):
    keys = list(group.keys)
    if group.interpolation == dst_type:
        return [(key.time, _components(key.value), key) for key in keys]
    if group.interpolation not in (1, 2) or dst_type != 1:
        raise KFMergeError(f"cannot convert interpolation {group.interpolation} to {dst_type}")
    samples = [(keys[0].time, _components(keys[0].value), keys[0])]
    for left, right in zip(keys, keys[1:]):
        duration = right.time - left.time
        if duration <= 0:
            raise KFMergeError("animation keys must increase in time")
        steps = max(1, math.ceil(duration * 30)) if group.interpolation == 2 else 1
        for step in range(1, steps):
            fraction = step / steps
            samples.append((left.time + duration * fraction,
                            _interpolate(left, right, fraction, group.interpolation), None))
        samples.append((right.time, _components(right.value), right))
    return samples


def _append_group(dst, src, offset):
    if not src.num_keys:
        return
    if src.interpolation not in (1, 2):
        raise KFMergeError(f"unsupported key interpolation {src.interpolation}")
    old = dst.num_keys
    if not old:
        dst.interpolation = 1
    if dst.interpolation not in (1, 2, 3):
        raise KFMergeError(f"unsupported base interpolation {dst.interpolation}")
    samples = _sample_keys(src, dst.interpolation)
    dst.num_keys = old + len(samples)
    dst.keys.update_size()
    for i, (at, value, source) in enumerate(samples):
        target = dst.keys[old + i]
        target.time = at + offset
        assigned = _set_components(target.value, value)
        if not isinstance(value, tuple):
            target.value = assigned
        if dst.interpolation == 2:
            for field in ("forward", "backward"):
                tangent = _components(getattr(source, field))
                assigned = _set_components(getattr(target, field), tangent)
                if not isinstance(value, tuple):
                    setattr(target, field, assigned)
        elif dst.interpolation == 3:
            raise KFMergeError("appending to TBC position or scale keys is unsupported")


def _axis(group, at):
    keys = list(group.keys)
    if not keys:
        return 0.0
    if at <= keys[0].time:
        return keys[0].value
    for left, right in zip(keys, keys[1:]):
        if at <= right.time:
            fraction = (at - left.time) / (right.time - left.time)
            return left.value + (right.value - left.value) * fraction
    return keys[-1].value


def _xyz_times(data):
    """Key times, subdivided so no span turns far enough for a quaternion to go the short way."""
    times = sorted({key.time for group in data.xyz_rotations for key in group.keys})
    result = times[:1]
    for left, right in zip(times, times[1:]):
        turn = max(abs(_axis(group, right) - _axis(group, left)) for group in data.xyz_rotations)
        steps = max(1, math.ceil(turn / MAX_KEY_TURN))
        result += [left + (right - left) * step / steps for step in range(1, steps + 1)]
    return result


def _xyz_quaternions(data):
    previous = None
    for at in _xyz_times(data):
        x, y, z = (_axis(group, at) / 2 for group in data.xyz_rotations)
        cx, sx = math.cos(x), math.sin(x)
        cy, sy = math.cos(y), math.sin(y)
        cz, sz = math.cos(z), math.sin(z)
        # NetImmerse applies X, then Y, then Z: R = Rz * Ry * Rx.
        value = (cx * cy * cz + sx * sy * sz,
                 sx * cy * cz - cx * sy * sz,
                 cx * sy * cz + sx * cy * sz,
                 cx * cy * sz - sx * sy * cz)
        if previous and sum(a * b for a, b in zip(previous, value)) < 0:
            value = tuple(-part for part in value)
        previous = value
        yield at, value


def _xyz_to_quaternions(data):
    """Resample Euler rotation tracks as linear quaternion keys at their combined key times."""
    values = list(_xyz_quaternions(data))
    data.rotation_type = 1
    data.num_rotation_keys = len(values)
    data.quaternion_keys.update_size()
    for key, (at, quaternion) in zip(data.quaternion_keys, values):
        key.time = at
        key.value.w, key.value.x, key.value.y, key.value.z = quaternion


def _append_rotations(dst, src, offset):
    if not src.num_rotation_keys:
        return
    if dst.num_rotation_keys and dst.rotation_type == 4:
        _xyz_to_quaternions(dst)
    if dst.num_rotation_keys and dst.rotation_type not in (1, 3):
        raise KFMergeError(f"unsupported base rotation type {dst.rotation_type}")
    if src.rotation_type == 4:
        values = list(_xyz_quaternions(src))
    elif src.rotation_type in (1, 3):
        values = [(key.time, (key.value.w, key.value.x, key.value.y, key.value.z))
                  for key in src.quaternion_keys]
    else:
        raise KFMergeError(f"unsupported source rotation type {src.rotation_type}")
    old = dst.num_rotation_keys
    if not old:
        dst.rotation_type = 1
    dst.num_rotation_keys = old + len(values)
    dst.quaternion_keys.update_size()
    for i, (at, quaternion) in enumerate(values):
        key = dst.quaternion_keys[old + i]
        key.time = at + offset
        key.value.w, key.value.x, key.value.y, key.value.z = quaternion
        if dst.rotation_type == 3:
            key.tbc.tension = 0.0
            key.tbc.bias = 0.0
            key.tbc.continuity = 0.0


def _append_data(dst, src, offset):
    _append_rotations(dst, src, offset)
    _append_group(dst.translations, src.translations, offset)
    _append_group(dst.scales, src.scales, offset)


def _append_text(dst, src, offset):
    old = dst.num_text_keys
    dst.num_text_keys = old + src.num_text_keys
    dst.text_keys.update_size()
    for i, key in enumerate(src.text_keys):
        target = dst.text_keys[old + i]
        target.time = key.time + offset
        target.value = key.value


def merge_kfs(base, additions, output):
    """Merge user-supplied animation tracks, retaining the base KF's existing keys."""
    nif = _nif_format()
    data, controls, names, _ = _read(base, nif)
    root = data.roots[0]
    all_controls = list(controls.values())
    text = root.extra_data
    end = max((key.time for key in text.text_keys), default=0.0)
    if not additions:
        raise KFMergeError("at least one animation KF is required")
    output = Path(output)
    if any(output.resolve() == Path(path).resolve() for path in (base, *additions)):
        raise KFMergeError("output must differ from every input KF")
    for path in additions:
        source, source_controls, source_names, source_labels = _read(path, nif)
        source_root = source.roots[0]
        duration = max((key.time for key in source_root.extra_data.text_keys), default=0.0)
        offset = end + 0.1
        _append_text(text, source_root.extra_data, offset)
        for name in source_names:
            source_control = source_controls[name]
            if name not in controls:
                extra = nif.NiStringExtraData()
                extra.string_data = source_labels[name]
                tail = text
                while tail.next_extra_data:
                    tail = tail.next_extra_data
                tail.next_extra_data = extra
                control = nif.NiKeyframeController()
                for field in ("flags", "frequency", "phase", "start_time"):
                    setattr(control, field, getattr(source_control, field))
                control.data = nif.NiKeyframeData()
                all_controls[-1].next_controller = control
                all_controls.append(control)
                controls[name] = control
                names.append(name)
            _append_data(controls[name].data, source_control.data, offset)
        end = offset + duration
        for control in all_controls:
            control.stop_time = end
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as stream:
        data.write(stream)
    return output


_WINDOW_EPS = 1e-3
SPLICE_CHANNELS = ("translation", "rotation")


def group_windows(text):
    """Map each lower-cased group name to its (start, stop) times from a NiTextKeyExtraData."""
    found = {}
    for key in text.text_keys:
        for line in bytes(key.value).decode("latin-1").replace("\r", "").split("\n"):
            name, sep, phase = line.partition(":")
            phase = phase.strip().lower()
            if sep and phase in ("start", "stop"):
                found.setdefault(name.strip().lower(), {})[phase] = key.time
    return {name: (v["start"], v["stop"]) for name, v in found.items() if len(v) == 2}


def _channel_keys(data, channel):
    if channel == "translation":
        if data.translations.interpolation != 1:
            raise KFMergeError("splicing needs linear translation keys")
        return [(key.time, _components(key.value)) for key in data.translations.keys]
    if data.rotation_type == 4:
        _xyz_to_quaternions(data)
    if data.num_rotation_keys and data.rotation_type != 1:
        raise KFMergeError(f"splicing needs linear rotation keys, not type {data.rotation_type}")
    return [(key.time, (key.value.w, key.value.x, key.value.y, key.value.z))
            for key in data.quaternion_keys]


def _blend(keys, at):
    if at <= keys[0][0]:
        return keys[0][1]
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        if at <= t1:
            fraction = (at - t0) / (t1 - t0)
            if len(v0) == 4 and sum(a * b for a, b in zip(v0, v1)) < 0:
                v1 = tuple(-x for x in v1)
            value = tuple(a + (b - a) * fraction for a, b in zip(v0, v1))
            if len(value) == 4:
                length = math.sqrt(sum(x * x for x in value)) or 1.0
                value = tuple(x / length for x in value)
            return value
    return keys[-1][1]


def _window_keys(keys, start, stop):
    low, high = start - _WINDOW_EPS, stop + _WINDOW_EPS
    inside = [(t, v) for t, v in keys if low <= t <= high]
    if not inside or inside[0][0] > low + 2 * _WINDOW_EPS:
        inside.insert(0, (start, _blend(keys, start)))
    if inside[-1][0] < high - 2 * _WINDOW_EPS:
        inside.append((stop, _blend(keys, stop)))
    return inside


def _store_keys(data, channel, keys):
    if channel == "translation":
        data.translations.num_keys = len(keys)
        data.translations.keys.update_size()
        for target, (at, value) in zip(data.translations.keys, keys):
            target.time = at
            target.value.x, target.value.y, target.value.z = value
        return
    data.rotation_type = 1
    data.num_rotation_keys = len(keys)
    data.quaternion_keys.update_size()
    for target, (at, value) in zip(data.quaternion_keys, keys):
        target.time = at
        target.value.w, target.value.x, target.value.y, target.value.z = value


def splice_kf(base, source, output, groups, bones=("bip01",), channels=("translation",)):
    """Copy the named bones' tracks inside the named groups from SOURCE into BASE.

    The source's group timing must match the base's, so text keys and every other
    track stay as the base has them.
    """
    nif = _nif_format()
    for channel in channels:
        if channel not in SPLICE_CHANNELS:
            raise KFMergeError(f"unknown splice channel {channel!r}")
    data, controls, _, _ = _read(base, nif)
    other, other_controls, _, _ = _read(source, nif)
    here = group_windows(data.roots[0].extra_data)
    there = group_windows(other.roots[0].extra_data)
    windows = []
    for group in groups:
        name = group.casefold()
        if name not in here or name not in there:
            raise KFMergeError(f"splice group {group!r} is missing from a KF")
        if any(abs(a - b) > 0.01 for a, b in zip(here[name], there[name])):
            raise KFMergeError(f"splice group {group!r} has different timing in the two KFs")
        windows.append(here[name])
    output = Path(output)
    if any(output.resolve() == Path(path).resolve() for path in (base, source)):
        raise KFMergeError("output must differ from every input KF")
    for bone in bones:
        name = bone.casefold().encode("latin-1")
        if name not in controls or name not in other_controls:
            raise KFMergeError(f"splice bone {bone!r} is missing from a KF")
        for channel in channels:
            ours = _channel_keys(controls[name].data, channel)
            theirs = _channel_keys(other_controls[name].data, channel)
            if not ours or not theirs:
                raise KFMergeError(f"splice bone {bone!r} has no {channel} keys")
            for start, stop in windows:
                low, high = start - _WINDOW_EPS, stop + _WINDOW_EPS
                ours = [(t, v) for t, v in ours if not low <= t <= high]
                ours += _window_keys(theirs, start, stop)
            ours.sort(key=lambda key: key[0])
            _store_keys(controls[name].data, channel, ours)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as stream:
        data.write(stream)
    return output


DODGE_GROUPS = (
    "missdr", "missdl", "missdfwd", "missdfwdr", "missdfwdl", "missdbk",
    "missdbkr", "missdbkl", "dodgeturnleft", "dodgeturnright", "dodgeleanback",
)
_KF_HEADER = b"NetImmerse File Format, Version 4.0.0.2\n"
_TEXT_BLOCK = b"NiTextKeyExtraData"


def text_keys(data):
    """The (time, text) keys of a NetImmerse 4.0.0.2 KF with one NiTextKeyExtraData block."""
    if not data.startswith(_KF_HEADER) or \
            data[len(_KF_HEADER):len(_KF_HEADER) + 4] != b"\x02\x00\x00\x04":
        raise KFMergeError("expected a NetImmerse 4.0.0.2 KF")
    marker = struct.pack("<I", len(_TEXT_BLOCK)) + _TEXT_BLOCK
    at = data.find(marker)
    if at < 0 or data.find(marker, at + 1) >= 0:
        raise KFMergeError("expected exactly one NiTextKeyExtraData block")
    # Block ID, next-extra-data reference and key count follow the type.
    at += len(marker)
    if at + 12 > len(data):
        raise KFMergeError("truncated text-key header")
    count = struct.unpack_from("<III", data, at)[2]
    if count > 10000:
        raise KFMergeError("unreasonable text-key count")
    at += 12
    keys = []
    for _ in range(count):
        if at + 8 > len(data):
            raise KFMergeError("truncated text key")
        time_, length = struct.unpack_from("<fI", data, at)
        at += 8
        if length > 4096 or at + length > len(data):
            raise KFMergeError("invalid text-key length")
        keys.append((time_, data[at:at + length].decode("ascii", "replace")))
        at += length
    return keys


def dodge_clip_ranges(data):
    """The eleven Can't Touch This (start, stop) pairs in a merged KF, in group order."""
    found = {}
    for time_, value in text_keys(data):
        name, sep, phase = value.partition(":")
        name, phase = name.casefold(), phase.strip().casefold()
        if not sep or name not in DODGE_GROUPS or phase not in {"start", "stop"}:
            continue
        if phase in found.setdefault(name, {}):
            raise KFMergeError(f"duplicate {name}: {phase} key")
        found[name][phase] = time_
    missing = [name for name in DODGE_GROUPS if set(found.get(name, {})) != {"start", "stop"}]
    if missing:
        raise KFMergeError("missing dodge clip keys: " + ", ".join(missing))
    ranges = [(found[name]["start"], found[name]["stop"]) for name in DODGE_GROUPS]
    if any(not (math.isfinite(start) and math.isfinite(stop) and 0 <= start < stop)
           for start, stop in ranges):
        raise KFMergeError("a dodge clip must stop after it starts")
    return ranges


def _extract_vanilla_kf(vanilla, name, near):
    """Write the retail KF from Morrowind.bsa next to NEAR and return its path."""
    archive_path = Path(vanilla) / "Morrowind.bsa"
    if not archive_path.is_file():
        raise KFMergeError(f"effective base animation KF not found: {Path(vanilla) / 'Meshes' / name}")
    try:
        archive = Bsa(archive_path)
    except ValueError as exc:
        raise KFMergeError(f"{archive_path}: {exc}") from exc
    entry = archive.by_hash.get(tes3_hash("Meshes/" + name))
    if entry is None:
        raise KFMergeError(f"{archive_path}: {name} is missing")
    extracted = Path(near).with_name(Path(near).name + ".base-tmp")
    extracted.parent.mkdir(parents=True, exist_ok=True)
    extracted.write_bytes(archive.read(entry))
    return extracted


def splice_tree_kfs(tree, vanilla, specs):
    """Replace each mod KF in a build tree with retail's plus the spliced tracks.

    A spec is {"source": relative path, "groups": [...], "bones": [...], "channels": [...]}.
    """
    tree = Path(tree)
    files = {path.relative_to(tree).as_posix().casefold(): path
             for path in tree.rglob("*") if path.is_file()}
    spliced = []
    for spec in specs:
        key = spec["source"].replace("\\", "/").casefold()
        path = files.get(key)
        if path is None:
            continue
        base = Path(vanilla) / path.relative_to(tree)
        extracted = None
        if not base.is_file():
            extracted = base = _extract_vanilla_kf(vanilla, path.name, path)
        temporary = path.with_name(path.name + ".splice-tmp")
        try:
            splice_kf(base, path, temporary, spec["groups"], spec.get("bones", ("bip01",)),
                      spec.get("channels", ("translation",)))
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
            if extracted is not None:
                extracted.unlink(missing_ok=True)
        spliced.append(path)
    return spliced


def has_xyz_rotation(raw):
    """Whether a NetImmerse 4.0.0.2 file holds a NiKeyframeData with XYZ Euler rotation keys."""
    at = raw.find(KEYFRAME_TAG)
    while at >= 0:
        fields = at + len(KEYFRAME_TAG)
        if fields + 8 <= len(raw):
            count, kind = struct.unpack_from("<II", raw, fields)
            if count and kind == XYZ_ROTATION:
                return True
        at = raw.find(KEYFRAME_TAG, at + 1)
    return False


def _key_group(raw, at, sizes):
    """Read a NIF key group's header; return its interpolation, key size and first key offset."""
    count = struct.unpack_from("<I", raw, at)[0]
    if not count:
        return count, 0, 0, at + 4
    interpolation = struct.unpack_from("<I", raw, at + 4)[0]
    if interpolation not in sizes:
        raise KFMergeError(f"key interpolation {interpolation}")
    return count, interpolation, sizes[interpolation], at + 8


def _skip_group(raw, at, sizes):
    count, _, size, first = _key_group(raw, at, sizes)
    return first + count * size


def _next_block_follows(raw, at):
    length = struct.unpack_from("<I", raw, at)[0]
    name = raw[at + 4:at + 4 + length]
    if 2 <= length <= 64 and len(name) == length and all(32 <= c < 127 for c in name):
        return True
    # The last block is followed by the footer: a root count and that many block indices.
    return at + 4 + length * 4 == len(raw)


def _quaternion_block(raw, at):
    """Return the end of a type 4 rotation section at `at` and its quaternion replacement."""
    # Rotation count and type, then one float, then the X, Y and Z groups.
    end = at + 12
    groups = []
    for _ in range(3):
        count, _interpolation, size, first = _key_group(raw, end, FLOAT_KEY_BYTES)
        groups.append(SimpleNamespace(keys=[
            SimpleNamespace(time=key_time, value=value)
            for key_time, value in (struct.unpack_from("<ff", raw, first + i * size)
                                    for i in range(count))]))
        end = first + count * size
    after = _skip_group(raw, end, VECTOR_KEY_BYTES)
    after = _skip_group(raw, after, FLOAT_KEY_BYTES)
    if not _next_block_follows(raw, after):
        raise KFMergeError("keyframe block does not end at the next block")
    keys = list(_xyz_quaternions(SimpleNamespace(xyz_rotations=groups)))
    if not keys:  # a count of zero has no type field after it
        return end, struct.pack("<I", 0)
    body = struct.pack("<II", len(keys), 1)
    for at_time, quaternion in keys:
        body += struct.pack("<5f", at_time, *quaternion)
    return end, body


def quaternion_rotation_bytes(raw):
    """Return `raw` with each XYZ Euler rotation section replaced by quaternion keys.

    NetImmerse 4.0.0.2 files carry no block offsets, so sections can be replaced in place. A whole
    mesh need not be parsed, and the rest of the file stays byte for byte.
    """
    out, kept, converted = [], 0, 0
    at = raw.find(KEYFRAME_TAG)
    try:
        while at >= 0:
            fields = at + len(KEYFRAME_TAG)
            count, kind = struct.unpack_from("<II", raw, fields)
            if count and kind == XYZ_ROTATION:
                end, body = _quaternion_block(raw, fields)
                out += [raw[kept:fields], body]
                kept = end
                converted += 1
            at = raw.find(KEYFRAME_TAG, fields)
    except struct.error as exc:
        raise KFMergeError(f"truncated keyframe block: {exc}") from exc
    return b"".join(out) + raw[kept:], converted


def quaternion_rotation_file(path, out):
    """Write `path` to `out` with every XYZ Euler rotation track as quaternion keys."""
    try:
        raw, converted = quaternion_rotation_bytes(Path(path).read_bytes())
    except KFMergeError as exc:
        raise KFMergeError(f"{path}: {exc}") from exc
    Path(out).write_bytes(raw)
    return converted


def quaternion_tree_rotations(tree):
    """Convert XYZ Euler rotation keys in a build tree: the Xbox engine cannot play them.

    Its evaluator writes a 16-byte quaternion into an 8-byte packed buffer, and a KF using them
    hangs the loader. Returns the converted paths and the (path, reason) of any left unconverted.
    """
    converted, failed = [], []
    for path in Path(tree).rglob("*"):
        if path.suffix.lower() not in (".nif", ".kf") or not path.is_file():
            continue
        if not has_xyz_rotation(path.read_bytes()):
            continue
        temporary = path.with_name(path.name + ".euler-tmp")
        try:
            quaternion_rotation_file(path, temporary)
            temporary.replace(path)
            converted.append(path)
        except KFMergeError as exc:
            failed.append((path, str(exc)))
        finally:
            temporary.unlink(missing_ok=True)
    return converted, failed


def merge_tree_kfs(tree, vanilla, additions):
    """Replace the winning base KF in a build tree and remove consumed source KFs."""
    tree = Path(tree)
    files = {path.relative_to(tree).as_posix().casefold(): path
             for path in tree.rglob("*") if path.is_file()}
    sources = []
    for name in additions:
        if not isinstance(name, str) or not name or Path(name).is_absolute():
            raise KFMergeError("animation.merge_kf entries must be relative paths")
        key = name.replace("\\", "/").casefold()
        path = files.get(key)
        if path is None:
            raise KFMergeError(f"animation.merge_kf source not found in build tree: {name}")
        if path in sources:
            raise KFMergeError(f"animation.merge_kf repeats {name}")
        sources.append(path)
    if not sources:
        raise KFMergeError("animation.merge_kf needs at least one source")
    output = files.get("meshes/xbase_anim.kf", tree / "Meshes" / "xbase_anim.kf")
    base = output if output.is_file() else Path(vanilla) / "Meshes" / "xbase_anim.kf"
    extracted = None
    if not base.is_file():
        extracted = base = _extract_vanilla_kf(vanilla, "xbase_anim.kf", output)
    if base in sources:
        raise KFMergeError("animation.merge_kf cannot include the base KF")
    temporary = output.with_name(output.name + ".merge-tmp")
    try:
        merge_kfs(base, sources, temporary)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
        if extracted is not None:
            extracted.unlink(missing_ok=True)
    for path in sources:
        path.unlink()
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", type=Path, help="effective xbase_anim.kf")
    parser.add_argument("additions", nargs="+", type=Path, help="animation KFs to append")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        output = merge_kfs(args.base, args.additions, args.out)
    except (OSError, KFMergeError) as exc:
        parser.error(str(exc))
    print(output)


if __name__ == "__main__":
    main()
