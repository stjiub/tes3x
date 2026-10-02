# Patch notes

Each implemented patch in [`patches.toml`](../patches.toml) has a page here named after it:
`mcp-98` is explained in `mcp-98.md`, tested by [`tests/game/mcp-98.toml`](../tests/game) and
listed in the [patch table](../docs/patches.md). The test suite fails while a patch has no page or
a page has no patch.

A page identifies its patch key, explains what the patch changes, why someone would use it and how
it works. It does not repeat the registry's category, channel, selection, origin, bit or summary,
or say how ready the patch is: no validation results, test-run data, plans or open investigations.
A measured effect that shows what the patch does, with the setup it was measured in, may go under
`How it works`. A permanent limitation belongs on the page; unfinished work does not.

## Template

```markdown
# <the patch's title from patches.toml>

Patch key: `<name from patches.toml>[=<takes>]`

One or two paragraphs: what the patch changes, and why someone would use it.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The engine behaviour, the hook or byte change, and the constraints that shape it. Give addresses
when they help explain the implementation.

## Using it

Optional. How to invoke or read a tool-like patch: console commands, output files.

## Configuration

Optional. Profile keys and `[Xbox]` settings in `Morrowind.ini`, with their defaults.

## Compatibility and limits

Optional. Intentional scope, interactions with other patches or mods, and durable limits.
```

`How it works` is required; the other sections appear only when there is something to say, in this
order.
