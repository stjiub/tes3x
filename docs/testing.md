# Testing and debugging

The patched game can record what it does, run commands without a controller, and time engine
functions. Everything it writes goes to the root of `E:`; copy it off the Xbox with
`tools/tes3x_fetch.py`, or read it from an emulator's disk.

| Patch | Gives you | Writes |
|---|---|---|
| `diagnostics` | session header, crash records, hang watchdog, snapshots | `E:\tes3xlog.txt` |
| `console` | the in-game console on a pad, commands from a file, their output in the log | `E:\tes3xlog.txt` |
| `profile` | call counts and CPU cycles for chosen functions | `E:\tes3xprof.bin` |

The `development` preset includes `diagnostics` and `console`. The profiler is added by name.

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
| `exit` | turn the Xbox off |
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

## Profiling

`--apply profile=VA[,VA...]`, or the pipeline's `--profile-target VA`, times up to 16 functions at
every direct call site. Counters go to `E:\tes3xprof.bin` on the console command `tes3xprof`, on
`tes3xprof mark` (dump, then reset), at loader checkpoints, and every `[Xbox] ProfileDumpFrames`
frames. `tes3xprof reset` clears them.

Only an original Xbox gives meaningful timings; an emulator run shows only that the build does not
crash. A command file makes measurements repeatable: move to the same place, wait the same number of
frames, then `tes3xprof mark`.

## Proof

A fix is proven by two runs of the same test, one without the patch and one with it, compared by
their logs. [Verification](verification.md) describes proof records and the `scenario.toml` a
patch can keep so the test can be run again. A scenario is a command file plus the log lines each
run must, or must not, show.
