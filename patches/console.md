# In-game console

The Xbox build keeps Morrowind's console, but no input reaches it. This patch opens it from the
pad, adds a way to type, and runs commands from a file, so a build can be tested without input.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

- `0x00098430`: the input check that gates `Console::Toggle` becomes a two-button combination.
- The console window fills the top half of the screen and keeps a command history.
- A opens the on-screen keyboard in the bottom half. Its 31-character limit is raised to 95 while
  the console owns it, at its key and space handlers, and new keys add the symbols some commands
  need and step through the history.
- Every call to the console's printf (`0x0008BB90`, 216 sites) goes through the payload
  (`hooks/tes3xconsole.c`), which logs the first lines of a command's output as `console< ...` and
  then prints as before.
- `tes3xexec.txt` in `E:\` or beside `default.xbe` runs through `CompileAndRun` once per launch.
- At the XBE entry, an `@start` line in that file puts the engine's own relaunch data (`'BXWM'`,
  New Game or a save path) in the kernel's launch data page, so the game starts without the main
  menu.
- `tes3x_mailbox` is checked once per frame: a debugger that writes a command and changes its
  sequence number has it run on the next frame, logged as `live> ...`. Nothing writes it on
  hardware, so it costs one comparison per frame.

## Using it

Back + right thumb click opens the console, and A raises the on-screen keyboard. The command file
format is in [testing](../docs/testing.md).

## Configuration

`[Xbox] ConsoleCombo` in `Morrowind.ini` sets the two inputs that open the console, as two input
indices; the default `7,9` is Back + right thumb click. See [ini keys](../docs/ini-keys.md).
