# Diagnostics and profiling

A patched build can record what it does, time engine functions and count where memory goes.
Everything it writes goes to the root of `E:`; copy it off the Xbox with `tes3x fetch`, or
read it from an emulator's disk.

| Patch or option | Gives you | Writes |
|---|---|---|
| [`diagnostics`](../patches/diagnostics.md) | session header, crash records, hang watchdog, snapshots | `E:\tes3xlog.txt` |
| [`console`](../patches/console.md) | the in-game console, [exec scripts](exec-scripts.md), command output in the log | `E:\tes3xlog.txt` |
| [`profile`](../patches/profile.md) | call counts and CPU cycles for chosen functions | `E:\tes3xprof.bin` |
| [`heap-census`](../patches/heap-census.md) | live engine heap by source file and line, or by call site | `E:\tes3xheap.bin` |
| [`mem-census`](../patches/mem-census.md) | kernel allocations and the XAPI heap by caller, with the address space | `E:\tes3xmem.bin` |
| `--pager-test` | the demand pager's synthetic workload and fault log | `E:\tes3xlog.txt` |

The `testing` preset includes `console` and `diagnostics`. The profiler, the two censuses and the
pager test are pipeline options rather than preset patches: their hooks stay installed whether or
not anything reads them, so they never belong in a play build.

## The log

`E:\tes3xlog.txt` is one line per event, `<ms since boot> <tag> <value>`, appended and kept
across launches up to 512 KB. A session starts with `entry.free_kb`.

With `[Xbox] Diagnostics=1` in `Morrowind.ini`, each session also logs `diag.build` (the payload
build), `diag.patches` (the low 32 bits of the patches applied), optional `diag.patches_hi`
(bits 32-63) and `diag.title_id` (the title ID, which picks the `E:/UDATA` folder saves go to),
and:

- a crash writes `crash.*` lines: the exception code and address, the registers and the last
  heartbeat, before the engine's own handling goes on;
- `HangWatchdog=1` records `hang.detected` when the main loop stops for `HangTimeoutSeconds`
  (default 60), and `hang.resumed` if it starts again;
- `Diagnostics=2` adds periodic snapshots.

The console command `tes3xdiag` writes a snapshot; `tes3xdiag 0`, `1` or `2` changes the level.
A build made with the pipeline's `--diag-test-faults` also has `tes3xdiag hang`, which stalls the
update loop for 15 seconds, and `tes3xdiag crash`, which writes to unmapped memory; both exist to
test the watchdog and the crash record. The hang trips the watchdog only with `HangTimeoutSeconds`
below 15, such as 10.

`tes3x diag pull` fetches the log and summarises it; `report` does the same for a copy.

## Memory tours

`tes3x tour make` writes an exec script that moves through a plugin's cells and marks
free memory after each move; `report` turns the log into a table and, if the game did not get to
the end, names the last command and any crash or hang.

```
tes3x tour make TR_Mainland.esm --exteriors --step 3 -o tes3xexec.txt
tes3x tour make TR_Mainland.esm --interiors --prefix "Narsis" -o tes3xexec.txt
tes3x tour make TR_Mainland.esm --interiors --prefix "Narsis" --working-set -o tes3xexec.txt
tes3x tour report tes3xlog.txt
```

Exteriors are visited row by row, alternating direction, so each move is to a nearby cell.
`--bounds=X0,Y0,X1,Y1` limits them to a box and `--step N` to every Nth cell in each direction;
`--every N` thins the whole tour instead, turning moves into jumps. `--wait` sets the frames
after each move (default 90), `--start` the `@start` line (default `new`), and the script ends
with `exit` unless `--no-exit`. Free memory is the kernel's count of free pages; the engine's own
heap can still be fragmented when it looks sufficient.

