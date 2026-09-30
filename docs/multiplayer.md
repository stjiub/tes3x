# Multiplayer

The `multiplayer` patch is experimental (channel `dev`). Each player runs the full game on a
console or in xemu, and a small TES3X server on a PC passes state between them. The server does not
run Morrowind and needs no game files. What the session shares is described in the
[patch notes](../patches/multiplayer.md).

## What you need

- A PC with Python 3.12 to run the server, reachable over UDP port 26500.
- One build of the game for every player, made from the same profile. Every client must load
  the same plugins in the same order, or the server refuses it.
- A saved game for each player. A console joins only once a game is loaded.

## Build

Enable the patch in the profile. It needs `diagnostics`, which the pipeline adds, and `console`
gives you the `tes3xnet` commands below:

```toml
[profile]
name = "net"
title = "Morrowind Net"
remote_root = "F:/Games/MorrowindNet"

[patches]
preset = "minimal"
enable = ["multiplayer", "console"]

[ini]
"Xbox:NetAddress" = "dhcp"
"Xbox:NetServer" = "192.168.1.10"
```

The pipeline also writes `TES3X Multiplayer.esp`, which holds the stand-ins that show the other
players. Deploy with `--deploy` as usual, to its own `remote_root` apart from your normal game:
saves made with this build depend on that plugin.

## Network settings

The keys are read from `Morrowind.ini` at each launch (see [ini keys](ini-keys.md)):

| Key | Set to |
|---|---|
| `NetAddress` | `dhcp` to take an address from the router, or a fixed one outside the router's DHCP range, with its prefix: `192.168.1.50/24` |
| `NetServer` | the server PC's address, or a name, with `:PORT` if not 26500 |
| `NetGateway` | your router; with `dhcp` the router's own answer is used, and this key overrides it |
| `NetDns` | the DNS server, when `NetServer` is a name; with `dhcp` the router's answer is used |

Leaving `NetAddress` empty turns the network off, and the build plays as a normal game.

With `dhcp` every console can share one build. A fixed address must differ per console, so a
second console then needs a second profile (or a second `[ini]` entry at deploy) that differs
only in that key. Give the server PC a fixed address, or a DHCP reservation, so `NetServer` stays
right.

## Start the server

On the PC:

```
python tools/tes3x_net.py serve
```

It listens on UDP 26500 on every interface and prints a line when a client joins, leaves or
times out, and a status block every 30 seconds (`--report SECONDS`). Allow Python through the
firewall for inbound UDP on port 26500.

The first client to join sets the session's load order and its game clock. Useful options:

| Option | Does |
|---|---|
| `--port N` | listen on another port (set `NetServer` to `ADDRESS:N` to match) |
| `--hour H`, `--timescale T` | start the clock at a game hour or speed instead of the first client's |
| `--load-order HASH` | fix the load order instead of taking the first client's |
| `--host NAME=ADDRESS` | answer DNS for a name, so `NetServer` can be that name |
| `--world DIR` | keep the world between sessions (below) |
| `--duration SECONDS` | stop after a while |

Stop the server with Ctrl+C. Without `--world` the session lives only as long as the server
does: stop it and the deaths, objects and equipment it recorded are gone. With `--world DIR` it
keeps the game clock, the deaths, the doors, locks and items taken, and the weather in one file
per load order in `DIR`, loads it when the first console joins and writes it every 10 seconds
while it changes, so a restarted server carries on where it stopped. A console's own save is
still loaded first; joining then applies what the world holds.

## Connect a console

1. Start the server.
2. Launch the build from the dashboard and load a save. The console joins by itself on the first
   frame of the loaded game; the server prints `client 1 joined` with the console's MAC and address.
3. Other players do the same. Each shows up for the others as a stand-in once they are in the
   same interior or within one exterior cell.

New Game and Load relaunch the title; the console leaves and joins again by itself, and the server
prints `rejoined`. If the server stops answering for five seconds, the console keeps trying.

## Connect xemu

xemu reaches the server through a tunnel instead of a network card. On the PC that runs xemu:

```
python tools/tes3x_net.py serve --tunnel 9369
```

serves the LAN and one xemu at once, so a console and xemu can play together. Then run xemu with
the tunnel on the same port, giving the guest its own addresses:

```
python tools/tes3x_xemu.py player2 profiles/net.toml --direct-engine --skip-intro --net-tunnel 9369 --save my-save.ess --exec load.txt -- --ini-set Xbox:NetAddress=10.0.2.15 --ini-set Xbox:NetServer=10.0.2.2
```

where `load.txt` loads the save:

```
@start load U:\TES3X\my-save.ess
```

`10.0.2.2` is the server as seen through the tunnel, whatever the PC's real address. Through the
tunnel the server also answers DHCP with `10.0.2.15`, so `NetAddress=dhcp` works there too. Start
the server before xemu; a second server on the same port leaves xemu talking to the wrong one. See
[testing](testing.md#running-in-xemu) for the runner's other options.

Each tunnel serves one xemu, and a tunnel takes two ports (`PORT` and `PORT+1`). For two players
on one PC, give the server a tunnel per xemu and start each xemu on its own:

```
python tools/tes3x_net.py serve --tunnel 9369 --tunnel 9371
python tools/tes3x_xemu.py player1 profiles/net.toml ... --net-tunnel 9369 ...
python tools/tes3x_xemu.py player2 profiles/net.toml ... --net-tunnel 9371 ...
```

Each tunnelled xemu gets a MAC made from its port (`02:00:00:00:24:99` for 9369), since the server
tells clients apart by MAC. Both guests can use the same addresses.

## Playing over the internet

The console only sends UDP to the server and to DNS, so a console behind a home router needs no
port forward. The server does: forward UDP 26500 to the server PC, or run the server on a host
with a public address. Set `NetServer` to that address or a name, and `NetGateway` to the
console's router. Expect round trips of tens of milliseconds instead of a LAN's two.

## Checking a session

With `console` in the build, open the console (Back + right thumb click) and type:

| Command | Does |
|---|---|
| `tes3xnet stat` | write the network counters to the log |
| `tes3xnet say TEXT` | send a line of text to the other players (logged there as `net.text`) |
| `tes3xnet down` | leave the session and stop the network card |
| `tes3xnet up ADDRESS[/BITS] [SERVER[:PORT] [GATEWAY]]` | start it again by hand |

The log is `E:\tes3xlog.txt`. A console whose plugins differ from the session's logs
`net.refused` and stops trying until the game is launched again; the server prints `refused`
with both load order hashes.

## Limits

- Menus no longer pause the world while joined, and resting or waiting is refused.
- Each console keeps its own save. Items taken, objects a script disables and locks are shared;
  containers' contents, items dropped and creatures spawned while playing are not yet.
- Stand-ins look like a Dark Elf named "Player N", whatever the player's character.
