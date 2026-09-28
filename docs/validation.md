# Patch scenarios

A patch folder can hold a `scenario.toml` that says how to test the patch in xemu: a command
script to run and the log lines to expect. A `single` scenario runs one build. A `comparison`
runs a build without the patch and one with it, for when the difference is the point.

## Format

```toml
kind = "comparison" # or "single"
purpose = "What this scenario tests."
procedure = "How to repeat it."
limitations = "What it doesn't cover."
watch = 'regex for the lines copied into the result'
apply = "profile=0x00137C50" # optional valued patch
timeout = 180
script = '''
@menu click MenuOptions MenuOptions_New_container
wait 30
player->getpos x
exit
'''

[expect]
control = ['diag\.enabled', '!feature\.result']
test = ['feature\.result 1', 'exec\.exit 0']
```

Each expectation is a regular expression that some log line must match; one starting with `!`
must match none. A `single` scenario has only `expect.test`. Any crash, hang or fatal error line
fails the run regardless.

Optional keys: `enable` replaces the default test patches, `xemu` the runner options, and `save`
names a save to load from `build/saves/`.

## Recording a result

`tes3x_validate.py record` checks finished runs against a scenario and, if they pass, writes a
result into the patch folder with the full logs and the hashes of everything that went into the
build. Machine paths are replaced with placeholders, and it needs a clean git tree. A run is a log
file or an xemu run folder.

`python tools/tes3x_validate.py check` checks every scenario and result.

## Recording a run on hardware

Keep each hardware log with its build's `.tes3x-pipeline.json`, and describe the Xbox in a local
file:

```toml
[hardware]
unit = "xbox-a"
board_revision = "1.4"
installed_ram_mb = 64
title_ram_mb = 64
cpu = "stock"
cpu_mhz = 733
bios = "retail 5101"
storage = "HDD"
video_mode = "480p"
```

Use a made-up unit name, never a serial number, MAC address or EEPROM data. Record a `single`
scenario with `--test`, and add `--control` for a comparison:

```powershell
python tools/tes3x_validate.py record mcp-102 --env hardware `
  --scenario patches/mcp-102/scenario.toml `
  --control build/hardware/control/tes3xlog.txt --control-build build/control `
  --test build/hardware/test/tes3xlog.txt --test-build build/test `
  --hardware-config hardware/xbox-a.toml
```

`--fixture LABEL=PATH` also hashes a script, save or other input. Don't put personal saves in a
public result.
