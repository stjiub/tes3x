"""PlayController: profile window play controller."""

from .common import (AgentListener, Fetch, MEMORY_LOG_SECONDS, OP_REBOOT, PIPELINE_MARKER, Path,
                     PlaySession, QMessageBox, QProcess, QProcessEnvironment, QTextCursor,
                     addon_registry, console_request, dashboard_agent_state, datetime,
                     fetch_destination, ftp_error, heartbeat_text, is_manager, key_fingerprint,
                     launch, load_or_create_key, os, parse_drives, re, resolve_xemu, running_xemu,
                     shutil, signal, struct, sys, tes3x_targets, time, tomllib)
from PySide6.QtCore import QObject


class PlayController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window

    def start_in_game_listener(self):
        """Start the shared Xbox/xemu agent endpoint after the real GUI is visible."""
        key_path = self.window.local_config_path().with_name("tes3x.agent.key")
        try:
            secret = load_or_create_key(key_path)
            listener = AgentListener(secret, self.window.in_game_event.emit)
            listener.start()
        except (OSError, ValueError, SystemExit) as exc:
            self.window.statusBar().showMessage(
                f"Could not start the in-game agent listener: {exc}", 10000
            )
            return
        self.in_game_listener = listener
        self.agent_fingerprint = key_fingerprint(secret)

    def lend_listener(self):
        """Close the agent listener so a deploy through the manager can bind its port. The
        manager pairs with the deploy, and with the GUI again once the deploy ends."""
        if self.in_game_listener is not None:
            self.in_game_listener.close()
            self.in_game_listener = None
        for name, state in self.window.target_runtime.items():
            if state.get("game_kind") == "manager" and state.pop("game", None):
                state["game_detail"] = "Manager's agent lent to the deploy"
                self.window.refresh_target_item(name)
        self.listener_lent = True

    def restore_listener(self):
        if self.listener_lent:
            self.listener_lent = False
            self.start_in_game_listener()

    def manager_paired(self, name):
        return bool(name) and "manager_agent" in self.window.target_features(name)

    def in_game_target(self, address, key=None):
        """Map an agent's source address to a configured target."""
        host = address[0]
        targets = tes3x_targets.targets(self.window.local_values())
        selected = self.window.target_picker.currentData()
        matching = [name for name, target in targets.items()
                    if target.get("kind") == "xbox" and target.get("host") == host]
        if selected in matching:
            return selected
        if matching:
            return matching[0]
        if host.startswith("127.") or host == "::1":
            # xemu reaches the listener through a local tunnel: the running session's target.
            running = [s.target for s in self.plays.values() if s.process is not None]
            if len(running) > 1 and key is not None:
                # Every xemu arrives from this PC; each has its own key, claimed by the oldest
                # run that has none yet.
                if key not in self.agent_keys:
                    taken = set(self.agent_keys.values())
                    free = [t for t in running if t not in taken]
                    if free:
                        self.agent_keys[key] = free[0]
                if self.agent_keys.get(key) in running:
                    return self.agent_keys[key]
            playing = running[0] if running else None
            if playing in targets:
                return playing
            target = targets.get(selected, {})
            if target.get("kind") == "xemu":
                return selected
        return None

    def handle_in_game_event(self, event):
        kind = event["kind"]
        if kind == "reply":
            self.agent_reply(event)
            return
        name = self.in_game_target(event["address"], event.get("client_key"))
        if not name:
            return
        if kind == "goodbye":
            state = self.window.target_runtime.setdefault(name, {})
            state.pop("game", None)
            state["game_detail"] = "In-game agent disconnected"
            self.window.refresh_target_item(name)
            return
        if kind == "stalled":
            self.window.set_target_runtime(name, game="stalled",
                                    game_detail="In-game heartbeat stopped")
            return
        values = {"game": "connected", "game_key": event.get("client_key"),
                  "game_address": event["address"]}
        if kind == "connected":
            manager = is_manager(event.get("payload", b""))
            values["game_kind"] = "manager" if manager else "game"
            values["game_detail"] = (f"{'Manager' if manager else 'In-game'} agent from "
                                     f"{event['address'][0]}:{event['address'][1]}")
        payload = event.get("payload", b"")
        if kind == "heartbeat" and len(payload) >= 16:
            _, frame_us, free_kb, dropped = struct.unpack_from("<IIII", payload)
            values["heartbeat"] = {"frame_us": frame_us, "free_kb": free_kb, "dropped": dropped}
            now = time.monotonic()
            if self.log_memory and now - self.memory_logged.get(name, 0) >= MEMORY_LOG_SECONDS:
                self.memory_logged[name] = now
                self.window.agent_lines.emit(name, [f"mem {heartbeat_text(values['heartbeat'])}"])
        self.window.set_target_runtime(name, **values)
        if kind == "log":
            lines = payload.decode("cp1252", "replace").splitlines()
            self.agent_logs[name].extend(lines)
            del self.agent_logs[name][:-2000]
            self.window.agent_lines.emit(name, lines)

    def agent_request(self, name, op, args, done):
        """Send a request to the game on target `name`; `done(event)`, if given, gets the
        reply."""
        address = self.window.target_runtime.get(name, {}).get("game_address")
        if self.in_game_listener is None or address is None \
                or self.window.target_runtime[name].get("game") != "connected":
            return None
        ident = self.in_game_listener.request(address[0], op, args, address[1])
        if ident is not None and done is not None:
            self.agent_replies[ident] = done
        return ident

    def agent_reply(self, event):
        for job in list(self.agent_fetches):
            if job["fetch"].handle(event):
                self.fetch_progress(job)
                return
        done = self.agent_replies.pop(event.get("id"), None)
        if done:
            done(event)

    def agent_console(self, name, line):
        def done(event):
            if event["status"] != "ok":
                self.window.agent_lines.emit(name, [f"» {line}: {event['status']}"])

        try:
            op, args = console_request(line)
        except ValueError as exc:
            self.window.agent_lines.emit(name, [f"» {exc}"])
            return
        if self.agent_request(name, op, args, done) is None:
            self.window.agent_lines.emit(name, ["» no game is connected"])

    def agent_reboot(self, name):
        def done(event):
            self.window.statusBar().showMessage(
                f"{name}: quit to dashboard: {event['status']}", 8000
            )

        if self.agent_request(name, OP_REBOOT, b"", done) is None:
            self.window.error("No game with the in-game agent is connected")

    def agent_fetch(self, name, path, destination=None):
        """Copy a file from the running game into build/agent-fetch/TARGET/TIME/."""
        destination = destination or fetch_destination(self.window.work_dir(), name, path)

        def send(op, args):
            return self.agent_request(name, op, args, None)

        job = {"fetch": Fetch(send, path), "target": name, "path": path,
               "destination": Path(destination)}
        self.agent_fetches.append(job)
        job["fetch"].start()
        self.fetch_progress(job)

    def fetch_progress(self, job):
        fetch = job["fetch"]
        if fetch.error:
            self.agent_fetches.remove(job)
            self.window.error(f"Fetch from {job['target']} failed: {fetch.error}")
        elif fetch.done:
            self.agent_fetches.remove(job)
            try:
                job["destination"].parent.mkdir(parents=True, exist_ok=True)
                job["destination"].write_bytes(fetch.data())
            except OSError as exc:
                self.window.error(f"Could not save {job['destination']}: {exc}")
                return
            self.window.statusBar().showMessage(
                f"Fetched {job['path']} to {job['destination']}", 10000
            )
            self.window.targets_page.refresh_logs()
        else:
            size = f" of {fetch.size:,}" if fetch.size is not None else ""
            self.window.statusBar().showMessage(
                f"Fetching {job['path']}: {fetch.received:,}{size} bytes…")

    def launch_manager_xemu(self, target):
        """Boot xemu into the manager. The target's disk is seeded once with a retail base and a
        console.ini, so the manager can install a server's build and join it, as on a console."""
        reason = self.play_available()
        if reason:
            self.window.error(reason)
            return
        out = self.window.work_dir() / "build" / "manager" / "xemu"
        config = str(self.window.local_config_path())
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        name = f"manager-{target['name']}-{stamp}"

        def start():
            port = self.window.local_values().get("server", {}).get("port", 26500)
            # the profile's own build rides on the disc, so a rebuild needs no download
            built = self.window.build_output() / "deploy" if self.window.profile_path else None
            if built is not None and (built / "tes3xbuild.json").is_file():
                profile = self.window.profile_plain["profile"]
                folder = profile.get("install_dir") or profile["name"]

                def link(source, target):
                    try:
                        os.link(source, target)
                    except OSError:
                        shutil.copy2(source, target)

                staged = out / "disc" / "Games" / folder
                shutil.copytree(built, staged, copy_function=link)
                # the manager offers Join for the build installed from a server
                manifest = staged / "tes3xbuild.json"
                raw = manifest.read_bytes()
                head = (f'{{\n "server": "10.0.2.2:{port}",\n "deployed": "'
                        f'{datetime.datetime.now(datetime.timezone.utc):%Y-%m-%dT%H:%M:%SZ}",')
                manifest.unlink()  # a hard link must not be written through
                manifest.write_bytes(head.encode() + raw[raw.index(b"{") + 1:])
            servers = out / "seed" / "servers.ini"
            servers.write_text(f"[10.0.2.2:{port}]\r\n", encoding="latin-1", newline="")
            environment = QProcessEnvironment.systemEnvironment()
            environment.insert("TES3X_CONFIG", config)
            self.play_run = self.window.work_dir() / "build" / "xemu" / name
            self.start_play_process(
                "xemu",
                [name, "--deploy", str(out / "disc"), "--target", target["name"],
                 "--config", config, "--net-nat", "--disk", str(self.play_disk()),
                 "--seed", f"Games/Base={out / 'seed' / 'Games' / 'Base'}",
                 "--seed", f"Games/Base/Morrowind.ini={out / 'seed' / 'Games' / 'Base' / 'Morrowind.ini'}",
                 "--seed", f"TES3X/console.ini={out / 'seed' / 'console.ini'}",
                 "--seed", f"UDATA/42530005/TES3X/servers.ini={servers}"],
                f"Manager in {target['name']}…", environment)

        self.window.run_steps([("manager",
                         ["xemu", str(out), "--config", config],
                         "Staging the manager for xemu…")], then=start)

    def launch_manager(self):
        selected = self.window.default_target()
        if selected and selected.get("kind") == "xemu":
            self.launch_manager_xemu(selected)
            return
        target = self.window.default_target("xbox")
        module = self.window.play_addons.get("xbox")
        if target is None or module is None:
            self.window.error("Select an Xbox target with the dashboard agent add-on enabled")
            return
        config = ["--config", str(self.window.local_config_path()), "--target", target["name"]]
        import tes3x.manager as tes3x_manager
        try:
            remote = tes3x_manager.remote_folder(target)
        except tes3x_manager.ManagerError as exc:
            self.window.error(exc)
            return
        console = Path(module.HERE) / "console.py"
        self.window.run_steps(
            [
                (console, ["ping", *config], "Looking for the Xbox dashboard agent…"),
                (
                    console,
                    ["run", remote + "/default.xbe", *config],
                    "Starting the manager on the Xbox…",
                ),
            ]
        )

    def set_play_gdb(self):
        self.play_gdb = not self.play_gdb
        if self.window.settings is not None:
            self.window.settings.setValue("play_gdb", self.play_gdb)

    def play_session(self, create=False):
        """The selected target's xemu run, if it has one."""
        name = self.window.target_picker.currentData() or ""
        if create:
            return self.plays.setdefault(name, PlaySession(name))
        return self.plays.get(name)

    @property
    def play_process(self):
        session = self.play_session()
        return session.process if session else None

    @play_process.setter
    def play_process(self, value):
        if value is None:
            self.plays.pop(self.window.target_picker.currentData() or "", None)
        else:
            self.play_session(True).process = value

    @property
    def play_pid(self):
        session = self.play_session()
        return session.pid if session else None

    @play_pid.setter
    def play_pid(self, value):
        session = self.play_session()
        if session:
            session.pid = value

    @property
    def play_run(self):
        session = self.play_session()
        return session.run if session else None

    @play_run.setter
    def play_run(self, value):
        if value is not None:
            self.play_session(True).run = value

    def play_available(self):
        """Why the selected target cannot run, or None when it can."""
        local = self.window.local_values()
        target = self.window.default_target()
        if target is None:
            return "Add and select a target in the Target tab"
        if target.get("kind") == "xbox":
            if not target.get("host"):
                return "Set the Xbox target's address in the target's Setup tab"
            module = self.window.play_addons.get("xbox")
            if module is None:
                return "Enable the Xbox dashboard agent add-on in File > Settings"
            selected = dict(local)
            selected["default_target"] = target["name"]
            return module.status(selected)
        xemu = resolve_xemu(target, self.window.local_config_path().parent)
        if not xemu.get("exe"):
            return "Set the xemu folder in the target's Setup tab"
        if target.get("ram", 64) == 128 and not xemu.get("bios_128mb"):
            return "Set a 128 MB BIOS in the target's Setup tab"
        return None

    def play(self):
        if self.play_process is not None:
            self.stop_play()
            return
        reason = self.play_available()
        if reason:
            self.window.error(reason)
            return
        if self.window.process is not None:
            self.window.error("A TES3X command is already running")
            return
        if not self.window.save_profile():
            return
        if self.window.build_status()[0] == "built":
            self.start_play()
            return
        self.window.run_pipeline([])
        if self.window.process is not None:
            self.window.after_command = self.start_play

    def play_iso(self):
        """The ISO an earlier play made of the current build, so it is not packed again."""
        built = (self.window.build_output() / PIPELINE_MARKER).stat().st_mtime
        runs = self.window.work_dir() / "build" / "xemu"
        name = self.window.target_picker.currentData()
        isos = sorted(runs.glob(f"play-{self.window.profile_path.stem}-{name}-*/game.iso"),
                      key=lambda path: path.stat().st_mtime)
        current = isos[-1] if isos and isos[-1].stat().st_mtime >= built else None
        for iso in isos:
            if iso != current:
                iso.unlink(missing_ok=True)
        return current

    def play_context(self):
        target = self.window.default_target()
        return {
            "profile": self.window.profile_path,
            "config": self.window.local_config_path(),
            "deploy": self.window.build_output() / "deploy",
            "plain": self.window.profile_plain,
            "local": self.window.local_values(),
            "target": target.get("name") if target else None,
        }

    def start_play(self):
        target = self.window.default_target()
        if target is None:
            self.window.error("Select a target")
            return
        module = self.window.play_addons.get(target.get("kind"))
        if module is not None:
            context = self.play_context()
            try:
                steps = module.play_steps(target.get("kind"), context)
            except ValueError as exc:
                self.window.error(exc)
                return
            question = module.confirm(target.get("kind"), context) \
                if hasattr(module, "confirm") else None
            key = f"confirmed/{target['name']}/{self.window.profile_path}"
            if question and not (
                self.window.settings and self.window.settings.value(key, False, bool)
            ):
                if QMessageBox.question(self.window, "TES3X", question) != \
                        QMessageBox.StandardButton.Yes:
                    return
                if self.window.settings is not None:
                    self.window.settings.setValue(key, True)
            self.window.run_steps(steps)
            return
        if target.get("kind") == "xemu" and "multiplayer" in self.window.patches.applied_patches:
            # a multiplayer build comes from the server through the manager, on the shared disk
            self.launch_manager_xemu(target)
            return
        ours = {str(s.pid) for s in self.plays.values() if s.pid}
        running = [pid for pid in running_xemu() if pid not in ours]
        if running and QMessageBox.question(
                self.window, "TES3X", f"xemu is already running (PID {', '.join(running)}). "
                "Start another session?") != QMessageBox.StandardButton.Yes:
            return
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        iso = self.play_iso()
        source = (["--iso", str(iso)] if iso
                  else ["--deploy", str(self.window.build_output() / "deploy"), "--keep-iso"])
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("TES3X_CONFIG", str(self.window.local_config_path()))
        name = f"play-{self.window.profile_path.stem}-{target['name']}-{stamp}"
        self.play_run = self.window.work_dir() / "build" / "xemu" / name
        self.start_play_process(
            "xemu",
            [
                name,
                *source,
                "--target",
                target["name"],
                "--config",
                str(self.window.local_config_path()),
                *(["--gdb"] if self.play_gdb else []),
                *(["--net-nat"] if self.play_network() else []),
                *(
                    ["--direct-engine"]
                    if not iso and "multiplayer" in self.window.patches.applied_patches
                    else []
                ),
                "--disk",
                str(self.play_disk()),
            ],
            f"Playing in {target['name']}…",
            environment,
        )

    def play_network(self):
        """Whether an xemu session needs its NIC. xemu's NAT gives the guest DHCP, routes UDP to
        any server and makes 10.0.2.2 this PC's loopback, where the in-game agent listens."""
        return bool(self.window.patches.applied_patches & {"agent", "multiplayer"})

    def play_disk(self):
        """The profile's own xemu disk, which keeps its saves between plays."""
        target = self.window.target_picker.currentData() or "default"
        return (
            self.window.work_dir()
            / "build"
            / "play"
            / self.window.profile_path.stem
            / target
            / "hdd.qcow2"
        )

    def reset_play_disk(self):
        if self.window.process is not None or self.play_process is not None:
            self.window.error("Stop running TES3X commands and this target's xemu before resetting "
                       "its saves")
            return
        disk = self.play_disk() if self.window.profile_path else None
        if disk is None or not disk.is_file():
            QMessageBox.information(self.window, "TES3X", "This profile has no xemu saves yet.")
            return
        answer = QMessageBox.question(
            self.window, "Reset xemu saves",
            f"Delete this profile's xemu disk and every save on it?\n\n{disk}")
        if answer == QMessageBox.StandardButton.Yes:
            # A save pool added to a kept disk stacks it on hdd-N.qcow2 layers.
            for layer in disk.parent.glob(f"{disk.stem}-*{disk.suffix}"):
                layer.unlink()
            disk.unlink()
            self.window.statusBar().showMessage(
                "Deleted the xemu saves; the next Play starts clean", 5000
            )

    def start_play_process(self, program, arguments, message, environment=None):
        """Start the long-lived xemu wrapper without occupying the command process slot."""
        self.window.output.clear()
        process = QProcess(self.window)
        process.setWorkingDirectory(str(self.window.work_dir()))
        if environment is not None:
            process.setProcessEnvironment(environment)
        process.setProgram(sys.executable)
        process.setArguments(launch(program, arguments))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        session = self.play_session(True)
        session.process, session.pid, session.output = process, None, ""
        process.readyReadStandardOutput.connect(lambda: self.append_play_output(session))
        process.finished.connect(lambda code, status: self.play_finished(session, code, status))
        self.window.statusBar().spinner.start()
        if session.target:
            self.window.set_target_runtime(session.target, process="starting")
        self.window.refresh_play_menu()
        process.start()
        self.window.statusBar().showMessage(message)

    def append_play_output(self, session=None):
        session = session or self.play_session()
        if session is None or session.process is None:
            return
        text = bytes(session.process.readAllStandardOutput()).decode(errors="replace")
        self.window.output.moveCursor(QTextCursor.MoveOperation.End)
        self.window.output.insertPlainText(
            f"[{session.target}] {text}" if len(self.plays) > 1 else text
        )
        session.output = (session.output + text)[-512:]
        match = re.search(r"xemu: started, pid (\d+)", session.output)
        if match and session.pid is None:
            session.pid = int(match.group(1))
            if session.target:
                self.window.set_target_runtime(session.target, process="running")
            self.window.statusBar().spinner.stop()
            port = session.run / "gdb.port" if self.play_gdb and session.run else None
            if port is not None and port.is_file():
                value = port.read_text().strip()
                attach = f'gdb -ex "target remote 127.0.0.1:{value}"'
                self.window.output.insertPlainText(f"\nGDB stub on 127.0.0.1:{value}; attach with "
                                            f"{attach}\n")
                self.window.statusBar().showMessage(f"Playing · GDB :{value}")
            else:
                self.window.statusBar().showMessage(
                    f"Playing in {session.target} · PID {session.pid}"
                )
            self.window.refresh_play_menu()

    def stop_play(self):
        session = self.play_session()
        if session is None or session.process is None:
            return
        if session.pid is None:
            self.window.error("xemu is still starting; wait for its PID before stopping it")
            return
        try:
            os.kill(session.pid, signal.SIGTERM)
        except OSError as exc:
            self.window.error(f"Could not stop xemu PID {session.pid}: {exc}")
            return
        self.window.statusBar().showMessage(f"Stopping {session.target} (PID {session.pid}); "
                                     "recovering its log…")
        if session.target:
            self.window.set_target_runtime(session.target, process="stopping")
        self.window.action_play.setEnabled(False)
        self.window.action_stop.setEnabled(False)

    def play_finished(self, session, code, _status):
        self.append_play_output(session)
        if session.target:
            self.window.set_target_runtime(session.target, process="stopped")
        if self.plays.get(session.target) is session:
            del self.plays[session.target]
        self.window.statusBar().spinner.stop()
        self.window.statusBar().showMessage(
            f"xemu session exited {code}; log recovery finished", 5000
        )
        self.window.refresh_play_menu()
        self.window.update_build_state()
        self.window.saves.refresh_saves(False)

    def refresh_all_targets(self):
        """Probe every Xbox target, so each one's dot follows it, not only the selected one's."""
        for name, target in tes3x_targets.targets(self.window.local_values()).items():
            if target.get("kind") == "xbox":
                self.refresh_ftp_status(name)

    def refresh_ftp_status(self, name=None):
        name = name if isinstance(name, str) else self.window.target_picker.currentData()
        if name in self.ftp_probes:
            return
        config = self.window.local_config_path()
        try:
            local = tomllib.loads(config.read_text(encoding="utf-8")) if config.is_file() else {}
        except (OSError, tomllib.TOMLDecodeError) as exc:
            if name:
                self.window.set_target_runtime(name, ftp="offline", dashboard="unknown",
                                        ftp_detail=f"Configuration error: {exc}")
            return
        try:
            target = tes3x_targets.resolve(local, name, kind="xbox")
        except tes3x_targets.TargetError:
            target = None
        host = target.get("host") if target else None
        if not host:
            if target:
                self.window.set_target_runtime(target["name"], ftp="offline", dashboard="unknown",
                                        ftp_detail="Set the Xbox host in Setup")
            return
        process = QProcess(self.window)
        process.setWorkingDirectory(str(self.window.work_dir()))
        process.setProgram(sys.executable)
        process.setArguments(["-m", "tes3x", "fetch", "E:/", "--list",
                              "--config", str(config), "--target", target["name"]])
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        name = target["name"]
        process.finished.connect(lambda code, status, name=name:
                                 self.ftp_probe_finished(name, code, status))
        self.ftp_probes[name] = process
        self.window.set_target_runtime(name, ftp="checking", dashboard="unknown")
        process.start()

    def ftp_probe_finished(self, name, code, _status):
        process = self.ftp_probes.pop(name, None)
        output = bytes(process.readAllStandardOutput()).decode(errors="replace").strip() \
            if process is not None else ""
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        self.window.set_target_runtime(name, ftp="connected" if code == 0 else "offline",
                                dashboard="checking" if code == 0 else "unknown",
                                ftp_detail=ftp_error(lines[-1]) if code != 0 and lines
                                else None if code == 0 else f"FTP probe exited {code}")
        if code != 0:
            self.window.target_drive_tips.pop(name, None)
        else:
            self.refresh_dashboard_status(name)

    def dashboard_status_command(self):
        """(script, arguments, expected version) for the built-in dashboard agent."""
        try:
            module = addon_registry().load("console")
        except ImportError:
            return None
        return getattr(module, "AGENT_STATUS", None)

    def refresh_dashboard_status(self, name=None):
        command = self.dashboard_status_command()
        name = name or self.window.target_picker.currentData()
        if command is None or not name or name in self.agent_probes:
            if command is None and name:
                self.window.set_target_runtime(name, dashboard="unknown")
            return
        script, arguments, expected = command
        process = QProcess(self.window)
        process.setWorkingDirectory(str(self.window.work_dir()))
        process.setProgram(sys.executable)
        process.setArguments(
            [
                str(script),
                *arguments,
                "--config",
                str(self.window.local_config_path()),
                "--target",
                name,
            ]
        )
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.finished.connect(lambda code, status, name=name, expected=expected:
                                 self.dashboard_probe_finished(name, expected, code, status))
        self.agent_probes[name] = process
        self.window.set_target_runtime(name, dashboard="checking")
        process.start()

    def dashboard_probe_finished(self, name, expected, code, _status):
        process = self.agent_probes.pop(name, None)
        output = bytes(process.readAllStandardOutput()).decode(errors="replace").strip() \
            if process is not None else ""
        state, detail = dashboard_agent_state(output, code, expected)
        self.window.set_target_runtime(name, dashboard=state, dashboard_detail=detail)
        self.window.targets_page.setup.agent_tested(name, state, detail)
        if state == "current" and name == self.window.target_picker.currentData():
            self.refresh_drive_status()
        if state == "current" and name == self.window.target_picker.currentData():
            self.refresh_drive_status()

    def drive_space_command(self):
        """(script, arguments) used by the built-in dashboard agent."""
        try:
            module = addon_registry().load("console")
        except ImportError:
            return None
        return getattr(module, "DRIVE_SPACE", None)

    def refresh_drive_status(self):
        """Free space on the Xbox's drives, from an add-on such as the dashboard agent."""
        command = self.drive_space_command()
        if command is None:
            return
        if self.drive_probe is not None:
            return
        script, arguments = command
        process = QProcess(self.window)
        process.setWorkingDirectory(str(self.window.work_dir()))
        process.setProgram(sys.executable)
        name = self.window.target_picker.currentData()
        process.setArguments(
            [
                str(script),
                *arguments,
                "--config",
                str(self.window.local_config_path()),
                *(["--target", name] if name else []),
            ]
        )
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.finished.connect(self.drive_probe_finished)
        self.drive_probe = process
        self.drive_probe_target = name
        process.start()

    def drive_probe_finished(self, code, _status):
        output = bytes(self.drive_probe.readAllStandardOutput()).decode(errors="replace").strip()
        self.drive_probe = None
        drives = parse_drives(output) if code == 0 else {}
        name = getattr(self, "drive_probe_target", self.window.target_picker.currentData())
        if not drives:
            self.window.target_drive_tips[name] = (
                "The dashboard agent did not report drive space: " + (output or f"exit {code}")
            )
            self.window.refresh_target_item(name)
            return
        deploy_drive = (self.window.xbox_destination() or "F:")[0].upper()
        lines = []
        for drive, (free, total) in sorted(drives.items()):
            if free is None:
                continue
            used = f", {100 - 100 * free // total}% used" if total else ""
            note = ""
            if drive == deploy_drive:
                note = " · deploy drive" + (", nearly full" if free < 2048 else "")
            lines.append(f"{drive}: {free / 1024:.1f} GB free of "
                         f"{(total or 0) / 1024:.1f} GB{used}{note}")
        self.window.target_drive_tips[name] = "\n".join(lines or ["No drive reported its space"])
        self.window.refresh_target_item(name)
