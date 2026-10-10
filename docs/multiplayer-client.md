# Playing multiplayer

Join a TES3X server from an Xbox or xemu, keep your character on the server, and play in the
same world as other players. The multiplayer patch is experimental; see the
[overview](multiplayer.md). Hosting and administration are in the
[server guide](multiplayer-server.md).

## What you need

- A console or xemu with the server's game build. Every player must load the same plugins in
  the same order; ask the admin for the matching build.
- The server's address or name, with `:PORT` if it uses a port other than 26500.
- The password and server key fingerprint, if the admin supplies them.

If the admin serves a build, use the [console manager](deployment.md#from-a-server) to install
it and join. Otherwise deploy the supplied profile to its own folder. If you need to make the
build yourself, see [building a client](#building-a-client).

## Connect a console

1. Ask the server admin for its address and the matching game build. The server must be running.
2. Launch the build from the dashboard, choose Join and select the server. Choose a retained
   character or create a new one; the console starts a New Game from server state.
3. Other players do the same. Each shows up for the others as a stand-in once they are in the
   same interior or within one exterior cell.

New Game and Load relaunch the title; the console leaves and joins again by itself, and the server
prints `rejoined`. If the server stops answering for 15 seconds, the console says the connection is
lost and keeps reconnecting; after 60 seconds it offers the main menu.

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
quits after confirmation; Load is gone, since a local save is not the server's character. A
multiplayer build redraws the menu buttons so they match the ones it adds.

## Saving and character state

Joining starts a New Game with your character's identity, inventory, worn items and last place
restored from the server. The rest of the supported state follows once you join. Topics,
factions and player globals are not retained yet and start at their New Game values.

**Save to Server** flushes every supported character field and your current place. The server
confirms the save after writing the character and world. **Leave** waits for that confirmation
before quitting; after 30 seconds without confirmation, you can choose to leave anyway or stay.
The server also receives changes while you play, but use Save to Server or Leave to confirm
they have been stored. The admin must enable world storage to keep characters after a restart.

Active effects resume with their remaining duration when you join again; time spent offline
does not count. Bound effects also retain the equipment they displaced.

## Death and respawn

A player who dies while joined is not offered the last save. The others see a notice. By default,
after five seconds the player gets up at the closest temple or Imperial shrine (the markers
Almsivi and Divine Intervention use), with full health, magicka and fatigue, the same bounty,
and 10 percent less carried gold. The admin can change the delay, destination and gold cost.
A dead player cannot leave or save until then; a console that loses power while dead comes
back at the marker on its next join.

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

With `NetAddress` empty the network stays off and the build plays as a normal game, until you
choose Join, which asks the router for an address by DHCP.

Every console can share one build. A fixed address belongs to one console: deploy writes it to
that console's `console.ini`, so a second console takes the same build deployed with its own
address. Ask the admin for a stable server address or name so `NetServer` stays right.

## Server keys and passwords

Traffic between your console and the server is encrypted. On the first join, the console trusts
the server's key and keeps it with its own key for that server in `U:\TES3X\servers.ini`.
Later joins refuse a changed server key. Ask the admin for the fingerprint before your first
join, especially over the internet; add it to the address to pin the key:
`NetServer=my.server.net#0123456789abcdef0123456789abcdef`.

A server with a password asks for it when you join, or you can set `NetPassword`. A typed
password is kept in that server's section of `servers.ini` and used in place of `NetPassword`.
Passwords are 1 to 64 printable ASCII characters. Once the server admits your console's key,
it does not ask again unless the admin revokes that admission. Repeated wrong attempts are
limited, so wait before retrying.

If the admin replaces the server key, verify the new fingerprint with them before deleting
that server's section from `servers.ini` and joining again. That section also holds your
console's key for the server: deleting it changes your identity there.

## Connect xemu

xemu can join any server, on this PC, the LAN or the internet, through its own NAT. Run the
xemu runner with `--net-nat` and give the build `NetAddress=dhcp` and the server's address:

```
tes3x xemu player2 net.toml --direct-engine --skip-intro --net-nat -- --ini-set Xbox:NetAddress=dhcp --ini-set Xbox:NetServer=my.server.net
```

xemu's NAT answers DHCP with `10.0.2.15`, resolves names through the PC and sends the session's
UDP out from the PC, so the server sees the PC's address. `10.0.2.2` there is the PC itself:
`NetServer=10.0.2.2` reaches a server running on the same PC. Each run's MAC is made from its
run name, so two xemus on one PC are two clients. The GUI's **Play** uses the NAT for any build with
`multiplayer` or `agent`.

### Local test tunnels

For a local test session, the admin can provide an xemu tunnel on the same PC. Give the runner
`--net-tunnel PORT` instead of `--net-nat`, using the port the admin assigned. Through a tunnel,
`NetServer=10.0.2.2` reaches that server and `NetAddress=dhcp` works. Each xemu needs its own
tunnel port. See [server tunnels](multiplayer-server.md#xemu-tunnels) for setup and examples.

## Playing over the internet

The console only sends UDP to the server and to DNS, so a console behind a home router needs no
port forward. Ask the admin for a reachable server address or name;
[hosting over the internet](multiplayer-server.md#hosting-over-the-internet) covers server setup.
Set `NetServer` to that address or a name, and `NetGateway` to the console's router. Expect
round trips of tens of milliseconds instead of a LAN's two.

## Checking a session

With `console` in the build, open the console (Back + right thumb click) and type:

| Command | Does |
|---|---|
| `tes3xnet stat` | write the network counters to the log |
| `tes3xnet say TEXT` | send a line of text to the other players (logged there as `net.text`) |
| `tes3xnet send NAME` | send `U:\TES3X\NAME` to the server when the admin asks for a diagnostic file; this does not save your multiplayer character |
| `tes3xnet save` | flush supported character state and wait for the server to confirm storage |
| `tes3xnet leave` | flush supported character state and quit after the server confirms storage |
| `tes3xnet down` | leave the session and stop the network card |
| `tes3xnet up ADDRESS[/BITS] [SERVER[:PORT] [GATEWAY]]` | start it again by hand |

The log is `E:\tes3xlog.txt`. A console whose plugins differ from the session's logs `net.refused`
and stops trying until the game is launched again; the server prints `refused` with both load order
hashes. `net.refused_reason` says why: 1 the load order, 2 the server is full, 3 a wrong password, 4
kicked, 5 banned, 6 the build is not the one the server hands out, 7 a protocol version the server
does not speak.

For build or protocol mismatches, update the game through the console manager. If the
manager itself needs an update, it offers **Check for update**. A message that the server
needs an update must be resolved by its admin. See
[protocol compatibility](multiplayer-server.md#protocol-compatibility) for update handling.

## Building a client

Enable the patch in the profile. It requires `net`, which requires `diagnostics`; the pipeline
adds both. `console` gives you the commands under [checking a session](#checking-a-session):

```toml
[profile]
name = "net"
title = "Morrowind Net"
install_dir = "MorrowindNet"

[patches]
preset = "minimal"
enable = ["multiplayer", "console"]

[ini]
"Xbox:NetAddress" = "dhcp"
"Xbox:NetServer" = "192.168.1.10"
```

The pipeline also writes `TES3X Multiplayer.esp`, which holds the stand-ins that show the other
players. Deploy with `--deploy` as usual, to its own `install_dir` apart from your normal game:
saves made with this build depend on that plugin.
