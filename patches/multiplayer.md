# Multiplayer

This patch adds a network driver for the Xbox NIC and a session with a TES3X server
(`tools/tes3x_net.py serve`). It is opt in twice: a build carries the driver only when its
profile enables `multiplayer`, and the driver starts only when `[Xbox] NetAddress` is set. Keys
are listed in [ini keys](../docs/ini-keys.md).

On the first frame after each launch the driver brings the NIC up with a static address, joins
the server and keeps the session alive with a heartbeat each second, also through loading
screens. While joined it sends the player's cell, position and heading every frame; the server
relays each client's state to the others. The NIC is stopped before the title relaunches, and
the relaunched title joins again by itself.

The server can be given by name; the driver asks the DNS server (by default the router) at each
launch and again whenever the server stops answering, so a server whose address changes is found
again. Only UDP is sent, and only to the server and the DNS server, so a console behind home NAT
needs no port forward. There is no DHCP yet.

Every client must load the same plugins in the same order, since shared objects are named by
their place in the load order. The server takes the first client's load order (or `serve
--load-order HASH`) and refuses any console whose plugins differ; that console logs
`net.refused` and stops trying until the game is next launched.

Besides positions, clients exchange events, which the server delivers to every other client in
order and resends until each is acknowledged. For now the only event is text: `tes3xnet say
TEXT` on one console is logged as `net.text` on the others.
