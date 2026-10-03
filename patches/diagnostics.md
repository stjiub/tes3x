# Crash and hang diagnostics

Patch key: `diagnostics`

The retail game gives nothing to go on when it crashes or stalls. This patch writes crash records,
a main-loop heartbeat and hang reports to `E:\tes3xlog.txt`, so a failure on the console leaves a
record of what happened.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

- A wrapper around the one call to the per-frame update function keeps the heartbeat and sits
  first in the exception handler chain, so a crash is recorded before the engine's own handling
  continues.
- A watchdog thread records a stall once the heartbeat stops for the timeout, and records it
  again if the main loop resumes.
- Each session logs the payload build ID and the mask of patches applied (`diag.build`,
  `diag.patches`, plus `diag.patches_hi` when needed), so a log identifies the build that wrote
  it, and the title ID
  (`diag.title_id`).

The payload source is `hooks/tes3xdiag.c`. The network foundation runs from its frame hook, so
network features bring this patch with them.

## Using it

Read a log with `tools/tes3x_diag.py pull` (from the Xbox) or `report` (a copied file).

## Configuration

Nothing is recorded unless `[Xbox] Diagnostics=1` is set in `Morrowind.ini`. `HangWatchdog` and
`HangTimeoutSeconds` control the watchdog, and `DiagnosticsLevel=2` adds periodic snapshots; see
[ini keys](../docs/ini-keys.md).
