# Function profiler

Patch key: `profile=VA[,VA...]`

The engine's own timing output is compiled out of the retail build. This patch times up to 16
engine functions, chosen when the XBE is patched, with the CPU's cycle counter.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

Each target's direct call sites are redirected to a timing stub (`hooks/tes3xprof.c`), which swaps
the caller's return address so it works for any calling convention. Counters are written raw to
`E:\tes3xprof.bin` on a console command, at a frame interval, or at bounded loader checkpoints.
Each block also records free memory and the active calls, with caller, `this` and the first two
stack arguments.

## Using it

Choose targets with `--apply profile=VA[,VA...]`, or the pipeline's repeatable
`--profile-target VA`. `tes3x prof` picks targets by symbol name and renders the dump.

## Configuration

`[Xbox] ProfileDumpFrames` and `ProfileDumpSeconds` in `Morrowind.ini` set the dump interval; see
[ini keys](../docs/ini-keys.md).

## Compatibility and limits

Never ship it in a play build: the stubs stay installed whether or not anything reads them. Only
timings from an original Xbox mean anything; xemu runs only prove it does not crash.
