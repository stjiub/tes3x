# Testing and debugging

The patched game can record what it does, run commands without a controller, and time engine
functions. Everything it writes goes to the root of `E:`; copy it off the Xbox with
`tools/tes3x_fetch.py`, or read it from an emulator's disk.

| Patch | Gives you | Writes |
|---|---|---|
| `diagnostics` | session header, crash records, hang watchdog, snapshots | `E:\tes3xlog.txt` |
| `console` | the in-game console on a pad, commands from a file, their output in the log | `E:\tes3xlog.txt` |
| `profile` | call counts and CPU cycles for chosen functions | `E:\tes3xprof.bin` |
| `heap-census` | live engine heap by source file and line, or by call site | `E:\tes3xheap.bin` |

`standard` includes `console`, and `development` adds `diagnostics`. The profiler and the heap
census are added by name.

## The log

`E:\tes3xlog.txt` is one line per event, `<ms since boot> <tag> <value>`, appended and kept
across launches up to 512 KB. A session starts with `entry.free_kb`.

With `[Xbox] Diagnostics=1` in `Morrowind.ini`, each session also logs `diag.build` (the payload
build) and `diag.patches` (a mask of the patches applied), and:

- a crash writes `crash.*` lines: the exception code and address, the registers and the last
  heartbeat, before the engine's own handling goes on;
- `HangWatchdog=1` records `hang.detected` when the main loop stops for `HangTimeoutSeconds`
  (default 60), and `hang.resumed` if it starts again;
- `Diagnostics=2` adds periodic snapshots.

The console command `tes3xdiag` writes a snapshot; `tes3xdiag 0`, `1` or `2` changes the level.
`tools/tes3x_diag.py pull` fetches the log and summarises it; `report` does the same for a copy.

## Commands without a controller

With `console`, a `tes3xexec.txt` in `E:\` or next to `default.xbe` runs once per launch. `E:\`
is checked first, so it can be changed without touching the game folder. A file in `E:\` runs on
every launch until it is deleted.

```
# start a New Game straight from boot, skipping the main menu
@start new
wait 30
player->getpos x
coc "Balmora"
wait 60
player->getpos x
exit
```

| Line | Meaning |
|---|---|
| `@start new` | start a New Game at boot, without the main menu |
| `@start load U:\DIR\NAME.ess` | load that save at boot, without the main menu |
| `@menu ...` | run only while the main menu is up, e.g. `@menu click MenuOptions MenuOptions_New_container` |
| `wait N` | pause N frames |
| `click MENU WIDGET` | press a menu widget by name |
| `mark LABEL` | log free memory now, as `mem.LABEL <KB>` |
| `exit` | turn the Xbox off |
| `reboot` | restart the Xbox into the dashboard |
| `# ...` | comment |
| anything else | a console command |

Lines run one per frame; ordinary lines start once the game has run 150 frames without the main
menu. `@start` applies only when the game was started directly, not from another program that
passed its own launch data.

`U:` is `E:\UDATA\42530005`. A save loads by path from any folder there; it does not need to
appear in the game's save list. **A path that does not load is not reported: the game starts a New
Game instead.** A test that loads a save should check something only that save has.

Each command is logged as `exec> ...` and the first 8 lines it prints as `console< ...`, such as
`console< GetPos >> -12288.00`. A command that loads a cell runs that cell's scripts, which print
too; the rest are counted as `console.more N`. Commands typed on the pad are logged the same way.

In an emulator, a debugger can also hand the running game one command at a time: write the text
into the payload's `tes3x_mailbox`, then change its sequence number. It runs on the next frame and
is logged as `live> ...`.

The file is read into memory sized to it and released when its last line has run
(`exec.done`), so even a long script costs only its own size while it runs.

## Memory tours

`tools/tes3x_tour.py make` writes a script that moves through a plugin's cells and marks free
memory after each move; `report` turns the log into a table and, if the game did not get to the
end, names the last command and any crash or hang.

```
python tools/tes3x_tour.py make TR_Mainland.esm --exteriors --step 3 -o tes3xexec.txt
python tools/tes3x_tour.py make TR_Mainland.esm --interiors --prefix "Narsis" -o tes3xexec.txt
python tools/tes3x_tour.py make TR_Mainland.esm --interiors --prefix "Narsis" --working-set -o tes3xexec.txt
python tools/tes3x_tour.py report tes3xlog.txt
```

Exteriors are visited row by row, alternating direction, so each move is to a nearby cell.
`--bounds=X0,Y0,X1,Y1` limits them to a box and `--step N` to every Nth cell in each direction;
`--every N` thins the whole tour instead, turning moves into jumps. `--wait` sets the frames
after each move (default 90), `--start` the `@start` line (default `new`), and the script ends
with `exit` unless `--no-exit`. Free memory is the kernel's count of free pages; the engine's own
heap can still be fragmented when it looks sufficient.

`--working-set` adds `tes3xws reset` before the first mark. Run the game with `heap-region` enabled;
each mark then samples the region's PTE accessed bits, and `report` adds the pages touched since the
previous mark, the union since reset and the committed-page count. Prefer named, content-rich cells
when the goal is a representative play workload; a wide exterior grid is a spatial baseline.

