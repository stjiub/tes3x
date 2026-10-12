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
the supported engine. The payload is built for that exact image. Beside it, `tes3xhook.json` lists the
entry points of the payload's hooks.

The patch then:

- appends the image as a new section (`.tes3xhk` by default), applying its PE32 relocations
  when the section lands at a different address;
- points the XBE's entry at the payload. The payload's entry initializes the selected features,
  logs the session to `E:\tes3xlog.txt`, and jumps to the game's original entry point.

Patches applied after it read `tes3xhook.json` to find their hooks, rebase the payload's hook
addresses, and redirect calls or branches in the engine to them. Engine-site metadata and opcode
limits keep their original values.

## Using it

The pipeline builds and applies the payload whenever a selected patch needs one. Directly, it is
`--apply payload=FILE.pe`, applied before any patch that uses a hook.

## Compatibility and limits

Building the payload needs LLVM (`clang` and `lld-link`). Relocations allow the section to move
within the same engine image; the original entry, kernel thunk slots and engine addresses still
require a build for each engine variant. An older fixed payload must land at its linked address.
Relocations do not adapt engine bindings between regional releases.
