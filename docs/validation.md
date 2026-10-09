# Patch game tests and validation

Each patch can have a repeatable in-game test in `tests/game/PATCH.toml`. The public scenario
runner builds the required variants, boots them in xemu, runs the test without controller input,
and decides whether the observed log satisfies the test. Contributors can therefore run the same
test before submitting a change.

Four related things have different jobs:

| thing | purpose | location |
|---|---|---|
| game test | Public test definition: setup, commands and expected observations | `tests/game/PATCH.toml` |
| scenario run | One execution of that definition in xemu | `build/xemu/` |
| validation record | Local logs and hashed provenance from a completed run | `build/validation/` by default |
| channel | Maintainer decision about public readiness | `dev`, `preview` or `release` in `patches.toml` |

A scenario pass is evidence from one environment, not a channel change. Recording a pass never
promotes a patch.

## Prerequisites

Set up a normal build profile and an xemu target in `tes3x.local.toml` first. See
[configuration](configuration.md) and [running in xemu](xemu.md). Run the
commands below from the public repository root.

## Run a patch's game test

```powershell
tes3x scenario PATCH profiles/my-build.toml
```

For example:

```powershell
tes3x scenario dxt5-size profiles/my-build.toml
```

The runner reads `tests/game/PATCH.toml`. A `single` test builds and runs the patch once. A
`comparison` test runs a control without the patch and a test build with it; both sides must pass,
and any numeric comparisons must also pass. The two sides run concurrently.

Every side uses a fresh xemu disk and writes its run folder under `build/xemu/`. The verdict checks:

- required and forbidden regular expressions;
- ordered log sequences;
- numeric control/test comparisons;
- in-game `assert` results; and
- unexpected `crash.*`, `hang.detected` and `fatal.*` lines.

The command exits nonzero when a build, boot or check fails. A runner failure names its `.out`
file; a completed scenario prints each failed expectation and the matching line, when one exists.

## Record a passing run

Add `--record` to retain sanitized logs and provenance:

```powershell
tes3x scenario PATCH profiles/my-build.toml --record
```

Records go to the ignored `build/validation/patches/PATCH/` directory by default. A passing record
requires a clean repository revision so its commit identifies the code that ran. While developing
uncommitted changes, run without `--record`; commit the change, then rerun with `--record` if you
need durable evidence.

Use another local result store when needed:

```powershell
tes3x scenario PATCH profiles/my-build.toml `
  --record --results path/to/local-results
```

`--results` paths are local state, not part of the public repository.

## Recheck an existing xemu run

Give a run a stable prefix if you may want to inspect or record it later:

```powershell
tes3x scenario PATCH profiles/my-build.toml --name scenario-PATCH-1
tes3x scenario PATCH profiles/my-build.toml `
  --name scenario-PATCH-1 --reuse --record
```

`--reuse` reads `build/xemu/scenario-PATCH-1-control` and/or `-test` without booting xemu again.
`--only control` and `--only test` are useful for diagnosis, but `--record` requires every side of
the scenario.

## Inspect and check local records

```powershell
tes3x validate status
tes3x validate check
```

`status` reports each patch's local standing, chronological last result, most recent pass, and
whether a game-test definition exists. `test exists = yes` does not mean the test passed.

| standing | meaning |
|---|---|
| `current` | A pass cites the current game-test hash. |
| `stale` | The game test changed after the last pass. |
| `legacy` | The pass predates hashed public game tests. |
| `none` | No local pass is recorded. A recorded failure does not count as a pass. |

`check` validates every public game-test definition and every record in the selected local store.
Maintainers with a complete result store can also enforce the channel gate:

```powershell
tes3x validate --results path/to/local-results check --gate
```

Global `--results` goes before `status`, `check` or `record`. `check --gate` requires every
`preview` and `release` patch to have a current pass. It is not useful with a contributor's empty
or partial result store.

## Record completed runs directly

The scenario runner calls the recorder automatically, but existing xemu run folders can be
recorded directly:

```powershell
tes3x validate record PATCH --env xemu `
  --control build/xemu/CONTROL --test build/xemu/TEST
```

Omit `--control` for a `single` game test. Original-Xbox logs can also be recorded with
`--env hardware`, the corresponding pipeline build folders and a local hardware description; see
`tes3x validate record --help`.

The scenario runner records only a complete pass. To preserve a failed completed run for local
diagnosis, call the recorder directly with `--result fail`. A failure record remains valid evidence
of what happened, but it does not change the patch's standing from `none`, `stale` or `legacy`.

## Write or review a game test

The game-test format, including scripts, expectations, sequences, comparisons, generated fixtures
and private save inputs, is documented under [game tests](game-tests.md). Unit tests check
that every committed definition is well formed, but they do not boot the game:

```powershell
python -m unittest tests/test_game_tests.py tests/test_validate.py
```