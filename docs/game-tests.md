# Game tests

A game test is a TOML file in `tests/game/`, named after the patch it tests: `mcp-37.toml` tests
`mcp-37`. `smoke.toml` is the [profile smoke test](testing.md#profile-smoke-tests). A game test
boots the game, so it never runs with the unit tests; they only check that each file is well
formed. Running, recording and checking game tests is covered in [validation](validation.md).

## Example

```toml
kind = "comparison"      # or "single"
purpose = "What this test shows."
procedure = "How it shows it."
limitations = "What it does not cover."
timeout = 180
script = '''
@start new
wait 60
coc "Balmora, Caius Cosades' House"
wait 60
"caius cosades"->cast "fire bite" player
wait 20
coc "Balmora"
wait 60
assert player->getpos x == -12288.00
exit
'''

[expect]
control = ['!mcp37\.cancelled', 'exec\.exit']
test = ['mcp37\.cancelled 1', 'exec\.exit']
```

## Kinds

A `single` test runs one build with the patch. A `comparison` also runs a control build without
it, for when the difference is the point. Both sides of a comparison must pass.

## Required keys

| key | meaning |
|---|---|
| `kind` | `single` or `comparison` |
| `purpose` | what the test shows |
| `procedure` | how it shows it |
| `script` | an [exec script](exec-scripts.md) |
| `[expect]` | `test`, and for a comparison also `control`: the regular expressions that side's log must satisfy |

Each expectation is a regular expression that some log line must match; one starting with `!` must
match none. A run fails on any crash, hang or fatal error line, a failed `assert`, or fewer asserts
run than the script has. Prefer asserts and a patch's own log counters, such as `mcp37.cancelled`,
to matching incidental output.

## Sequences and comparisons

`[sequence]` gives ordered regular expressions for a role when presence alone is not enough. A
comparison test can also use `[[compare]]` with a one-capture `pattern` and a numeric `relation`
(`>`, `>=`, `<`, `<=`, `==` or `!=`); every captured test value is compared with the corresponding
control value.

```toml
[sequence]
test = ['autosave\.slot 1', 'autosave\.slot 2', 'autosave\.slot 3', 'autosave\.slot 1']

[[compare]]
pattern = 'diag\.free_kb ([0-9]+)'
relation = ">"
```

## Optional keys

| key | meaning |
|---|---|
| `limitations` | what the test does not cover |
| `timeout` | seconds before a run is stopped, default 300 |
| `watch` | a regular expression for the log lines worth keeping |
| `enable` | the other patches every build carries (default `diagnostics` and `console`) |
| `apply` | a valued patch, such as `profile=0x00137C50` |
| `save` | a save the script loads, by fixture name under `build/saves/` |
| `xemu` | xemu runner options (default `--skip-intro`, `--no-reboot`) |
| `pipeline` | extra pipeline options, such as `--diag-test-faults` |
| `required_mods` | enabled profile mods the scenario needs, checked before the run |
| `allow` | the failure patterns a test causes on purpose: `crash\.`, `hang\.detected` or `fatal\.` |
| `agent` | requests sent through the in-game agent; see [Driving the agent](#driving-the-agent) |

`save` and `required_mods` name inputs that stay on your machine: they are hashed into a result's
provenance but never copied into the repository.

## Shaping the build

`[profile]` overlays the chosen profile's `profile`, `rules`, `preferences`, `package` or `ini`
tables, such as `profile = { save_pool = "TES3X Test" }`.

`[fixture]` generates a test mod from your own `Morrowind.esm` and makes it the build's only mod:
`opcodes = [0x2001]` appends those opcode calls to the global script `Main` (or `script`), and
`texture = true` adds one 4x4 texture so there is an asset to pack.

## Driving the agent

`[agent]` runs a tunnel (`tes3x_net.py serve`) and a listener (`tes3x_agent.py`) with a throwaway
key beside each xemu, and points the build's `NetAgent` at it. Once a game log line matches
`after`, the listener runs each `console` line, copies each `fetch` path into
`build/xemu/NAME.fetched/`, then sends `exit` when `exit = true`:

```toml
[agent]
after = 'mem\.agent-ready '
console = ["player->getpos x"]
fetch = ['E:\tes3xlog.txt']
exit = true
```

The run fails unless every request succeeds; the listener's output is in
`build/xemu/NAME.agent.txt`.
