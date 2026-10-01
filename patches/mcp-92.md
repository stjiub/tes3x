# Summoned creature crash fix

Patch key: `mcp-92`

Unsummoning a creature by leaving the cell or dispelling its summon can destroy the actor while
magic which targets it remains active. Later cleanup can then follow the stale actor reference and
crash. Killing the creature normally retires that magic first; this patch makes unsummoning do the
same.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The Xbox unsummon path at `0x000C7BB0` already retires spells cast by the summoned actor, an extra
cleanup absent from the corresponding PC path. It then calls the same virtual actor cleanup that
MCP replaces. The patch keeps the Xbox cleanup and replaces that virtual call at `0x000C7CCA` with
the engine's existing `MobileActor::retireMagic` wrapper at `0x0015B3C0`. The wrapper removes magic
targeting the actor before the actor is destroyed.

The patch changes nine bytes and needs no injected code.
