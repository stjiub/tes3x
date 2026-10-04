"""The Targets and Server workspaces of the TES3X GUI."""

import argparse
import datetime
import io
import json
from pathlib import Path, PureWindowsPath
import socket
import sys
import threading

import tomlkit
from PySide6.QtCore import QProcess, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSpinBox, QSplitter, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

import tes3x_deploy
import tes3x_diag
import tes3x_ftp
import tes3x_net
from tes3x_pipeline import MARKER as PIPELINE_MARKER

ROOT = Path(__file__).resolve().parents[1]
LOG_SUFFIXES = {".txt", ".log"}
CAPABILITY_REASONS = {
    "ftp": "the Xbox's FTP server is not answering",
    "pull_logs": "neither FTP nor the in-game agent is available",
    "dashboard_control": "the dashboard agent is not answering or is outdated",
    "commands": "no game with the in-game agent is connected",
    "agent_fetch": "no game with the in-game agent is connected",
    "installed_builds": "the Xbox's FTP server is not answering",
}


def heading(text, grow=4):
    label = QLabel(text)
    font = label.font()
    font.setPointSize(font.pointSize() + grow)
    font.setBold(True)
    label.setFont(font)
    return label


def monospace(widget):
    widget.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
    return widget


def gate(button, features, needed):
    """Enable a button when the target offers `needed`; otherwise say what is missing."""
    allowed = needed in features
    button.setEnabled(allowed)
    tip = button.property("baseTip") or ""
    button.setToolTip(tip if allowed else f"{tip}\nUnavailable: {CAPABILITY_REASONS[needed]}"
                      .strip())


def tip_button(text, tip, handler):
    button = QPushButton(text)
    button.setProperty("baseTip", tip)
    button.setToolTip(tip)
    button.clicked.connect(handler)
    return button


