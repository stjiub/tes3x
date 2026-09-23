# mcp-102: mod removal fix

Port of Morrowind Code Patch fix 102.

The save writer omits an object's action flags (`ACTN`) only when they hold the default value 1.
When a mod that had disabled an object is removed, the save still carries `ACTN=0`, and the loader
passes it unchanged to the action-state setter, so the object stays inactive for ever: containers
that cannot be opened, doors that cannot be used.

## What the patch changes

The sole Xbox action-state setter at `0x0012A650`. Its existing-state branch at `0x0012A65A` now
joins the newly allocated path at `0x0012A668`, and one shared store writes `saved_flags | 1`:
the default active bit is restored and every other flag is kept. No payload code is needed; the
patcher reports 21 changed bytes.

## Evidence

[2026-09-21, xemu](2026-09-21-xemu.toml): the same 34-master save, with Graphic Herbalism
removed, holds 256 `ACTN=0` objects. A probe on the same reference read the flags back as 0 on the
control build and 1 on the patched build.
