"""The Targets and Server workspaces of the TES3X GUI."""

import argparse
import datetime
import ftplib
import io
import json
from pathlib import Path, PureWindowsPath
import queue
import re
import socket
import sys
import threading
import tomllib
import zipfile

import tomlkit
from PySide6.QtCore import QProcess, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFontDatabase
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
    QGroupBox, QInputDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSpinBox, QSplitter, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

import tes3x_deploy
import tes3x_diag
import tes3x_ftp
import tes3x_net
import tes3x_targets
from tes3x_pipeline import (MARKER as PIPELINE_MARKER, PipelineError, resolve_patch_plan,
                            validate_local_config)
from tes3x_xemu_setup import download_xemu, find_files as find_xemu_files

ROOT = Path(__file__).resolve().parents[1]
LOG_SUFFIXES = {".txt", ".log"}
CAPABILITY_REASONS = {
    "ftp": "the Xbox's FTP server is not answering",
    "pull_logs": "neither FTP nor the in-game agent is available",
    "dashboard_control": "the dashboard agent is not answering or is outdated",
    "commands": "no game with the in-game agent is connected",
    "agent_fetch": "no game with the in-game agent is connected",
    "installed_builds": "the Xbox's FTP server is not answering",
    "install_dashboard_agent": "the Xbox's FTP server is not answering",
    "install_manager": "neither the Xbox's FTP server nor the manager is answering",
}
MANAGER_PROFILE = "manager"


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


def manager_version():
    """The console manager version this PC would install, or None."""
    try:
        import tes3x_manager
        return tes3x_manager.version()
    except (ImportError, OSError):
        return None


def log_summary(text):
    """tes3x_diag's report of the latest session in a hook log."""
    stream = io.StringIO()
    tes3x_diag.report(tes3x_diag.parse_log(text), stream=stream)
    return stream.getvalue()


