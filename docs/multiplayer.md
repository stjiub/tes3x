# Multiplayer

The `multiplayer` patch is experimental (channel `dev`). Each player runs the full game on a
console or in xemu, and a small TES3X server on a PC passes state between them. The server does not
run Morrowind and needs no game files. What the session shares is described in the
[patch notes](../patches/multiplayer.md).

## What you need

- A PC with Python 3.12 to run the server, reachable over UDP port 26500.
- One build of the game for every player, made from the same profile. Every client must load
  the same plugins in the same order, or the server refuses it.
- A server world folder (`--world DIR`) to retain characters between sessions. Join creates or
  loads a character from server state through a New Game.

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

The keys are read at each launch, from the console's `E:\TES3X\console.ini` and then
`Morrowind.ini` (see [ini keys](ini-keys.md)); deploy puts `NetAddress`, `NetGateway` and `NetDns`
in `console.ini`:

| Key | Set to |
|---|---|
| `NetAddress` | `dhcp` to take an address from the router, or a fixed one outside the router's DHCP range, with its prefix: `192.168.1.50/24` |
| `NetServer` | the server PC's address, or a name, with `:PORT` if not 26500 |
| `NetGateway` | your router; with `dhcp` the router's own answer is used, and this key overrides it |
| `NetDns` | the DNS server, when `NetServer` is a name; with `dhcp` the router's answer is used |
| `NetPassword` | the server's password, if it has one |

Leaving `NetAddress` empty turns the network off, and the build plays as a normal game.

Every console can share one build. A fixed address belongs to one console: deploy writes it to
that console's `console.ini`, so a second console takes the same build deployed with its own
address. Give the server PC a fixed address, or a DHCP reservation, so `NetServer` stays right.

## Start the server

On the PC:

```
python tools/tes3x_net.py serve
```

It listens on UDP 26500 on every interface and prints a line when a client joins, leaves or
times out. The admin command `status` prints the clock, weather and each client's counters;
`--report SECONDS` prints that block on a timer. Allow Python through the
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
| `--stop-wait SECONDS` | how long stopping waits for consoles' state flushes (default 60) |
| `--respawn temple\|shrine\|nearest` | where a player who dies comes back (default `nearest`) |
| `--idle-timeout SECONDS` | drop a client silent this long (default 20; consoles stop waiting at 15) |
| `--respawn-delay SECONDS` | how long a dead player lies before coming back (default 5) |
| `--death-gold PERCENT` | the share of carried gold a death costs (default 10) |
| `--build DIR` | hand this build to the console manager (below) |

Stop the server with Ctrl+C, or the admin command `stop`. It asks each joined console to flush
its supported character state, writes that state and the world, and waits up to `--stop-wait` for
the requested snapshots. A second Ctrl+C stops at once. Without `--world` the session lives only as long as the server
does: stop it and the deaths, objects and equipment it recorded are gone. With `--world DIR` it
keeps the game clock, the deaths, the doors, locks and items taken, the items dropped or placed,
containers' contents, actors' AI settings and disposition, and the weather in one file per
load order in `DIR`, loads it when the
first console joins and writes it every 10 seconds
while it changes, so a restarted server carries on where it stopped. A joined client starts from
the data files and applies the server's world; it does not import changes from a local save.

With `--world` the server also keeps each character under `characters` in `DIR`. A joining
console starts a New Game and receives the character's identity, inventory, worn items and last
place in a small server-generated state file; the rest follows after it joins. The console sends
inventory, equipment, level, attributes, skills and their current modifiers, active effects, journal, current
health, magicka and fatigue as they change, and who the character is: name, race, sex, head, hair,
birthsign and class, including
a class made in character creation. The server keeps the latest supported state in `stream.json`.
It also keeps where the player last was, so a crash or power cut loses at most about one polling
interval of supported state. Save to Server flushes every supported character field and the
current place through the reliable queue. The server acknowledges the snapshot after writing
the character and world. Leave waits for that acknowledgement before quitting; after 30 seconds
without confirmation, the player chooses to leave anyway or stay.

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
| `status` | the clock, weather, each client's counters, and what the world holds |
| `help` | the list of commands |
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

### Remote admin

To manage a server from another machine, such as one in Docker or on a rented host, start it
with `--remote-admin 26503` and an admin password of at least eight characters on the first
line of `admin-password.txt` in its `--world` folder (or `--admin-password-file FILE`). Then,
from anywhere that reaches that UDP port:

```
python tools/tes3x_net.py admin --server my.server.net --password-file admin-password.txt list
```

takes every command above. The GUI's Server workspace does the same under **A remote server**.
Each command asks the server for a single-use challenge first; the command and its reply are
encrypted and authenticated with a key made from the password and that challenge, so a
listener on the network can neither read nor replay them, nor send commands of its own. The
password is stretched with scrypt. Like a console's password, five wrong tries from one address
allow one more a minute, and while that holds even the right password is refused. Keep the
admin password apart from the console password: anyone with it can kick, ban and stop.

### Protocol compatibility

