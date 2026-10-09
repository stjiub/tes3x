# Ini keys

Keys TES3X patches read from the `[Xbox]` section of `Morrowind.ini`. A key does nothing unless
its patch is enabled. Edit them in the game folder's `Morrowind.ini` on the Xbox, or set them for
a build in the profile, which the GUI's INI tab does for you:

```toml
[ini]
"Xbox:AutosaveSlots" = 5
```

Each key is looked up in `E:\TES3X\console.ini` first, the [console's own
settings](deployment.md#console-settings), and in `Morrowind.ini` only when that file does not set
it. `NetAgent`, `OverlayBase`, `NetAddress`, `NetGateway` and `NetDns` describe the console, not the
build, so the pipeline writes them there at deploy instead of into `Morrowind.ini`. Installing the
console manager writes `Manager` there.

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

## `bow-view`

| key | values | default |
|---|---|---|
| `BowViewOffsetZ` | `-64` to `64` game units | `-12`; `0` retains the retail position |

The offset affects only the first-person bow and arms while an arrow or bolt is nocked.

## `net`

| key | values | default |
|---|---|---|
| `NetAddress` | the console's address, `A.B.C.D` or `A.B.C.D/BITS`, or `dhcp` | empty - the network stays off; `/24` when no prefix is given |
| `NetGateway` | the router, `A.B.C.D` | empty, or the lease's router with `dhcp` - needed only when the server or DNS is on another subnet |
| `NetDns` | the DNS server, `A.B.C.D` | the lease's with `dhcp`, else `NetGateway`; used only when `NetServer` is a name |

## `multiplayer`

| key | values | default |
|---|---|---|
| `NetServer` | the server, an address or a name, with `:PORT` if not `26500`, and `#FINGERPRINT` to pin its key | empty - answer ARP and UDP echo only |
| `NetPassword` | the server's password, up to 64 printable ASCII characters | empty - for a server without one; a server asks for it only on a console's first join |
| `Manager` | the console manager's XBE, `F:\Games\TES3XManager\default.xbe` (drives C, E, F, G) or a `\Device\...` path | empty - no Manager entry on the main menu; installing the manager sets it in `console.ini` |

The keys are read on the first frame of each launch, including the relaunch on New Game and Load.
A server name is looked up at each launch and again whenever the server stops answering.

## `agent`

| key | values | default |
|---|---|---|
| `NetAgent` | the GUI host's IPv4 address, with `:PORT` if not `26501`, then `#FINGERPRINT` | empty - no in-game status or log connection |

The fingerprint is the 32-character hexadecimal BLAKE2b fingerprint shown by the GUI. The game
refuses a listener whose key does not match it.

## `data-overlay`

| key | values | default |
|---|---|---|
| `OverlayBase` | a game folder, `F:\Games\Morrowind Game of the Year` (drives C, E, F, G) or a `\Device\...` path | empty - the overlay stays off |

Read on the first access to `D:\`, from `console.ini` and then the build's `Morrowind.ini`. With
`profile.install_layout = "overlay"`, the pipeline writes it from the local
`deploy.retail_root`; xemu runs substitute the base carried on their disc image.