class TargetsPage(QWidget):
    """Runtime view of the selected Xbox or xemu target."""

    builds_listed = Signal(str, object, str)
    build_removed = Signal(str, str, str)

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
        self.setup = TargetSetup(window)
        self.tabs.addTab(self.create_overview(), "Overview")
        self.tabs.addTab(self.setup, "Setup")
        self.setup.dirty_changed.connect(
            lambda dirty: self.tabs.setTabText(self.tabs.indexOf(self.setup),
                                               "Setup *" if dirty else "Setup"))
        self.tabs.addTab(self.create_console(), "Console")
        self.logs_page = self.create_logs()
        self.tabs.addTab(self.logs_page, "Logs")
        self.builds_page = self.create_builds()
        self.tabs.addTab(self.builds_page, "Builds")
        self.tabs.currentChanged.connect(self.tab_shown)
        layout.addWidget(self.tabs, 1)
        self.builds_listed.connect(self.show_builds)
        self.build_removed.connect(self.removed_build)
        self.builds_busy = False
        self.builds_tried = set()

    # Overview

    def create_overview(self):
        """One section per thing that can answer on the target, each with its own actions."""
        page = QWidget()
        layout = QVBoxLayout(page)
        self.fields = {}
        self.field_labels = {}
        self.check_button = tip_button("Check connection", "Probe FTP and the dashboard agent",
                                       self.check_connection)
        self.pull_button = tip_button("Pull logs", "Copy E:\\tes3x* from the console",
                                      self.window.pull_logs)
        layout.addWidget(self.section("Connection", (("address", "Address"),
                                                     ("memory", "Memory"), ("ftp", "FTP"),
                                                     ("drives", "Drives")),
                                      self.check_button, self.pull_button))
        self.quit_button = tip_button("Quit to dashboard",
                                      "Ask the running game to return to the dashboard",
                                      self.quit_game)
        self.fetch_button = tip_button("Fetch file…", "Copy a file from the running game",
                                       self.fetch_file)
        layout.addWidget(self.section("Agent", (("game", "Status"),
                                                ("heartbeat", "Heartbeat")),
                                      self.quit_button, self.fetch_button))
        self.software = QWidget()
        software = QVBoxLayout(self.software)
        software.setContentsMargins(0, 0, 0, 0)
        self.agent_install = tip_button("Install / update", "Copy the dashboard agent to "
                                        "XBMC4Gamers over FTP", lambda: self.run_agent("install"))
        self.restart_button = tip_button("Restart dashboard", "Restart XBMC4Gamers on the Xbox",
                                         self.restart_dashboard)
        self.agent_remove = tip_button("Remove", "Remove the dashboard agent from XBMC4Gamers",
                                       lambda: self.run_agent("uninstall"))
        software.addWidget(self.section("Dashboard agent", (("dashboard", "Status"),
                                                            ("dashboard_version", "Version")),
                                        self.agent_install, self.restart_button,
                                        self.agent_remove))
        self.manager_install = tip_button("Install / update", "Stage the console manager and copy "
                                          "it to the games folder", self.window.install_manager)
        software.addWidget(self.section("Console manager", (("manager", "Status"),
                                                            ("manager_version", "Version")),
                                        self.manager_install))
        layout.addWidget(self.software)
        layout.addStretch()
        return page

    def section(self, title, rows, *buttons):
        box = QGroupBox(title)
        form = QFormLayout(box)
        for key, label in rows:
            value = QLabel()
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setWordWrap(True)
            self.fields[key] = value
            self.field_labels[key] = QLabel(label)
            form.addRow(self.field_labels[key], value)
        if buttons:
            row = QHBoxLayout()
            for button in buttons:
                row.addWidget(button)
            row.addStretch()
            form.addRow(row)
        return box

    def show_field(self, key, text):
        """Set a field, hiding its row when there is nothing to say."""
        self.fields[key].setText(text or "")
        self.fields[key].setVisible(bool(text))
        self.field_labels[key].setVisible(bool(text))

    def run_agent(self, command):
        name = self.name
        if command == "uninstall" and QMessageBox.question(
                self, "TES3X", f"Remove the dashboard agent from {name}?")                 != QMessageBox.StandardButton.Yes:
            return
        if self.window.process is not None:
            self.window.error("A TES3X command is already running")
            return
        verb = "Installing the dashboard agent on" if command == "install" else             "Removing the dashboard agent from"
        self.window.run_steps([(ROOT / "addons" / "console" / "console.py", [
            command, "--config", str(self.window.local_config_path()), "--target", name],
            f"{verb} {name}…")], then=lambda: self.window.refresh_dashboard_status(name))

    def manager_state(self, running):
        """(status, version) of the console manager on the target."""
        builds = self.window.target_builds.get(self.name)
        found = [record.get("version") or "?" for record in (builds or {}).values()
                 if record and record.get("profile") == MANAGER_PROFILE]
        if running:
            status = "Running"
        elif builds is None:
            status = "?"
        else:
            status = "Installed" if found else "Not installed"
        version = ", ".join(found) or "?"
        available = manager_version()
        if available and found and version != available:
            version += f" (PC: {available})"
        return status, version

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
        self.console_memory = QCheckBox("Log memory")
        self.console_memory.setToolTip(
            "Print the game's free memory and frame time here every few seconds")
        self.console_memory.toggled.connect(
            lambda on: setattr(self.window, "log_memory", on))
        row.addWidget(self.console_entry, 1)
        row.addWidget(self.console_memory)
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
        self.builds_remove = tip_button("Remove…", "Delete the selected folder from the console",
                                        self.remove_build)
        self.builds_remove.setEnabled(False)
        row.addWidget(self.builds_note, 1)
        row.addWidget(self.builds_refresh)
        row.addWidget(self.builds_remove)
        layout.addLayout(row)
        self.builds_list = QTreeWidget()
        self.builds_list.setHeaderLabels(["Folder", "Profile", "Save pool", "Deployed"])
        self.builds_list.setRootIsDecorated(False)
        self.builds_list.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.builds_list.itemSelectionChanged.connect(
            lambda: self.builds_remove.setEnabled(bool(self.builds_list.selectedItems())
                                                  and not self.builds_busy))
        layout.addWidget(self.builds_list, 1)
        return page

    def remove_build(self):
        items = self.builds_list.selectedItems()
        target = self.window.target_settings(self.name)
        if not items or self.builds_busy or target.get("kind") != "xbox":
            return
        folder = items[0].text(0)
        record = (self.window.target_builds.get(self.name) or {}).get(folder)
        games_root = target.get("games_root", "").replace("\\", "/").rstrip("/")
        path = f"{games_root}/{folder}"
        retail = target.get("retail_root", "").replace("\\", "/").rstrip("/")
        warning = ""
        if record is None:
            warning = ("\n\nTES3X did not deploy this folder; it may be a game installed by hand, "
                       "and its files cannot be restored from this PC.")
        elif retail and retail.casefold() == path.casefold():
            warning = ("\n\nThis is the target's retail base: overlay builds read their game "
                       "files from it, and stop working until it is installed again.")
        elif record.get("profile") == "manager":
            warning = "\n\nThis is the console manager."
        if QMessageBox.question(
                self, "Remove build", f"Delete {path} and everything in it from {self.name}?"
                + warning + "\n\nSaves are kept: they are under E:\\UDATA, not in the folder.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.builds_busy = True
        self.builds_remove.setEnabled(False)
        self.builds_note.setText(f"Removing {path}…")
        name = self.name
        args = argparse.Namespace(host=target.get("host"), port=target.get("port", 21),
                                  user=target.get("user", tes3x_ftp.DEFAULT_USER),
                                  password=target.get("password", tes3x_ftp.DEFAULT_PASSWORD))

        def work():
            try:
                ftp = tes3x_ftp.connect(args)
                try:
                    count = tes3x_deploy.remove_remote_folder(ftp, path)
                finally:
                    ftp.quit()
                self.build_removed.emit(name, path, f"{count} files deleted")
            except (OSError, EOFError) + tes3x_deploy.ftplib.all_errors as exc:
                self.build_removed.emit(name, path, f"failed: {exc}")

        threading.Thread(target=work, daemon=True).start()

    def removed_build(self, name, path, result):
        self.builds_busy = False
        self.window.statusBar().showMessage(f"{name}: {path}: {result}", 10000)
        self.window.target_builds.pop(name, None)
        if name == self.name:
            self.refresh_builds()

    def refresh_builds(self):
        target = self.window.target_settings(self.name)
        if target.get("kind") != "xbox" or self.builds_busy:
            return
        self.builds_tried.add(self.name)
        games_root = target.get("games_root")
        if not games_root:
            self.builds_note.setText("No games folder set")
            return
        self.builds_busy = True
        self.builds_note.setText("Listing…")
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
                self.builds_note.setText(f"Failed: {error}")
            return
        self.window.target_builds[name] = builds
        if name == self.name:
            self.populate_builds()
            self.refresh()

    def populate_builds(self):
        self.builds_list.clear()
        target = self.window.target_settings(self.name)
        if target.get("kind") != "xbox":
            self.builds_note.clear()
            return
        builds = self.window.target_builds.get(self.name)
        if builds is None:
            self.builds_note.clear()
            return
        mine = self.window.profile_name()
        for folder, record in sorted(builds.items(), key=lambda item: item[0].casefold()):
            if record is None:
                item = QTreeWidgetItem([folder, "not TES3X", "", ""])
                for column in range(4):
                    item.setForeground(column, self.palette().placeholderText())
                self.builds_list.addTopLevelItem(item)
                continue
            profile = record.get("profile") or "unknown"
            if record.get("version"):
                profile += " " + record["version"]
            item = QTreeWidgetItem([folder, profile,
                                    record.get("save_pool") or "", record.get("deployed") or ""])
            if mine and record.get("profile") == mine:
                font = item.font(0)
                font.setBold(True)
                for column in range(4):
                    item.setFont(column, font)
            self.builds_list.addTopLevelItem(item)
        self.builds_note.clear()

    # State

    def set_target(self, name):
        changed = name != self.name
        self.name = name
        self.setup.set_target(name)
        if changed:
            self.console_view.setPlainText("\n".join(self.window.agent_logs.get(name, [])))
            self.console_view.moveCursor(self.console_view.textCursor().MoveOperation.End)
            self.populate_builds()
            self.refresh_logs()
        self.refresh()

    def tab_shown(self, index):
        if self.tabs.widget(index) is self.logs_page:
            self.refresh_logs()
        elif self.tabs.widget(index) is self.builds_page and \
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
        xbox = kind == "xbox"
        self.title.setText(name or "No target")
        self.state.setText(("Xbox" if xbox else "xemu") + (f" · {label}" if name else ""))
        self.show_field("address", target.get("host") or "?" if xbox else "Local")
        self.show_field("memory", f"{target.get('ram', 64)} MB")
        if xbox:
            ftp = runtime.get("ftp")
            self.show_field("ftp", {"checking": "Checking…", "connected": "On",
                                    "offline": "Off"}.get(ftp, "?"))
            self.fields["ftp"].setToolTip(runtime.get("ftp_detail") or "")
            self.show_field("drives", self.window.target_drive_tips.get(name))
        else:
            self.show_field("ftp", None)
            self.show_field("drives", None)
        running = runtime.get("game") in {"connected", "stalled"}
        manager = running and runtime.get("game_kind") == "manager"
        self.show_field("game", ("Stalled" if runtime.get("game") == "stalled" else
                                 "Manager" if manager else "Game") if running else "Off")
        self.fields["game"].setToolTip(runtime.get("game_detail") or "")
        beat = runtime.get("heartbeat")
        self.show_field("heartbeat", f"{beat['frame_us'] / 1000:.1f} ms frame · "
                        f"{beat['free_kb']:,} KB free · {beat['dropped']} dropped"
                        if beat and running and not manager else None)
        for button in (self.check_button, self.builds_refresh):
            button.setVisible(xbox)
        self.software.setVisible(xbox and bool(name))
        detail = runtime.get("dashboard_detail") or ""
        version = re.search(r"\btes3xagent (\d+)\b", detail) or re.search(
            r"Dashboard agent (\d+);", detail)
        self.show_field("dashboard", {"current": "On", "outdated": "Outdated", "missing": "Off",
                                      "checking": "Checking…"}.get(runtime.get("dashboard"), "?"))
        self.fields["dashboard"].setToolTip(detail)
        self.show_field("dashboard_version", version.group(1) if version else "?")
        if name and xbox:
            status, installed = self.manager_state(manager)
            self.show_field("manager", status)
            self.show_field("manager_version", installed)
            if self.window.target_builds.get(name) is None and not self.builds_busy and                     name not in self.builds_tried and "installed_builds" in features:
                self.refresh_builds()
        self.check_button.setEnabled(xbox and self.window.process is None)
        busy = self.window.process is not None
        gate(self.pull_button, features, "pull_logs")
        gate(self.log_pull, features, "pull_logs")
        gate(self.restart_button, features, "dashboard_control")
        gate(self.builds_refresh, features, "installed_builds")
        gate(self.agent_install, features, "install_dashboard_agent")
        gate(self.agent_remove, features, "install_dashboard_agent")
        gate(self.manager_install, features, "install_manager")
        for button in (self.pull_button, self.log_pull, self.restart_button, self.agent_install,
                       self.agent_remove, self.manager_install):
            button.setEnabled(button.isEnabled() and not busy)
        for button in (self.quit_button, self.console_quit, self.send_button):
            gate(button, features, "commands")
        for button in (self.fetch_button, self.console_fetch):
            gate(button, features, "agent_fetch")
        self.console_entry.setEnabled("commands" in features)


TARGET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
XBOX_KEYS = {"kind", "host", "port", "user", "password", "games_root", "retail_root",
             "dashboard", "ram"}


def write_targets(document, values, use_targets, current=None):
    """Write {name: target} into a tomlkit document of tes3x.local.toml; without `use_targets`
    the first Xbox and xemu go back to the legacy [deploy] and [xemu] tables."""
    def update_table(section, entries):
        table = document.get(section)
        if table is None:
            table = tomlkit.table()
            document[section] = table
        for key, value in entries.items():
            if value == "":
                table.pop(key, None)
            else:
                table[key] = value

    if not use_targets:
        target = next((value for value in values.values() if value.get("kind") == "xbox"), {})
        legacy_dir = target.get("legacy_install_dir", "")
        games = target.get("games_root", "").rstrip("/\\")
        entries = {key: target.get(key, "") for key in
                   ("host", "port", "user", "password", "retail_root")}
        entries["remote_root"] = games + "/" + legacy_dir if games and legacy_dir else ""
        update_table("deploy", entries)
        xemu = next((value for value in values.values() if value.get("kind") == "xemu"), None)
        if xemu is not None:
            update_table("xemu", {key: xemu.get(key, "") for key in tes3x_targets.XEMU_KEYS})
        return

    document.pop("deploy", None)
    tables = document.get("targets")
    if tables is None:
        tables = tomlkit.table()
        document["targets"] = tables
    for name in list(tables):
        if name not in values:
            del tables[name]
    keys = (*sorted(XBOX_KEYS), *sorted(tes3x_targets.XEMU_KEYS))
    for name, value in values.items():
        target = tables.get(name)
        if target is None:
            target = tomlkit.table()
            tables[name] = target
        kind = value.get("kind", "xbox")
        clean = {key: value.get(key) for key in keys}
        kind_keys = {"kind", "ram", *tes3x_targets.XEMU_KEYS} if kind == "xemu" else XBOX_KEYS
        for key in list(target):
            if key in keys and (key not in kind_keys or clean[key] in ("", None, False)):
                del target[key]
        target["kind"] = kind
        for key, entry in clean.items():
            if key == "kind" or key not in kind_keys or entry in ("", None, False):
                continue
            if kind == "xbox" and key == "ram" and entry == 64:
                continue
            target[key] = entry
    if not tables:
        del document["targets"]
        document.pop("default_target", None)
    elif document.get("default_target") not in values:
        document["default_target"] = current if current in values else next(iter(values))


class TargetSetup(QWidget):
    """Edit the selected target's entry in tes3x.local.toml, and add or remove targets."""

    probe_done = Signal(bool, str)
    dirty_changed = Signal(bool)

    @property
    def dirty(self):
        return self._dirty

    @dirty.setter
    def dirty(self, value):
        self._dirty = value
        button = getattr(self, "revert_button", None)
        if button is not None:
            button.setEnabled(value)
        self.dirty_changed.emit(value)
    XEMU_FILES = (("exe", "Executable"), ("bootrom", "MCPX boot ROM"), ("bios", "BIOS"),
                  ("bios_128mb", "BIOS for 128 MB runs"), ("eeprom", "EEPROM"),
                  ("hdd", "Clean HDD image"))

    def __init__(self, window):
        super().__init__()
        self.window = window
        self._dirty = False
        self.name = None
        self.agent_testing = None
        self.carried = {}
        self.legacy = False
        self.loading = False
        self.dirty = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self.legacy_notice = QWidget()
        notice = QHBoxLayout(self.legacy_notice)
        notice.setContentsMargins(0, 0, 0, 6)
        note = QLabel("This PC's settings still use [deploy]. Convert it to an Xbox target when "
                      "you are ready.")
        note.setWordWrap(True)
        notice.addWidget(note, 1)
        notice.addWidget(tip_button("Convert", "Rewrite [deploy] as a named Xbox target",
                                    self.convert_legacy))
        layout.addWidget(self.legacy_notice)

        self.empty = QWidget()
        empty = QHBoxLayout(self.empty)
        empty.setContentsMargins(0, 0, 0, 0)
        empty.addWidget(QLabel("No targets yet. Add each Xbox and xemu setup you deploy to."))
        empty.addWidget(tip_button("Add target…", "Add an Xbox or xemu target", self.add_target))
        empty.addStretch()
        layout.addWidget(self.empty)

        self.editor = QWidget()
        editor = QVBoxLayout(self.editor)
        editor.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self.name_field = QLineEdit()
        self.kind = QComboBox()
        self.kind.addItem("Xbox", "xbox")
        self.kind.addItem("xemu", "xemu")
        self.ram = QComboBox()
        self.ram.addItem("64 MB", 64)
        self.ram.addItem("128 MB", 128)
        form.addRow("Name", self.name_field)
        form.addRow("Kind", self.kind)
        form.addRow("Memory", self.ram)
        editor.addLayout(form)
        self.form = form
        self.xbox_rows, self.xemu_rows = [], []
        self.host = QLineEdit()
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.user = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.games_root = QLineEdit()
        self.games_root.setPlaceholderText("F:/Games")
        self.retail_root = QLineEdit()
        self.dashboard = QLineEdit()
        self.dashboard.setPlaceholderText("Auto-detect")
        self.games_root.textChanged.connect(self.update_retail_placeholder)
        for label, field in (("Host", self.host), ("Port", self.port), ("User", self.user),
                             ("Password", self.password), ("Games root", self.games_root),
                             ("Shared retail base", self.retail_root),
                             ("Dashboard root", self.dashboard)):
            form.addRow(label, field)
            self.xbox_rows.append(field)
        probe = QHBoxLayout()
        self.test_button = tip_button("Test FTP", "Log in to this address over FTP with the "
                                      "details above", self.test_connection)
        self.agent_test_button = tip_button("Test agent", "Ask the dashboard agent on the saved "
                                            "target to answer", self.test_agent)
        self.test_status = QLabel()
        probe.addWidget(self.test_button)
        probe.addWidget(self.agent_test_button)
        probe.addWidget(self.test_status, 1)
        form.addRow("", probe)
        self.xbox_rows.append(probe)

        self.xemu_fields = {}
        for key, label in (("folder", "xemu folder"), *self.XEMU_FILES,
                           ("extract_xiso", "extract-xiso"), ("gdb", "GDB"),
                           ("template", "Config template")):
            field = QLineEdit()
            self.xemu_fields[key] = field
            row = QHBoxLayout()
            row.addWidget(field, 1)
            browse = QPushButton("Browse…")
            browse.clicked.connect(lambda _checked=False, key=key: self.browse_xemu(key))
            row.addWidget(browse)
            if key == "folder":
                row.addWidget(tip_button("Download xemu", "Download the latest xemu release "
                                         "into this folder", self.download_xemu))
                field.textChanged.connect(self.show_xemu_files)
            form.addRow(label, row)
            self.xemu_rows.append(row)
        layout.addWidget(self.editor)
        layout.addStretch()

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 4, 0, 0)
        self.save_button = tip_button("Save", "Write this target to this PC's settings",
                                      self.save)
        self.revert_button = tip_button("Discard", "Discard unsaved changes", self.revert)
        self.revert_button.setEnabled(False)
        self.save_status = QLabel()
        actions.addWidget(self.save_button)
        actions.addWidget(self.revert_button)
        actions.addWidget(self.save_status, 1)
        outer.addLayout(actions)

        for field in (self.name_field, self.host, self.user, self.password, self.games_root,
                      self.retail_root, self.dashboard, *self.xemu_fields.values()):
            field.textChanged.connect(self.changed)
        self.port.valueChanged.connect(self.changed)
        self.ram.currentIndexChanged.connect(self.changed)
        self.kind.currentIndexChanged.connect(self.kind_changed)
        self.probe_done.connect(self.probe_finished)
        self.show_target(None)

    # Reading and writing tes3x.local.toml

    def config_path(self):
        return Path(self.window.local_config_path())

    def read(self):
        path = self.config_path()
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        document = tomlkit.parse(text)
        return document, tomllib.loads(tomlkit.dumps(document))

    @staticmethod
    def all_values(plain):
        return {name: tes3x_targets.resolve(plain, name) for name in tes3x_targets.targets(plain)}

    def write(self, document, values, use_targets, selected):
        write_targets(document, values, use_targets, selected)
        text = tomlkit.dumps(document)
        validate_local_config(tomllib.loads(text))
        path = self.config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
        self.dirty = False
        self.name = None
        self.window.targets_changed(selected)

    def attempt(self, action, *args):
        try:
            action(*args)
            return True
        except (OSError, PipelineError, tomlkit.exceptions.ParseError,
                tomllib.TOMLDecodeError) as exc:
            QMessageBox.critical(self, "TES3X", f"Could not save the targets: {exc}")
            return False

    # The selected target

    def set_target(self, name):
        if name == self.name and not self.dirty:
            return
        if name != self.name and self.dirty and self.name:
            answer = QMessageBox.question(
                self, "TES3X", f"Save the changes to target {self.name}?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard)
            if answer == QMessageBox.StandardButton.Save and not self.save(select=False):
                return
        if name != self.name or not self.dirty:
            self.show_target(name)

    def show_target(self, name):
        try:
            _document, plain = self.read()
        except (OSError, tomlkit.exceptions.ParseError, tomllib.TOMLDecodeError):
            plain = {}
        self.legacy = bool(plain.get("deploy")) and not plain.get("targets")
        self.legacy_notice.setVisible(self.legacy)
        target = self.all_values(plain).get(name) if name else None
        self.empty.setVisible(not tes3x_targets.targets(plain))
        self.editor.setVisible(target is not None)
        self.save_button.setVisible(target is not None)
        self.revert_button.setVisible(target is not None)
        self.name = name if target is not None else None
        self.dirty = False
        self.save_status.clear()
        self.test_status.clear()
        if target is None:
            return
        self.carried = {key: target[key] for key in ("legacy", "legacy_install_dir",
                                                     "legacy_xemu") if key in target}
        self.loading = True
        self.name_field.setText(name)
        self.kind.setCurrentIndex(max(0, self.kind.findData(target.get("kind", "xbox"))))
        self.ram.setCurrentIndex(max(0, self.ram.findData(target.get("ram", 64))))
        self.host.setText(target.get("host", ""))
        self.port.setValue(target.get("port", 21))
        self.user.setText(target.get("user", "xbox"))
        self.password.setText(target.get("password", "xbox"))
        self.games_root.setText(target.get("games_root", ""))
        self.retail_root.setText(target.get("retail_root", ""))
        self.dashboard.setText(target.get("dashboard", ""))
        for key, field in self.xemu_fields.items():
            field.setText(str(target.get(key, "")))
        self.loading = False
        self.kind_changed()
        self.dirty = False
        self.save_status.clear()

    def changed(self, *_args):
        if not self.loading and self.name:
            self.dirty = True
            self.save_status.setText("Unsaved changes")

    def kind_changed(self, *_args):
        xbox = self.kind.currentData() == "xbox"
        for row in self.xbox_rows:
            self.form.setRowVisible(row, xbox)
        for row in self.xemu_rows:
            self.form.setRowVisible(row, not xbox)
        if not xbox:
            self.show_xemu_files()
        self.changed()

    def update_retail_placeholder(self, *_args):
        games = self.games_root.text().strip().rstrip("/\\")
        suggested = games + "/MorrowindRetail" if games else "F:/Games/MorrowindRetail"
        self.retail_root.setPlaceholderText(
            f"{suggested} (recommended for overlay profiles; set explicitly)")

    def target_data(self):
        return {
            **self.carried,
            "kind": self.kind.currentData(),
            "ram": self.ram.currentData(),
            "host": self.host.text().strip(),
            "port": self.port.value(),
            "user": self.user.text().strip(),
            "password": self.password.text(),
            "games_root": self.games_root.text().strip(),
            "retail_root": self.retail_root.text().strip(),
            "dashboard": self.dashboard.text().strip(),
            **{key: field.text().strip() for key, field in self.xemu_fields.items()},
        }

    def save(self, select=True):
        if not self.name:
            return False
        name = self.name_field.text().strip()
        try:
            document, plain = self.read()
        except (OSError, tomlkit.exceptions.ParseError, tomllib.TOMLDecodeError) as exc:
            QMessageBox.critical(self, "TES3X", f"Could not read the settings: {exc}")
            return False
        values = self.all_values(plain)
        if not TARGET_NAME.fullmatch(name):
            QMessageBox.warning(self, "TES3X", "Target names use letters, numbers, dot, "
                                "underscore and dash.")
            return False
        if name != self.name and name in values:
            QMessageBox.warning(self, "TES3X", f"Target {name} already exists.")
            return False
        data = self.target_data()
        values = {(name if key == self.name else key): (data if key == self.name else value)
                  for key, value in values.items()}
        if name != self.name and document.get("default_target") == self.name:
            document["default_target"] = name
        selected = name if select else self.window.target_picker.currentData()
        return self.attempt(self.write, document, values, not self.legacy, selected)

    def revert(self):
        self.show_target(self.name)

    def edit(self, action):
        """Apply `action(values)` to every target and save; it returns the target to select."""
        if self.dirty and QMessageBox.question(
                self, "TES3X", f"Discard the unsaved changes to {self.name}?") \
                != QMessageBox.StandardButton.Yes:
            return False
        try:
            document, plain = self.read()
        except (OSError, tomlkit.exceptions.ParseError, tomllib.TOMLDecodeError) as exc:
            QMessageBox.critical(self, "TES3X", f"Could not read the settings: {exc}")
            return False
        values = self.all_values(plain)
        selected = action(values)
        if selected is None:
            return False
        self.dirty = False
        return self.attempt(self.write, document, values, True, selected)

    def ask_name(self, title, suggested, values):
        name, ok = QInputDialog.getText(self, title, "Target name", text=suggested)
        name = name.strip()
        if not ok:
            return None
        if not TARGET_NAME.fullmatch(name) or name in values:
            QMessageBox.warning(self, "TES3X", "Choose a new target name using letters, "
                                "numbers, dot, underscore or dash.")
            return None
        return name

    def add_target(self, name=None):
        def add(values):
            chosen = name if isinstance(name, str) else self.ask_name("Add target", "", values)
            if chosen:
                values[chosen] = {"kind": "xbox", "ram": 64, "port": 21, "user": "xbox",
                                  "password": "xbox", "games_root": "F:/Games"}
            return chosen
        if not self.edit(add):
            return False
        self.window.show_target_setup()
        return True

    def duplicate_target(self, name=None):
        source = self.window.target_picker.currentData()
        if not source:
            return False

        def duplicate(values):
            chosen = name if isinstance(name, str) else self.ask_name(
                "Duplicate target", source + "-copy", values)
            if chosen and source in values:
                values[chosen] = {key: value for key, value in values[source].items()
                                  if not key.startswith("legacy")}
            return chosen
        return self.edit(duplicate)

    def remove_target(self):
        name = self.window.target_picker.currentData()
        if not name or QMessageBox.question(
                self, "TES3X", f"Remove target {name} from this PC's settings? Nothing on the "
                "Xbox or in the xemu folder is deleted.") != QMessageBox.StandardButton.Yes:
            return False

        def remove(values):
            values.pop(name, None)
            return next(iter(values), "")
        return self.edit(remove)

    def convert_legacy(self):
        return self.edit(lambda values: self.window.target_picker.currentData() or "")

    # Connection test and xemu files

    def test_connection(self):
        host = self.host.text().strip()
        if not host:
            self.test_status.setText("Set a host first")
            return
        port, user, password = self.port.value(), self.user.text().strip(), self.password.text()
        self.test_button.setEnabled(False)
        self.test_status.setText("Connecting…")

        def probe():
            try:
                ftp = ftplib.FTP(encoding="latin-1")
                ftp.connect(host, port, timeout=5)
                ftp.login(user or "xbox", password or "xbox")
                ftp.quit()
                self.probe_done.emit(True, f"Connected to {host}")
            except (OSError, EOFError) + ftplib.all_errors as exc:
                self.probe_done.emit(False, str(exc))

        threading.Thread(target=probe, daemon=True).start()

    def probe_finished(self, ok, message):
        self.test_button.setEnabled(True)
        self.test_status.setText(message if ok else "Could not connect: " + message)

    def test_agent(self):
        if self.dirty:
            self.test_status.setText("Save first; the agent test uses the saved target")
            return
        if self.window.dashboard_status_command() is None:
            self.test_status.setText("The dashboard agent add-on is not available")
            return
        self.agent_testing = self.name
        self.agent_test_button.setEnabled(False)
        self.test_status.setText("Asking the dashboard agent…")
        self.window.refresh_dashboard_status(self.name)

    def agent_tested(self, name, state, detail):
        if name != self.agent_testing:
            return
        self.agent_testing = None
        self.agent_test_button.setEnabled(True)
        if name == self.name:
            self.test_status.setText({
                "current": "The dashboard agent answered",
                "outdated": "The dashboard agent answered but is outdated",
            }.get(state, "No answer from the dashboard agent") + (f": {detail}" if detail else ""))

    def xemu_folder(self):
        text = self.xemu_fields["folder"].text().strip()
        folder = Path(text).expanduser() if text else None
        return folder if folder is None or folder.is_absolute() else \
            self.config_path().parent / folder

    def browse_xemu(self, key):
        field = self.xemu_fields[key]
        if key == "folder":
            selected = QFileDialog.getExistingDirectory(self, "Select xemu folder", field.text())
        else:
            selected, _ = QFileDialog.getOpenFileName(self, "Select file", field.text())
        if selected:
            field.setText(selected)

    def show_xemu_files(self, *_args):
        folder = self.xemu_folder()
        found = find_xemu_files(folder) if folder else {}
        for key, _label in self.XEMU_FILES:
            self.xemu_fields[key].setPlaceholderText(
                f"Found: {found[key].name}" if key in found
                else "Made by xemu" if key == "eeprom" and folder
                else "Optional" if key == "bios_128mb" else "Not found in the xemu folder"
                if folder else "")

    def download_xemu(self):
        folder = self.xemu_folder() or self.config_path().parent / "xemu"
        if find_xemu_files(folder).get("exe") and QMessageBox.question(
                self, "TES3X", f"Replace the xemu in {folder} with the latest release?") \
                != QMessageBox.StandardButton.Yes:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            version = download_xemu(folder)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "TES3X", f"Could not download xemu: {exc}")
            return
        QApplication.restoreOverrideCursor()
        self.xemu_fields["folder"].setText(folder.as_posix())
        self.show_xemu_files()
        QMessageBox.information(self, "TES3X", f"Downloaded xemu {version} to {folder}. Copy your "
                                "MCPX boot ROM and BIOS there, then save the target.")


