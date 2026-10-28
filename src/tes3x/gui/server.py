"""The Server workspace of the TES3X GUI: multiplayer worlds and host settings."""

import datetime
import queue
import socket
import sys
import threading
import tomlkit
import tomllib
import tes3x.content as tes3x_content
import tes3x.net as tes3x_net
import tes3x.net.worlds as tes3x_worlds
from PySide6.QtCore import QProcess, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
                               QSplitter, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)
from pathlib import Path
from tes3x.pipeline import PipelineError, resolve_patch_plan
from .widgets import heading, monospace


# key, label, kind, default, help. These are kept in the world's world.toml.
WORLD_FIELDS = (
    ('name', 'Server name', 'string', '', 'Display name shown in TES3X Dash'),
    ('description', 'Description', 'string', '', 'Optional description shown in TES3X Dash'),
    ("profile", "Build profile", "profile", "", "The build Xboxes' Dashes download on "
     "joining; consoles with another build are refused"),
    ("content_entry", "Library entry", "string", "", "Instead of a build profile: the content "
     "library entry this world requires; Dashes download it from the library"),
    ("content_revision", "Library revision", "string", "", "The entry's exact revision; empty "
     "takes the latest listed one at the next start and keeps it"),
    ("content_source", "Library address", "string", "", "[HOST]:PORT of a library on another "
     "PC; empty is this PC's library"),
    ("content_key", "Library key", "string", "", "The other PC's library key (tes3x content "
     "key)"),
    ("content_build", "Library build", "string", "", "The revision's build ID, for a library on "
     "another PC"),
    ("password_file", "Password file", "file", "", "Consoles with a new key must give this "
     "password"),
    ("max_players", "Max players", "int", 16, ""),
    ("respawn", "Respawn", "choice", "nearest", "Where a dead player wakes"),
    ("respawn_delay", "Respawn delay (s)", "float", 0.0, "0 keeps the server's default"),
    ("idle_timeout", "Idle timeout (s)", "float", 0.0, "Drop a silent console after this long; "
     "0 keeps the server's default (20). Consoles stop waiting at 15"),
    ("death_gold", "Death gold (%)", "int", 10, "Share of carried gold a death costs"),
    ("combat_leave", "Leave countdown in combat (s)", "int", 10, "How long a player in combat "
     "waits after pressing Leave; 0 leaves at once"),
    ("auto_level", "Level up without resting", "bool", False, "Off: the level-up screen comes at the end of a rest"),
    ("hour", "Start hour", "float", -1.0, "Game hour when a new session starts; -1 for the "
     "server's default"),
    ("timescale", "Timescale", "float", 0.0, "0 keeps the server's default"),
    ("save_every", "Save every (s)", "int", 0, "Ask joined consoles to save this often; 0 for "
     "the server's default"),
    ("welcome", "Welcome message", "string", "", "Shown in a box, to continue with A, once a player is in as their character (80 "
     "characters)"),
    ("load_state", "Load from server state", "bool", True, "A character loads as a New Game "
     "built from the server's kept state, so the player spawns once; topics and factions "
     "start fresh"),
    ("serve_retail", "Send retail files", "bool", False, "For testing: also serve the retail files "
     "and the XBEs themselves, so a console without a retail base can install"),
)


# These belong to this PC and stay in the local config's [server].
HOST_FIELDS = (
    ("port", "Game port", "int", tes3x_net.PORT, "UDP port consoles join"),
    ("admin_port", "Admin port", "int", tes3x_net.ADMIN_PORT, "Local UDP port for admin commands"),
    ("hosts", "DNS names", "lines", [], "NAME=ADDRESS, one per line, answered to consoles"),
    ("tunnels", "xemu tunnels", "text", "", "Tunnel ports for xemu guests, comma separated"),
    ("remote_admin", "Remote admin port", "int", 0, "Also take admin commands from other "
     f"machines on this UDP port ({tes3x_net.REMOTE_ADMIN_PORT} by convention); 0 keeps admin "
     "to this PC"),
    ("admin_password_file", "Admin password file", "file", "", "The remote admin password on "
     f"its first line, at least {tes3x_net.ADMIN_PASSWORD_MIN} characters"),
)


SERVER_FIELDS = WORLD_FIELDS + HOST_FIELDS


WORLD_KEYS = {key for key, *_ in WORLD_FIELDS}


DEFAULT_WORLD = "Default"


def multiplayer_profile(path):
    """Whether the profile at `path` builds with the multiplayer patch, which joining needs."""
    try:
        profile = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        return "multiplayer" in resolve_patch_plan(profile)["applied"]
    except (OSError, tomllib.TOMLDecodeError, PipelineError):
        return False


