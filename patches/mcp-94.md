# Book and scroll scaling fix

Patch key: `mcp-94`

The game sizes books, the journal and scrolls from the screen's width. On a 4:3 picture that
fits, but on a 16:9 one, such as the 1280x720 that [`hd-video`](hd-video.md) renders, the
menus grow taller than the screen and their top and bottom are cut off. This patch sizes them
from the screen's height instead, so they fit at any aspect ratio.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

Two places compute the scale: the helper that ShowBookMenu and the journal share, and
ShowScrollMenu. Each divides 640 by the screen width. The patch makes each divide 480 by the
screen height, reusing a 480.0 constant the game already has. At 640x480 both give the same
scale, so the retail picture is unchanged.

The PC Morrowind Code Patch makes the same change with 400 in place of 480, which also enlarges
books on 4:3 screens; this port keeps the retail size.