SERVER_FIELDS = (
    # key, label, kind, default, help
    ("world", "World folder", "folder", "", "Keeps the server key, password, characters, "
     "saves and bans; empty keeps nothing between runs"),
    ("profile", "Build profile", "profile", "", "The build consoles' managers download on "
     "joining; consoles with another build are refused"),
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
    ("remote_admin", "Remote admin port", "int", 0, "Also take admin commands from other "
     f"machines on this UDP port ({tes3x_net.REMOTE_ADMIN_PORT} by convention); 0 keeps admin "
     "to this PC"),
    ("admin_password_file", "Admin password file", "file", "", "The remote admin password on "
     f"its first line, at least {tes3x_net.ADMIN_PASSWORD_MIN} characters"),
)
REMOTE_KEYS = ("mode", "remote_address", "remote_password")


def multiplayer_profile(path):
    """Whether the profile at `path` builds with the multiplayer patch, which joining needs."""
    try:
        profile = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        return "multiplayer" in resolve_patch_plan(profile)["applied"]
    except (OSError, tomllib.TOMLDecodeError, PipelineError):
        return False


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
    if values.get("remote_admin"):
        args += ["--remote-admin", str(values["remote_admin"])]
    if values.get("admin_password_file"):
        args += ["--admin-password-file", str(values["admin_password_file"])]
    if values.get("build"):
        args += ["--build", str(values["build"])]
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
    """Run `tes3x_net.py serve` locally, or reach one elsewhere through its remote admin port,
    and manage its players."""

    remote_answer = Signal(str, str, str)

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.process = None
        self.admin = None
        self.stopping = False
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
        form = QFormLayout()
        self.inputs = {}
        for key, label, kind, default, help_text in SERVER_FIELDS:
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
        self.command_entry = monospace(QLineEdit())
        self.command_entry.setPlaceholderText("Admin command; help lists them")
        self.command_entry.returnPressed.connect(self.send_typed)
        right.addWidget(self.command_entry)
        right.setSizes([260, 400])
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
                                         "build to managers that update")
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

    def load(self):
        saved = self.window.local_values().get("server", {})
        self.mode.setCurrentIndex(max(0, self.mode.findData(saved.get("mode", "local"))))
        self.remote_address.setText(str(saved.get("remote_address", "")))
        self.remote_password.setText(str(saved.get("remote_password", "")))
        self.remember_password.setChecked("remote_password" in saved)
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
            values = {**self.values(), **self.remote_values()}
            for key, value in values.items():
                table[key] = value
            for key in [key for key in table if key not in values]:
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
        self.window.run_steps([(ROOT / "tools" / "tes3x_pipeline.py", [
            profile, *(["--config", str(config)] if config.is_file() else []),
            *(["--target", target] if target else [])],
            f"Building {Path(profile).stem} for the server…")], then=then)

    def start(self):
        if self.process is not None or not self.save():
            return
        values = self.values()
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
        process = QProcess(self)
        process.setWorkingDirectory(str(self.window.work_dir()))
        process.setProgram(sys.executable)
        process.setArguments(["-u", str(ROOT / "tools" / "tes3x_net.py"),
                              *server_arguments(values)])
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self.read_output)
        process.finished.connect(self.finished)
        self.process, self.stopping = process, False
        self.admin_port = values.get("admin_port", tes3x_net.ADMIN_PORT)
        self.admin = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.admin.bind(("127.0.0.1", 0))
        self.admin.setblocking(False)
        self.log.appendPlainText(f"$ tes3x_net.py {' '.join(server_arguments(values))}")
        process.start()
        self.poll.start()
        self.set_status("Running")
        self.update_buttons()

    def stop(self):
        if self.process is None:
            return
        if not self.stopping:
            # A clean stop asks every console to save its character first.
            self.stopping = True
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

    def finished(self, code, _status):
        self.read_output()
        self.log.appendPlainText(f"server exited ({code})")
        self.process = None
        self.poll.stop()
        if self.admin is not None:
            self.admin.close()
            self.admin = None
        self.players.clear()
        self.set_status("Stopped")
        self.update_buttons()

    STATUS_COLORS = (("Running", "#2e7d32"), ("Connected", "#2e7d32"),
                     ("Stopping", "#b26a00"), ("Connecting", "#b26a00"),
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
        remote = self.mode.currentData() == "remote"
        connected = self.remote is not None
        self.mode.setEnabled(not running and not connected)
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
        for button in (self.kick_button, self.ban_button, self.save_button, self.bans_button):
            button.setEnabled(live)

    def shutdown(self):
        """On closing the GUI: stop the server rather than orphan it."""
        self.disconnect_remote()
        if self.process is not None:
            self.process.kill()
            self.process.waitForFinished(3000)


def fetch_destination(work_dir, target, path):
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path(work_dir) / "build" / "agent-fetch" / target / stamp / PureWindowsPath(path).name