def server_arguments(values):
    """`tes3x net serve` arguments for the saved [server] settings."""
    args = ["serve"]
    for key, flag in (("world", "--world"), ("key", "--key"), ("password_file", "--password-file")):
        if values.get(key):
            args += [flag, str(values[key])]
    for key, flag in (("port", "--port"), ("admin_port", "--admin-port"),
                      ("max_players", "--max-players"), ("death_gold", "--death-gold"),
                      ("combat_leave", "--combat-leave")):
        if key in values:
            args += [flag, str(values[key])]
    if values.get("auto_level"):
        args += ["--level-up", "auto"]
    if values.get("respawn"):
        args += ["--respawn", values["respawn"]]
    for key, flag in (("respawn_delay", "--respawn-delay"), ("idle_timeout", "--idle-timeout"),
                      ("timescale", "--timescale"),
                      ("save_every", "--save-every")):
        if values.get(key):
            args += [flag, str(values[key])]
    if values.get("hour", -1) >= 0:
        args += ["--hour", str(values["hour"])]
    for host in values.get("hosts", []):
        args += ["--host", host]
    for port in values.get("tunnels", []):
        args += ["--tunnel", str(port)]
    if values.get("remote_admin"):
        args += ["--remote-admin", str(values["remote_admin"])]
    if values.get("admin_password_file"):
        args += ["--admin-password-file", str(values["admin_password_file"])]
    if values.get("build"):
        args += ["--build", str(values["build"])]
    if values.get("content_entry"):
        args += ["--content-entry", values["content_entry"]]
        for key in ("content_revision", "content_source", "content_key", "content_build"):
            if values.get(key):
                args += ["--" + key.replace("_", "-"), values[key]]
        if values.get("content_state"):
            args += ["--content-state", str(values["content_state"])]
    for key in ('name', 'description'):
        if values.get(key):
            args += ['--' + key, values[key]]
    if values.get("welcome"):
        args += ["--welcome", str(values["welcome"])]
    if values.get("load_state", True):
        args.append("--load-state")
    if values.get("serve_retail"):
        args += ["--serve-origin", "build", "--serve-origin", "retail", "--serve-origin", "xbe"]
    return args


def parse_clients(reply):
    """Rows of `admin list`: (id, player, state, key, mac, address)."""
    rows = []
    for line in reply.splitlines():
        head, _, rest = line.partition(": ")
        if not head.startswith("client ") or not rest:
            continue
        # the name comes last and may hold commas
        rest, _, name = rest.partition(", name ")
        fields = [part.strip() for part in rest.split(",")]
        state = fields[0]
        values = dict(part.split(" ", 1) for part in fields[1:] if " " in part)
        rows.append((head[7:], name.strip() or "-", state, values.get("key", "-"),
                     values.get("mac", "-"), values.get("address", "-")))
    return rows


