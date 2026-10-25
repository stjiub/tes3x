# Hosting a multiplayer server

Run and administer the TES3X server, retain the world and characters, and distribute a
matching game build. The server does not run Morrowind and needs no game files unless you
choose to serve a build. Players should use [Playing multiplayer](multiplayer-client.md);
the [overview](multiplayer.md) introduces the session.

## What you need

- A host with Python 3.12 or newer, or the [Docker setup](#run-the-server-in-docker).
- Inbound UDP port 26500 reachable by players, or another port selected with `--port`.
- A world folder (`--world DIR`) to retain characters and world state between sessions.
- A matching game build for players. See [building a client](multiplayer-client.md#building-a-client)
  and [handing out the build](#handing-out-the-build).

## Start the server

On the PC:

```
tes3x net serve --world world
```

It listens on UDP 26500 on every interface and prints a line when a client joins, leaves or
times out. The admin command `status` prints the clock, weather and each client's counters;
`--report SECONDS` prints that block on a timer. Allow the server through the firewall for
inbound UDP on port 26500 for the Python process running the server. Without `--world`,
the session is temporary and its state is lost when the server stops.

In the GUI, the **Server > Builds** tab can start its library automatically with a local multiplayer
server.

The first client to join sets the session's load order and its game clock. Useful options:

| Option | Does |
|---|---|
| `--port N` | listen on another port (set `NetServer` to `ADDRESS:N` to match) |
| `--name TEXT` | optional display name in the Dash |
| `--description TEXT` | optional server description in the Dash |
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
| `--combat-leave SECONDS` | how long a player in combat waits after pressing Leave; taking damage cancels it and leaving combat ends it early (default 10, 0 leaves at once) |
| `--death-gold PERCENT` | the share of carried gold a death costs (default 10) |
| `--level-up rest\|auto` | `rest` (default): a level is gained at the end of a rest, as in the retail game; `auto`: the level-up screen opens as soon as the level is earned |
| `--build DIR` | hand this build to the Dash (below) |
| `--content-entry ID` | instead of `--build`, require a content library's entry ([below](#builds-from-a-content-library)) |
| `--retail-base DIR` | clean retail source for an overlay build; defaults to `paths.vanilla_root` |
| `--welcome TEXT` | show a line of up to 80 characters when a player is in the world |
| `--starts FILE` | where new characters may begin (`[[start]]` tables; default: the bundled list) |
| `--start-gold N` | the gold a new character starts with (default 50) |
| `--save-every SECONDS` | flush every joined character this often |
| `--bind ADDRESS` | listen on one address instead of every interface |

Stop the server with Ctrl+C, or the admin command `stop`. It asks each joined console to flush
its state before exiting. A normal shutdown, including `--duration`, returns exit code 0 even
when no clients joined. Startup errors and unhandled failures return a nonzero code.

## World and character storage

During shutdown, the server writes the supported character state and the world, and waits up
to `--stop-wait` for the requested snapshots. A second Ctrl+C stops at once. Without `--world`
the session lives only as long as the server does: stop it and the deaths, objects and equipment
it recorded are gone. With `--world DIR` it
keeps the game clock, the deaths, the doors, locks and items taken, the items dropped or placed,
containers' contents, actors' AI settings and disposition, actors' last place and health,
attributes, skills and barter gold, kill counts,
the weather, learned topics, the journal,
faction ranks, the world's globals, references' script locals, the quest scripts still running,
characters that scripts moved and the follow, escort, travel and wander orders players and quest
scripts gave them in one file per load order in `DIR`, loads it when the
first console joins in game and writes it every 10 seconds
while it changes, so a restarted server carries on where it stopped. A joined client starts from
the data files and applies the server's world; it does not import changes from a local save.

With `--world` the server also keeps each character under `characters` in `DIR`. A joining console
starts a New Game and receives the character's identity, inventory, worn items and last place in a
small server-generated state file; the rest follows after it joins. The console sends inventory,
equipment, level, attributes, skills and their current modifiers, active effects, journal, learned topics, factions, its own globals, current
health, magicka and fatigue as they change, and who the character is: name, race, sex, head, hair,
birthsign and class, including a class made in character creation. The server keeps the latest
supported state in `stream.json`. It also keeps where the player last was, so a crash or power cut
loses at most about one polling interval of supported state. Exit flushes every supported
character field and the current place through the reliable queue. The server acknowledges the
snapshot after writing the character and world. Exit waits for that acknowledgement before
quitting; after 30 seconds without confirmation, the player chooses to leave anyway or stay.

World and character state files are replaced atomically. The server retries brief Windows
file locks for up to 0.25 seconds; lasting write failures remain errors.

### Worlds in the GUI

The GUI's **Server > Multiplayer** tab keeps a list of worlds, picked beside **Start**. Each is a folder
under **Settings > Paths > Server worlds** (`worlds` beside the local config by default), with its
settings in `world.toml` and everything `--world` keeps. The cog next to the list makes a new
world, duplicates one (with or without its characters), renames or deletes it, or opens its folder.
**World settings** belong to the selected world; **This PC** settings, such as the ports, stay in
the local config and apply to every world. Starting the server serves the selected world, so
characters and world state carry over every stop and start.

Instead of a **Build profile**, a world can name a **Library entry** from the Builds tab's
library. With **Library revision** empty, the next Start fills in the entry's latest listed
revision and keeps it, so a later publication does not change the world's build; clear the field
to move to the newest one. For a library on another PC, also fill in its address, key and the
revision's build ID ([builds from a content library](#builds-from-a-content-library)).

All worlds share one server key, `server.key` in the worlds folder, because a console refuses a
different key at the same address. The first time the GUI opens without worlds, it makes
`Default` from the earlier settings and copies an earlier world folder into it, its key included;
the earlier folder is left as it was.

The **Characters** tab lists the selected world's characters: name, race, class, level, when the
server last wrote them, and the fingerprint of the console key that plays them. **Delete** removes
one while the server is stopped; that console makes a new character there on its next join.

## Keys and passwords

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
console again. A wrong password is refused. Each IPv4 address or IPv6 /64 gets five tries,
then one a minute; changing an IPv6 address within that subnet does not replenish the budget.
Join password, handshake and remote admin limits each keep at most 1,024 source buckets.
When a table fills, new sources are refused until a bucket's entire burst has recovered;
existing limits are preserved. Previously admitted keys bypass the join password limit.
On a server that faces the internet, give players the fingerprint as well: the password travels
encrypted to the server's key, so a console that has pinned the key cannot give it to an
impostor on its first join.

The password is a shared secret, and a console's `NetPassword` is plain text in its own ini. The
server need not keep it in plain text: `tes3x net hash-password --out password.txt` asks for the
password (not echoed) and writes a salted scrypt line (`scrypt$...`) in its place, and
`--password-file` accepts that file as it does a plain one. A stolen file then does not give away
the password, which players may have used elsewhere. Each guess costs the server one scrypt
(about 50 ms), and the limit above still applies first.

Keep `server.key`, the password files and `admin-password.txt` readable only by the account that
runs the server. The server makes `server.key` with mode 0600 and warns at start when a key or
password file is readable by other accounts (on Linux; modes mean nothing on Windows). The
server appends to `admitted.txt` without locking, so edit it only while the server is stopped,
and run one server per `--world` folder.

## Kicks and bans

The server takes admin commands typed at its own window, or from the same PC with
`tes3x net admin COMMAND` (the server listens for them on `127.0.0.1` only, at
`--admin-port`, by default `26502`; `--admin-port 0` turns that off):

| Command | Does |
|---|---|
| `list` | the clients: number, key fingerprint, MAC and address |
| `status` | the clock, weather, each client's counters, and what the world holds |
| `kick N` | drop client N; its console stops trying until the game is launched again |
| `ban N` | ban client N's key and MAC, and drop it |
| `ban key FINGERPRINT`, `ban mac MAC`, `ban address A.B.C.D` | ban one of them; `unban` the same way lifts it |
| `bans` | the bans |
| `save [N]` | ask every console, or client N, to save its character now |
| `stop` | ask every console for its character, wait for the saves, then stop |
| `say TEXT`, `tell N TEXT` | show a line of text, prefixed `Admin:`, to every player or to client N |
| `log [normal\|verbose]` | show or change how much the server prints |

Any other word prints the list of commands. Players' chat appears in the server's output as
`client N (NAME) says: TEXT`, private messages as `client N (NAME) to client M (NAME): TEXT`,
and a player's `msg admin TEXT` as `client N (NAME) to admin: TEXT`.

Bans are kept in `bans.txt` in the `--world` folder. A key is a player's identity, but a player
can make a new one by deleting `servers.ini`; on a server with a password, a new key needs the
password again. The MAC is what the console reports, so a modified build can change it. An
address ban drops everything from that address before the handshake, and also stops everyone
else who shares it, such as a household behind one router.

## Remote admin

To manage a server from another machine, such as one in Docker or on a rented host, start it
with `--remote-admin 26503` and an admin password of at least eight characters on the first
line of `admin-password.txt` in its `--world` folder (or `--admin-password-file FILE`). Then,
from anywhere that reaches that UDP port:

```
tes3x net admin --server my.server.net --password-file admin-password.txt list
```

takes every command above. The GUI's Server workspace does the same under **A remote server**.
Each command asks the server for a single-use challenge first; the command and its reply are
encrypted and authenticated with a key made from the password and that challenge, so a
listener on the network can neither read nor replay them, nor send commands of its own. The
password is stretched with scrypt. `admin-password.txt` may hold the result instead of the
password: `tes3x net hash-password --admin --out admin-password.txt` writes a `secret$...` line,
and the admin then keeps the password for `--password-file` on the client. That line is still
enough to send admin commands, so keep it private. Five wrong tries from one IPv4 address or IPv6 /64
allow one more a minute, and while that holds even the right password is refused. Keep the
admin password apart from the console password: anyone with it can kick, ban and stop.

## Handing out the build

Players need the server's build: the same plugins in the same order and a matching XBE. With
`--build DIR`, naming the pipeline's staged game folder (`deploy`, holding `tes3xbuild.json`), the
server hands it to the [TES3X Dash](deployment.md#from-a-server), which installs it and
joins:

```
tes3x net serve --world world --build build/net/deploy
```

In the GUI, set **Build profile** in the **Server > Multiplayer** tab instead.

The Dash asks through the same encrypted session a console joins by, with the console's key
for this server and the password if there is one, so a console the server would refuse gets no
build. The server answers with its manifest's SHA-256 and a ticket, and serves the manifest and
files over plain HTTP on TCP port 26500 (`--http-port` for another; allow it through the
firewall too). The Dash checks every file against the manifest and the manifest against the
hash the session gave, so the HTTP side needs no encryption.

What it serves is the admin's choice, by each file's origin in the manifest. By default only
`build` files (plugins, archives, textures, `Morrowind.ini`) and the XBE deltas: the Dash
rebuilds the XBEs from the player's own retail copy and takes unchanged retail files from it.
`--serve-origin retail` and `--serve-origin xbe` add retail files and whole XBEs, for players
without a clean retail copy; only do so where you may hand them out. The deltas are read from
`deltas` beside `DIR` (`--deltas` for another folder). The server rereads the manifest when it
changes, so a new build can be staged while it runs; a Dash that is mid-update when it changes
stops without touching the installed build, and its next update gets the new one.

For an overlay build, the server also publishes the required retail files' sizes and hashes
from `--retail-base DIR`, defaulting to `paths.vanilla_root` in the local config. The Dash
checks and completes a separate retail folder before installing the overlay. Downloads from
that source follow the same retail/XBE policy; an existing complete base needs no retail
downloads. Update the Dash for this dependency format (Dash level 2), and restart the
server after changing its clean retail source. A full build continues to install its retail
files into the build folder.

The download server supports byte ranges. On a connection interruption, the Dash retries
from the last saved byte up to five times, then keeps the partial file for the next attempt.
It verifies the complete file against the manifest's SHA-256. Disconnects produce a short log
message and leave the server running.

A server with `--build` also refuses a console whose build is not that one, by the `build` id in
each manifest. Joining from the main menu then asks "Build out of date. Update it in the TES3X
Dash?": Open Dash starts the Dash on that server's page, where X updates the build, and
Back stays in the game. Without the Dash installed the server's row just says "build out of
date". A console with no manifest is checked by its load order only.

The Dash records the exact revision it installed, and the game reports it, with a digest of any
local patch selection, when it joins. A console whose revision differs from the world's, or that
has a local patch selection, is refused the same way. A build copied to the console without the
Dash records no revision and is checked by its build ID alone.

### Builds from a content library

A world can instead require one exact revision of a [content library](content.md) entry. The
Dash then downloads the build from that library, on this PC or another, and the world's
authorization lets it do so even when the library is private and has not approved that Xbox:

```sh
tes3x net serve --world world --content-state content-state --content-entry my-profile
```

`--content-state` names a library on this PC. The world reads the library's key and the entry's
latest listed revision (or `--content-revision HEX`), and records in the library that this world
may authorize that revision. The Dash reaches the library at this server's host on port 26510,
or at `--content-source HOST:PORT`. For a library on another PC, give `--content-source`,
`--content-key` (from `tes3x content key` there), `--content-entry`, `--content-build` and
`--content-revision`, then run the command the server prints on the library's PC:

```sh
tes3x content delegate WORLD_KEY my-profile REVISION --label "My world"
```

The world signs authorizations with `content-delegation.key` in `--world`; keep it with the
world. Each authorization names one Xbox's content identity, the library's key and the exact
revision, and expires after an hour. The library keeps a delegated revision even when it is
withdrawn, and `prune` does not delete it. Publishing a newer revision does not change what the
world requires; restart the world with the new revision to move it. `--build` and a content
source are alternatives.

The Dash's **Install** on the server's page fetches the world's revision from the library, and
**Join** checks the installed revision first and offers to install the required one. A console
that already has the revision joins without contacting the library.

## Run the server in Docker

`server/` in a [checkout](development.md#setting-up-a-checkout) holds a Docker Compose setup for a
machine that should only run TES3X's servers, with no Python install and no GUI. One image holds
both; the `multiplayer` and `content` profiles each start one service, and either runs without
the other. The multiplayer service runs `tes3x net serve` with `--world` on a Docker volume, so
the server key, characters, saves, admitted keys and bans survive restarts and rebuilds. From the
checkout:

```
cd server
docker compose --profile multiplayer up -d
docker compose --profile multiplayer logs -f
```

The server publishes UDP 26500; point each console's `NetServer` at the Docker host's address.
Add server options under the service's `command:` in `server/compose.yaml`, one per line, and
run `docker compose --profile multiplayer up -d` again. Admin commands run inside the container:

```
docker compose exec multiplayer python -m tes3x net admin list
```

The `content` profile runs a [content library](content.md#run-the-library-in-docker). To have
the world require one of its builds, start the content service first, then uncomment the
multiplayer service's `/state` volume and its `--content-state=/state` and `--content-entry`
lines: the Dash downloads the world's build from the library at the same address. Stopping either
service leaves the other running. There is no `--build` here: the library hands out builds.

To require a password, copy the file into the volume, uncomment `--password-file` in
`compose.yaml` and start again:

```
docker compose cp password.txt multiplayer:/world/password.txt
docker compose up -d
```

To manage it from the GUI or another PC, put an admin password in the volume, uncomment the
`--remote-admin` line and its port in `compose.yaml`, and start again:

```
docker compose cp admin-password.txt multiplayer:/world/admin-password.txt
docker compose up -d
```

`docker compose stop multiplayer` stops the server as Ctrl+C does: it asks every joined console
to save and waits up to 60 seconds. `docker compose cp multiplayer:/world ./world-backup` copies
the world out.

[xemu](multiplayer-client.md#connect-xemu) on its NAT joins the container like a console does,
at the Docker host's address (or `10.0.2.2` when xemu runs on that host). A tunnel cannot reach the container: it
talks to xemu over the host's own `127.0.0.1`. For tunnels, run the server directly on the PC
that runs xemu, or on Linux add `network_mode: host` to the service, which also replaces its
`ports:`. A `--host` DNS name needs `53:53/udp` published as well.

## Hosting over the internet

Forward UDP 26500 to the server host, or run the server on a host with a public address.
Consoles behind home routers need no port forward. Give players the public address or name,
the port if it is not 26500, and the server key fingerprint. If you serve a build, also allow
the HTTP TCP port (26500 by default). Remote administration needs its own UDP port when enabled.

## Xemu tunnels

A tunnel hands the guest's raw frames to a server on this PC, which is how the
automated tests run. On the PC that runs xemu:

```
tes3x net serve --tunnel 9369
```

serves the LAN and one xemu at once, so a console and xemu can play together. Then run xemu with
the tunnel on the same port, giving the guest its own addresses:

```
tes3x xemu player2 net.toml --direct-engine --skip-intro --net-tunnel 9369 --save my-save.ess --exec load.txt -- --ini-set Xbox:NetAddress=10.0.2.15 --ini-set Xbox:NetServer=10.0.2.2
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
tes3x net serve --tunnel 9369 --tunnel 9371
tes3x xemu player1 net.toml ... --net-tunnel 9369 ...
tes3x xemu player2 net.toml ... --net-tunnel 9371 ...
```

Each tunnelled xemu gets a MAC made from its port (`02:00:00:00:24:99` for 9369), since the server
tells clients apart by MAC. Both guests can use the same addresses.

## Character restore diagnostics

To see how much of a character the server could restore without its save, start the server with
`--rebuild` and join from a different save. The server applies the character's kept state over
that save, asks the console to save 10 seconds later, and compares the save that arrives with
the kept one. The report goes beside the upload as `uploads/KEY/NAME.diff.txt`, one row per kind
of state; neither save is kept as the character. `tes3x ess --diff KEPT OTHER` makes the same
report from any two saves.

The report separates attributes into base/current pairs and skills into base/current pairs,
alongside their saved progress. Other mobile fields remain byte comparisons.

These diagnostic sessions use uploaded saves as fixtures. Normal joins always load
characters from structured server state; see
[saving and character state](multiplayer-client.md#saving-and-character-state).
Uploaded files go under `uploads`; only explicit `--adopt` or `--rebuild` diagnostic sessions
use uploaded saves as fixtures. `--load-state` remains a compatibility option. Normal character
loading sends identity, inventory, worn items and last place as a small `char-*.t3c` file.

## Protocol compatibility

Active effects retain their native source, magnitude and elapsed time, so their timers resume
when the character joins again. Bound effects also retain the equipment they displaced, using
item identities and condition/charge rather than engine pointers.

The game and Dash have separate protocol versions. Gameplay state changes can require a new
game protocol without changing the Dash's build discovery and download protocol. These are
compatibility numbers, separate from TES3X release versions; they change only when the relevant
wire format becomes incompatible.

The server checks compatibility after the authenticated handshake. An incompatible client gets
an explicit refusal instead of a timeout. The Dash shows **Dash update needed** and offers
**Check for update**; if the server is older, it says the server needs an update. The game offers
to open the Dash to update its build when the server requires a newer game client.

Restart the server process after updating server code. Rebuilding its staged game folder does
not replace the running server or a Dash already open on a console. Relaunch the Dash after
installing or rebuilding it. Old clients without these messages may still show a generic refusal
or timeout; updating them once installs the clearer handling.

## Accepted client events

The server drops unknown kinds, server-only kinds and malformed payloads before applying
or forwarding them. Rejected events still consume their delivery sequence, so they cannot
block later events. Each event carries at most 80 data bytes. Counts must describe the entire
payload; fixed records accept no trailing bytes. Multipart headers require a nonzero part
count and an index below it. Object and spell ids are 1–31 bytes followed by a zero, with no
control characters. Empty equipment lists are allowed.

| Client kind | Accepted payload | Delivery |
|---|---|---|
| `TEXT` | Up to 80 text bytes, including empty text | Broadcast |
| `HOLD`, `HOLD_BROKEN` | Reference and target u32, then u32 on/off (0–1) or reason (0–3) | Targeted |
| `HIT`, `PLAYER_HIT` | Reference and target u32, finite health damage float, optional finite fatigue damage float | Targeted |
| `DEATH` | Reference u32 | Broadcast once |
| `STATUS` | Reference u32 and five signed i16 stats | Retained and broadcast |
| `BOUNTY` | Signed i32 bounty | Retained and broadcast |
| `BUSY` | One byte, 0 or 1 | Broadcast |
| `SPELL`, `CAST` | `SPELL` record, source type 1, player flag 0–1, one spell id | Targeted / broadcast |
| `SHOT` | `SHOT` record with finite shot values, player flag 0–1, one ammunition id | Broadcast |
| `AFFECT` | Reference u32, effect index 0–7, one spell id | Broadcast |
| `EQUIPMENT` | Part/count bytes followed by zero-terminated item ids | Retained and broadcast |
| `IDENTITY` | Part 0–1, count 2, female 0–1, exactly two zero-terminated values | Retained and broadcast |
| `ACTOR_EQUIPMENT` | Nonzero reference u32, part/count bytes, item ids | Retained and broadcast |
| `OBJECTS` | Count byte followed by `OBJECT` records, state bits limited to 0–15 | Retained and broadcast |
| `REMOVE`, `WANT` | Count byte followed by spawn ids u32 / cell indices u16 | Server handles |
| `WEATHER` | Flags 0–1, count byte, then region u16/weather 0–9 pairs | Retained and broadcast |
| `SPAWN` | `SPAWN` record, optional leveled reference u32, one base id; finite rotation and valid position | Server assigns id and broadcasts |
| `CONTENTS` | `CONTENTS_HEAD`, valid part/count, rolled flag 0–1, complete item entries | Retained and broadcast on completion |
| `OFFER` | `BULK_OFFER` record and one zero-terminated filename | Server handles |
| `GAME` | Launch token u32, zero-terminated filename (possibly empty), optional launch kind 0–2 | Server handles |
| `PICK` | Selection kind (`PICK_CHARACTER`, `PICK_START`, `PICK_NEW`) and index bytes | Server handles |
| `SNAPSHOT` | Token u32 and `STATE_BODY` with finite position and heading | Server handles |
| `PLAYER` | One accepted sub-kind followed by its shape below | Retained; learned topics, journal indices and faction rank and reputation also reach other joined characters |

`PLAYER` accepts `ITEMS` (part/count, one item id, complete stack entries), `LEVEL` (one
`LEVEL` record), `SKILLS` and `MODIFIERS` (count and complete records with valid indices and
finite values), `JOURNAL` (count and complete index u16/quest id pairs), `VITALS` (three finite
floats), `SPELLS` (add/remove, part/count and spell ids), `IDENTITY` and `WORN` (part/count
and snapshot fragment), `EFFECTS` (u16 part/count and snapshot fragment), and `DEATH` or
`ALIVE` (no body), and `TOPICS` (count and complete zero-terminated names of at most 63 bytes,
without controls or double quotes). Topics are additions to the shared world's learned list,
capped at 4,096 names; new topics reach other joined characters, and the whole list is replayed
before character READY. Character streams also retain their learned topics. `FACTIONS` carries a
count and entries of a flags byte, rank i32 and reputation i32, each followed by a faction id of
at most 31 bytes; a console must send all three values. The world keeps each faction's rank and
reputation (up to 512 factions) and sends changes to the other joined characters; each character
keeps its own membership and expulsion. Only a character that already was a member moves the
world's rank; one that has just joined is sent the world's rank instead. `GLOBALS` carries a
count and entries of a finite f32 value followed by a global's name of at most 31 bytes. They
belong to the character alone: the server keeps up to 128 in `stream.json`, puts them in the
New Game's state file, so they are set before global scripts first run, and replays them before
READY. The console chooses which globals are the character's. `WORLD_GLOBALS` has the same
form and carries every other global the console keeps (not the clock, `Random100`, chargen's
or derived ones). The world file keeps the latest value of each, up to 1,024, and sends a
change to the other joined characters; the later of two changes wins. The server replays
them before READY and puts them in the state file when the world is loaded, which after a
restart happens once a console is in game. `LOCALS` (event 37) carries a data-file
reference's refid, a count and entries of a slot (kind << 14 | index; short, long, float) and
a raw u32 value. The server keeps the latest of each, up to 16,384 values, relays the event
and replays all of them after WELCOME. The world file also keeps each actor's latest state from
its authority (up to 8,192 actors). When a cell gets an authority and nobody had it before, the
server sends that client `ACTOR_PLACE` (event 38, server only) before `AUTHORITY`: a count and
up to two entries of refid, position, heading, health, magicka, fatigue and the actor flags,
for the actors last reported in that cell. `ACTOR_STATS` (event 44) carries what an actor's record holds beyond that:
refid, a count and entries of a slot (attribute base or current, barter gold, corpse hourstamp,
an NPC's skill base and current, a creature's combat, magic and stealth base and current, whether it has been talked to or
attacked, the death-crime byte) and
a raw u32 value. The cell's authority sends a change, any console a
change of barter gold; the server keeps the latest of each (up to 16,384 values), relays it and
replays all after WELCOME, and each console writes them into the actor once it is loaded.
`ACTOR_EFFECTS` (event 47) carries an actor's active effects as a complete set: refid, part and
parts, then whole 608-byte records of the player-effects format (up to six per actor, 24 actors).
The cell's authority sends a set when it changes and every 20 s while one runs; the server keeps
the latest, relays each part and replays all after WELCOME, and a console ends the actor's
effects and re-creates the set once the actor is loaded, before its statistics. Bound effects and
the abilities, diseases and curses of the actor's spell list are not kept. `KILL`
(event 45) is a death the authority counted, with the id of the object the engine counts it
under; the server keeps one per refid (up to 8,192) and answers every console with `KILLS`
(event 46), the id and its count, which each console writes into its kill counter, so
`GetDeadCount` agrees everywhere and after a restart. `ACTOR_MOVE` (event 39) is an actor a script or a
dialogue moved: refid, the cell index it left, the cell index it is in, position and rotation
(radians, as Position and PositionCell give it). The server keeps the latest of each (up to 4,096) with the first cell it left, relays it
in that form and replays all of them after WELCOME. `AI_PACKAGE` (event 40) is an AI package
a player's dialogue or a quest script gave a data-file actor: refid, type (1 travel, 2 wander,
3 escort, 4 follow), target kind, the escort or follow cell index, a client, the package's
settings (destination, or wander range, duration, time and idle chances), reset, then the
target actor's id of at most 31 bytes. Escort and follow need a target, travel and wander have
none. A console names its own player as target kind 1 and another player's stand-in as kind 2
with that player's client. The server keeps the latest package of each actor (up to 4,096) and
a player target by character; it sends the package to that character's console as kind 1 and
to the others as kind 2, replays all of them before READY, and holds back one whose target
player is away until that player joins. `RECORD` (event 43) is a spell made at the spell maker:
a blob sent in parts of at most 78 bytes (part, parts, chunk) holding the cast type, magicka
cost, flags, the id the engine rolled, the name and up to eight effects. The server checks the
whole blob, keeps it in the world file (up to 4,096), sends it whole to the other joined
consoles and replays every record before a joining character's items and spells; a console
builds it under the same id. `WORLD_SCRIPTS` (player sub-kind 21) is a
global script a console started after its first READY: op (1 running, 2 stopped, 3 values),
the script's reference (refid; 1 the player), the script id, a count and `LOCALS` entries. The
server keeps each script (up to 512, 64 locals each) with the locals of the first console
running it, drops it when the last one stops it, and hands one nobody runs any more to a
player before READY, values first, then running. Identity, worn and effect snapshots are checked when reassembled;
effects are capped at 64 entries. Server-to-client player sub-kinds are rejected.

Inventory entries carry a signed i32 count and a flags byte (only `ENTRY_DATA`), optional
condition/charge u32 values, and, for containers, an item id. These checks define payload
shape; they do not establish whether a client is entitled to change a particular reference.
