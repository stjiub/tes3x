# Contributing

Thanks for helping. [Development](docs/development.md) covers setting up a checkout, the tests and
the generated files; this page covers how a change gets in.

## Before you start

Open an issue first for anything beyond a small fix, so the approach can be agreed before code is
written. For an engine fix, the issue is where we establish that the Xbox build actually has the
bug: a fix existing in the Morrowind Code Patch is a lead, not a reason (see
[patch policy](docs/patch-policy.md)).

## Pull requests

- Branch from `main` and open the pull request against `main`. Link the issue it addresses, if
  any, with `Fixes #123`.
- One change per pull request. Keep refactoring separate from behaviour changes, and open a new
  issue for anything else you notice along the way.
- The tests and lint must pass (`ruff check`, `python -m unittest discover -s tests`). They also
  fail when a generated page or a payload object is stale.
- Add a line to the `Unreleased` section of [CHANGELOG.md](CHANGELOG.md) when the change affects
  users.
- Write commit subjects in the imperative, short, without a trailing period; an area prefix such
  as `dash:` helps.

## Engine patches

- A new patch starts in the `dev` channel. Only the maintainer promotes it.
- Give it an entry in `patches.toml`, a notes page in `patches/` following the
  [template](patches/README.md), and a [game test](docs/game-tests.md) where one is practical.
  Run `tes3x patches --write` after editing any of the tables.
- After changing anything in `hooks/`, rebuild the payload objects and commit them with the source
  change ([payload objects](docs/development.md#payload-objects)).
- A patch must bind on both the NTSC and PAL GOTY engines. Use `TES3X_SYM(NAME)` for engine
  values in inline assembly and `TES3X_OPTIONAL` for another patch's functions; never select code
  by region or by which other patches are present.
- Say in the pull request what you tested: xemu, an original Xbox, or both. Performance claims
  need measurements on an Xbox; xemu timings do not count.

## Releases

Releases are tagged from `main` by the maintainer; see [releases](docs/development.md#releases).
