# In-game agent

Patch key: `agent`

This patch lets a running game report its status and diagnostic log to the TES3X GUI, and take
commands from it. It keeps the target visible after the dashboard and FTP stop for the game, lets
the GUI distinguish a running frame loop from one that has stalled, and lets it run console lines,
copy files off the console and quit to the dashboard without power-cycling it.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The game opens an independent UDP channel through the shared `net` transport and initiates a
Noise XX handshake with the GUI. `NetAgent` pins the GUI's key fingerprint, so a different
listener cannot receive status or log text. Once connected, the game sends a heartbeat once per
second and forwards new diagnostic log lines from a fixed-size ring. A slow or unavailable GUI
can make the ring discard old lines, but cannot grow the game's memory use.

The heartbeat and log stream are best-effort telemetry. Missing packet sequence numbers expose
drops, and no heartbeat for three seconds makes the GUI show the target as stalled. The GUI
acknowledges each heartbeat; after six seconds without an answer the game pairs again with a new
session, so a restarted GUI picks up a game that is already running.

## Using it

Requests from the GUI carry an ID and are resent until answered; the game runs each between
frames and answers a repeated ID without running it again.

- **Console line**: runs like a line typed into the in-game console, so engine commands and
  TES3X commands such as `tes3xnet stat` work, and their output arrives in the log stream. This
  needs the [`console`](console.md) patch as well. `exit` shuts the console down.
- **Read file**: returns up to 1 KB of a file at an offset, from `C:`, `E:`, `F:`, `G:`, the
  `X:`/`Y:`/`Z:` cache partitions, or the title's `D:`, `T:` and `U:`. The GUI keeps eight reads
  in flight to copy a whole file, such as `E:\tes3xprof.bin` or a save.
- **Reboot**: returns to the dashboard through the firmware, as an exec script's `reboot` does.

A hard hang stops the frame loop, so commands then go unanswered; the stalled heartbeat shows
why. `tes3x agent` offers the same requests from the command line (`--console`, `--fetch`,
`--exit`, `--reboot`, `--until-end`); it listens on the GUI's port, so run it while the GUI is
closed.

## Configuration

Set `NetAgent` to the GUI host and its fingerprint, for example
`192.168.1.10#0123456789abcdef0123456789abcdef`. The port defaults to `26501`; put `:PORT`
before the fingerprint to change it. The pipeline makes this setting from the selected target
and the persistent GUI key unless the profile supplies an explicit value, and deploy writes it to
the console's `E:\TES3X\console.ini`, not the build (see
[console settings](../docs/deployment.md#console-settings)).

`NetAddress` is `dhcp` or a static console address as described for the shared [`net`](net.md)
transport. An agent build without `multiplayer` uses `dhcp` unless the profile sets it; a
multiplayer build keeps the profile's own network settings.

In xemu the pipeline points `NetAgent` at `10.0.2.2`. The GUI's Play turns on xemu's NAT for an
agent build, where that address is the PC's own loopback; from the command line pass `--net-nat`
to the xemu runner. A tunnel (`tes3x net serve --tunnel PORT` and `--net-tunnel PORT`) works
too: it forwards agent port `26501` to localhost, and `--forward AGENT_PORT` adds another.
