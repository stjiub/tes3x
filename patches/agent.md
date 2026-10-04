# In-game agent

Patch key: `agent`

This patch lets a running game report its status and diagnostic log to the TES3X GUI. It keeps
the target visible after the dashboard and FTP stop for the game, and lets the GUI distinguish a
running frame loop from one that has stalled.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The game opens an independent UDP channel through the shared `net` transport and initiates a
Noise XX handshake with the GUI. `NetAgent` pins the GUI's key fingerprint, so a different
listener cannot receive status or log text. Once connected, the game sends a heartbeat once per
second and forwards new diagnostic log lines from a fixed-size ring. A slow or unavailable GUI
can make the ring discard old lines, but cannot grow the game's memory use.

The heartbeat and log stream are best-effort telemetry. Missing packet sequence numbers expose
drops, and no heartbeat for three seconds makes the GUI show the target as stalled.

## Configuration

Set `NetAgent` to the GUI host and its fingerprint, for example
`192.168.1.10#0123456789abcdef0123456789abcdef`. The port defaults to `26501`; put `:PORT`
before the fingerprint to change it. The pipeline writes this setting from the selected target
and the persistent GUI key unless the profile supplies an explicit value.

`NetAddress` must also be `dhcp` or a static console address as described for the shared
[`net`](net.md) transport. Leaving it empty keeps the NIC off.

For xemu's raw UDP backend, run `tes3x_net.py serve --tunnel PORT` beside the listener. The tunnel
forwards agent port `26501` to localhost; add `--forward AGENT_PORT` when `NetAgent` names another
port.
