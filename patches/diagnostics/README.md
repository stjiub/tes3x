# diagnostics

Crash records, a main-loop heartbeat and a hang watchdog, written to `E:\tes3xlog.txt`.

Enabled at run time by `[Xbox] Diagnostics=1` in `Morrowind.ini`; the watchdog by
`HangWatchdog=1` and `HangTimeoutSeconds`. Each session logs the payload build ID and the mask of
patches applied (`diag.build`, `diag.patches`), which validation results rely on.

- A wrapper around the one call to the per-frame update function keeps the heartbeat and sits
  first in the exception handler chain, so a crash is recorded before the engine's own handling
  continues.
- The watchdog thread records a stall once the heartbeat stops for the timeout, and records it
  again if the main loop resumes.

Read a log with `tools/tes3x_diag.py pull` (from the Xbox) or `report` (a copied file).

## Evidence

In xemu, the watchdog recorded and recovered from a deliberate 15-second update stall, and
the wrapper recorded a deliberate null write before the engine's normal exception search went on.
This observation predates structured validation results. Hardware validation is separate.
