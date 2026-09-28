# Ini keys

Keys TES3X patches read from the `[Xbox]` section of `Morrowind.ini`. A key does nothing unless
its patch is enabled. Edit them in the game folder's `Morrowind.ini` on the Xbox, or set them for
a build in the profile, which the GUI's INI tab does for you:

```toml
[ini]
"Xbox:AutosaveSlots" = 5
```

## `console`

| key | values | default |
|---|---|---|
| `ConsoleCombo` | two different input indices, `A,B` | `7,9` - Back + right thumb click |

An invalid combination falls back to the default. Input indices:

| index | button |
|---|---|
| 1, 2 | D-pad up, down |
| 4, 5 | D-pad right, left |
| 6 | Start |
| 7 | Back |
| 8 | Left thumb click |
| 9 | Right thumb click |
| 10, 11 | A, B |

Both buttons of the combination are consumed while held, so their usual actions do not fire.

## `diagnostics`

| key | values | default |
|---|---|---|
| `Diagnostics` | `0`, `1` or `2` | `0` - off; nonzero enables diagnostics at that level |
| `DiagnosticsLevel` | `0`, `1` or `2` | value of `Diagnostics`; `2` adds periodic snapshots and notes |
| `HangWatchdog` | `0` or `1` | `1` |
| `HangTimeoutSeconds` | `10` to `600` | `60` |

## `profile`

| key | values | default |
|---|---|---|
| `ProfileDumpFrames` | frames between automatic dumps | `0` - dump only on the `tes3xprof` console command |
| `ProfileDumpSeconds` | minimum seconds between periodic checkpoints; `0` disables them | `30` |

## `mcp-1`

| key | values | default |
|---|---|---|
| `DropReplacedRefs` | `0` or `1` | `1` - drop a saved reference whose plugin no longer resolves |

## `rotating-autosaves`

| key | values | default |
|---|---|---|
| `RotatingAutosaves` | `0` or `1` | `1`; `0` restores the retail single autosave |
| `AutosaveSlots` | `1` to `9` | `3`; out of range uses the default |

## `transition-autosaves`

| key | values | default |
|---|---|---|
| `TransitionAutosaves` | `0` or `1` | `1`; `0` disables the added saves |

## `video-arena`

| key | values | default |
|---|---|---|
| `VideoMemoryKB` | `15872` to `49152` | `22528` |

Used only when the Xbox has more than 64 MB; on 64 MB the arena keeps its retail size.

## `heap-region`

| key | values | default |
|---|---|---|
| `HeapRegionKB` | `17408` to `98304` | `98304` |

Only pages the heap actually uses consume memory, so a large value costs address space, not RAM.