`--working-set` adds `tes3xws reset` before the first mark. Run the game with
[`heap-region`](../patches/heap-region.md) enabled; each mark then samples the region's
page-table accessed bits, and `report` adds the pages touched since the previous mark, the union
since reset and the committed-page count. `tes3xws` samples by hand and `tes3xws stop` ends the
sampling. Prefer named, content-rich cells when the goal is a representative play workload; a wide
exterior grid is a spatial baseline.

`tes3xws pulse N` instead sweeps on a system thread every nominal `N` milliseconds, including
while a cell-loading command blocks the main thread. The next mark or `tes3xws stop` reports the
peak bucket, the conservative sum of two adjacent buckets, the sample count, and the actual TSC
duration of the longest and busiest buckets. Use the actual duration rather than `N`: a sweep or
host scheduling can make a bucket longer. A failed heap-region commit also writes a final sample.

## Profiling

The pipeline's `--profile-target VA`, or `tes3x patch --apply profile=VA[,VA...]`, times up to 16
functions at every direct call site. Counters go to `E:\tes3xprof.bin` on the console command
`tes3xprof`, on `tes3xprof mark` (dump, then reset), at loader checkpoints, and every
`[Xbox] ProfileDumpFrames` frames. `tes3xprof reset` clears them.

`tes3x prof targets NAME` looks a function up in the [symbol map](symbol-map.md) and prints
the `--apply profile=` value for it; `tes3x prof report tes3xprof.bin` renders the dump.

Only an original Xbox gives meaningful timings; an emulator run shows only that the build does not
crash. An exec script makes measurements repeatable: move to the same place, wait the same number
of frames, then `tes3xprof mark`.

## Heap census

The pipeline's `--heap-census`, or `tes3x patch --apply heap-census`, counts what the engine's main
heap holds. Every direct call to `Memory_Heap::Allocate` and `::Free` is redirected; each allocation
is attributed to the source file and line the engine passes, or, where it passes none, to its call
site. Allocation counts are exact, and live blocks are tracked for one address in four. A snapshot
is appended to `E:\tes3xheap.bin` at the first frame (with `diagnostics`) and on the console command
`tes3xheap`; each also records the kernel's memory statistics.

`tes3x heap tes3xheap.bin` renders it, naming call sites from the
[symbol map](symbol-map.md): `--by-file` groups by source file, `--last` shows only the last
snapshot, and `--compare BASELINE.bin` subtracts another census, such as retail's.

The census needs an 8 MB table, so run it on a console or emulator with 128 MB. The engine's
allocations do not depend on the memory size, so a 128 MB census also describes a 64 MB console.
Allocations outside that heap, such as the texture and vertex-buffer arena, are not counted.

## Memory census

The pipeline's `--mem-census`, or `tes3x patch --apply mem-census`, covers memory outside the engine
heap: committed virtual memory, contiguous and pool allocations and the XAPI heap, each by caller.
Snapshots append to `E:\tes3xmem.bin` at the first frame (with `diagnostics`) and on the console
command `tes3xmem`. `tes3x mem tes3xmem.bin` renders them; `--skip NAME` leaves a function out of
caller chains.

## Demand pager test

The pipeline's `--pager-test` adds TES3X's demand pager on its own: a 64 MB reserved region whose
pages are committed on first touch, filled from `E:\tes3xpage.bin`, and evicted to it under a 1 MB
resident budget. Nothing in the engine is paged; the console commands exercise it.

- `tes3xpager [runs] [cache] [faults] [reboot]` runs a synthetic workload over the region from two
  threads of its own, so the game keeps drawing, and logs the pager's counters and page-in times.
  `cache` puts the paging file on the title's `Z:` partition instead of `E:`, `faults` drains the
  fault log when the workload completes, and `reboot` returns to the dashboard afterwards.
- `tes3xfaults` writes the fault log, the address, instruction and allocation group of recent
  faults, to the log as `pager.fault` lines; `tes3xfaults reset` clears it.

[`info-name-arena`](../patches/info-name-arena.md) uses the same pager in a real build, and
`tes3xfaults` works there too.
