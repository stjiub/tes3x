# Multiplayer

See the [patch table](../../docs/patches.md) for policy and status.

This patch adds a network driver for the Xbox NIC and a session with a TES3X server
(`tools/tes3x_net.py serve`). It is opt in twice: a build carries the driver only when its
profile enables `multiplayer`, and the driver starts only when `[Xbox] NetAddress` is set. Keys
are listed in [ini keys](../../docs/ini-keys.md).

On the first frame after each launch the driver brings the NIC up with a static address, joins
the server and keeps the session alive with a heartbeat each second, also through loading
screens. While joined it sends the player's cell, position and heading every frame; the server
relays each client's state to the others. The NIC is stopped before the title relaunches, and
the relaunched title joins again by itself.

Only UDP is sent, and only to the server, so a console behind home NAT needs no port forward.
There is no DHCP yet.
