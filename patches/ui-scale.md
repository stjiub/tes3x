# UI scaling

Patch key: `ui-scale`

Scale in-game text, the help strip, minimap, resource meters, equipment art, the options,
level-up and character creation menus, message boxes, and common menu panels together to leave more of the picture visible. On a
640×480 widescreen display, an optional horizontal correction keeps their proportions after
the display stretches the frame to 16:9.

## How it works

The pipeline writes smaller font metrics into the staged `.fnt` files without changing their
texture atlases. The patch changes fixed-size art just after the game builds the HUD, before its
first layout. It keeps the HUD frame at its retail size, so the map and
meters remain near their original screen edges. The scaling also changes their layout and hit
rectangles. After the HUD layout, the patch scales the centered rectangle that the game uses to
size ordinary windows. Their builders receive the smaller dimensions and set matching size bounds;
the underlying HUD frame stays at its retail size. Each refresh starts from the game's unscaled
rectangle, so opening menus again does not multiply the factor.

The options menu uses its own fixed dimensions. The patch scales its button blocks and images
with its outer rectangle before the game's normal layout pass.

The level-up menu starts from the common rectangle but has fixed content that would otherwise
make it grow beyond that size. The patch scales its two fixed-width blocks and illustration
before layout.

Message boxes size to their text. The patch reduces their fixed maximum width before layout,
so longer messages wrap within the scaled area.

The character name menu has a fixed keyboard. The patch scales its keys, rows, buttons and clip
insets together before layout. The keys retain their original spacing, so the outer menu stays
wide enough to contain them.

The race, class choice and birthsign screens have their own fixed layouts. The patch scales their
controls and panels together before those screens' final layout passes.

## Using it

Set `[ui] scale` in a profile to a factor such as `0.8`; this selects `ui-scale` and applies the
same factor to fonts, HUD art, the options, level-up and character creation menus, message boxes, and common menu panels. For 480p
stretched to fill a 16:9 display, use both settings:

```toml
[ui]
scale = 0.8
widescreen_480p = true
```

The widescreen setting multiplies font widths and spacing by another 0.75 in the staged font files.
It applies the same correction to HUD art and supported menu widths at 640×480. Because font files
are baked into the build, use a profile without `widescreen_480p` for 720p output.
Leave it off when 480p is shown at 4:3. The extra horizontal correction does not run at 720p.
The separate [`hd-video`](hd-video.md) patch can enable 720p, but is not needed for 480p; with 720p disabled
in the dashboard, the game keeps its retail 640×480 request.

Availability and selection are in the [patch table](../docs/patches.md).

## Compatibility and limits

This patch scales retail text, the help strip, minimap, resource bars, equipment art, the options,
level-up and character creation menus, message box width limits, and menus sized from the HUD's central area, including inventory and map.
The in-game console keyboard keeps its full-width layout, fitted to the current screen. The
standalone Dash keyboard is unaffected by this engine patch.
Other menus with independent fixed dimensions keep their retail size. Controller labels follow
their auto-sized font rows. Text wraps
differently at smaller sizes. Art
whose dimensions differ from the retail HUD may keep its original size.
