# Main menu still

Patch key: `menu-still`

The retail launcher shows the title over a looping movie. A build that starts the engine directly
shows the engine's own main menu, which plays the logo movie once and then sits on a black
screen. This patch skips the movie and puts a still picture behind the menu: the parchment and
dragon emblem from the start of the retail logo movie, with no title text, so the menu entries
do not cover a logo.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The build tool takes one frame of the retail `mw_logo.bik` with `ffmpeg` and stages it as
`Data Files\Textures\tes3x_menu_still.raw`. When the main menu opens, the hook
(`hooks/tes3xmenustill.c`) waits for the graphics hardware to finish, paints the picture into
both display buffers and stops the engine's per-frame colour clear while the menu is up. Starting
a new game or loading one releases it, and every other screen draws as before.

## Compatibility and limits

- The build tool needs `ffmpeg` on the `PATH` and the retail `mw_logo.bik`. Without either, the
  patch is inert and the logo movie plays as usual.
- The picture is fitted to the screen's height and centred, with black bars beside it on a
  widescreen mode.
- It replaces the logo movie only on the main menu; it does not change the pause menu.
