# Menu hint line

Patch key: `dash-button`

Adds a line under the engine's menu, main and pause alike, that shows a pad button's icon and what
it does. This patch's own entry is White, which leaves the game for the TES3X Dash, where builds,
servers and saves are managed. In a build that includes multiplayer the same line offers Black for
Join (main menu) and for Save to Server and Leave (pause menu), so the engine's buttons stay as
they are.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The hook (`hooks/tes3xdashbutton.c`) runs right after the engine builds its menu. It adds a block
holding the Black and White icons and their text, in the gold of the server list's text, under the
last button, grows the menu by one line and moves the main menu a little lower. The menu is
widened while the line shows. The console patch reports the pad's Black and White each frame, and
pressing White while the menu is up starts the Dash. The patch needs `boot-clear`,
which is turned on with it: a build that offers a way back to the Dash is one that starts without
the retail launcher. `menu-still` is separate and can be turned off.

## Configuration

`[Xbox] Dash` in `Morrowind.ini` is the Dash's path, which installing the Dash from the Dash
itself writes. Without it White does nothing and, without multiplayer, no line is shown.

## Compatibility and limits

- White needs the `console` patch, which reports the pad's buttons.
- Pressing White from the pause menu leaves the game without saving; save first.
