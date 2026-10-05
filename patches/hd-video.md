# HD video output

Patch key: `hd-video`

Retail Morrowind always renders at 640x480 and never offers 720p, even on a console whose
dashboard allows it. With this patch, the game follows the dashboard's HDTV settings and renders
at the best mode they allow. With 720p on, it renders at 1280x720 and outputs a progressive 16:9
signal, so an HDTV shows a sharper widescreen picture instead of scaling up a 4:3 one.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The hook (`hooks/tes3xvideo.c`) runs just before the game creates its renderer. It looks through
the display modes Direct3D lists for the connected AV pack and the dashboard settings. If 1280x720
progressive is among them, it sets the game's renderer settings to 1280x720, so the engine,
viewport and menus all use the new size. Then, at device creation, it copies that mode's
progressive and widescreen flags into the present parameters. If 720p is not listed, the game
keeps its retail 640x480 request, and Direct3D picks 480p or 480i from the dashboard as it does
without the patch. The patch never asks for a mode the console cannot output.

Books, the journal and scrolls scale with the screen and would not fit a 16:9 picture, so this
patch turns on [`mcp-94`](mcp-94.md), which scales them by screen height.

## Using it

The dashboard's HDTV settings (under Settings, Video) and the AV cable choose the mode. Nothing in
`Morrowind.ini` changes it:

| AV cable | Dashboard | Picture |
|---|---|---|
| HD AV pack (component) | 720p on | 1280x720, 720p |
| HD AV pack (component) | 720p off, 480p on | 640x480, 480p (as retail) |
| HD AV pack (component) | both off | 640x480, 480i (as retail) |
| Any other cable | - | 640x480, as retail |

To play at 480p, turn 720p off in the dashboard and leave 480p on. That setting applies to every
game on the console, so other 720p games drop to 480p as well. On a 64 MB console, use 480p; see
[Compatibility and limits](#compatibility-and-limits).

## Compatibility and limits

- **Memory.** At 720p the colour, back and depth buffers are three times larger. In game this
  costs about 7 MB more than 640x480 (measured in Seyda Neen with retail data, 128 MB, xemu). A
  stock 64 MB console has little to spare even for retail data, and modded load orders already
  run short, so expect 720p to need a console upgraded to 128 MB. At 480p and 480i the patch
  costs no memory.
- **Speed.** At 720p the console draws three times as many pixels per frame, which can lower the
  frame rate.
- **Picture.** At 720p the camera keeps the retail horizontal field of view, so it shows less
  above and below than at 4:3. The HUD and menus are laid out in pixels, so they look smaller on
  screen than at 640x480.
