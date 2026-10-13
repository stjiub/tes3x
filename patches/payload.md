# Code payload injection

Patch key: `payload=FILE.pe`

Most TES3X fixes need new code, not just changed bytes. This patch adds that code to the retail
XBE as a new section and runs it before the game starts. Every patch that names a payload source
in the patch table depends on it.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

Each payload source in `hooks/` is compiled once into an object file, and TES3X ships those
objects in `payloads/objects/`. The code is freestanding: it cannot link the Xbox SDK libraries
the game already contains, so it calls the kernel through the game's own import thunks and reaches
engine functions by address. None of those addresses is compiled in. Each is an undefined symbol
in the object, so the same objects serve every supported engine.

`tes3x payload` builds a payload for one XBE. It finds every engine address the selected
sources need, mostly by content search, and links only those sources' objects with TES3X's own
linker. The link places the code at the section address the XBE will give it and fills in the
engine addresses, kernel thunk slots and original entry point for that image. A feature whose
patch is not selected is not linked, and code that calls it skips the call. Beside the PE,
`tes3xhook.json` lists the hooks later patches redirect to.

The patch then:

- appends the image as a new section (`.tes3xhk` by default);
- points the XBE's entry at the payload. The payload's entry initializes the selected features,
  logs the session to `E:\tes3xlog.txt`, and jumps to the game's original entry point.

Patches applied after it read `tes3xhook.json` to find their hooks and redirect calls or
branches in the engine to them.

## Using it

The pipeline builds the payload when a selected patch needs code. Directly:

```
tes3x payload morrowind.xbe --src tes3xhook.c --src tes3xlog.c --src tes3xini.c --src tes3xdiag.c
tes3x patch morrowind.xbe --apply payload=build/hooks/tes3xhook.pe --out patched.xbe
```

Apply `payload` to the same XBE it was built for, before any patch that uses a hook.

## Compatibility and limits

Linking needs no compiler. A source edited since its bundled object was built is compiled again,
which needs LLVM's `clang`, as do `--cflags` and `--rebuild`. A payload is linked for one XBE's
section address and is refused by any other. Content locators reject an engine whose expected
sites do not match, and an address that has no locator is accepted only on the NTSC GOTY engine.
