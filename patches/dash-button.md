# TES3X button

Patch key: `dash-button`

Adds a TES3X button to the engine's menu that leaves the game for the TES3X Dash, where builds,
servers and saves are managed. The same menu is the main menu and the pause menu, so the button
is in both. In a build that includes multiplayer it takes the place of the Dash button the
multiplayer menu used to add.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The hook (`hooks/tes3xdashbutton.c`) runs right after the engine builds its menu. It adds a block
of three images, `menu_tes3x`, `menu_tes3x_over` and `menu_tes3x_pressed`, above Options, the way
the engine makes its own buttons, grows the menu by one row and links the pad's Up and Down
through it. Pressing it starts the Dash. The build tool stages the three textures and lists them
for the engine to prefer over the archive.

The menu is either the retail one or this one, so the build tool stages all of the redrawn button
textures, not just this button's, and the patch needs `boot-clear` and `menu-still`, which are
turned on with it. A build that offers a way back to the Dash is one that starts without the retail
launcher, which is the same builds that get the still. The main menu is moved up to make room for
the extra row.

## Configuration

`[Xbox] Dash` in `Morrowind.ini` is the Dash's path, which installing the Dash from the Dash
itself writes. Without it no button is added.

## Compatibility and limits

- Pressing the button from the pause menu leaves the game without saving; save first.
- The artwork is drawn for the menu's 128-pixel-wide buttons.
