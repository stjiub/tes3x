# Multiplayer

The `multiplayer` patch is experimental (channel `dev`). Each player runs the full game on a
console or in xemu, and a small TES3X server passes state between them. The server does not run
Morrowind and needs no game files. What the session shares is described in the
[patch notes](../patches/multiplayer.md).

Choose the guide for what you want to do:

- [Playing multiplayer](multiplayer-client.md): get the matching build, join from Xbox or xemu,
  save and leave, configure your connection, and check the current gameplay limits.
- [Hosting a multiplayer server](multiplayer-server.md): start a server, retain world and
  character state, manage access and players, hand out builds, and run in Docker.

Every player must load the same plugins in the same order. Characters are created and loaded
from server state through a New Game; local saves are not multiplayer characters. The server
needs `--world DIR` to keep characters and world state after it stops.
