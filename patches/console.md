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
  see [testing and debugging](../docs/testing.md).
- At the XBE entry, an `@start` line puts the engine's own relaunch data (`'BXWM'`, New Game or a
  save path) in the kernel's launch data page, so the game starts without the main menu.
- `tes3x_mailbox`, checked once per frame: a debugger that writes a command and changes its
  sequence number has it run on the next frame, logged as `live> ...`. Nothing writes it on
  hardware, so it costs one comparison per frame.
