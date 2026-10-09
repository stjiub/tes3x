# Testing

TES3X is tested in steps, from fast checks on the PC to play on an original Xbox. Each step catches
what the one before cannot, and costs more to run.

| Step | Checks | Needs |
|---|---|---|
| [Unit tests](#unit-tests) | the tools, the patch table and every test definition | Python |
| [Profile check](#profile-check) | that a profile resolves: mods, masters, patches, paths | your game files |
| [Profile smoke test](#profile-smoke-tests) | that a build boots, starts a game and travels | [xemu](xemu.md) |
| [Patch game tests](#patch-game-tests) | that a patch does what it claims, in game | xemu |
| [Original Xbox](#original-xbox) | timing, memory and anything xemu does not model | a modded Xbox |

## Unit tests

```powershell
python -m pip install -e .[gui,dev]
python -m unittest discover -s tests
```

They import the installed `tes3x` package, so install the checkout first, editable, as above;
the GUI tests skip without PySide6. They run without game files or an emulator. They also fail when a generated page such as
[patches.md](patches.md) is out of date, or a patch's notes break the
[patch page contract](../patches/README.md).

GitHub Actions runs them on Windows and Linux, with Python 3.12 and 3.13, for every push to
`main` and every pull request (`.github/workflows/tests.yml`).

## Profile check

```powershell
python tools/tes3x_pipeline.py profiles/my-build.toml --check
```

Resolves and validates the profile without building it: mod folders, missing masters, patch
selection and local paths. See [pipeline](pipeline.md).

## Profile smoke tests

Boot a profile in xemu, start a new game, walk to Balmora and check the log for crashes and
hangs:

```powershell
python tools/tes3x_test.py profiles/my-build.toml --record
python tools/tes3x_test.py profiles/my-build.toml --keep-artifacts always
python tools/tes3x_test.py profiles/my-build.toml --library-all --record
python tools/tes3x_test.py profiles/my-build.toml --library-all --library "D:/Mods To Test"
```

The test build adds `diagnostics` and `console` to the profile, and runs the exec script in
`tests/game/smoke.toml`. A pass only means that one route worked; it isn't a full playthrough.

A run's log and build record are kept when a test fails and deleted when it passes. The ISO and
the built game files, about 2 GB a run, are deleted either way. `--keep-artifacts never` or
`always` changes that; `always` keeps the ISO and game files too. `--library-all` tests every mod
in the library on its own, using the profile for everything else.

## Patch game tests

Each patch can have a game test, `tests/game/PATCH.toml`: an [exec script](exec-scripts.md) and
the log lines it must produce. The scenario runner builds the variants the test needs, boots them
in xemu and decides whether the logs satisfy it:

```powershell
python tools/tes3x_scenario.py PATCH profiles/my-build.toml
```

The file format is in [game tests](game-tests.md); running, recording and checking results in
[validation](validation.md).

## Original Xbox

xemu shows whether a build runs, not how fast or in how much memory. Timings, memory pressure and
anything that depends on the real hardware need a console. Deploy a build to its own folder (see
[pipeline](pipeline.md)), play or run an exec script on it, then fetch the log with
`tools/tes3x_diag.py pull`. [Diagnostics and profiling](diagnostics.md) covers the log, the
profiler and the memory censuses; [validation](validation.md) covers recording a hardware run as
evidence.
