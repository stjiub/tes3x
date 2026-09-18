"""Audit NIF texture references and optionally resample PCM mod WAVs."""
import argparse
import json
import subprocess
import wave
from pathlib import Path

from tes3x_reach import nif_textures


def wav_info(path):
    with wave.open(str(path), 'rb') as stream:
        return {'channels': stream.getnchannels(), 'bits': stream.getsampwidth() * 8,
                'rate': stream.getframerate(), 'frames': stream.getnframes()}


def convert_wav(source, target, sox, max_rate):
    if not 8000 <= max_rate <= 48000:
        raise ValueError('sound sample-rate cap must be 8000..48000 Hz')
    info = wav_info(source)
    if info['channels'] not in (1, 2):
        raise ValueError(f'{source}: expected mono or stereo WAV')
    if info['rate'] <= max_rate and info['bits'] <= 16:
        return False
    bits = min(info['bits'], 16)
    rate = min(info['rate'], max_rate)
    target = Path(target)
    temp = target.with_name(target.name + '.converting.wav')
    try:
        result = subprocess.run([str(Path(sox).resolve()), '-G', str(source), '-b', str(bits),
                                 '-e', 'unsigned-integer' if bits == 8 else 'signed-integer',
                                 str(temp), 'rate', '-v', str(rate)],
                                capture_output=True, timeout=120)
        if result.returncode:
            raise ValueError(f'SoX failed for {source}: {result.stderr.decode(errors="replace")}')
        output = wav_info(temp)
        if output['channels'] != info['channels'] or output['rate'] != rate or output['bits'] != bits:
            raise ValueError(f'SoX output format mismatch: {source}')
        expected = info['frames'] * rate / info['rate']
        if abs(output['frames'] - expected) > 2:
            raise ValueError(f'SoX output duration mismatch: {source}')
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)
    return True


def audit(tree):
    meshes, sounds, errors = [], [], []
    for path in sorted(Path(tree).rglob('*')):
        if not path.is_file():
            continue
        rel = path.relative_to(tree).as_posix()
        try:
            if path.suffix.lower() == '.nif':
                meshes.append({'path': rel, 'textures': sorted(set(nif_textures(path.read_bytes())))})
            elif path.suffix.lower() == '.wav':
                sounds.append({'path': rel, **wav_info(path)})
        except (ValueError, wave.Error, EOFError) as exc:
            errors.append({'path': rel, 'error': str(exc)})
    return {'mesh_check_scope': '4.0.0.2 header and external texture fields only',
            'meshes': meshes, 'sounds': sounds, 'errors': errors}


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('tree')
    ap.add_argument('--json', required=True)
    args = ap.parse_args()
    report = audit(args.tree)
    Path(args.json).write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f"{len(report['meshes'])} NIFs, {len(report['sounds'])} WAVs, {len(report['errors'])} errors")
    raise SystemExit(bool(report['errors']))
