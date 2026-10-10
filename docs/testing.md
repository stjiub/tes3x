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

The unit tests run from a checkout; see [tests and lint](development.md#tests-and-lint).

## Profile check

```powershell
tes3x pipeline profiles/my-build.toml --check
```

Resolves and validates the profile without building it: mod folders, missing masters, patch
selection and local paths. See [pipeline](pipeline.md).

## Profile smoke tests

Boot a profile in xemu, start a new game, check the player's position, travel to Balmora and
check again, and check the log for crashes and hangs:

```powershell
tes3x test profiles/my-build.toml --record
tes3x test profiles/my-build.toml --keep-artifacts always
tes3x test profiles/my-build.toml --library-all --record
tes3x test profiles/my-build.toml --library-all --library "D:/Mods to test"
```

The test build adds `diagnostics` and `console` to the profile, and runs the bundled smoke test,
`smoke.toml`. A pass only means that one route worked; it isn't a full playthrough.

Runs, transient scripts and recorded results default to the TES3X data folder, under
`build/xemu/`, `build/profile-tests/` and `profile-tests/results/`. `--work-root` changes
the transient script/result folder; `--results` changes the durable record folder.

A run's log and build record are kept when a test fails and deleted when it passes. The ISO and
the built game files, about 2 GB a run, are deleted either way. `--keep-artifacts never` or
`always` changes that; `always` keeps the ISO and game files too. `--library-all` tests every mod
in the library on its own, using the profile for everything else.

## Patch game tests

Each patch can have a game test, `tests/game/PATCH.toml`: an [exec script](exec-scripts.md) and
the log lines it must produce. The scenario runner builds the variants the test needs, boots them
in xemu and decides whether the logs satisfy it:

```powershell
tes3x scenario PATCH profiles/my-build.toml
```

The file format is in [game tests](game-tests.md); running, recording and checking results in
[validation](validation.md).

## Original Xbox

xemu shows whether a build runs, not how fast or in how much memory. Timings, memory pressure and
anything that depends on the real hardware need a console. Deploy a build to its own folder (see
[pipeline](pipeline.md)), play or run an exec script on it, then fetch the log with
`tes3x diag pull`. [Diagnostics and profiling](diagnostics.md) covers the log, the
profiler and the memory censuses; [validation](validation.md) covers recording a hardware run as
evidence.
