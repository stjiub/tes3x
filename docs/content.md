# Content servers

A content server lets the TES3X Dash browse and install completed builds without joining a
multiplayer world. The installed build appears in **Builds**, where it can be verified and
launched normally. Multiplayer servers and content servers have separate saved addresses.

## Host builds from the GUI

Open **Content** in the GUI. Turn on **Always share every completed build** to publish every
`deploy` folder under the configured `paths.build_root` (normally `build`). New builds appear
after the server's next library scan. You can also use **Add completed build** to publish a
particular deploy folder. The list shows each build's revision and source folder. Press **Start
content server** to host it independently of the multiplayer server.

Only completed pipeline outputs are published. Publishing copies a revision to the content
server's state folder so that an active download keeps reading the same files after a profile is
rebuilt. Keep enough PC disk space for those copies. The state folder also holds the content
server's private identity key; preserve it when moving the library to another PC. Removing a
build from the source list stops advertising it, while old snapshots remain for active tickets.

The server listens on TCP port 26510 by default. Configure its listen address and port in the
GUI, and allow that TCP port through the host firewall for consoles that need it. **Serve retail
files** and **Serve whole XBEs** are separate choices and are off by default. The server always
serves build-origin files and XBE deltas. Retail files are offered to anyone who can reach this
content server when retail serving is enabled, so choose a network appropriate for your library.

The same service can be started without the GUI:

```sh
tes3x content serve --all-builds --build-root build --state content-state
tes3x content serve --build build/my-profile/deploy --state content-state
```

Repeat `--build` to share several chosen folders. Use `--bind`, `--port`, `--retail-base`, and
repeatable `--serve-origin build|retail|xbe` to set the same hosting options. The default origin
is `build` only. For an overlay build, `--retail-base` supplies the separate retail dependency
that the Dash can install when retail serving is enabled.

The GUI's **Recent install activity** list shows the build, console address, reported stage and
progress, bytes sent by the server, and the current file. A completed row means the Dash reported
that it committed the install; transferred bytes alone do not establish success. If reports stop
for 90 seconds, the row becomes **unknown**. The list keeps up to 128 attempts during this server
run. It is cleared when the content server restarts. The Dash signs reports with its content
identity in `E:\TES3X\content-id.key`; keep that file if you move its content settings to another
Xbox. If a final report is not acknowledged, the Dash keeps it locally and retries on its next
contact with that library. It keeps at most 16 results. A report cannot be recovered after its
six-hour ticket expires or the server restarts. Older Dashes can still download, but cannot report
a completed install.

## Browse on the Xbox

Open the Dash's **Content** tab and press **X** to add a host name or IP address. Include
`:port` if it does not use 26510. Press **A** on the saved server to see its offered builds, then
**A** on a build to install it. An installed build is listed in **Builds** and launches from
there without multiplayer join data. Open the library and install the same entry again to update
it after the PC rebuilds it. If the entry changes while its list is open, Install refreshes that
entry and uses its latest revision. Press **Back** on the host list to forget a content server;
this leaves downloaded builds installed. Installed builds are not updated in the background.

The Dash pins a content server's signing key on first contact. If its key later changes, the Dash
shows the old and new fingerprints and asks before accepting the new key. Catalogs and tickets
are signed; the existing staged installer checks each downloaded file against the signed
manifest's SHA-256, resumes complete staged files, checks free space, and rebuilds XBEs from
deltas against the retail base where possible. A content server's saved key is separate from
your multiplayer player identity.

## Current scope

Content browsing is a standalone download path. A multiplayer server still serves its own
single build through **Servers**; it does not yet point clients at a separate content library.
The content server currently keeps old copied revisions in its state folder until the admin
removes them manually while the server is stopped. Activity reports are signed by a stable Dash
identity and bound to the transfer ticket. Linking a resumed download to its earlier attempt is
still pending. Shared archive storage, access control, remote content sources for multiplayer,
and Docker service profiles are planned separately.
