"""Create an example local config and build profile without overwriting existing files."""
import argparse
from pathlib import Path

from tes3x.paths import CONFIG_NAME, data_dir, resource


def initialize(folder):
    folder = Path(folder)
    for source, target in ((resource('examples', 'local.toml'), folder / CONFIG_NAME),
                           (resource('examples', 'profile.toml'),
                            folder / 'profiles' / 'my-build.toml')):
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open('xb') as stream:
                stream.write(source.read_bytes())
        except FileExistsError:
            print(f'kept {target}')
        else:
            print(f'created {target}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', nargs='?', help='destination (default: TES3X data folder)')
    args = parser.parse_args()
    initialize(Path(args.folder).resolve() if args.folder else data_dir().resolve())


if __name__ == '__main__':
    main()
