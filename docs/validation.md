# Patch validation

TES3X keeps a patch's release decision separate from its test results.

Each implemented patch has one channel in [`patches.toml`](../patches.toml):

| channel | meaning |
|---|---|
| `development` | Available for investigation and testing. |
| `release` | The maintainer has approved it for supported public use. |

Every patch starts in `development`. Passing automated validation is input to the maintainer's
decision; it never promotes a patch. A release-channel patch can remain opt-in, since channel and
selection policy answer different questions.

## Scenarios and results

A `patches/<patch>/scenario.toml` is the human-written validation contract. It contains:

- the behavior being exercised and the repeatable procedure;
- a command script and controlled runner settings;
- required and forbidden log observations;
- the limits of what the scenario establishes;
- whether it is a `single` exercise or a `comparison`.

A single scenario runs one test build. It suits features whose behavior can be observed directly.
A comparison scenario runs a control without the patch and a test build with it; use one when the
contrast establishes an engine defect, a fix's causal effect, or a performance difference.

The generated validation result records one execution of that contract. It keeps the complete
logs, selected observations and hashes. Its provenance sidecar captures sanitized build and run
commands, the clean TES3X revision, retail and patched XBE hashes, toolchain versions, profile
hash, selected patches, mod list, plugin order and hashes, Data Files digest, INI hashes and
overrides, scripted inputs, platform, BIOS and RAM. Machine paths are replaced with placeholders.

A passing result says that the scenario's expectations passed on that revision and platform. It
does not establish general correctness, absence of regressions, hardware behavior when run in
xemu, or release readiness. The scenario's `limitations` should name the important gaps.

Full logs are retained as run artifacts, not treated as exact golden output. Timestamps, build
identifiers and other harmless values change between runs. The scenario's regular expressions are
the stable oracle; the runner also rejects crash, hang and fatal log lines globally.

Historic schema 1 and 2 files remain valid validation results. New results use schema 3 and bind
the result to its scenario by path and hash.

## Scenario format

```toml
kind = "comparison" # or "single"
purpose = "The specific behavior this scenario exercises."
procedure = "How the run can be repeated."
limitations = "Behavior and environments this scenario does not cover."
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

For `kind = "single"`, omit `expect.control` and keep `expect.test`. Every expectation is a
regular expression matched against a log line. A value starting with `!` must match no line.

`enable` can replace the default common patches, `apply` supplies a valued patch, `xemu` replaces
the default runner options, `timeout` changes the run limit, and `save` names a fixture under the
private `build/saves/` directory. The build profile remains an explicit runner argument because
content and retail paths are local; its exact identity and hash are captured in the result.

## Running in xemu

```powershell
python tools/tes3x_scenario.py mcp-102 profiles/one-mod.toml
python tools/tes3x_scenario.py mcp-102 profiles/one-mod.toml --record
```

The runner builds the roles required by the scenario, boots them concurrently when there are two,
checks their logs, and writes a schema 3 result only when the complete scenario passes. `--reuse`
checks existing run folders without booting xemu again.

Then cite the result as validation and regenerate the table:

```toml
validation = ["patches/mcp-102/2026-09-24-xemu.toml"]
```

```powershell
python tools/tes3x_patches.py --write
python tools/tes3x_validate.py check
```

Recording a passing result requires a clean TES3X tree. The result does not modify `channel`.

## Recording hardware validation

Keep each hardware log with its build's `.tes3x-pipeline.json`. Describe the console in a private
local file:

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

Use a pseudonymous unit name, never a serial number, MAC address or EEPROM identifier. Record a
single scenario with `--test`; add `--control` for a comparison:

```powershell
python tools/tes3x_validate.py record mcp-102 --env hardware `
  --scenario patches/mcp-102/scenario.toml `
  --control build/hardware/control/tes3xlog.txt --control-build build/control `
  --test build/hardware/test/tes3xlog.txt --test-build build/test `
  --hardware-config hardware/xbox-a.toml
```

`--fixture LABEL=PATH` hashes an additional script, save or other controlled input. Do not put
personal saves in public validation results.

Hardware validation and manual playtesting are separate. A scripted hardware result records its
narrow observations. Manual testing informs the maintainer's release decision but does not need a
generated validation result or a stronger formal claim.
