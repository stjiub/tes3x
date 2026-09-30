# Multiplayer

This patch adds a network driver for the Xbox NIC and a session with a TES3X server
(`tools/tes3x_net.py serve`). It is opt in twice: a build carries the driver only when its
profile enables `multiplayer`, and the driver starts only when `[Xbox] NetAddress` is set. Keys
are listed in [ini keys](../docs/ini-keys.md). Starting a server and connecting consoles and xemu is
covered in [multiplayer](../docs/multiplayer.md).

On the first frame after each launch the driver brings the NIC up with a static address, or one
leased by DHCP when `NetAddress` is `dhcp`, joins
the server and keeps the session alive with a heartbeat each second, also through loading
screens. While joined it sends the player's cell, position and heading every frame; the server
relays each client's state to the others. The NIC is stopped before the title relaunches, and
the relaunched title joins again by itself.

The server can be given by name; the driver asks the DNS server (by default the router) at each
launch and again whenever the server stops answering, so a server whose address changes is found
again. Only UDP is sent, and only to the server and the DNS server, so a console behind home NAT
needs no port forward, apart from DHCP's broadcasts. A lease is renewed from half its time on;
the session waits while the address is lost.

Every client must load the same plugins in the same order, since shared objects are named by
their place in the load order. The server takes the first client's load order (or `serve
--load-order HASH`) and refuses any console whose plugins differ; that console logs
`net.refused` and stops trying until the game is next launched.

Besides positions, clients exchange events, which the server delivers to every other client in
order and resends until each is acknowledged. `tes3xnet say TEXT` on one console is logged as
`net.text` on the others.

Other players appear as stand-ins from `TES3X Multiplayer.esp`, which the pipeline writes. Each
copies its player's movement, animation, drawn weapon or readied spell, and whatever the player
has equipped; the server keeps each player's equipment and sends it to consoles that join later.

The server also names one client per loaded cell as its authority: that console runs the AI of
the actors there and sends their positions ten times a second, and the other consoles stop those
actors' AI and place them where the authority says. Damage done to such an actor, and a
dialogue held with it, go to the authority. When an actor dies on its authority, the server
records the death and every console, including one that joins later, kills its copy. Only actors
placed by the data files take part; creatures spawned while playing, such as from leveled lists,
still run separately on each console.

Objects placed by the data files are shared the same way: when one console takes an item, or a
script disables an object, or a door or container is locked or unlocked, the server records the
new state and every console applies it, including one that joins later, once the object's cell
is loaded. The server can keep these, with the deaths, the clock and the weather, between
sessions (`serve --world DIR`). Containers' contents and items dropped or made while playing are
not shared yet.

A spell takes effect on the console that runs its target. When a spell cast on one console
reaches another player's stand-in, or an actor another console runs, that console applies the
spell to the real target instead. Spells the player made in their own game are not known to the
other consoles and are not applied there. An enchantment's damage still reaches its target as a
plain hit, without its other effects.

Other consoles see the cast and the shot too: a stand-in casts the same spell, or looses the same
arrow, bolt or thrown weapon, but what it fires never does anything there, since the real hit
arrives from the console that fired. Stand-ins aim where they face, so a bolt or an arrow may
fly a little differently than it did for its shooter.
