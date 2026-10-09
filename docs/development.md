# Development

This page is for working on TES3X itself: running it from a git checkout, testing changes,
regenerating the generated pages and making releases. Playing builds needs none of it; start at
[getting started](getting-started.md) instead.

## Setting up a checkout

You need Python 3.12 or newer and git. Building needs [LLVM](https://releases.llvm.org)
(`clang` and `lld-link`) on `PATH` or in `paths.llvm`, and installing `.7z` and `.rar` mods
needs [7-Zip](https://www.7-zip.org).

```powershell
git clone https://github.com/stjiub/tes3x
cd tes3x
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e .[gui,dev]
```

On Linux, activate with `source .venv/bin/activate`. Activate the environment in each new
terminal; `tes3x` is on `PATH` while it is. `[gui]` adds PySide6 and tomlkit, `[dev]` adds Ruff.

The editable install reads TES3X's own files (the patch table, hooks, symbols, examples, add-ons)
from the checkout, so edits take effect without reinstalling. A `tes3x.local.toml` in the
checkout's root is used before the one in the data folder; see
[local config](configuration.md#local-config) for the whole lookup.

## Tests and lint

```powershell
ruff check
python -m unittest discover -s tests
```

The unit tests import the installed `tes3x` package, so install the checkout first, as above. They
need no game files or emulator, and the GUI tests skip without PySide6. They also fail when a
generated page is out of date, or a patch's notes break the
[patch page contract](../patches/README.md). Ruff checks pyflakes' rules only
(`[tool.ruff]` in `pyproject.toml`); bundled third-party code is excluded.

GitHub Actions runs both on Windows and Linux, with Python 3.12 and 3.13, for every push to
`main` and every pull request (`.github/workflows/tests.yml`). It then builds the wheel, installs
it into a fresh environment and runs `tests/installed_check.py` from outside the checkout, which
fails when a resource a command needs is missing from the wheel.

[Testing](testing.md) covers the steps after the unit tests: profile checks, smoke tests in xemu
and game tests.

## Generated files

Some pages and sources are generated; edit their source and regenerate them:

| Run | Regenerates | From |
|---|---|---|
| `tes3x patches --write` | [patches.md](patches.md), [candidates.md](candidates.md), [catalog.md](catalog.md) | `patches.toml`, `candidates.toml`, `catalog.toml` |
| `tes3x docs --write` | [index.md](index.md) | `docs/nav.toml` |
| `tes3x font` | `manager/fontdata.c` | pinned OFL fonts, downloaded once |
| `tes3x menuart` | the menu buttons in `assets/menu` | one font; needs Pillow and numpy |

`tes3x patches --check` and `tes3x docs` check without writing. `tes3x docs` also resolves every
relative link and anchor in the README, `docs/` and `patches/`, and requires every page in `docs/`
to be listed once in `nav.toml`.

The font and menu output is committed, so a build needs neither the fonts nor Pillow's FreeType.

## Building the portable folder

```powershell
tes3x package --zip
```

writes `build/package/TES3X-<version>/` and `TES3X-<version>.zip`: an embedded Python 3.12 with
TES3X and its packages installed from a wheel built from the checkout, `TES3X.exe`, which starts
the GUI with that Python, and `tes3x-cli.exe`, which runs `tes3x` commands in a terminal.
`externals/` holds 7-Zip and LLVM's `clang` and `lld-link`, each with its license.

It also carries the [console manager](deployment.md#installing-the-console-manager) as
`manager/default.xbe` and its launcher as `manager/launcher.xbe`, built with nxdk (`paths.nxdk`),
or taken from `--manager XBE` and `--launcher XBE`. `--no-manager` leaves both out.

Building the folder needs a C compiler for the two launchers (MSYS2's `gcc` or `clang`, or
`--cc`). The first build downloads Python, 7-Zip and LLVM, about 880 MB, mostly the LLVM release,
checks each against a pinned SHA-256, and keeps them in the data folder's `package` folder for
later builds.

The folder's version, in its `VERSION` file, is `X.Y.Z` at a `vX.Y.Z` tag, and
`X.Y.Z-dev.N+gSHA` for a commit N past it, with `.dirty` when the checkout has uncommitted
changes. The GUI shows it in its title and in **Help > About TES3X**. `tes3x --version` prints `__version__` from
`src/tes3x/__init__.py`, which is also `X.Y.Z` before the first tag.

PySide6's unused CMake object files are left out, and the build fails if a path, counted from
`TES3X-<version>/`, is over 180 characters, so the zip unpacks within Windows' path limit.

The `portable` workflow (`.github/workflows/portable.yml`) builds the zip on Windows when run from
the Actions tab or for a `vX.Y.Z` tag, unpacks it and checks it, and keeps it as the run's
artifact. It has no nxdk, so its zip has no console manager.

## Releases

A release is published by hand on GitHub from a downloaded, tested zip. The console manager
fetches its own updates from the latest release, so every release also carries a signed manager
release:

```powershell
tes3x release manager OUT --key KEY --manager XBE --launcher XBE
tes3x release check OUT
gh release upload vX.Y.Z OUT/release.json OUT/release.json.sig OUT/manager.xbe OUT/launcher.xbe
```

`release manager` writes `release.json` (the manager's version, read from its XBE, and each
file's size and SHA-256), its Ed25519 signature `release.json.sig`, `manager.xbe` and
`launcher.xbe`. `release check` checks them against `keys/release.pub`. How a console finds,
checks and installs a release is in [signed updates](deployment.md#signed-updates).

### The release key

The private half of the release key stays out of every repository. `tes3x release keygen KEY`
makes one with a passphrase, `tes3x release protect KEY` sets or changes the passphrase, and
`TES3X_RELEASE_PASSPHRASE` supplies it to scripts. The public half is `keys/release.pub`, which
the manager is built with.

A key is replaced by a release, signed with the old key, whose manager carries the new one.

### Forks

A fork makes its own key and puts the public half in `keys/release.pub`; its managers then take
only its releases. It sets its own update feed as `MGR_FEED` in `manager/mgr.h`.

## More

- [Patch policy](patch-policy.md): which engine fixes TES3X takes on and what each defaults to.
- [Patch notes](../patches/README.md): what a patch's notes page holds, and its template.
- [Symbol map](symbol-map.md): names for functions and code sites in the retail XBE.
