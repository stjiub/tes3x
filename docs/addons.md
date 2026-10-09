# Add-ons

Add-ons are optional parts of TES3X for particular setups. Each lives in its own folder under
`addons/` and does nothing until it is switched on, on the **Add-ons** page of the GUI's
**File > Settings** or in `tes3x.local.toml`:

```toml
[addons]
console = true
```

## `console`: Play on Xbox

For a softmodded Xbox running the [XBMC4Gamers](https://github.com/Rocky5/XBMC4Gamers) dashboard.
A small agent inside the dashboard starts games on request, so a build can go from the PC to the
screen in one step. With an Xbox target selected, **Play**:

1. checks that the agent answers,
2. deploys the build over FTP to the selected target's `games_root` and the profile's
   `install_dir`, deleting files there that the build does not have,
3. starts `default.xbe` from that folder. In an overlay-layout build this is the patched engine,
   because the retail launcher cannot read from the separate data-only base.

It asks before the first deploy of each profile. The Xbox must be on and showing the dashboard;
once a game is running, the dashboard, and with it the agent and FTP, are gone until you return to
it. Fetch logs afterwards with **Actions > Pull Xbox logs**.

### Setup

1. Set the Xbox target's address on the **Target** workspace's **Setup** tab; FTP must work.
2. On the **Overview** tab, press **Install / update** under **Dashboard agent**. This copies the
   stable launcher and its reloadable body into the dashboard, makes an authentication token in the
   selected Xbox target's local configuration, and adds one line to the skin's `Startup.xml`,
   keeping the original under `build/console-backup/` in the folder TES3X was started from.
3. Restart the dashboard once. The agent starts about 30 seconds after the dashboard's startup
   screen closes.
4. Tick **Xbox dashboard agent (XBMC4Gamers)** on the **Add-ons** page of **File > Settings** for
   Play on Xbox. Installing, updating, restarting and removing the agent stay on the target either
   way.

The **Overview** tab's **Drives** row shows each drive's free space once the agent answers, and
flags the deploy drive as nearly full under 2 GB. A deploy asks the agent too (see
[free space](deployment.md#free-space)).

Later installs replace the body and ask the running launcher to reload it, without restarting the
dashboard. An XBMC4Gamers update that replaces the skin removes the launch line; install again.
**Restart dashboard** performs a dashboard-only restart, and **Remove** stops and
removes the launcher, body, token and launch line. The installer detects common dashboard
layouts. For another layout, set the root on that Xbox target:

```toml
[targets.living_room]
dashboard = "E:/XBMC4Gamers"
```

### From the command line

These run from a [checkout](development.md#setting-up-a-checkout):

```
python addons/console/console.py ping
python addons/console/console.py run "F:/Games/Morrowind/default.xbe"
python addons/console/console.py stat "E:/tes3xlog.txt"
python addons/console/console.py drives                    # free/total MB per drive
python addons/console/console.py reload | stop             # reload its body, or end the agent
python addons/console/console.py wait --timeout 120        # until the agent answers
python addons/console/console.py restart                   # restart XBMC4Gamers
python addons/console/console.py reboot | shutdown         # restart or power off the Xbox
python addons/console/console.py install | uninstall
```

### Security

The agent listens on TCP port 7353 only to private-LAN peers. Every request must carry the random
token made during installation; keep `tes3x.local.toml` private. The general `builtin` command is
limited to notifications, window activation and screenshots. FTP access remains more powerful
than the agent, so change the Xbox FTP password as well on a network you do not fully trust.

## Writing an add-on

Add-ons are written in a [checkout](development.md#setting-up-a-checkout).

An add-on is a Python package in `addons/` named in `NAMES` in `addons/__init__.py`, whose
docstring lists what the package provides: a label and description for Settings, a status check,
its Play targets and the commands each one runs, and optional Settings buttons.
