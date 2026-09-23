# console: in-game console on a pad

The Xbox build keeps Morrowind's console, but no input reaches it. The patch opens it from the
pad, adds a way to type, and runs commands from a file for testing without input.

## What the patch changes

- `0x00098430`: the input check that gates `Console::Toggle` becomes a two-button combination,
  `[Xbox] ConsoleCombo` (default Back + right thumb click).
- The on-screen keyboard's 31-character limit is raised to 95 while the console owns it, at its key
  and space handlers.
- Every call to the console's printf (`0x0008BB90`, 216 sites) goes through the payload, which
  logs the first lines of a command's output as `console< ...` and then prints as before.
- `tes3xexec.txt` in `E:\` or beside `default.xbe` runs through `CompileAndRun` once per launch;
  see the [README](../../README.md#in-game-console).

## Testing it

[`scenario.toml`](scenario.toml) runs a New Game, reads the player's position, moves to Balmora
and reads it again. The control build has no console patch and never reads the file.
