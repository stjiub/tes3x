# Add-ons

Add-ons are optional parts of TES3X for particular setups. Each lives in its own folder under
`addons/` and does nothing until it is switched on, in the GUI's **File > Settings** or in
`tes3x.local.toml`:

```toml
[addons]
console = true
```

## `console`: Play on Xbox

For a softmodded Xbox running the [XBMC4Gamers](https://github.com/Rocky5/XBMC4Gamers) dashboard.
A small agent inside the dashboard starts games on request, so a build can go from the PC to the
screen in one step. The GUI's **Play** button gains an **Xbox** target that:

1. checks that the agent answers,
2. deploys the build over FTP to the profile's Xbox folder (the Build tab's Xbox folder, or
   `[deploy] remote_root`), deleting files there that the build does not have,
3. starts `default.xbe` from that folder.

It asks before the first deploy of each profile. The Xbox must be on and showing the dashboard;
once a game is running, the dashboard, and with it the agent and FTP, are gone until you return to
it. Fetch logs afterwards with **Actions > Pull Xbox logs**.

### Setup

1. Set the Xbox's address under **File > Settings** (`[deploy] host`); FTP must work.
2. Tick the add-on and press **Install agent on Xbox**. This copies `agent.py` into the dashboard
   and adds one line to the skin's `Startup.xml`, keeping the original under
   `build/console-backup/`.
3. Restart the dashboard once. The agent starts about 30 seconds after the dashboard's startup
   screen closes.

An XBMC4Gamers update that replaces the skin removes the line; install again. **Remove agent**
undoes the install. If the dashboard folder is not `F:/XBMC4Gamers`, set it:

```toml
[console]
dashboard = "E:/XBMC4Gamers"
```

### From the command line

```
python addons/console/console.py ping
python addons/console/console.py run "F:/Games/Morrowind/default.xbe"
python addons/console/console.py stat "E:/tes3xlog.txt"
python addons/console/console.py wait --timeout 120        # until the agent answers
python addons/console/console.py reboot | shutdown
python addons/console/console.py install | uninstall
```

### Security

The agent listens on TCP port 7353 with no password and will start any XBE or run any dashboard
command it is sent, much as the console's FTP server already accepts anyone on the network with
the default login. Use it only on a network you trust.

## Writing an add-on

An add-on is a Python package in `addons/` named in `NAMES` in `addons/__init__.py`, whose
docstring lists what the package provides: a label and description for Settings, a status check,
its Play targets and the commands each one runs, and optional Settings buttons.
