# Main menu still

Patch key: `menu-still`

The retail launcher shows the title over a looping movie. A build that starts the engine directly
shows the engine's own main menu, which plays the logo movie once and then sits on a black
screen. This patch skips the movie and puts a still picture behind the menu: the parchment,
emblem and Morrowind title from the start of the retail menu loop, without its drifting runes, zoomed slightly and cropped at the top so the menu's buttons sit under
the title.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The build tool averages the first second of the retail `mw_menu.bik` with `ffmpeg`, which removes
the video's compression blocks, and stages it as
`Data Files\Textures\tes3x_menu_still.raw`. When the main menu opens, the hook
(`hooks/tes3xmenustill.c`) waits for the graphics hardware to finish, paints the picture into
both display buffers and stops the engine's per-frame colour clear while the menu is up. Starting
a new game or loading one releases it, and every other screen draws as before. With no colour
clear, what a menu hides would stay on screen, so the picture is painted again, a frame in each
buffer, whenever the menu shows or hides something.

## Compatibility and limits

- The build tool needs `ffmpeg` on the `PATH` and the retail `mw_menu.bik`. Without either, the
  patch is inert and the logo movie plays as usual.
- The picture is fitted to the screen's height and centred, with black bars beside it on a
  widescreen mode.
- It replaces the logo movie only on the main menu; it does not change the pause menu.