class ServerPage(QWidget):
    """Run `tes3x net serve` locally, or reach one elsewhere through its remote admin port,
    and manage its players."""

    remote_answer = Signal(str, str, str)

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.process = None
        self.admin = None
        self.stopping = False
        self.content_autostart_pending = False
        self.remote = None  # the command queue of a connected remote server
        self.remote_pending = set()
        self.remote_answer.connect(self.remote_answered)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 4)
        top = QHBoxLayout()
        self.status = QLabel()
        self.status.setMinimumWidth(180)
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top.addWidget(self.status)
        self.local_bar = QWidget()
        local_bar = QHBoxLayout(self.local_bar)
        local_bar.setContentsMargins(0, 0, 0, 0)
        from tes3x.gui.common import menu_button
        local_bar.addWidget(QLabel("World"))
        self.world_picker = QComboBox()
        self.world_picker.setMinimumWidth(180)
        self.world_picker.activated.connect(self.world_activated)
        local_bar.addWidget(self.world_picker)
        self.world_actions = menu_button("Manage worlds", (
            ("New…", self.new_world), ("Duplicate…", self.duplicate_world),
            ("Rename…", self.rename_world), ("Delete", self.delete_world),
            ("Open folder", self.open_world_folder)))
        local_bar.addWidget(self.world_actions)
        local_bar.addSpacing(12)
        self.world = None
        self.start_button = QPushButton("Start")
        self.start_button.clicked.connect(self.start)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop)
        save = QPushButton("Save settings")
        save.clicked.connect(self.save)
        for button in (self.start_button, self.stop_button, save):
            local_bar.addWidget(button)
        top.addWidget(self.local_bar)
        self.remote_bar = QWidget()
        remote_bar = QHBoxLayout(self.remote_bar)
        remote_bar.setContentsMargins(0, 0, 0, 0)
        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self.toggle_remote)
        self.remote_stop = QPushButton("Stop server")
        self.remote_stop.setToolTip("Ask every console to save, then stop the remote server")
        self.remote_stop.clicked.connect(self.stop_remote_server)
        remote_bar.addWidget(self.connect_button)
        remote_bar.addWidget(self.remote_stop)
        top.addWidget(self.remote_bar)
        top.addStretch()
        layout.addLayout(top)
        split = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(split, 1)

        form_page = QWidget()
        form_layout = QVBoxLayout(form_page)
        form_layout.addWidget(heading("Multiplayer server"))
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Manage"))
        self.mode = QComboBox()
        self.mode.addItem("A server on this PC", "local")
        self.mode.addItem("A remote server", "remote")
        self.mode.currentIndexChanged.connect(self.mode_changed)
        mode_row.addWidget(self.mode, 1)
        form_layout.addLayout(mode_row)

        self.remote_box = QWidget()
        remote_layout = QVBoxLayout(self.remote_box)
        remote_layout.setContentsMargins(0, 0, 0, 0)
        about = QLabel("Connects to a server started with --remote-admin, such as one in Docker "
                       "or on another PC, using its admin password.")
        about.setWordWrap(True)
        remote_layout.addWidget(about)
        remote_form = QFormLayout()
        self.remote_address = QLineEdit()
        self.remote_address.setPlaceholderText(
            f"host or host:port (port {tes3x_net.REMOTE_ADMIN_PORT} by default)")
        self.remote_password = QLineEdit()
        self.remote_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.remember_password = QCheckBox("Remember the password in the local config")
        remote_form.addRow("Address", self.remote_address)
        remote_form.addRow("Admin password", self.remote_password)
        remote_form.addRow("", self.remember_password)
        remote_layout.addLayout(remote_form)
        form_layout.addWidget(self.remote_box)

        self.local_box = QWidget()
        local_layout = QVBoxLayout(self.local_box)
        local_layout.setContentsMargins(0, 0, 0, 0)
        self.inputs = {}
        for title, fields in (("World settings", WORLD_FIELDS), ("This PC", HOST_FIELDS)):
            local_layout.addWidget(heading(title, 1))
            form = QFormLayout()
            for key, label, kind, default, help_text in fields:
                widget = self.field(kind, default)
                if help_text:
                    widget.setToolTip(help_text)
                self.inputs[key] = (kind, widget)
                form.addRow(label, widget)
            local_layout.addLayout(form)
        form_layout.addWidget(self.local_box)
        form_layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_page)
        split.addWidget(scroll)

        right = QSplitter(Qt.Orientation.Vertical)
        self.lists = QTabWidget()
        players = QWidget()
        players_layout = QVBoxLayout(players)
        players_layout.setContentsMargins(0, 0, 0, 0)
        self.players = QTreeWidget()
        self.players.setHeaderLabels(["Client", "Player", "State", "Key", "MAC", "Address"])
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
        self.say_button = QPushButton("Message all")
        self.say_button.setToolTip("Show a message on every console")
        self.say_button.clicked.connect(lambda: self.message_players(False))
        self.tell_button = QPushButton("Message player")
        self.tell_button.setToolTip("Show a message on the selected player's console")
        self.tell_button.clicked.connect(lambda: self.message_players(True))
        for button in (self.kick_button, self.ban_button, self.save_button, self.bans_button,
                       self.say_button, self.tell_button):
            player_buttons.addWidget(button)
        player_buttons.addStretch()
        players_layout.addLayout(player_buttons)
        self.lists.addTab(players, "Players")

        characters = QWidget()
        characters_layout = QVBoxLayout(characters)
        characters_layout.setContentsMargins(0, 0, 0, 0)
        self.characters = QTreeWidget()
        self.characters.setHeaderLabels(["Character", "Race", "Class", "Level", "Last played",
                                         "State", "Key"])
        self.characters.setRootIsDecorated(False)
        self.characters.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.characters.itemSelectionChanged.connect(lambda: self.update_buttons())
        characters_layout.addWidget(self.characters, 1)
        character_buttons = QHBoxLayout()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.show_characters)
        self.delete_character_button = QPushButton("Delete")
        self.delete_character_button.clicked.connect(self.delete_character)
        self.character_folder_button = QPushButton("Open folder")
        self.character_folder_button.clicked.connect(self.open_character_folder)
        for button in (refresh, self.delete_character_button, self.character_folder_button):
            character_buttons.addWidget(button)
        character_buttons.addStretch()
        characters_layout.addLayout(character_buttons)
        self.characters_tab = self.lists.addTab(characters, "Characters")
        right.addWidget(self.lists)
        self.log = monospace(QPlainTextEdit())
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        right.addWidget(self.log)
        command_row = QWidget()
        command_layout = QHBoxLayout(command_row)
        command_layout.setContentsMargins(0, 0, 0, 0)
        self.command_entry = monospace(QLineEdit())
        self.command_entry.setPlaceholderText("Admin command (say TEXT, tell N TEXT, kick N, "
                                              "...); help lists them")
        self.command_entry.returnPressed.connect(self.send_typed)
        self.command_send = QPushButton("Send")
        self.command_send.clicked.connect(self.send_typed)
        command_layout.addWidget(self.command_entry, 1)
        command_layout.addWidget(self.command_send)
        right.addWidget(command_row)
        right.setStretchFactor(0, 0)
        right.setStretchFactor(1, 1)
        right.setStretchFactor(2, 0)
        right.setCollapsible(2, False)
        right.setSizes([260, 360, 34])
        split.addWidget(right)
        split.setSizes([420, 700])

        self.poll = QTimer(self)
        self.poll.setInterval(500)
        self.poll.timeout.connect(self.poll_admin)
        self.ticks = 0
        self.load()
        self.mode_changed()

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
        elif kind == "bool":
            widget = QCheckBox()
            widget.setChecked(bool(default))
        elif kind == "lines":
            widget = QPlainTextEdit()
            widget.setFixedHeight(64)
        elif kind == "profile":
            widget = QWidget()
            row = QHBoxLayout(widget)
            row.setContentsMargins(0, 0, 0, 0)
            widget.combo = QComboBox()
            widget.combo.addItem("None", "")
            for path in self.window.profile_files():
                if multiplayer_profile(path):
                    widget.combo.addItem(path.stem, str(path.resolve()))
            widget.combo.currentIndexChanged.connect(lambda *_: self.update_buttons())
            self.build_button = QPushButton("Build")
            self.build_button.setToolTip("Stage the profile; a running server serves the new "
                                         "build to Dashes that update")
            self.build_button.clicked.connect(lambda: self.build_profile())
            row.addWidget(widget.combo, 1)
            row.addWidget(self.build_button)
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
        chosen = (QFileDialog.getExistingDirectory(self, "Folder", start) if kind == "folder"
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
            elif kind == "bool":
                out[key] = widget.isChecked()
            elif kind == "string":
                if widget.text().strip():
                    out[key] = widget.text().strip()
            elif kind == "choice":
                out[key] = widget.currentText()
            elif kind == "profile":
                if widget.combo.currentData():
                    out[key] = widget.combo.currentData()
            elif kind == "lines":
                out[key] = [line.strip() for line in widget.toPlainText().splitlines()
                            if line.strip()]
            elif key == "tunnels":
                out[key] = [int(part) for part in widget.text().replace(" ", "").split(",")
                            if part.isdigit()]
        return out

    def remote_values(self):
        out = {"mode": self.mode.currentData()}
        if self.remote_address.text().strip():
            out["remote_address"] = self.remote_address.text().strip()
        if self.remember_password.isChecked() and self.remote_password.text():
            out["remote_password"] = self.remote_password.text()
        return out

    def worlds_root(self):
        return self.window.local_path("worlds") or self.window.work_dir() / "worlds"

    def world_folder(self):
        return tes3x_worlds.folder(self.worlds_root(), self.world) if self.world else None

    def load(self):
        saved = self.window.local_values().get("server", {})
        self.mode.setCurrentIndex(max(0, self.mode.findData(saved.get("mode", "local"))))
        self.remote_address.setText(str(saved.get("remote_address", "")))
        self.remote_password.setText(str(saved.get("remote_password", "")))
        self.remember_password.setChecked("remote_password" in saved)
        self.show_values({key: value for key, value in saved.items() if key not in WORLD_KEYS})
        self.refresh_worlds(self.first_world(saved))

    def first_world(self, saved):
        """The world to show: the saved one, else a Default world made from the settings this
        PC kept before worlds, with what an old world folder held."""
        root = self.worlds_root()
        names = tes3x_worlds.worlds(root)
        chosen = str(saved.get("world", ""))
        if chosen in names:
            return chosen
        if names:
            return names[0]
        settings = {key: value for key, value in saved.items() if key in WORLD_KEYS}
        try:
            if chosen and Path(chosen).is_dir():
                tes3x_worlds.adopt(root, DEFAULT_WORLD, chosen, settings)
                self.log.appendPlainText(f"copied world folder {chosen} into world "
                                         f"{DEFAULT_WORLD} in {root}")
            else:
                tes3x_worlds.create(root, DEFAULT_WORLD, settings)
        except (OSError, tes3x_worlds.WorldError) as exc:
            self.log.appendPlainText(f"could not make world {DEFAULT_WORLD} in {root}: {exc}")
            return None
        return DEFAULT_WORLD

    def refresh_worlds(self, select=None):
        names = tes3x_worlds.worlds(self.worlds_root())
        self.world_picker.blockSignals(True)
        self.world_picker.clear()
        self.world_picker.addItems(names)
        self.world = select if select in names else (names[0] if names else None)
        self.world_picker.setCurrentIndex(names.index(self.world) if self.world else -1)
        self.world_picker.blockSignals(False)
        self.load_world()

    def load_world(self):
        try:
            values = tes3x_worlds.load(self.worlds_root(), self.world) if self.world else {}
        except tes3x_worlds.WorldError as exc:
            self.log.appendPlainText(str(exc))
            values = {}
        self.show_values({key: values.get(key, default) for key, _, _, default, _ in WORLD_FIELDS})
        self.show_characters()
        self.update_buttons()

    def world_activated(self, index):
        name = self.world_picker.itemText(index)
        if name == self.world:
            return
        if not self.save():  # keep the edits made to the world being left
            self.world_picker.setCurrentIndex(self.world_picker.findText(self.world or ""))
            return
        self.world = name
        self.load_world()
        self.save()

    def ask_world_name(self, title, suggested):
        name, ok = QInputDialog.getText(self, title, "World name:", text=suggested)
        return name.strip() if ok and name.strip() else None

    def world_action(self, action, select):
        try:
            name = action()
        except (OSError, tes3x_worlds.WorldError) as exc:
            QMessageBox.critical(self, "TES3X", str(exc))
            return
        self.refresh_worlds(name if select else None)
        self.save()

    def new_world(self):
        name = self.ask_world_name("New world", "")
        if name and self.save():
            self.world_action(lambda: tes3x_worlds.create(self.worlds_root(), name, {}), True)

    def duplicate_world(self):
        if not self.world:
            return
        name = self.ask_world_name("Duplicate world", self.world + " copy")
        if not name or not self.save():
            return
        keep = QMessageBox.question(
            self, "TES3X", f"Copy {self.world}'s characters too? No copies its settings and "
            "world state only.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No |
            QMessageBox.StandardButton.Cancel)
        if keep == QMessageBox.StandardButton.Cancel:
            return
        source = self.world
        self.world_action(lambda: tes3x_worlds.duplicate(
            self.worlds_root(), source, name, keep == QMessageBox.StandardButton.Yes), True)

    def rename_world(self):
        if not self.world:
            return
        name = self.ask_world_name("Rename world", self.world)
        if name and name != self.world and self.save():
            source = self.world
            self.world_action(lambda: tes3x_worlds.rename(self.worlds_root(), source, name), True)

    def delete_world(self):
        if not self.world:
            return
        count = len(tes3x_worlds.characters(self.world_folder()))
        if QMessageBox.question(
                self, "TES3X", f"Delete world {self.world} and everything it keeps"
                + (f", including {count} character{'s' * (count != 1)}" if count else "")
                + "? This cannot be undone.") != QMessageBox.StandardButton.Yes:
            return
        name = self.world
        self.world_action(lambda: tes3x_worlds.delete(self.worlds_root(), name), False)

    def open_world_folder(self):
        folder = self.world_folder() or self.worlds_root()
        if folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def show_characters(self):
        selected = self.selected_character()
        self.characters.clear()
        for character in tes3x_worlds.characters(self.world_folder()) if self.world else []:
            played = datetime.datetime.fromtimestamp(character["played"])
            item = QTreeWidgetItem([
                character["name"], character["race"], character["class"],
                "?" if character["level"] is None else str(character["level"]),
                played.strftime("%Y-%m-%d %H:%M"), "dead" if character["dead"] else "alive",
                character["key"][:16]])
            item.setData(0, Qt.ItemDataRole.UserRole, str(character["folder"]))
            item.setToolTip(6, character["key"])
            self.characters.addTopLevelItem(item)
            if str(character["folder"]) == selected:
                item.setSelected(True)
        self.update_buttons()

    def selected_character(self):
        items = self.characters.selectedItems()
        return items[0].data(0, Qt.ItemDataRole.UserRole) if items else None

    def delete_character(self):
        folder = self.selected_character()
        if folder is None or self.process is not None:
            return
        name = self.characters.selectedItems()[0].text(0)
        if QMessageBox.question(self, "TES3X", f"Delete {name} from world {self.world}? Its "
                                "console starts a new character there next time. This cannot "
                                "be undone.") != QMessageBox.StandardButton.Yes:
            return
        try:
            tes3x_worlds.delete_character(self.world_folder(), folder)
        except (OSError, tes3x_worlds.WorldError) as exc:
            QMessageBox.critical(self, "TES3X", f"Could not delete {name}: {exc}")
        self.show_characters()

    def open_character_folder(self):
        folder = self.selected_character()
        if folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def showEvent(self, event):
        super().showEvent(event)
        # Settings may have moved the worlds folder.
        if self.process is None and [self.world_picker.itemText(i) for i in range(
                self.world_picker.count())] != tes3x_worlds.worlds(self.worlds_root()):
            self.refresh_worlds(self.world)
        elif self.process is None:
            self.show_characters()

    def show_values(self, saved):
        for key, (kind, widget) in self.inputs.items():
            if key not in saved:
                continue
            value = saved[key]
            if kind in ("folder", "file"):
                widget.line.setText(str(value))
            elif kind in ("int", "float"):
                widget.setValue(value)
            elif kind == "bool":
                widget.setChecked(bool(value))
            elif kind == "string":
                widget.setText(str(value))
            elif kind == "choice":
                widget.setCurrentText(str(value))
            elif kind == "profile":
                if widget.combo.findData(str(value)) < 0 and multiplayer_profile(value):
                    widget.combo.addItem(Path(value).stem, str(value))
                widget.combo.setCurrentIndex(max(0, widget.combo.findData(str(value))))
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
            values = self.values()
            local = {key: value for key, value in values.items() if key not in WORLD_KEYS}
            local.update(self.remote_values())
            if self.world:
                local["world"] = self.world
                tes3x_worlds.save(self.worlds_root(), self.world,
                                  {key: value for key, value in values.items()
                                   if key in WORLD_KEYS})
            for key, value in local.items():
                table[key] = value
            for key in [key for key in table if key not in local]:
                del table[key]
            path.write_text(tomlkit.dumps(document), encoding="utf-8", newline="")
        except (OSError, tomlkit.exceptions.ParseError) as exc:
            QMessageBox.critical(self, "TES3X", f"Could not save server settings: {exc}")
            return False
        return True

    def profile_deploy(self, profile):
        """The staged game folder the pipeline publishes for `profile`."""
        name = tomllib.loads(Path(profile).read_text(encoding="utf-8"))["profile"]["name"]
        root = self.window.local_path("build_root") or self.window.work_dir() / "build"
        return root / name / "deploy"

    def build_profile(self, then=None):
        profile = self.values().get("profile")
        if not profile or not self.save():
            return
        if self.window.process is not None:
            self.window.error("A TES3X command is already running")
            return
        config = self.window.local_config_path()
        target = self.window.target_picker.currentData()
        self.window.run_steps([("pipeline", [
            profile, *(["--config", str(config)] if config.is_file() else []),
            *(["--target", target] if target else [])],
            f"Building {Path(profile).stem} for the server…")], then=then)

    def start(self):
        if self.process is not None or not self.save():
            return
        values = self.values()
        if values.get("profile") and values.get("content_entry"):
            self.window.error("Choose a build profile or a library entry, not both")
            return
        if values.get("content_entry") and not values.get("content_source"):
            state = self.window.work_dir() / "content-state"
            try:
                rows = tes3x_content.read_publications(state).get(values["content_entry"], [])
            except (OSError, ValueError) as exc:
                self.window.error(f"Content library: {exc}")
                return
            wanted = values.get("content_revision")
            row = next((row for row in rows if row["revision"] == wanted), None) if wanted \
                else next((row for row in reversed(rows) if row["listed"]), None)
            if row is None:
                self.window.error(f"The content library has no revision "
                                  f"{wanted or '(listed)'} of {values['content_entry']}")
                return
            if not wanted:
                # keep it: a later publication must not change what the world requires
                self.inputs["content_revision"][1].setText(row["revision"])
                if not self.save():
                    return
                values["content_revision"] = row["revision"]
            values["content_state"] = state
        if values.get("profile"):
            try:
                build = self.profile_deploy(values["profile"])
            except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
                self.window.error(f"Build profile: {exc}")
                return
            if not (build / "tes3xbuild.json").is_file():
                if QMessageBox.question(self, "TES3X", f"{Path(values['profile']).stem} is not "
                                        "built. Build it, then start?") \
                        == QMessageBox.StandardButton.Yes:
                    self.build_profile(then=self.start)
                return
            values["build"] = build
        if self.world:
            values["world"] = self.world_folder()
            values["key"] = self.worlds_root() / tes3x_worlds.SERVER_KEY
        process = QProcess(self)
        process.setWorkingDirectory(str(self.window.work_dir()))
        process.setProgram(sys.executable)
        process.setArguments(["-u", "-m", "tes3x", "net", *server_arguments(values)])
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self.read_output)
        process.finished.connect(self.finished)
        process.errorOccurred.connect(self.process_error)
        self.process, self.stopping = process, False
        self.content_autostart_pending = self.window.content_page.with_multiplayer.isChecked()
        self.admin_port = values.get("admin_port", tes3x_net.ADMIN_PORT)
        self.admin = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.admin.bind(("127.0.0.1", 0))
        self.admin.setblocking(False)
        self.log.appendPlainText(f"$ tes3x net {' '.join(server_arguments(values))}")
        process.start()
        self.poll.start()
        self.set_status("Starting…")
        self.update_buttons()

    def stop(self):
        if self.process is None:
            return
        if not self.stopping:
            # A clean stop asks every console to save its character first.
            self.stopping = True
            self.content_autostart_pending = False
            self.send_admin("stop")
            self.set_status("Stopping: waiting for consoles to save…")
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
            if line.startswith("serving on "):
                self.server_started()

    def process_error(self, _error):
        if self.process is not None:
            self.log.appendPlainText(self.process.errorString())
            if self.process.error() == QProcess.ProcessError.FailedToStart:
                self.finished(-1, None)

    def server_started(self):
        if self.process is not None and self.status.text().startswith("Starting"):
            self.set_status("Running")
        if self.content_autostart_pending:
            self.content_autostart_pending = False
            if self.window.content_page.with_multiplayer.isChecked():
                self.window.content_page.start()

    def finished(self, code, _status):
        self.content_autostart_pending = False
        self.read_output()
        self.log.appendPlainText(f"server exited ({code})")
        self.process = None
        self.poll.stop()
        if self.admin is not None:
            self.admin.close()
            self.admin = None
        self.players.clear()
        self.set_status("Stopped")
        self.show_characters()

    STATUS_COLORS = (("Running", "#2e7d32"), ("Connected", "#2e7d32"),
                     ("Starting", "#b26a00"), ("Stopping", "#b26a00"),
                     ("Connecting", "#b26a00"),
                     ("Stopped", "#616161"), ("Not connected", "#616161"))

    def set_status(self, text):
        color = next((c for prefix, c in self.STATUS_COLORS if text.startswith(prefix)), "#c62828")
        self.status.setText(text)
        self.status.setStyleSheet(f"background: {color}; color: white; font-weight: bold; "
                                  "border-radius: 4px; padding: 4px 10px;")

    def mode_changed(self, *_args):
        remote = self.mode.currentData() == "remote"
        self.remote_box.setVisible(remote)
        self.local_box.setVisible(not remote)
        self.remote_bar.setVisible(remote)
        self.local_bar.setVisible(not remote)
        self.players.clear()
        self.set_status(("Connected" if self.remote else "Not connected") if remote else
                            ("Running" if self.process else "Stopped"))
        self.update_buttons()

    def toggle_remote(self):
        if self.remote is not None:
            self.disconnect_remote()
            return
        host, _, port = self.remote_address.text().strip().partition(":")
        password = self.remote_password.text().encode("utf-8")
        if not host or (port and not port.isdigit()):
            QMessageBox.warning(self, "TES3X", "Give the server's address as host or host:port")
            return
        if len(password) < tes3x_net.ADMIN_PASSWORD_MIN:
            QMessageBox.warning(self, "TES3X", "The admin password has at least "
                                f"{tes3x_net.ADMIN_PASSWORD_MIN} characters")
            return
        self.save()
        port = int(port or tes3x_net.REMOTE_ADMIN_PORT)
        jobs = queue.Queue()

        def work():
            secret = tes3x_net.admin_secret(password)
            while (line := jobs.get()) is not None:
                try:
                    reply = tes3x_net.remote_admin_request(host, port, secret, line)
                    self.remote_answer.emit(line, reply, "")
                except tes3x_net.RemoteAdminError as exc:
                    self.remote_answer.emit(line, "", str(exc))

        threading.Thread(target=work, daemon=True).start()
        self.remote, self.remote_pending = jobs, set()
        self.set_status(f"Connecting to {host}:{port}…")
        self.log.appendPlainText(f"remote admin {host}:{port}")
        self.send_admin("list")
        self.poll.start()
        self.update_buttons()

    def disconnect_remote(self):
        if self.remote is not None:
            self.remote.put(None)
        self.remote = None
        if self.process is None:
            self.poll.stop()
        self.players.clear()
        self.set_status("Not connected")
        self.update_buttons()

    def stop_remote_server(self):
        if self.remote is not None and QMessageBox.question(
                self, "TES3X", "Stop the remote server? It asks every console to save first.") \
                == QMessageBox.StandardButton.Yes:
            self.send_admin("stop")

    def remote_answered(self, line, reply, error):
        self.remote_pending.discard(line)
        if self.remote is None:
            return
        if error:
            self.set_status(f"Remote: {error}")
            if line != "list" or "refused" in error:
                self.log.appendPlainText(f"admin {line}: {error}")
            if "refused" in error:
                self.disconnect_remote()
                self.set_status(f"Remote: {error}")
            return
        self.set_status("Connected")
        self.admin_reply(reply)

    def admin_reply(self, reply):
        if reply.startswith("client ") or reply == "no clients":
            self.server_started()
            self.show_clients(parse_clients(reply))
        else:
            self.log.appendPlainText(f"admin: {reply}")

    def send_typed(self):
        line = self.command_entry.text().strip()
        self.command_entry.clear()
        if line:
            self.send_admin(line)

    def send_admin(self, line):
        if self.mode.currentData() == "remote":
            # One list at a time, so a slow or silent server does not pile them up.
            if self.remote is not None and not (line == "list" and line in self.remote_pending):
                self.remote_pending.add(line)
                self.remote.put(line)
                if line != "list":
                    self.log.appendPlainText(f"admin> {line}")
            return
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
        if self.mode.currentData() == "remote":
            self.ticks += 1
            if self.remote is not None and self.ticks % 6 == 1:
                self.send_admin("list")
            return
        if self.admin is None:
            return
        while True:
            try:
                reply = self.admin.recv(65536).decode("utf-8", "replace")
            except (BlockingIOError, ConnectionResetError):
                break
            except OSError:
                return
            self.admin_reply(reply)
        self.ticks += 1
        if self.ticks % 6 == 1 and not self.stopping:
            self.send_admin("list")
        if self.ticks % 20 == 0:
            self.show_characters()

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

    def message_players(self, one):
        client = self.selected_client() if one else None
        if one and client is None:
            return
        text, ok = QInputDialog.getText(
            self, "TES3X", f"Message for client {client}:" if one else "Message for everyone:")
        if ok and text.strip():
            self.send_admin(f"tell {client} {text.strip()}" if one else f"say {text.strip()}")

    def update_buttons(self):
        running = self.process is not None
        remote = self.mode.currentData() == "remote"
        connected = self.remote is not None
        self.mode.setEnabled(not running and not connected)
        self.world_picker.setEnabled(not running)
        self.world_actions.setEnabled(not running)
        self.lists.setTabVisible(self.characters_tab, not remote)
        chosen = self.selected_character() is not None
        self.delete_character_button.setEnabled(chosen and not running)
        self.delete_character_button.setToolTip(
            "Stop the server first: it keeps its characters in memory" if running else "")
        self.character_folder_button.setEnabled(chosen)
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.stop_button.setText("Force stop" if self.stopping else "Stop")
        for key, (kind, widget) in self.inputs.items():
            (widget.combo if kind == "profile" else widget).setEnabled(not running)
        self.build_button.setEnabled(bool(self.values().get("profile")))
        self.connect_button.setText("Disconnect" if connected else "Connect")
        self.remote_stop.setEnabled(connected)
        for widget in (self.remote_address, self.remote_password, self.remember_password):
            widget.setEnabled(not connected)
        live = connected if remote else running and not self.stopping
        for button in (self.kick_button, self.ban_button, self.save_button, self.bans_button,
                       self.say_button, self.tell_button, self.command_send):
            button.setEnabled(live)
        self.command_entry.setEnabled(live)

    def shutdown(self):
        """On closing the GUI: stop the server rather than orphan it."""
        self.disconnect_remote()
        if self.process is not None:
            self.process.kill()
            self.process.waitForFinished(3000)
