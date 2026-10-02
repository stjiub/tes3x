# Keep responses of repeated topics

Patch key: `dialogue-merge`

A plugin that adds a response to an existing dialogue topic carries that topic's DIAL record
followed by its new INFO records. When the engine meets a DIAL it already has, it merges the two.
On PC the topic keeps its responses and the plugin's are added to them. The Xbox build instead
replaces the topic's response list with the new, still-empty one, so every response loaded before
that plugin disappears from the game.

Any plugin that touches a retail topic therefore removes the retail responses under it. Mods that
add a greeting, a rumor or a voice line erase the game's own. Tamriel Data and Tamriel Rebuilt
repeat several hundred topics between them, including `Hello`, the greetings, `Latest Rumors` and
some journal topics, and lose about 24,000 earlier responses that way. This patch restores the PC
behavior.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The repeated-DIAL merge at `0x000FE060` corresponds to the PC's `0x004B2790`. The PC function adds
the replacement's responses to the existing topic. The Xbox function copies the replacement's
response-list head over the existing one at `0x000FE0E1`. The replacement was just constructed
and its responses have not loaded yet, so that head is always empty. The patch replaces the
three-byte store with no-ops. Later responses then attach to the existing chain, as on PC, and the
post-load pass frees their temporary name tables as it does for every other response.

### Effect with Tamriel Rebuilt

With Tamriel Data 26.08 and TR_Mainland 26.08.23 loaded after the Xbox's Morrowind.esm, the
retail build loses 23,983 responses: 18,098 from Morrowind.esm and 5,885 from Tamriel Data. Two
of the affected topics, counted in game:

| Topic | Without the patch | With it |
|---|---|---|
| `Hello` | 199 | 7,174 |
| `Latest Rumors` | 1,680 | 2,024 |

The lost responses were loaded but unreachable, so restoring them costs no memory. With them back
on their topics, the heap in use after loading falls by about 2.8 MB (52.1 to 49.3 MB at the first
frame, measured in xemu with 128 MB). The peak during loading does not change, so the patch does
not lower the memory a load order needs.

## Compatibility and limits

Responses from earlier plugins come back, which can change what NPCs say in a load order that was
arranged around their absence. A plugin that relied on repeating a topic to suppress its earlier
responses gets the PC behavior instead.