def log_catalog(work_dir):
    """Every pulled, fetched or recovered log under build/, newest first.

    Each entry: path, when (datetime), source, target (or None), profile (or None)."""
    build = Path(work_dir) / "build"
    entries = []

    def add(path, source, target=None, profile=None):
        try:
            when = datetime.datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            return
        entries.append({"path": path, "when": when, "source": source, "target": target,
                        "profile": profile})

    for meta_dir in sorted((build / "xbox-logs").glob("**/")):
        meta = {}
        try:
            meta = json.loads((meta_dir / "pull.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        for path in meta_dir.iterdir() if meta_dir.is_dir() else ():
            if path.is_file() and path.suffix.lower() in LOG_SUFFIXES:
                add(path, "pulled", meta.get("target"), meta.get("profile"))
    for path in sorted((build / "agent-fetch").glob("*/*/*")):
        if path.is_file() and path.suffix.lower() in LOG_SUFFIXES:
            add(path, "agent fetch", path.parent.parent.name)
    for path in sorted((build / "xemu").glob("*/tes3xlog.txt")):
        profile = None
        try:
            marker = json.loads((path.parent / "pipeline" / PIPELINE_MARKER)
                                .read_text(encoding="utf-8"))
            profile = marker.get("profile")
        except (OSError, ValueError):
            pass
        add(path, "xemu run", None, profile)
    entries.sort(key=lambda entry: entry["when"], reverse=True)
    return entries


def log_summary(text):
    """tes3x_diag's report of the latest session in a hook log."""
    stream = io.StringIO()
    tes3x_diag.report(tes3x_diag.parse_log(text), stream=stream)
    return stream.getvalue()


class TargetsPage(QWidget):
    """Runtime view of the selected Xbox or xemu target."""

    builds_listed = Signal(str, object, str)

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.name = None
        self.history, self.history_at = [], 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 4)
        header = QHBoxLayout()
        self.title = heading("No target")
        self.state = QLabel()
        header.addWidget(self.title)
        header.addSpacing(12)
        header.addWidget(self.state, 1)
        layout.addLayout(header)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.create_overview(), "Overview")
        self.tabs.addTab(self.create_console(), "Console")
        self.tabs.addTab(self.create_logs(), "Logs")
        self.tabs.addTab(self.create_builds(), "Builds")
        self.tabs.currentChanged.connect(self.tab_shown)
        layout.addWidget(self.tabs, 1)
        self.builds_listed.connect(self.show_builds)
        self.builds_busy = False

    # Overview

    def create_overview(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.fields = {}
        for key, label in (("kind", "Kind"), ("address", "Address"), ("state", "State"),
                           ("dashboard", "Dashboard"), ("game", "In game"),
                           ("heartbeat", "Heartbeat"), ("drives", "Drives")):
            value = QLabel()
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setWordWrap(True)
            self.fields[key] = value
            form.addRow(label, value)
        layout.addLayout(form)
        actions = QHBoxLayout()
        self.check_button = tip_button("Check connection", "Probe FTP and the dashboard agent",
                                       self.check_connection)
        self.pull_button = tip_button("Pull logs", "Copy E:\\tes3x* from the console",
                                      self.window.pull_logs)
        self.restart_button = tip_button("Restart dashboard", "Restart XBMC4Gamers on the Xbox",
                                         self.restart_dashboard)
        self.quit_button = tip_button("Quit to dashboard",
                                      "Ask the running game to return to the dashboard",
                                      self.quit_game)
        self.fetch_button = tip_button("Fetch file…", "Copy a file from the running game",
                                       self.fetch_file)
        for button in (self.check_button, self.pull_button, self.restart_button,
                       self.quit_button, self.fetch_button):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        layout.addStretch()
        return page

    def check_connection(self):
        self.window.refresh_ftp_status()

    def restart_dashboard(self):
        if QMessageBox.question(self, "TES3X", f"Restart the dashboard on {self.name}?") \
                != QMessageBox.StandardButton.Yes:
            return
        self.window.start_command(ROOT / "addons" / "console" / "console.py", [
            "restart", "--config", str(self.window.local_config_path()), "--target", self.name],
            f"Restarting the dashboard on {self.name}…")

    def quit_game(self):
        if QMessageBox.question(self, "TES3X", f"Quit the game on {self.name} to the dashboard?"
                                " Unsaved progress is lost.") != QMessageBox.StandardButton.Yes:
            return
        self.window.agent_reboot(self.name)

    def fetch_file(self):
        path, ok = QInputDialog.getText(self, "Fetch from the game", "Console path:",
                                        text="E:\\tes3xprof.bin")
        if ok and path.strip():
            self.window.agent_fetch(self.name, path.strip())

    # Console

    def create_console(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.console_view = monospace(QPlainTextEdit())
        self.console_view.setReadOnly(True)
        self.console_view.setMaximumBlockCount(5000)
        layout.addWidget(self.console_view, 1)
        row = QHBoxLayout()
        self.console_entry = monospace(QLineEdit())
        self.console_entry.setPlaceholderText(
            "Console command, such as player->getpos x or tes3xnet stat (Up/Down for history)")
        self.console_entry.setMaxLength(95)
        self.console_entry.returnPressed.connect(self.send_command)
        self.console_entry.installEventFilter(self)
        self.send_button = tip_button("Send", "Run the line in the game, as the console would",
                                      self.send_command)
        self.console_fetch = tip_button("Fetch file…", "Copy a file from the running game",
                                        self.fetch_file)
        self.console_quit = tip_button("Quit to dashboard",
                                       "Ask the running game to return to the dashboard",
                                       self.quit_game)
        row.addWidget(self.console_entry, 1)
        for button in (self.send_button, self.console_fetch, self.console_quit):
            row.addWidget(button)
        layout.addLayout(row)
        return page

    def eventFilter(self, widget, event):
        if widget is self.console_entry and event.type() == event.Type.KeyPress and \
                event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self.history:
            step = -1 if event.key() == Qt.Key.Key_Up else 1
            self.history_at = max(0, min(len(self.history), self.history_at + step))
            self.console_entry.setText(self.history[self.history_at]
                                       if self.history_at < len(self.history) else "")
            return True
        return super().eventFilter(widget, event)

    def send_command(self):
        line = self.console_entry.text().strip()
        if not line or not self.send_button.isEnabled():
            return
        if not self.history or self.history[-1] != line:
            self.history.append(line)
        self.history_at = len(self.history)
        self.console_entry.clear()
        self.console_view.appendPlainText(f"» {line}")
        self.window.agent_console(self.name, line)

    def append_lines(self, name, lines):
        if name == self.name:
            for line in lines:
                self.console_view.appendPlainText(line)

    # Logs

    def create_logs(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self.log_scope = QComboBox()
        self.log_scope.addItem("This target", "target")
        self.log_scope.addItem("This target and profile", "profile")
        self.log_scope.addItem("Everything", "all")
        self.log_scope.currentIndexChanged.connect(self.refresh_logs)
        self.log_pull = tip_button("Pull logs", "Copy E:\\tes3x* from the console",
                                   self.window.pull_logs)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_logs)
        self.log_summary = QCheckBox("Summary")
        self.log_summary.setToolTip("Crash, hang and session summary of a hook log")
        self.log_summary.toggled.connect(self.show_log)
        open_folder = QPushButton("Open folder")
        open_folder.clicked.connect(self.open_log_folder)
        row.addWidget(QLabel("Show"))
        row.addWidget(self.log_scope)
        row.addWidget(refresh)
        row.addWidget(self.log_pull)
        row.addStretch()
        row.addWidget(self.log_summary)
        row.addWidget(open_folder)
        layout.addLayout(row)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.log_list = QTreeWidget()
        self.log_list.setHeaderLabels(["When", "Source", "Target", "Profile", "File"])
        self.log_list.setRootIsDecorated(False)
        self.log_list.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.log_list.itemSelectionChanged.connect(self.show_log)
        self.log_view = monospace(QPlainTextEdit())
        self.log_view.setReadOnly(True)
        split.addWidget(self.log_list)
        split.addWidget(self.log_view)
        split.setSizes([420, 600])
        layout.addWidget(split, 1)
        return page

    def log_matches(self, entry, scope):
        if scope == "all":
            return True
        target = self.window.target_settings(self.name)
        if entry["source"] == "xemu run":
            mine = target.get("kind") == "xemu"
        else:
            mine = entry["target"] == self.name
        if scope == "profile":
            mine &= entry["profile"] in (None, self.window.profile_name())
        return mine

    def refresh_logs(self, *_args):
        selected = self.selected_log()
        scope = self.log_scope.currentData()
        self.log_list.clear()
        for entry in log_catalog(self.window.work_dir()):
            if not self.log_matches(entry, scope):
                continue
            item = QTreeWidgetItem([entry["when"].strftime("%Y-%m-%d %H:%M"), entry["source"],
                                    entry["target"] or "", entry["profile"] or "",
                                    entry["path"].name])
            item.setData(0, Qt.ItemDataRole.UserRole, str(entry["path"]))
            item.setToolTip(4, str(entry["path"]))
            self.log_list.addTopLevelItem(item)
            if selected and str(entry["path"]) == str(selected):
                item.setSelected(True)

    def selected_log(self):
        items = self.log_list.selectedItems()
        return Path(items[0].data(0, Qt.ItemDataRole.UserRole)) if items else None

    def show_log(self, *_args):
        path = self.selected_log()
        if path is None:
            self.log_view.clear()
            return
        try:
            text = path.read_text(encoding="latin-1")
        except OSError as exc:
            self.log_view.setPlainText(str(exc))
            return
        self.log_view.setPlainText(log_summary(text) if self.log_summary.isChecked() else text)

    def open_log_folder(self):
        path = self.selected_log()
        folder = path.parent if path else Path(self.window.work_dir()) / "build"
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # Builds

    def create_builds(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self.builds_note = QLabel()
        self.builds_note.setWordWrap(True)
        self.builds_refresh = tip_button("Refresh", "List the game folders on the console",
                                         self.refresh_builds)
        row.addWidget(self.builds_note, 1)
        row.addWidget(self.builds_refresh)
        layout.addLayout(row)
        self.builds_list = QTreeWidget()
        self.builds_list.setHeaderLabels(["Folder", "Profile", "Save pool", "Deployed"])
        self.builds_list.setRootIsDecorated(False)
        self.builds_list.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.builds_list, 1)
        return page

    def refresh_builds(self):
        target = self.window.target_settings(self.name)
        if target.get("kind") != "xbox" or self.builds_busy:
            return
        games_root = target.get("games_root")
        if not games_root:
            self.builds_note.setText("This target has no games folder configured.")
            return
        self.builds_busy = True
        self.builds_note.setText(f"Listing {games_root}…")
        name = self.name
        args = argparse.Namespace(host=target.get("host"), port=target.get("port", 21),
                                  user=target.get("user", tes3x_ftp.DEFAULT_USER),
                                  password=target.get("password", tes3x_ftp.DEFAULT_PASSWORD))

        def work():
            try:
                ftp = tes3x_ftp.connect(args)
                try:
                    builds = tes3x_deploy.installed_builds(ftp, games_root)
                finally:
                    ftp.quit()
                self.builds_listed.emit(name, builds, "")
            except (OSError, EOFError) + tes3x_deploy.ftplib.all_errors as exc:
                self.builds_listed.emit(name, None, str(exc))

        threading.Thread(target=work, daemon=True).start()

    def show_builds(self, name, builds, error):
        self.builds_busy = False
        if error or builds is None:
            self.window.target_builds.pop(name, None)
            if name == self.name:
                self.builds_note.setText(f"Could not list the console's games: {error}")
            return
        self.window.target_builds[name] = builds
        if name == self.name:
            self.populate_builds()

    def populate_builds(self):
        self.builds_list.clear()
        target = self.window.target_settings(self.name)
        if target.get("kind") != "xbox":
            self.builds_note.setText("xemu boots the open profile's build directly; its "
                                     "recovered run logs are under Logs.")
            return
        builds = self.window.target_builds.get(self.name)
        if builds is None:
            self.builds_note.setText(f"Game folders under {target.get('games_root', '?')} "
                                     "appear here once listed.")
            return
        mine = self.window.profile_name()
        tes3x_folders = 0
        for folder, record in sorted(builds.items(), key=lambda item: item[0].casefold()):
            if record is None:
                continue
            tes3x_folders += 1
            item = QTreeWidgetItem([folder, record.get("profile") or "unknown",
                                    record.get("save_pool") or "", record.get("deployed") or ""])
            if mine and record.get("profile") == mine:
                font = item.font(0)
                font.setBold(True)
                for column in range(4):
                    item.setFont(column, font)
            self.builds_list.addTopLevelItem(item)
        other = len(builds) - tes3x_folders
        self.builds_note.setText(
            f"{tes3x_folders} TES3X build{'s' if tes3x_folders != 1 else ''} under "
            f"{target.get('games_root')}" + (f"; {other} other folder{'s' if other != 1 else ''}"
                                             " not deployed by TES3X" if other else ""))

    # State

    def set_target(self, name):
        changed = name != self.name
        self.name = name
        if changed:
            self.console_view.setPlainText("\n".join(self.window.agent_logs.get(name, [])))
            self.console_view.moveCursor(self.console_view.textCursor().MoveOperation.End)
            self.populate_builds()
            self.refresh_logs()
        self.refresh()

    def tab_shown(self, index):
        if self.tabs.widget(index) is self.tabs.widget(2):
            self.refresh_logs()
        elif self.tabs.widget(index) is self.tabs.widget(3) and \
                self.window.target_builds.get(self.name) is None and \
                "installed_builds" in self.window.target_features(self.name):
            self.refresh_builds()

    def refresh(self):
        name = self.name
        target = self.window.target_settings(name)
        runtime = self.window.target_runtime.get(name, {})
        features = self.window.target_features(name)
        kind = target.get("kind", "xbox")
        label = self.window.target_label(name)
        self.title.setText(name or "No target")
        self.state.setText(label)
        self.fields["kind"].setText("Xbox" if kind == "xbox" else "xemu")
        if kind == "xbox":
            self.fields["address"].setText(f"{target.get('host') or 'not configured'}"
                                           f" · {target.get('ram', 64)} MB")
        else:
            self.fields["address"].setText(f"local emulator · {target.get('ram', 64)} MB")
        self.fields["state"].setText(label)
        self.fields["dashboard"].setText(runtime.get("dashboard_detail", "") if kind == "xbox"
                                         else "—")
        self.fields["game"].setText(runtime.get("game_detail", "Not connected"))
        beat = runtime.get("heartbeat")
        if beat and runtime.get("game") == "connected":
            self.fields["heartbeat"].setText(
                f"{beat['frame_us'] / 1000:.1f} ms frame · {beat['free_kb']:,} KB free · "
                f"{beat['dropped']} log lines dropped")
        else:
            self.fields["heartbeat"].setText("—")
        self.fields["drives"].setText(self.window.target_drive_tips.get(name, "—"))
        xbox = kind == "xbox"
        for button in (self.check_button, self.restart_button, self.builds_refresh):
            button.setVisible(xbox)
        self.check_button.setEnabled(xbox and self.window.process is None)
        busy = self.window.process is not None
        gate(self.pull_button, features, "pull_logs")
        gate(self.log_pull, features, "pull_logs")
        gate(self.restart_button, features, "dashboard_control")
        gate(self.builds_refresh, features, "installed_builds")
        for button in (self.pull_button, self.log_pull, self.restart_button):
            button.setEnabled(button.isEnabled() and not busy)
        for button in (self.quit_button, self.console_quit, self.send_button):
            gate(button, features, "commands")
        for button in (self.fetch_button, self.console_fetch):
            gate(button, features, "agent_fetch")
        self.console_entry.setEnabled("commands" in features)


SERVER_FIELDS = (
    # key, label, kind, default, help
    ("world", "World folder", "folder", "", "Keeps the server key, password, characters, "
     "saves and bans; empty keeps nothing between runs"),
    ("port", "Game port", "int", tes3x_net.PORT, "UDP port consoles join"),
    ("admin_port", "Admin port", "int", tes3x_net.ADMIN_PORT, "Local UDP port for admin commands"),
    ("password_file", "Password file", "file", "", "Consoles with a new key must give this "
     "password"),
    ("max_players", "Max players", "int", 16, ""),
    ("respawn", "Respawn", "choice", "nearest", "Where a dead player wakes"),
    ("respawn_delay", "Respawn delay (s)", "float", 0.0, "0 keeps the server's default"),
    ("death_gold", "Death gold (%)", "int", 10, "Share of carried gold a death costs"),
    ("hour", "Start hour", "float", -1.0, "Game hour when a new session starts; -1 for the "
     "server's default"),
    ("timescale", "Timescale", "float", 0.0, "0 keeps the server's default"),
    ("save_every", "Save every (s)", "int", 0, "Ask joined consoles to save this often; 0 for "
     "the server's default"),
    ("hosts", "DNS names", "lines", [], "NAME=ADDRESS, one per line, answered to consoles"),
    ("tunnels", "xemu tunnels", "text", "", "Tunnel ports for xemu guests, comma separated"),
)


def server_arguments(values):
    """`tes3x_net.py serve` arguments for the saved [server] settings."""
    args = ["serve"]
    for key, flag in (("world", "--world"), ("password_file", "--password-file")):
        if values.get(key):
            args += [flag, str(values[key])]
    for key, flag in (("port", "--port"), ("admin_port", "--admin-port"),
                      ("max_players", "--max-players"), ("death_gold", "--death-gold")):
        if key in values:
            args += [flag, str(values[key])]
    if values.get("respawn"):
        args += ["--respawn", values["respawn"]]
    for key, flag in (("respawn_delay", "--respawn-delay"), ("timescale", "--timescale"),
                      ("save_every", "--save-every")):
        if values.get(key):
            args += [flag, str(values[key])]
    if values.get("hour", -1) >= 0:
        args += ["--hour", str(values["hour"])]
    for host in values.get("hosts", []):
        args += ["--host", host]
    for port in values.get("tunnels", []):
        args += ["--tunnel", str(port)]
    return args


def parse_clients(reply):
    """Rows of `admin list`: (id, state, key, mac, address)."""
    rows = []
    for line in reply.splitlines():
        head, _, rest = line.partition(": ")
        if not head.startswith("client ") or not rest:
            continue
        fields = [part.strip() for part in rest.split(",")]
        state = fields[0]
        values = dict(part.split(" ", 1) for part in fields[1:] if " " in part)
        rows.append((head[7:], state, values.get("key", "-"), values.get("mac", "-"),
                     values.get("address", "-")))
    return rows


class ServerPage(QWidget):
    """Run `tes3x_net.py serve` locally and manage its players."""

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.process = None
        self.running = {}
        self.admin = None
        self.stopping = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 4)
        split = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(split)

        form_page = QWidget()
        form_layout = QVBoxLayout(form_page)
        form_layout.addWidget(heading("Multiplayer server"))
        about = QLabel("Runs tes3x_net.py serve on this machine. Settings are kept in the "
                       "[server] table of the local config.")
        about.setWordWrap(True)
        form_layout.addWidget(about)
        form = QFormLayout()
        self.inputs = {}
        for key, label, kind, default, help_text in SERVER_FIELDS:
            widget = self.field(kind, default)
            if help_text:
                widget.setToolTip(help_text)
            self.inputs[key] = (kind, widget)
            form.addRow(label, widget)
        form_layout.addLayout(form)
        buttons = QHBoxLayout()
        self.start_button = QPushButton("Start")
        self.start_button.clicked.connect(self.start)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop)
        save = QPushButton("Save settings")
        save.clicked.connect(self.save)
        for button in (self.start_button, self.stop_button, save):
            buttons.addWidget(button)
        buttons.addStretch()
        form_layout.addLayout(buttons)
        self.status = QLabel("Stopped")
        form_layout.addWidget(self.status)
        form_layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_page)
        split.addWidget(scroll)

        right = QSplitter(Qt.Orientation.Vertical)
        players = QWidget()
        players_layout = QVBoxLayout(players)
        players_layout.setContentsMargins(0, 0, 0, 0)
        players_layout.addWidget(heading("Players", 1))
        self.players = QTreeWidget()
        self.players.setHeaderLabels(["Client", "State", "Key", "MAC", "Address"])
        self.players.setRootIsDecorated(False)
        self.players.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        players_layout.addWidget(self.players, 1)
        player_buttons = QHBoxLayout()
        self.kick_button = QPushButton("Kick")
        self.kick_button.clicked.connect(lambda: self.player_command("kick"))
        self.ban_button = QPushButton("Ban")
        self.ban_button.setToolTip("Ban the client's key and MAC address")
        self.ban_button.clicked.connect(lambda: self.player_command("ban"))
        self.save_button = QPushButton("Ask all to save")
        self.save_button.clicked.connect(lambda: self.send_admin("save"))
        self.bans_button = QPushButton("Bans")
        self.bans_button.clicked.connect(lambda: self.send_admin("bans"))
        for button in (self.kick_button, self.ban_button, self.save_button, self.bans_button):
            player_buttons.addWidget(button)
        player_buttons.addStretch()
        players_layout.addLayout(player_buttons)
        right.addWidget(players)
        self.log = monospace(QPlainTextEdit())
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        right.addWidget(self.log)
        right.setSizes([260, 400])
        split.addWidget(right)
        split.setSizes([420, 700])

        self.poll = QTimer(self)
        self.poll.setInterval(500)
        self.poll.timeout.connect(self.poll_admin)
        self.ticks = 0
        self.load()
        self.update_buttons()

    def field(self, kind, default):
        if kind == "int":
            widget = QSpinBox()
            widget.setRange(0, 65535)
            widget.setValue(default)
        elif kind == "float":
            widget = QDoubleSpinBox()
            widget.setRange(-1, 100000)
            widget.setDecimals(2)
            widget.setValue(default)
        elif kind == "choice":
            widget = QComboBox()
            widget.addItems(["nearest", "shrine", "temple"])
        elif kind == "lines":
            widget = QPlainTextEdit()
            widget.setFixedHeight(64)
        elif kind in ("folder", "file"):
            widget = QWidget()
            row = QHBoxLayout(widget)
            row.setContentsMargins(0, 0, 0, 0)
            line = QLineEdit()
            browse = QPushButton("…")
            browse.setFixedWidth(32)
            browse.clicked.connect(lambda _=False, line=line, kind=kind: self.browse(line, kind))
            row.addWidget(line, 1)
            row.addWidget(browse)
            widget.line = line
        else:
            widget = QLineEdit()
        return widget

    def browse(self, line, kind):
        start = line.text() or str(self.window.work_dir())
        chosen = (QFileDialog.getExistingDirectory(self, "World folder", start) if kind == "folder"
                  else QFileDialog.getOpenFileName(self, "Password file", start)[0])
        if chosen:
            line.setText(chosen)

    def values(self):
        out = {}
        for key, (kind, widget) in self.inputs.items():
            if kind in ("folder", "file"):
                text = widget.line.text().strip()
                if text:
                    out[key] = text
            elif kind in ("int", "float"):
                out[key] = widget.value()
            elif kind == "choice":
                out[key] = widget.currentText()
            elif kind == "lines":
                out[key] = [line.strip() for line in widget.toPlainText().splitlines()
                            if line.strip()]
            elif key == "tunnels":
                out[key] = [int(part) for part in widget.text().replace(" ", "").split(",")
                            if part.isdigit()]
        return out

    def load(self):
        saved = self.window.local_values().get("server", {})
        for key, (kind, widget) in self.inputs.items():
            if key not in saved:
                continue
            value = saved[key]
            if kind in ("folder", "file"):
                widget.line.setText(str(value))
            elif kind in ("int", "float"):
                widget.setValue(value)
            elif kind == "choice":
                widget.setCurrentText(str(value))
            elif kind == "lines":
                widget.setPlainText("\n".join(value))
            elif key == "tunnels":
                widget.setText(", ".join(str(port) for port in value))

    def save(self):
        path = self.window.local_config_path()
        try:
            document = tomlkit.parse(path.read_text(encoding="utf-8")) if path.is_file() \
                else tomlkit.document()
            table = document.get("server")
            if table is None:
                table = tomlkit.table()
                document["server"] = table
            for key, value in self.values().items():
                table[key] = value
            for key in [key for key in table if key not in self.values()]:
                del table[key]
            path.write_text(tomlkit.dumps(document), encoding="utf-8", newline="")
        except (OSError, tomlkit.exceptions.ParseError) as exc:
            QMessageBox.critical(self, "TES3X", f"Could not save server settings: {exc}")
            return False
        return True

    def start(self):
        if self.process is not None or not self.save():
            return
        values = self.values()
        process = QProcess(self)
        process.setWorkingDirectory(str(self.window.work_dir()))
        process.setProgram(sys.executable)
        process.setArguments(["-u", str(ROOT / "tools" / "tes3x_net.py"),
                              *server_arguments(values)])
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self.read_output)
        process.finished.connect(self.finished)
        self.process, self.stopping, self.running = process, False, values
        self.admin_port = values.get("admin_port", tes3x_net.ADMIN_PORT)
        self.admin = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.admin.bind(("127.0.0.1", 0))
        self.admin.setblocking(False)
        self.log.appendPlainText(f"$ tes3x_net.py {' '.join(server_arguments(values))}")
        process.start()
        self.poll.start()
        self.status.setText("Running")
        self.update_buttons()

    def stop(self):
        if self.process is None:
            return
        if not self.stopping:
            # A clean stop asks every console to save its character first.
            self.stopping = True
            self.send_admin("stop")
            self.status.setText("Stopping: waiting for consoles to save…")
            QTimer.singleShot(90_000, self.kill)
        else:
            self.kill()
        self.update_buttons()

    def kill(self):
        if self.process is not None:
            self.process.kill()

    def read_output(self):
        if self.process is None:
            return
        text = bytes(self.process.readAllStandardOutput()).decode("utf-8", "replace")
        for line in text.splitlines():
            self.log.appendPlainText(line)

    def finished(self, code, _status):
        self.read_output()
        self.log.appendPlainText(f"server exited ({code})")
        self.process = None
        self.poll.stop()
        if self.admin is not None:
            self.admin.close()
            self.admin = None
        self.players.clear()
        self.status.setText("Stopped")
        self.update_buttons()

    def send_admin(self, line):
        if self.admin is None:
            return
        try:
            self.admin.sendto(line.encode("utf-8"), ("127.0.0.1", self.admin_port))
        except OSError as exc:
            self.log.appendPlainText(f"admin: {exc}")
            return
        if line != "list":
            self.log.appendPlainText(f"admin> {line}")

    def poll_admin(self):
        if self.admin is None:
            return
        while True:
            try:
                reply = self.admin.recv(65536).decode("utf-8", "replace")
            except (BlockingIOError, ConnectionResetError):
                break
            except OSError:
                return
            if reply.startswith("client ") or reply == "no clients":
                self.show_clients(parse_clients(reply))
            else:
                self.log.appendPlainText(f"admin: {reply}")
        self.ticks += 1
        if self.ticks % 6 == 1 and not self.stopping:
            self.send_admin("list")

    def show_clients(self, rows):
        selected = self.selected_client()
        self.players.clear()
        for row in rows:
            item = QTreeWidgetItem(list(row))
            self.players.addTopLevelItem(item)
            if row[0] == selected:
                item.setSelected(True)
        self.update_buttons()

    def selected_client(self):
        items = self.players.selectedItems()
        return items[0].text(0) if items else None

    def player_command(self, verb):
        client = self.selected_client()
        if client is None:
            return
        question = {"kick": f"Kick client {client}?",
                    "ban": f"Ban client {client}'s key and MAC address?"}[verb]
        if QMessageBox.question(self, "TES3X", question) == QMessageBox.StandardButton.Yes:
            self.send_admin(f"{verb} {client}")

    def update_buttons(self):
        running = self.process is not None
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.stop_button.setText("Force stop" if self.stopping else "Stop")
        for key, (_, widget) in self.inputs.items():
            widget.setEnabled(not running)
        for button in (self.kick_button, self.ban_button, self.save_button, self.bans_button):
            button.setEnabled(running and not self.stopping)

    def shutdown(self):
        """On closing the GUI: stop the server rather than orphan it."""
        if self.process is not None:
            self.process.kill()
            self.process.waitForFinished(3000)


def fetch_destination(work_dir, target, path):
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path(work_dir) / "build" / "agent-fetch" / target / stamp / PureWindowsPath(path).name

