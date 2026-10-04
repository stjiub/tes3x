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
| `NetPassword` | the server's password, if it has one |

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
| `--password-file FILE` | ask new consoles for the password on this file's first line (below) |
| `--max-players N` | refuse consoles beyond this many (default 16) |
| `--duration SECONDS` | stop after a while |
| `--stop-wait SECONDS` | how long stopping waits for consoles' saves (default 60) |
| `--respawn temple\|shrine\|nearest` | where a player who dies comes back (default `nearest`) |
| `--respawn-delay SECONDS` | how long a dead player lies before coming back (default 5) |
| `--death-gold PERCENT` | the share of carried gold a death costs (default 10) |

Stop the server with Ctrl+C, or the admin command `stop`. Before it exits it asks every
joined console to save its character and waits, up to `--stop-wait`, for each save to arrive;
it names any console that did not send one. A second Ctrl+C stops at once. Without `--world` the session lives only as long as the server
does: stop it and the deaths, objects and equipment it recorded are gone. With `--world DIR` it
keeps the game clock, the deaths, the doors, locks and items taken, the items dropped or placed,
containers' contents, actors' AI settings and disposition, and the weather in one file per
load order in `DIR`, loads it when the
first console joins and writes it every 10 seconds
while it changes, so a restarted server carries on where it stopped. A console's own save is
still loaded first; joining then applies what the world holds.

With `--world` the server also keeps each console's character under `characters` in `DIR`.
Every save made while joined goes to one save slot per server and character and is uploaded; a
console that joins running another game is sent the kept save and loads it. Between saves the
console sends its inventory, level, attributes, skills, journal and current health, magicka and
fatigue as they change. The server
keeps the latest of them in `stream.json` beside the save and applies them over it when the
console next loads that save, so a crash loses at most about a second of those. It also keeps
where the player last was and puts them back there, so a crash or a power cut does not return a
player to where the save was made. Exit while joined saves and uploads first, and quits once
the server has the save; if the server does not confirm it within 30 seconds, the player chooses
to leave anyway or stay.

A player who dies while joined is not offered the last save. The others see a notice, and after
`--respawn-delay` the player gets up at the closest temple or Imperial shrine (the markers
Almsivi and Divine Intervention use), with full health, magicka and fatigue, the same bounty, and
`--death-gold` percent less gold. A dead player cannot leave or save until then; a console that
loses power while dead comes back at the marker on its next join.

### Keys and passwords

Traffic between a console and the server is encrypted. The server has a key of its own, kept in
`server.key` in the `--world` folder (or `--key FILE`), and prints its fingerprint when it
starts. Each console makes a key for each server on its first join and keeps both in
`U:\TES3X\servers.ini`. A console trusts the first key it meets and refuses a different one
later; to accept a server's new key, delete that server's section from `servers.ini`. To pin the
key before the first join, add the fingerprint to the address:
`NetServer=my.server.net#0123456789abcdef0123456789abcdef`.

With `--password-file FILE`, a console whose key the server has not seen must give the password
(1 to 64 printable ASCII characters), as `NetPassword` or typed when Join asks for it; a typed
password is kept in that server's section of `servers.ini` and used in place of `NetPassword`.
The server remembers each key that gave it
in `admitted.txt` in the `--world` folder and does not ask again; delete a line there to ask that
console again. A wrong password is refused, and one address gets five tries, then one a minute.
On a server that faces the internet, give players the fingerprint as well: the password travels
encrypted to the server's key, so a console that has pinned the key cannot give it to an
impostor on its first join.

### Kicks and bans

The server takes admin commands typed at its own window, or from the same PC with
`python tools/tes3x_net.py admin COMMAND` (the server listens for them on `127.0.0.1` only, at
`--admin-port`, by default `26502`; `--admin-port 0` turns that off):