Active effects retain their native source, magnitude and elapsed time, so their timers resume
when the character joins again. Bound effects also retain the equipment they displaced, using
item identities and condition/charge rather than engine pointers.

The game and manager have separate protocol versions. Gameplay state changes can require a new
game protocol without changing the manager's build discovery and download protocol. These are
compatibility numbers, separate from TES3X release versions; they change only when the relevant
wire format becomes incompatible.

The server checks compatibility after the authenticated handshake. An incompatible client gets
an explicit refusal instead of a timeout. The manager shows **Manager update needed** and offers
**Check for update**; if the server is older, it says the server needs an update. The game offers
to open the manager to update its build when the server requires a newer game client.

Restart the server process after updating server code. Rebuilding its staged game folder does
not replace the running server or a manager already open on a console. Relaunch the manager after
installing or rebuilding it. Old clients without these messages may still show a generic refusal
or timeout; updating them once installs the clearer handling.

### Handing out the build

Players need the server's build: the same plugins in the same order and a matching XBE. With
`--build DIR`, naming the pipeline's staged game folder (`deploy`, holding `tes3xbuild.json`), the
server hands it to the [console manager](deployment.md#from-a-server), which installs it and
joins:

```
python tools/tes3x_net.py serve --world world --build build/pipeline/net/deploy
```

In the GUI, set **Build profile** on the Server page instead.

The manager asks through the same encrypted session a console joins by, with the console's key
for this server and the password if there is one, so a console the server would refuse gets no
build. The server answers with its manifest's SHA-256 and a ticket, and serves the manifest and
files over plain HTTP on TCP port 26500 (`--http-port` for another; allow it through the
firewall too). The manager checks every file against the manifest and the manifest against the
hash the session gave, so the HTTP side needs no encryption.

What it serves is the admin's choice, by each file's origin in the manifest. By default only
`build` files (plugins, archives, textures, `Morrowind.ini`) and the XBE deltas: the manager
rebuilds the XBEs from the player's own retail copy and takes unchanged retail files from it.
`--serve-origin retail` and `--serve-origin xbe` add retail files and whole XBEs, for players
without a clean retail copy; only do so where you may hand them out. The deltas are read from
`deltas` beside `DIR` (`--deltas` for another folder). The server rereads the manifest when it
changes, so a new build can be staged while it runs; a manager that is mid-update when it changes
stops without touching the installed build, and its next update gets the new one.

A server with `--build` also refuses a console whose build is not that one, by the `build` id in
each manifest. Joining from the main menu then asks "Build out of date. Update it in the TES3X
Manager?": Open Manager starts the manager on that server's page, where X updates the build, and
Back stays in the game. Without the manager installed the server's row just says "build out of
date". A console with no manifest is checked by its load order only.

## Run the server in Docker

`server/` holds a Docker Compose setup for a machine that should only run the server, with no
Python install and no GUI. It runs the same `tes3x_net.py serve` with `--world` on a Docker volume,
so the server key, characters, saves, admitted keys and bans survive restarts and rebuilds. From
the repository:

```
cd server
docker compose up -d
docker compose logs -f
```

The server publishes UDP 26500; point each console's `NetServer` at the Docker host's address.
Add server options under `command:` in `server/compose.yaml`, one per line, and run
`docker compose up -d` again. Admin commands run inside the container:

```
docker compose exec server python tools/tes3x_net.py admin list
```

To require a password, copy the file into the volume, uncomment `--password-file` in
`compose.yaml` and start again:

```
docker compose cp password.txt server:/world/password.txt
docker compose up -d
```

To manage it from the GUI or another PC, put an admin password in the volume, uncomment the
`--remote-admin` line and its port in `compose.yaml`, and start again:

```
docker compose cp admin-password.txt server:/world/admin-password.txt
docker compose up -d
```

`docker compose stop` stops the server as Ctrl+C does: it asks every joined console to save and
waits up to 60 seconds. `docker compose cp server:/world ./world-backup` copies the world out.

[xemu](#connect-xemu) on its NAT joins the container like a console does, at the Docker host's
address (or `10.0.2.2` when xemu runs on that host). A tunnel cannot reach the container: it
talks to xemu over the host's own `127.0.0.1`. For tunnels, run the server directly on the PC
that runs xemu, or on Linux add `network_mode: host` to the service, which also replaces its
`ports:`. A `--host` DNS name needs `53:53/udp` published as well.

## Connect a console

1. Start the server.
2. Launch the build from the dashboard, choose Join and select the server. Choose a retained
   character or create a new one; the console starts a New Game from server state.
3. Other players do the same. Each shows up for the others as a stand-in once they are in the
   same interior or within one exterior cell.

New Game and Load relaunch the title; the console leaves and joins again by itself, and the server
prints `rejoined`. If the server stops answering for 15 seconds, the console says the connection is lost and keeps
reconnecting; after 60 seconds it offers the main menu.

The main menu has **Join** between Load and Options, and below it **Manager** once the
[console manager](deployment.md#installing-the-console-manager) is installed, which starts the
manager. Join opens a list of the servers this console
knows: the one `NetServer` names and those in `U:\TES3X\servers.ini`, newest first. Choose one
to connect without loading a game; the server lists that console's characters with "New
character", and the chosen character is sent and loaded, joining the same server. **New Server**
opens the keyboard to type an address or name, with `:PORT` when it is not 26500 (the `!?#` key
has `.` and `:`), and joins it. Join needs neither `NetServer` nor `NetAddress` (it asks DHCP for
an address). A join that fails says why on the server's row: no answer, no network address, name
not found, different mods, server full, kicked, banned or server key changed; a server that wants a
password raises the keyboard for it. New and Load from the main menu still play alone.

While joined, the pause menu has **Save to Server** in place of Save, which flushes the character
state and waits for confirmation, and **Leave** in place of Exit, which flushes to the server and
quits after confirmation; Load is gone, since a local save is not the server's character. A multiplayer build redraws the
menu buttons so they match the ones it adds; `tools/tes3x_menuart.py` renders them from the
bundled Fondamento font (SIL Open Font License, `assets/fonts`).

## Connect xemu

xemu can join any server, on this PC, the LAN or the internet, through its own NAT. Run the
xemu runner with `--net-nat` and give the build `NetAddress=dhcp` and the server's address:

```
python tools/tes3x_xemu.py player2 profiles/net.toml --direct-engine --skip-intro --net-nat -- --ini-set Xbox:NetAddress=dhcp --ini-set Xbox:NetServer=my.server.net
```

xemu's NAT answers DHCP with `10.0.2.15`, resolves names through the PC and sends the session's
UDP out from the PC, so the server sees the PC's address. `10.0.2.2` there is the PC itself:
`NetServer=10.0.2.2` reaches a server running on the same PC. Each run's MAC is made from its
run name, so two xemus on one PC are two clients. The GUI's Play uses the NAT for any build with
`multiplayer` or `agent`.

A tunnel instead hands the guest's raw frames to a server on this PC, which is how the
automated tests run. On the PC that runs xemu:

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
| `tes3xnet send NAME` | send `U:\TES3X\NAME` to the server; files go under `uploads`; only explicit `--adopt` or `--rebuild` diagnostic sessions use uploaded saves as fixtures |
| `tes3xnet save` | flush supported character state and wait for the server to confirm storage |
| `tes3xnet leave` | flush supported character state and quit after the server confirms storage |
| `tes3xnet down` | leave the session and stop the network card |
| `tes3xnet up ADDRESS[/BITS] [SERVER[:PORT] [GATEWAY]]` | start it again by hand |

The log is `E:\tes3xlog.txt`. A console whose plugins differ from the session's logs
`net.refused` and stops trying until the game is launched again; the server prints `refused`
with both load order hashes. `net.refused_reason` says why: 1 the load order, 2 the server is
full, 3 a wrong password, 4 kicked, 5 banned, 6 the build is not the one the server hands out.

To see how much of a character the server could restore without its save, start the server with
`--rebuild` and join from a different save. The server applies the character's kept state over
that save, asks the console to save 10 seconds later, and compares the save that arrives with
the kept one. The report goes beside the upload as `uploads/KEY/NAME.diff.txt`, one row per kind
of state; neither save is kept as the character. `tes3x_ess.py --diff KEPT OTHER` makes the same
report from any two saves.

The report separates attributes into base/current pairs and skills into base/current pairs,
alongside their saved progress. Other mobile fields remain byte comparisons.

Character loading always uses server state; `--load-state` remains a compatibility option. The
server sends identity, inventory, worn items and last place as a small `char-*.t3c` file. The
console starts a New Game that becomes that character on the loading screen, in that place; the
rest of the kept state follows once it joins. State the server does not keep yet, including
topics, factions and player globals, starts at its New Game value.

## Limits

- Menus no longer pause the world while joined, and resting or waiting is refused.
- A multiplayer character loads only from server state. The server restores inventory, equipment,
  level, base and current attributes and skills, journal, current health, magicka and fatigue
  (health no lower than 1), position, identity and active effects. Active effects retain their
  source, rolled magnitude, resistance and elapsed game time; remaining duration resumes on join,
  with time stopped while offline. At most 64 active effect entries are retained. Consumed
  potions include their adjusted source definition. Custom spell and enchantment definitions
  are not recreated; a source must exist in the build, and a missing caster cannot be resolved.
  Ghost caster ids belong to the current session. Topics, factions and player globals are not
  retained yet and start at New Game values. Restoring a journal only moves a quest forward,
  never back.
- Items taken, objects a script disables, locks, and items
  dropped or placed (by the console or a script) are shared, with their stack size, condition and
  charge, and so are containers' contents: the first player to open a container decides what it
  holds. Corpses' inventories, creatures spawned while playing and items made in play (potions,
  enchanted items) are not shared yet.
- Two players taking from the same container at the same moment can both get the same items.
- If a script on every console places the same object at different moments, each console's copy
  is shared, so the object appears more than once.
- Stand-ins receive the player's name, race, sex, head, hair and equipment. Some combinations,
  including beast races and closed helmets, still need validation.
