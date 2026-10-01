# Running in xemu

[xemu](https://xemu.app) is an original-Xbox emulator. TES3X uses it to check that a build boots and
plays, and to run game tests without a console. Its timings do not reflect a real Xbox: use it to
check that a build runs, not how fast.

## Setup

`tools/tes3x_xemu.py` needs these under `[xemu]` in `tes3x.local.toml`, all of which you supply
yourself. The GUI's local settings hold the same keys.

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

## A run

`tools/tes3x_xemu.py` builds a profile, packs it as a disc image, boots it on a fresh copy of the
clean hard disk, and copies the log back out:

```powershell
python tools/tes3x_xemu.py first-run profiles/my-build.toml --direct-engine --skip-intro --exec script.txt
```

- `--exec` puts an [exec script](exec-scripts.md) on the disk.
- `--direct-engine` boots the game itself instead of the retail launcher. `@start` needs it: the
  launcher has no hook, and the game relaunches itself on New Game.
- Everything after `--` goes to the pipeline, such as `-- --preset minimal`.
- `--help` lists the rest, including screenshots, saves, memory size and GDB.

Each run gets a new folder under `build/xemu/` with the log, the disc image and xemu's output.

## What to expect

- Without `[Xbox] Diagnostics=1` there is no crash record and no watchdog. A crash shows as
  `crash.*` lines in the log, a stall after the first frame as `hang.detected`; a hang while
  loading leaves only the session header. See [diagnostics](diagnostics.md).
- Drive a run with an exec script, not keystrokes: xemu samples held keys and loses short presses.