| Command | Does |
|---|---|
| `list` | the clients: number, key fingerprint, MAC and address |
| `kick N` | drop client N; its console stops trying until the game is launched again |
| `ban N` | ban client N's key and MAC, and drop it |
| `ban key FINGERPRINT`, `ban mac MAC`, `ban address A.B.C.D` | ban one of them; `unban` the same way lifts it |
| `bans` | the bans |
| `save [N]` | ask every console, or client N, to save its character now |
| `stop` | ask every console for its character, wait for the saves, then stop |

Bans are kept in `bans.txt` in the `--world` folder. A key is a player's identity, but a player
can make a new one by deleting `servers.ini`; on a server with a password, a new key needs the
password again. The MAC is what the console reports, so a modified build can change it. An
address ban drops everything from that address before the handshake, and also stops everyone
else who shares it, such as a household behind one router.

## Connect a console

1. Start the server.
2. Launch the build from the dashboard and load a save. The console joins by itself on the first
   frame of the loaded game; the server prints `client 1 joined` with the console's MAC and address.
3. Other players do the same. Each shows up for the others as a stand-in once they are in the
   same interior or within one exterior cell.

New Game and Load relaunch the title; the console leaves and joins again by itself, and the server
prints `rejoined`. If the server stops answering for five seconds, the console keeps trying.

The main menu has **Join** between Options and Exit. It opens a list of the servers this console
knows: the one `NetServer` names and those in `U:\TES3X\servers.ini`, newest first. Choose one
to connect without loading a game; the server lists that console's characters with "New
character", and the chosen character is sent and loaded, joining the same server. **New Server**
opens the keyboard to type an address or name, with `:PORT` when it is not 26500 (the `!?#` key
has `.` and `:`), and joins it. Join needs neither `NetServer` nor `NetAddress` (it asks DHCP for
an address). A join that fails says why on the server's row: no answer, no network address, name
not found, different mods, server full, kicked, banned or server key changed; a server that wants a
password raises the keyboard for it. New and Load from the main menu still play alone.

While joined, the pause menu has **Save to Server** in place of Save, which saves the character
and sends it to the server, and **Leave** in place of Exit, which saves to the server and then
quits; Load is gone, since a local save is not the server's character. A multiplayer build redraws the
menu buttons so they match the ones it adds; `tools/tes3x_menuart.py` renders them from the
bundled Fondamento font (SIL Open Font License, `assets/fonts`).

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
[xemu](xemu.md) for the runner's other options.

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
| `tes3xnet send NAME` | send `U:\TES3X\NAME` to the server; a save that names a player is kept as that console's character under `characters` in its `--world` folder, with three earlier versions, and any other file under `uploads` |
| `tes3xnet leave` | what Exit's Yes does while joined: save, upload, and quit once the server has the save |
| `tes3xnet down` | leave the session and stop the network card |
| `tes3xnet up ADDRESS[/BITS] [SERVER[:PORT] [GATEWAY]]` | start it again by hand |

The log is `E:\tes3xlog.txt`. A console whose plugins differ from the session's logs
`net.refused` and stops trying until the game is launched again; the server prints `refused`
with both load order hashes. `net.refused_reason` says why: 1 the load order, 2 the server is
full, 3 a wrong password, 4 kicked, 5 banned.

## Limits

- Menus no longer pause the world while joined, and resting or waiting is refused.
- A console needs a save of its own to join. Over a kept character the server restores the
  inventory, level, attributes, skills, journal, current health, magicka and fatigue (health
  no lower than 1) and the player's position; spells, active effects, topics, factions and bounty come from the last save.
  Restoring only moves a
  quest forward, never back.
- Items taken, objects a script disables, locks, and items
  dropped or placed (by the console or a script) are shared, with their stack size, condition and
  charge, and so are containers' contents: the first player to open a container decides what it
  holds. Corpses' inventories, creatures spawned while playing and items made in play (potions,
  enchanted items) are not shared yet.
- Two players taking from the same container at the same moment can both get the same items.
- If a script on every console places the same object at different moments, each console's copy
  is shared, so the object appears more than once.
- Stand-ins look like a Dark Elf named "Player N", whatever the player's character.