## Profiling

`--apply profile=VA[,VA...]`, or the pipeline's `--profile-target VA`, times up to 16 functions at
every direct call site. Counters go to `E:\tes3xprof.bin` on the console command `tes3xprof`, on
`tes3xprof mark` (dump, then reset), at loader checkpoints, and every `[Xbox] ProfileDumpFrames`
frames. `tes3xprof reset` clears them.

`tools/tes3x_prof.py targets NAME` looks a function up in the [symbol map](symbol-map.md) and prints
the `--apply profile=` value for it; `tools/tes3x_prof.py report tes3xprof.bin` renders the dump.

Only an original Xbox gives meaningful timings; an emulator run shows only that the build does not
crash. A command file makes measurements repeatable: move to the same place, wait the same number of
frames, then `tes3xprof mark`.

## Heap census

`--apply heap-census`, or the pipeline's `--heap-census`, counts what the engine's main heap holds.
Every direct call to `Memory_Heap::Allocate` and `::Free` is redirected; each allocation is
attributed to the source file and line the engine passes, or, where it passes none, to its call
site. Allocation counts are exact, and live blocks are tracked for one address in four. A snapshot
is appended to `E:\tes3xheap.bin` at the first frame (with `diagnostics`) and on the console
command `tes3xheap`; each also records the kernel's memory statistics. The record layout is
`heap_header` and `heap_site` in `hooks/tes3xheap.c`.

`tools/tes3x_heap.py tes3xheap.bin` renders it, naming call sites from the
[symbol map](symbol-map.md): `--by-file` groups by source file, `--last` shows only the last
snapshot, and `--compare BASELINE.bin` subtracts another census, such as retail's.

`--apply mem-census`, or the pipeline's `--mem-census`, covers memory outside that heap: committed
virtual memory, contiguous and pool allocations and the XAPI heap, each by caller.
`tools/tes3x_mem.py tes3xmem.bin` renders `E:\tes3xmem.bin`; `--skip NAME` leaves a function out of
caller chains.

The census needs an 8 MB table, so run it on a console or emulator with 128 MB. The engine's
allocations do not depend on the memory size, so a 128 MB census also describes a 64 MB console.
Allocations outside that heap, such as the texture and vertex-buffer arena, are not counted.

## Running in xemu

`tools/tes3x_xemu.py` builds a profile, packs it as a disc image, boots it in
[xemu](https://xemu.app) on a fresh copy of a clean hard disk, and copies the log back out. It
needs these under `[xemu]` in `tes3x.local.toml`, all of which you supply yourself:

| key | file |
|---|---|
| `exe` | `xemu.exe` |
| `bootrom` | the MCPX boot ROM |
| `bios` | the BIOS for normal runs, such as a retail one |
| `bios_128mb` | optional: a BIOS that uses 128 MB, for `--ram 128 --bios 128mb` |
| `eeprom` | an EEPROM image |
| `hdd` | a clean hard disk image; xemu's own blank `xbox_hdd.qcow2` (7 MB) is best. Runs never write to it: each run, and each profile the GUI plays, gets an overlay holding only what the game writes |
| `extract_xiso` | [extract-xiso](https://github.com/XboxDev/extract-xiso), which packs the disc image |
| `gdb` | optional: `gdb`, for the `--gdb` options |

```powershell
python tools/tes3x_xemu.py first-run profiles/my-build.toml --direct-engine --skip-intro --exec script.txt
```

`--exec` puts a [command file](#commands-without-a-controller) on the disk, and `--direct-engine`
boots the game itself instead of the retail launcher, which `@start` needs. Each run gets a new
folder under `build/xemu/` with the log, the disc image and xemu's output. Everything after `--`
goes to the pipeline, such as `-- --preset minimal`. `--help` lists the rest, including
screenshots, saves and GDB.

The GUI's local settings hold the same keys. xemu timings don't reflect a real Xbox,
so use it to check that a build runs, not how fast.

## Profile smoke tests

Boot a profile in xemu, start a new game, walk to Balmora and check the log for crashes and
hangs:

```powershell
python tools/tes3x_test.py profiles/my-build.toml --record
python tools/tes3x_test.py profiles/my-build.toml --keep-artifacts always
python tools/tes3x_test.py profiles/my-build.toml --library-all --record
python tools/tes3x_test.py profiles/my-build.toml --library-all --library "D:/Mods To Test"
```

The test build adds `diagnostics` and `console` to the profile. A pass only means that one route
worked; it isn't a full playthrough.

A run's log and build record are kept when a test fails and deleted when it passes. The ISO and
the built game files, about 2 GB a run, are deleted either way. `--keep-artifacts never` or
`always` changes that; `always` keeps the ISO and game files too. `--library-all` tests every mod in the library on its own, using
the profile for everything else.

## Patch scenarios

A patch folder can hold a `scenario.toml`: a script to run and the log lines to expect. See
[validation](validation.md).
