# diagnostics

Crash records, a main-loop heartbeat and a hang watchdog, written to `E:\tes3xlog.txt`.

Enabled at run time by `[Xbox] Diagnostics=1` in `Morrowind.ini`; the watchdog by
`HangWatchdog=1` and `HangTimeoutSeconds`. Each session logs the payload build ID and the mask of
patches applied (`diag.build`, `diag.patches`), so a log identifies the build that wrote it, and
the title ID (`diag.title_id`).

- A wrapper around the one call to the per-frame update function keeps the heartbeat and sits
  first in the exception handler chain, so a crash is recorded before the engine's own handling
  continues.
- The watchdog thread records a stall once the heartbeat stops for the timeout, and records it
  again if the main loop resumes.

Read a log with `tools/tes3x_diag.py pull` (from the Xbox) or `report` (a copied file).
