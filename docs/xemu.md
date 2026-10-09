# Running in xemu

[xemu](https://xemu.app) is an original-Xbox emulator. TES3X supports playing profiles in it with
persistent saves, as well as scripted runs and game tests without a console. Its timings do not
reflect a real Xbox, so use original hardware for performance measurements.

## Setup

Each xemu `[targets.NAME]` in `tes3x.local.toml` holds the emulator and firmware it uses. The GUI's
target editor writes the same keys, all of which you supply yourself.

| key | file |
|---|---|
| `folder` | optional: a folder the files below are found in when not set; the GUI's Settings download xemu and a blank HDD image into it |
| `exe` | `xemu.exe` |
| `bootrom` | the MCPX boot ROM |
| `bios` | the BIOS for normal runs, such as a retail one |
| `bios_128mb` | optional: a BIOS that uses 128 MB, for `--ram 128 --bios 128mb` |
| `eeprom` | optional: an EEPROM image; without one xemu makes a new one each run |
| `hdd` | a clean hard disk image; xemu's own blank `xbox_hdd.qcow2` (4.5 MB) is best, and the GUI's Settings download it. Runs never write to it: each run, and each profile the GUI plays, gets an overlay holding only what the game writes |
| `extract_xiso` | [extract-xiso](https://github.com/XboxDev/extract-xiso), which packs the disc image |
| `gdb` | optional: `gdb`, for the `--gdb` options |
| `template` | optional: a base `xemu.toml` whose storage and network sections TES3X replaces |

Different targets can select different xemu versions, firmware, clean disks and guest memory.
Use `--target NAME`, or pass `--ram 64|128` for one run. A 128 MB target uses `bios_128mb` and
clears the XBE's `Limit64MB` flag in the staged copy. `--video 480i|480p|720p` boots a run with that
dashboard HDTV setting written into its copy of the EEPROM, on xemu's HDTV AV pack, and shows the
picture at the shape the game draws it; it needs an `eeprom`. The former shared `[xemu]` table is still
read as fallback defaults for older local configurations.

## Playing from the GUI

Open a profile and choose **Play**. Each profile keeps its own emulated hard disk, so saves persist
between sessions and appear in the GUI's **Saves** tab. **Actions > Reset xemu saves…** gives that
profile a clean disk again. Select an xemu target to show that disk in Saves; an Xbox target shows
that console's saves instead. Choose another xemu configuration from the target dropdown.
**Debug with GDB** in the Play menu opens xemu's debugger stub; while the game runs, the status bar
shows its port and the command to attach (`gdb -ex "target remote 127.0.0.1:PORT"`). Play becomes
**Stop** while the session is open; it terminates only the PID that this run started, then lets the
runner recover the log from the emulated disk.

An overlay-layout profile is also playable in xemu. TES3X adds the clean retail base to the disc
image for that run; it does not need the Xbox's shared-base path.

A build with `multiplayer` or `agent` plays with xemu's NAT network: the game gets an address by
DHCP, joins servers anywhere, and reaches the GUI's in-game agent listener at `10.0.2.2`. The
runner's `--net-nat` does the same for scripted runs; see
[multiplayer](multiplayer.md#connect-xemu).

## Scripted and automated runs

`tes3x xemu` builds a profile, packs it as a disc image, boots it on a fresh copy of the
clean hard disk, and copies the log back out:

```powershell
tes3x xemu first-run profiles/my-build.toml --direct-engine --skip-intro --exec script.txt
```

- `--exec` puts an [exec script](exec-scripts.md) on the disk.
- `--direct-engine` boots the game itself instead of the retail launcher. For an overlay-layout
  build this also exercises the overlay against the disc's `Base` folder. Without the option,
  xemu Play reconstructs a full disc so the unpatched retail launcher works. `@start` needs direct
  engine boot: the launcher has no hook, and the game relaunches itself on New Game.
- `--add DISC=FILE` puts another file on the disc, such as a second program for the exec script's
  `launch` to start: `--add Tool/default.xbe=build/tool/bin/default.xbe` is
  `\Device\CdRom0\Tool\default.xbe`.
- `--put DEST=SOURCE` copies a file or folder onto the run's `E:` drive, such as a build folder
  for the console manager to find: `--put Games/Test=build/test-build` is `E:\Games\Test`.
  `--deploy` with a folder holding only the manager boots the manager instead of the game.
- Everything after `--` goes to the pipeline, such as `-- --preset minimal`.
- `--help` lists the rest, including screenshots, saves, memory size and GDB.

Each run gets a new folder under `build/xemu/` with the log, the disc image and xemu's output.

## What to expect

- Without `[Xbox] Diagnostics=1` there is no crash record and no watchdog. A crash shows as
  `crash.*` lines in the log, a stall after the first frame as `hang.detected`; a hang while
  loading leaves only the session header. See [diagnostics](diagnostics.md).
- Drive an automated run with an exec script, not synthetic keystrokes: xemu samples held keys and
  loses short presses. Normal interactive play uses the configured controller or keyboard.
