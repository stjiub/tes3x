"""Append NetImmerse 4.0.0.2 animation clips to a base KF."""

import argparse
import math
from pathlib import Path
import time

from tes3x.bsa import Bsa, tes3_hash


class KFMergeError(ValueError):
    pass


def _nif_format():
    # PyFFI 2.2.3 still calls time.clock and imports the setuptools distutils shim.
    if not hasattr(time, "clock"):
        time.clock = time.perf_counter
    try:
        import setuptools  # noqa: F401
        from pyffi.formats.nif import NifFormat
    except ImportError as exc:
        raise KFMergeError("KF merging needs PyFFI 2.2.3 (install tes3x[kf])") from exc
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


def _xyz_quaternions(data):
    times = sorted({key.time for group in data.xyz_rotations for key in group.keys})
    for at in times:
        x, y, z = (_axis(group, at) / 2 for group in data.xyz_rotations)
        cx, sx = math.cos(x), math.sin(x)
        cy, sy = math.cos(y), math.sin(y)
        cz, sz = math.cos(z), math.sin(z)
        yield at, (cx * cy * cz - sx * sy * sz,
                   sx * cy * cz + cx * sy * sz,
                   cx * sy * cz - sx * cy * sz,
                   cx * cy * sz + sx * sy * cz)


def _append_rotations(dst, src, offset):
    if not src.num_rotation_keys:
        return
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
        archive_path = Path(vanilla) / "Morrowind.bsa"
        if not archive_path.is_file():
            raise KFMergeError(f"effective base animation KF not found: {base}")
        try:
            archive = Bsa(archive_path)
        except ValueError as exc:
            raise KFMergeError(f"{archive_path}: {exc}") from exc
        entry = archive.by_hash.get(tes3_hash("Meshes/xbase_anim.kf"))
        if entry is None:
            raise KFMergeError(f"{archive_path}: xbase_anim.kf is missing")
        extracted = output.with_name(output.name + ".base-tmp")
        extracted.parent.mkdir(parents=True, exist_ok=True)
        extracted.write_bytes(archive.read(entry))
        base = extracted
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
