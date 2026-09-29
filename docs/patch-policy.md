# Patch policy

Which engine fixes TES3X takes on and what each one defaults to. The fixes themselves are in
[patches.md](patches.md), and the ones not implemented are in [candidates.md](candidates.md).

## Categories

| category | what it covers | default |
|---|---|---|
| `core` | crashes, hangs, save corruption, data loss | `recommended` at release |
| `correctness` | clear bugs in formulas, scripting, saving or display | `recommended` at release |
| `compat` | something a particular mod needs | off |
| `performance` | less CPU, disk or memory work | off until measured on an Xbox |
| `instrumentation` | logging, profiling, crash reports | `testing` once it reaches preview |
| `qol` | interface and convenience changes | off |
| `balance` | changes to how the game plays | off, never in `recommended` |

## Channels

- `dev`: contributor-only work that is incomplete, changing or unsafe.
- `preview`: works end to end but needs broader testing.
- `release`: validated and ready for general use.

Every implemented patch starts in `dev`. Promotion to `preview` or `release` is a maintainer
decision; recording a validation result does not promote it automatically.

Every fix can be turned on or off by itself. Fixes taken from another project keep its numbering,
so Morrowind Code Patch fix 97 is `mcp-97`.

## Deciding what to port

A fix existing in the Morrowind Code Patch doesn't mean the Xbox needs it. The Xbox and PC builds
share most of their code, but not all of it, so each fix gets checked against the Xbox build
first. MCP's bytes can't be copied across either; each port is written fresh against the Xbox
code.

We sort a fix by what it actually does, not by where MCP filed it. Some of MCP's "bug fixes"
change balance or AI, and those stay off by default. When a real bug fix and a rebalance come
bundled together, they become two separate patches.

Where it makes sense, a patch reads an `[Xbox]` setting from `Morrowind.ini` so it can be tuned
or turned off without rebuilding. See [ini keys](ini-keys.md).

Fixes to records, scripts or assets belong in ordinary mods, not here.

Every fix we look at gets an entry in `candidates.toml`, including the ones we decide not to
port, with the reason.
