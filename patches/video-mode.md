# 720p video output

Patch key: `video-mode`

Retail Morrowind always renders at 640x480, whatever the dashboard allows. With this patch, a
console set up for 720p renders the game at 1280x720 and outputs a 720p progressive widescreen
signal, so an HDTV shows a sharper 16:9 picture instead of scaling up a 4:3 one. It needs the HD AV pack
(component cables) and **720p** turned on under the dashboard's HDTV settings. Any other setup
keeps the retail 640x480 picture, at 480p or 480i as the dashboard and cables allow.

720p needs about 7 MB more memory, so plan on a console upgraded to 128 MB; see
[Compatibility and limits](#compatibility-and-limits).

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The hook (`hooks/tes3xvideo.c`) runs just before the game creates its renderer. It looks through
the display modes Direct3D lists for the connected AV pack and the dashboard settings. If 1280x720
progressive is among them, it sets the game's renderer settings to 1280x720, so the engine,
viewport and menus all use the new size. Then, at device creation, it copies that mode's
progressive and widescreen flags into the present parameters. If 720p is not listed, nothing
changes, so the patch never asks for a mode the console cannot output.

Books, the journal and scrolls scale with the screen and would not fit a 16:9 picture, so this
patch turns on [`mcp-94`](mcp-94.md), which scales them by screen height.

## Compatibility and limits

- **Memory.** The colour, back and depth buffers are three times larger at 1280x720. In game this
  costs about 7 MB more than 640x480 (measured in Seyda Neen with retail data, 128 MB, xemu). A
  stock 64 MB console has little to spare even for retail data, and modded load orders already
  run short, so expect 720p to need a console upgraded to 128 MB.
- **Speed.** The console draws three times as many pixels per frame, which can lower the frame
  rate.
- **Picture.** The camera keeps the retail horizontal field of view, so at 16:9 it shows less
  above and below than at 4:3. The HUD and menus are laid out in pixels, so they look smaller on
  screen than at 640x480.
