# Xbox network foundation

Patch key: `net`

This patch provides the Xbox network transport shared by TES3X features that communicate while
the game is running. It owns the NIC, ARP, IPv4 and UDP and lets independent consumers register a
local port, route and receive handler.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The transport starts the physical adapter once and dispatches incoming UDP datagrams to the
consumer registered for their destination port. Each consumer keeps its own route and protocol
state, so one session can stop or reconnect without changing another.

The transport also owns the receive interrupt, periodic network tick and shutdown sequence. It
stops DMA and disconnects the interrupt before a title relaunch or return to the dashboard, since
the Xbox kernel survives a quick reboot while the title's memory does not.

## Configuration

This is a dependency of network features rather than a useful patch on its own. The consuming
patch supplies its connection settings and commands.
