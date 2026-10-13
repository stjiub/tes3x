# Code payload injection

Patch key: `payload=FILE.pe`

Most TES3X fixes need new code, not just changed bytes. This patch adds that code to the retail
XBE as a new section and runs it before the game starts. Every patch that names a payload source
in the patch table depends on it.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

`tes3x payload` compiles the selected sources in `hooks/` with clang and links them with
lld-link into a small PE image. The code is freestanding: it cannot link the Xbox SDK libraries
the game already contains, so it calls the kernel through the game's own import thunks and reaches
engine functions by address. Some addresses come from content searches; others are constants for
the supported engine. By default the payload is built for that exact image. With
`--portable-bindings`, the original entry, kernel thunk slots and supported engine addresses are
stored in a binding table filled from the target XBE at patch time. Beside the PE,
`tes3xhook.json` lists its hooks and binding layout.

The patch then:

- appends the image as a new section (`.tes3xhk` by default), applying its PE32 relocations
  when the section lands at a different address;
- fills any binding table from the target XBE, checking required kernel ordinals and engine sites;
- points the XBE's entry at the payload. The payload's entry initializes the selected features,
  logs the session to `E:\tes3xlog.txt`, and jumps to the game's original entry point.

Patches applied after it read `tes3xhook.json` to find their hooks, rebase the payload's hook
addresses, and redirect calls or branches in the engine to them. Engine-site metadata and opcode
limits keep their original values.

## Using it

The pipeline builds and applies the payload whenever a selected patch needs one. Directly, it is
`--apply payload=FILE.pe`, applied before any patch that uses a hook.

For a payload using diagnostics, console, networking and multiplayer, pass
`--portable-bindings` to `tes3x payload` with those sources. Apply the resulting PE and adjacent
manifest to either supported GOTY engine with `tes3x patch`. The pipeline currently builds its
payload from source for each profile.

## Compatibility and limits

Building from source needs LLVM (`clang` and `lld-link`). An older fixed payload must land at its
linked address. The runtime binding table covers the diagnostics, console, networking and
multiplayer source set on the sampled NTSC and PAL GOTY engines; other source sets still use
image-specific builds. Content locators reject an engine whose expected sites do not match.
