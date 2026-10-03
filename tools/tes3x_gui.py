#!/usr/bin/env python3
"""TES3X GUI. It edits the same TOML the command-line tools read."""

import argparse
import collections
from collections import defaultdict
import datetime
import fnmatch
import ftplib
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import tomllib
import uuid
import zipfile

try:
    import tomlkit
    from PySide6.QtCore import (QAbstractTableModel, QFile, QModelIndex, QProcess,
                                QProcessEnvironment, QSettings, QSortFilterProxyModel, QTimer, Qt,
                                QUrl, Signal)
    from PySide6.QtGui import (QAction, QColor, QDesktopServices, QIcon,
                               QKeySequence, QPainter, QPen, QPixmap, QTextCursor)
    from PySide6.QtWidgets import (
        QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
        QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
        QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
        QPushButton, QScrollArea, QSpinBox, QSplitter, QStackedWidget, QStatusBar, QStyle,
        QTableView, QTabWidget, QTextBrowser, QTextEdit, QToolButton, QTreeWidget, QTreeWidgetItem,
        QVBoxLayout, QWidget,
    )
except ImportError as exc:
    raise SystemExit(
        "TES3X GUI dependencies are not installed; run "
        "`python -m pip install -r requirements-gui.txt`"
    ) from exc

from tes3x_build import DEFAULT_EXCLUDE, PLUGIN_EXT, Mod, plugin_masters, texture_dims
from tes3x_bsa import Bsa
from tes3x_library import (ARCHIVES, CATALOG_NAME, LibraryError, append_mods, convert_profile,
                           discover_library, extract_archive, free_id, guess_release,
                           index_library, install_files, install_layout, load_library, nexus_id,
                           resolve_selection)
from tes3x_catalog import STATUS_LABELS as COMPAT_LABELS, CatalogError, load as load_catalog
from tes3x_catalog import match as match_catalog, needs as catalog_needs
from tes3x_patches import (CATEGORIES as PATCH_CATEGORIES, PATCHES as PATCH_CATALOG, SOURCES,
                           patch_spec)
from tes3x_plugins import (BASE_MASTERS, collect, dependency_order, fetch_rules, sort_files,
                           warnings as mlox_notes)
from tes3x_pipeline import (DEPLOY_CONFLICT, DEPLOY_NO_SPACE, MARKER as PIPELINE_MARKER, PipelineError,
                            resolve_patch_plan, validate_local_config, validate_profile)
from tes3x_records import records, subrecords
from tes3x_deploy import parse_drives
from tes3x_agent import AgentListener, key_fingerprint, load_or_create_key
import tes3x_nexus as nexus
import tes3x_saves as saves_tool
import tes3x_savepool
import tes3x_targets
from tes3x_xemu_setup import download_xemu, find_files as find_xemu_files, resolve as resolve_xemu


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
ROLE = Qt.ItemDataRole.UserRole
EXTRA = Qt.ItemDataRole.UserRole + 1
TEMPLATE = ROOT / "examples" / "profile.toml"
PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
RETAIL_PLUGINS = ("Morrowind.esm", "Tribunal.esm", "Bloodmoon.esm")
EXPANSION_PLACEHOLDERS = {"tribunal.esm", "bloodmoon.esm"}
WINS = QColor(60, 170, 60, 60)
LOSES = QColor(210, 60, 60, 60)
WARNING = QColor(200, 40, 40)
COMPAT = {"works": ("\u2713", QColor(60, 170, 60)),
          "works-with-requirements": ("*", QColor(215, 150, 20)),
          "broken": ("\u2717", WARNING), "not-possible": ("\u2717", WARNING)}
XEMU_STARTED = "xemu: started"
CHECK_MARKER = ".tes3x-check.json"
DEPLOYS_MARKER = ".tes3x-deploys.json"


def target_capabilities(target, runtime=None):
    """Runtime features a target can offer now, not merely features its kind supports."""
    runtime = runtime or {}
    capabilities = {"cached_logs", "cached_saves"}
    kind = target.get("kind") if target else None
    game = runtime.get("game")

    if kind == "xbox":
        if target.get("host") and target.get("games_root"):
            capabilities.add("configured_deploy")
        if runtime.get("ftp") == "connected":
            capabilities.update({"ftp", "remote_files", "remote_saves", "installed_builds",
                                 "pull_logs", "install_dashboard_agent"})
        dashboard = runtime.get("dashboard")
        if dashboard == "current":
            capabilities.update({"dashboard_agent", "dashboard_control", "launch"})
        elif dashboard == "outdated":
            capabilities.add("update_dashboard_agent")
    elif kind == "xemu":
        capabilities.update({"xemu_disk", "recovered_logs"})
        if target.get("exe") or target.get("folder"):
            capabilities.add("launch")
        if runtime.get("process") in {"starting", "running"}:
            capabilities.update({"process_control", "runner_output"})

    if game == "connected":
        capabilities.update({"in_game_agent", "live_status", "live_logs", "commands",
                             "agent_fetch", "pull_logs"})
    elif game == "stalled":
        capabilities.update({"in_game_agent", "stalled"})
    return frozenset(capabilities)


def target_runtime_label(target, runtime=None):
    """Short state label shared by target badges and capability-driven pages."""
    runtime = runtime or {}
    if runtime.get("game") == "connected":
        return "In game"
    if runtime.get("game") == "stalled":
        return "Stalled"
    if target and target.get("kind") == "xemu":
        return {"starting": "Starting", "running": "xemu running",
                "stopping": "Stopping"}.get(
            runtime.get("process"), "Stopped")
    if runtime.get("ftp") == "checking":
        return "Checking"
    if runtime.get("ftp") == "connected":
        return {"current": "Dashboard", "outdated": "Dashboard agent outdated",
                "missing": "Dashboard, no agent", "checking": "Checking agent"}.get(
                    runtime.get("dashboard"), "Dashboard reachable")
    return "Off" if runtime.get("ftp") == "offline" else "Unknown"


def dashboard_agent_state(output, code, expected):
    """(state, detail) from the dashboard agent's authenticated ping."""
    match = re.search(r"\bok tes3xagent (\d+)\b", output) if code == 0 else None
    if match is None:
        return "missing", output.strip() or f"agent probe exited {code}"
    version = int(match.group(1))
    if version < expected:
        return "outdated", f"Dashboard agent {version}; update to {expected}"
    return "current", output.strip()


def version_label():
    """The version, with the commit when run from a checkout."""
    try:
        commit = subprocess.run(["git", "-C", str(ROOT), "describe", "--always", "--dirty"],
                                capture_output=True, text=True, timeout=5).stdout.strip()
    except OSError:
        commit = ""
    return f"TES3X {VERSION}" + (f" ({commit})" if commit else "")


def tinted_icon(icon, colour):
    """Keep a platform icon's shape while giving toolbar actions distinct accents."""
    source = icon.pixmap(18, 18)
    if source.isNull():
        return icon
    result = QPixmap(source.size())
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.drawPixmap(0, 0, source)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(result.rect(), QColor(colour))
    painter.end()
    return QIcon(result)


def theme_icon(widget, theme, fallback):
    return QIcon.fromTheme(theme, widget.style().standardIcon(fallback))


def dot_icon(colour):
    pixmap = QPixmap(12, 12)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(colour))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(2, 2, 8, 8)
    painter.end()
    return QIcon(pixmap)


def icon_button(action):
    button = QToolButton()
    button.setDefaultAction(action)
    button.setAutoRaise(True)
    return button


def running_xemu():
    """PIDs of xemu processes, whoever started them."""
    try:
        if sys.platform == "win32":
            output = subprocess.run(["tasklist", "/FI", "IMAGENAME eq xemu.exe", "/FO", "CSV",
                                     "/NH"], capture_output=True, text=True, timeout=5).stdout
            return [line.split('","')[1] for line in output.splitlines()
                    if line.lower().startswith('"xemu.exe"')]
        output = subprocess.run(["pgrep", "-x", "xemu"], capture_output=True, text=True,
                                timeout=5).stdout
        return output.split()
    except (OSError, subprocess.SubprocessError, IndexError):
        return []


class Spinner(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.angle = 0
        self.setFixedSize(16, 16)
        self.timer = QTimer(self)
        self.timer.setInterval(60)
        self.timer.timeout.connect(self.step)
        self.hide()

    def start(self):
        self.show()
        self.timer.start()

    def stop(self):
        self.timer.stop()
        self.hide()

    def step(self):
        self.angle = (self.angle - 30) % 360
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self.palette().highlight().color(), 2.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(self.rect().adjusted(2, 2, -2, -2), self.angle * 16, 270 * 16)


class StatusBar(QStatusBar):
    """Messages go to a label beside the spinner; QStatusBar's own would hide the spinner."""

    def __init__(self):
        super().__init__()
        self.spinner = Spinner()
        self.message = QLabel()
        self.addWidget(self.spinner)
        self.addWidget(self.message, 1)
        self.expiry = QTimer(self)
        self.expiry.setSingleShot(True)
        self.expiry.timeout.connect(self.message.clear)

    def showMessage(self, text, timeout=0):
        self.message.setText(text)
        self.expiry.stop()
        if timeout:
            self.expiry.start(timeout)

    def clearMessage(self):
        self.message.clear()


def plugin_header(path):
    """Author and description from a plugin's TES3 header."""
    try:
        header = next(records(path), None)
        if header is None or header[0] != b"TES3":
            return "", ""
        hedr = next((value for tag, value in subrecords(header[2]) if tag == b"HEDR"), b"")
    except (OSError, ValueError):
        return "", ""
    text = [hedr[start:end].split(b"\0")[0].decode("cp1252", "replace").strip()
            for start, end in ((8, 40), (40, 296))]
    return text[0], text[1]


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def addon_registry():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import addons
    return addons


def enabled_addons(local):
    """The add-ons switched on in the local config that import, by name."""
    registry = addon_registry()
    found = {}
    for name in registry.enabled(local):
        try:
            found[name] = registry.load(name)
        except ImportError:
            continue
    return found


def new_table(document, section):
    """Append a table, set off by a blank line even after an array of tables."""
    if not document.as_string().endswith("\n\n"):
        document.add(tomlkit.nl())
    table = tomlkit.table()
    document[section] = table
    return table


def drop_empty(document, section):
    if section in document and not document[section]:
        del document[section]


def default_config_path():
    local = Path.cwd() / "tes3x.local.toml"
    return (local if local.is_file() else ROOT / "tes3x.local.toml").resolve()


class LocalSettingsDialog(QDialog):
    """Edit the machine-local TOML without discarding comments or private xemu settings."""

    probe_done = Signal(bool, str)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = Path(path).resolve()
        self.setWindowTitle("TES3X settings")
        self.resize(920, 650)
        self.setMinimumSize(720, 480)
        try:
            text = self.path.read_text(encoding="utf-8") if self.path.is_file() else ""
            self.document = tomlkit.parse(text)
        except (OSError, tomlkit.exceptions.ParseError) as exc:
            raise PipelineError(str(exc)) from exc

        plain = tomllib.loads(tomlkit.dumps(self.document))
        paths = plain.get("paths", {})
        self.fields = {}
        self.legacy_deploy = bool(plain.get("deploy")) and not plain.get("targets")
        self.use_targets = not self.legacy_deploy
        self.target_values = {name: tes3x_targets.resolve(plain, name) for name in
                              tes3x_targets.targets(plain)}
        self.current_target_name = None
        self.target_loading = False

        layout = QVBoxLayout(self)
        content = QHBoxLayout()
        self.categories = QListWidget()
        self.categories.setFixedWidth(155)
        self.pages = QStackedWidget()
        content.addWidget(self.categories)
        content.addWidget(self.pages, 1)
        layout.addLayout(content, 1)
        for label, page in (("Paths", self.path_group(paths)),
                            ("Targets", self.targets_page(plain)),
                            ("Add-ons", self.addons_group(plain.get("addons", {})))):
            self.add_page(label, page)
        self.categories.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.categories.setCurrentRow(0)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.probe_done.connect(self.target_probe_finished)

    def add_page(self, label, page):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(page)
        self.categories.addItem(label)
        self.pages.addWidget(scroll)

    @staticmethod
    def page(title, description=""):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 12, 20, 20)
        heading = QLabel(title)
        font = heading.font()
        font.setPointSize(font.pointSize() + 4)
        font.setBold(True)
        heading.setFont(font)
        layout.addWidget(heading)
        if description:
            about = QLabel(description)
            about.setWordWrap(True)
            layout.addWidget(about)
        return page, layout

    @staticmethod
    def form_section(layout, title):
        heading = QLabel(title)
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)
        heading.setContentsMargins(0, 12, 0, 2)
        layout.addWidget(heading)
        body = QWidget()
        form = QFormLayout(body)
        form.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(body)
        return form

    def line(self, value="", password=False):
        field = QLineEdit(str(value))
        if password:
            field.setEchoMode(QLineEdit.EchoMode.Password)
        return field

    def browse_row(self, key, value, files=False):
        field = self.line(value)
        self.fields[key] = field
        button = QPushButton("Browse…")

        def browse():
            if files:
                selected, _ = QFileDialog.getOpenFileName(self, "Select file", field.text())
            else:
                selected = QFileDialog.getExistingDirectory(self, "Select directory", field.text())
            if selected:
                field.setText(selected)

        button.clicked.connect(browse)
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(field)
        row_layout.addWidget(button)
        return row

    def path_group(self, values):
        group, layout = self.page("Paths", "Folders and tools shared by every profile.")
        form = self.form_section(layout, "Project paths")
        for key, label in (("vanilla_root", "Clean game root"),
                           ("mod_library", "Mod library"),
                           ("profiles", "Profiles"),
                           ("build_root", "Build output"),
                           ("llvm", "LLVM tools")):
            form.addRow(label, self.browse_row("paths." + key, values.get(key, "")))
        rules_row = self.browse_row("paths.mlox_rules", values.get("mlox_rules", ""), files=True)
        download = QPushButton("Download")
        download.setToolTip("Download the current mlox rules and use them")
        download.clicked.connect(self.download_mlox_rules)
        rules_row.layout().addWidget(download)
        form.addRow("mlox rules (optional)", rules_row)
        hardlink = QCheckBox("Hardlink unchanged retail files")
        hardlink.setChecked(values.get("hardlink_retail", False))
        self.fields["paths.hardlink_retail"] = hardlink
        form.addRow("", hardlink)
        layout.addStretch()
        return group

    def targets_page(self, plain):
        page, layout = self.page("Targets", "Choose the Xboxes and xemu configurations that can "
                                 "run any profile.")
        if self.legacy_deploy:
            self.legacy_notice = QWidget()
            notice_layout = QHBoxLayout(self.legacy_notice)
            notice_layout.setContentsMargins(0, 6, 0, 6)
            note = QLabel("This file still uses [deploy]. Convert it to an Xbox target when "
                          "you are ready.")
            note.setWordWrap(True)
            convert = QPushButton("Convert")
            convert.clicked.connect(self.convert_legacy)
            notice_layout.addWidget(note, 1)
            notice_layout.addWidget(convert)
            layout.addWidget(self.legacy_notice)

        body = QHBoxLayout()
        left = QVBoxLayout()
        self.target_list = QListWidget()
        self.target_list.setMinimumWidth(180)
        left.addWidget(self.target_list, 1)
        actions = QHBoxLayout()
        for label, handler in (("Add", self.add_target), ("Duplicate", self.duplicate_target),
                               ("Remove", self.remove_target)):
            button = QPushButton(label)
            button.clicked.connect(handler)
            actions.addWidget(button)
        left.addLayout(actions)
        body.addLayout(left, 1)

        self.target_editor = QWidget()
        editor = QVBoxLayout(self.target_editor)
        editor.setContentsMargins(18, 0, 0, 0)
        form = self.form_section(editor, "Target")
        self.target_name = QLineEdit()
        self.target_kind = QComboBox()
        self.target_kind.addItem("Xbox", "xbox")
        self.target_kind.addItem("xemu", "xemu")
        self.target_ram = QComboBox()
        self.target_ram.addItem("64 MB", 64)
        self.target_ram.addItem("128 MB", 128)
        form.addRow("Name", self.target_name)
        form.addRow("Kind", self.target_kind)
        form.addRow("Memory", self.target_ram)

        self.xbox_fields = QWidget()
        xbox = QFormLayout(self.xbox_fields)
        xbox.setContentsMargins(0, 8, 0, 0)
        self.target_host = self.line()
        self.target_port = QSpinBox()
        self.target_port.setRange(1, 65535)
        self.target_user = self.line("xbox")
        self.target_password = self.line("xbox", password=True)
        self.target_games_root = self.line()
        self.target_games_root.setPlaceholderText("F:/Games")
        self.target_retail_root = self.line()
        self.target_dashboard = self.line()
        self.target_dashboard.setPlaceholderText("Auto-detect")
        self.target_games_root.textChanged.connect(self.update_retail_placeholder)
        for label, field in (("Host", self.target_host), ("Port", self.target_port),
                             ("User", self.target_user), ("Password", self.target_password),
                             ("Games root", self.target_games_root),
                             ("Shared retail base", self.target_retail_root),
                             ("Dashboard root", self.target_dashboard)):
            xbox.addRow(label, field)
        self.update_retail_placeholder()
        probe_row = QHBoxLayout()
        self.target_test = QPushButton("Test connection")
        self.target_test.clicked.connect(self.test_target_connection)
        self.target_test_status = QLabel()
        probe_row.addWidget(self.target_test)
        probe_row.addWidget(self.target_test_status, 1)
        xbox.addRow("", probe_row)
        agent_row = QHBoxLayout()
        self.target_agent_buttons = []
        for label, command in (("Install / update agent", "install"),
                               ("Restart dashboard", "restart"),
                               ("Remove agent", "uninstall")):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, label=label, command=command:
                                   self.run_target_agent(label, command))
            self.target_agent_buttons.append(button)
            agent_row.addWidget(button)
        self.target_agent_status = QLabel()
        agent_row.addWidget(self.target_agent_status, 1)
        xbox.addRow("Dashboard agent", agent_row)
        editor.addWidget(self.xbox_fields)

        self.xemu_fields_widget = QWidget()
        xemu = QFormLayout(self.xemu_fields_widget)
        xemu.setContentsMargins(0, 8, 0, 0)
        self.xemu_target_fields = {}
        for key, label in (("folder", "xemu folder"), *self.XEMU_FILES,
                           ("extract_xiso", "extract-xiso"), ("gdb", "GDB"),
                           ("template", "Config template")):
            field = self.line()
            self.xemu_target_fields[key] = field
            button = QPushButton("Browse…")
            button.clicked.connect(lambda _checked=False, key=key:
                                   self.browse_target_xemu(key))
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(field)
            row_layout.addWidget(button)
            if key == "folder":
                download = QPushButton("Download xemu")
                download.clicked.connect(self.download_target_xemu)
                field.textChanged.connect(self.show_target_xemu_files)
            xemu.addRow(label, row)
            if key == "folder":
                xemu.addRow("", download)
        editor.addWidget(self.xemu_fields_widget)
        editor.addStretch()
        body.addWidget(self.target_editor, 2)
        layout.addLayout(body, 1)

        self.target_list.currentRowChanged.connect(self.target_selected)
        self.target_kind.currentIndexChanged.connect(self.target_kind_changed)
        self.refresh_target_list(plain.get("default_target"))
        return page

    def target_data(self):
        return {
            "kind": self.target_kind.currentData(),
            "ram": self.target_ram.currentData(),
            "host": self.target_host.text().strip(),
            "port": self.target_port.value(),
            "user": self.target_user.text().strip(),
            "password": self.target_password.text(),
            "games_root": self.target_games_root.text().strip(),
            "retail_root": self.target_retail_root.text().strip(),
            "dashboard": self.target_dashboard.text().strip(),
            **{key: field.text().strip() for key, field in self.xemu_target_fields.items()},
        }

    def store_current_target(self):
        if self.target_loading or not self.current_target_name:
            return
        name = self.target_name.text().strip()
        if not PROFILE_NAME.fullmatch(name):
            raise PipelineError("target names use letters, numbers, dot, underscore and dash")
        if name != self.current_target_name and name in self.target_values:
            raise PipelineError(f"target {name!r} already exists")
        values = self.target_data()
        previous = self.target_values.pop(self.current_target_name, {})
        for key in ("legacy", "legacy_install_dir", "legacy_xemu"):
            if key in previous:
                values[key] = previous[key]
        self.target_values[name] = values
        self.current_target_name = name

    def refresh_target_list(self, selected=None):
        selected = selected or self.current_target_name
        self.target_loading = True
        self.target_list.clear()
        for name, target in self.target_values.items():
            kind = target.get("kind", "xbox")
            detail = (target.get("host", "") if kind == "xbox"
                      else f"{target.get('ram', 64)} MB")
            item = QListWidgetItem(f"{name}  ·  {detail or kind}")
            item.setData(ROLE, name)
            self.target_list.addItem(item)
        row = next((index for index in range(self.target_list.count())
                    if self.target_list.item(index).data(ROLE) == selected), 0)
        self.target_loading = False
        if self.target_list.count():
            self.target_list.setCurrentRow(row)
        else:
            self.current_target_name = None
            self.target_editor.setEnabled(False)

    def target_selected(self, row):
        if self.target_loading:
            return
        try:
            self.store_current_target()
        except PipelineError as exc:
            QMessageBox.warning(self, "TES3X", str(exc))
        item = self.target_list.item(row)
        if item is None:
            return
        name = item.data(ROLE)
        target = self.target_values[name]
        self.target_loading = True
        self.current_target_name = name
        self.target_name.setText(name)
        self.target_kind.setCurrentIndex(max(0, self.target_kind.findData(
            target.get("kind", "xbox"))))
        self.target_ram.setCurrentIndex(max(0, self.target_ram.findData(target.get("ram", 64))))
        self.target_host.setText(target.get("host", ""))
        self.target_port.setValue(target.get("port", 21))
        self.target_user.setText(target.get("user", "xbox"))
        self.target_password.setText(target.get("password", "xbox"))
        self.target_games_root.setText(target.get("games_root", ""))
        self.target_retail_root.setText(target.get("retail_root", ""))
        self.target_dashboard.setText(target.get("dashboard", ""))
        self.target_agent_status.clear()
        for key, field in self.xemu_target_fields.items():
            field.setText(str(target.get(key, "")))
        self.target_editor.setEnabled(True)
        self.target_loading = False
        self.target_kind_changed()

    def update_retail_placeholder(self, *_args):
        games = self.target_games_root.text().strip().rstrip("/\\")
        suggested = games + "/MorrowindRetail" if games else "F:/Games/MorrowindRetail"
        self.target_retail_root.setPlaceholderText(
            f"{suggested} (recommended for overlay profiles; set explicitly)")

    def target_kind_changed(self, *_args):
        xbox = self.target_kind.currentData() == "xbox"
        self.xbox_fields.setVisible(xbox)
        self.xemu_fields_widget.setVisible(not xbox)
        self.target_test.setEnabled(xbox)
        for button in self.target_agent_buttons:
            button.setEnabled(xbox)
        if not xbox:
            self.show_target_xemu_files()

    def convert_legacy(self):
        self.store_current_target()
        self.use_targets = True
        for target in self.target_values.values():
            target.pop("legacy", None)
            target.pop("legacy_install_dir", None)
        self.legacy_notice.hide()

    def add_target(self, name=None):
        if isinstance(name, bool) or name is None:
            name, ok = QInputDialog.getText(self, "Add target", "Target name")
            if not ok:
                return
        name = name.strip()
        if not PROFILE_NAME.fullmatch(name) or name in self.target_values:
            QMessageBox.warning(self, "TES3X", "Choose a new target name using letters, "
                                "numbers, dot, underscore or dash.")
            return
        self.store_current_target()
        self.use_targets = True
        self.target_values[name] = {"kind": "xbox", "ram": 64, "port": 21,
                                    "user": "xbox", "password": "xbox"}
        self.refresh_target_list(name)

    def duplicate_target(self, name=None):
        if not self.current_target_name:
            return
        suggested = name if isinstance(name, str) else self.current_target_name + "-copy"
        if not isinstance(name, str):
            suggested, ok = QInputDialog.getText(self, "Duplicate target", "Target name",
                                                  text=suggested)
            if not ok:
                return
        self.store_current_target()
        source = self.current_target_name
        if not PROFILE_NAME.fullmatch(suggested) or suggested in self.target_values:
            QMessageBox.warning(self, "TES3X", "Choose a new valid target name.")
            return
        self.use_targets = True
        self.target_values[suggested] = dict(self.target_values[source])
        self.refresh_target_list(suggested)

    def remove_target(self):
        if not self.current_target_name:
            return
        del self.target_values[self.current_target_name]
        self.current_target_name = None
        self.use_targets = True
        self.refresh_target_list()

    def test_target_connection(self):
        if self.target_kind.currentData() != "xbox" or not self.target_host.text().strip():
            self.target_test_status.setText("Set a host first")
            return
        host = self.target_host.text().strip()
        port = self.target_port.value()
        user, password = self.target_user.text().strip(), self.target_password.text()
        self.target_test.setEnabled(False)
        self.target_test_status.setText("Connecting…")

        def probe():
            try:
                ftp = ftplib.FTP(encoding="latin-1")
                ftp.connect(host, port, timeout=5)
                ftp.login(user or "xbox", password or "xbox")
                ftp.quit()
                self.probe_done.emit(True, f"Connected to {host}")
            except ftplib.all_errors as exc:
                self.probe_done.emit(False, str(exc))

        threading.Thread(target=probe, daemon=True).start()

    def target_probe_finished(self, ok, message):
        self.target_test.setEnabled(self.target_kind.currentData() == "xbox")
        self.target_test_status.setText(message if ok else "Could not connect: " + message)

    def run_target_agent(self, label, command):
        """Run a dashboard-agent lifecycle command for the Xbox being edited."""
        if command == "uninstall" and QMessageBox.question(
                self, "TES3X", f"Remove the dashboard agent from {self.current_target_name}?") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            self.save_settings()
        except (OSError, PipelineError, tomlkit.exceptions.ParseError) as exc:
            QMessageBox.critical(self, "TES3X", str(exc))
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            done = subprocess.run(
                [sys.executable, str(ROOT / "addons" / "console" / "console.py"), command,
                 "--config", str(self.path), "--target", self.current_target_name],
                cwd=self.path.parent, capture_output=True, text=True, timeout=120)
            output, ok = (done.stdout + done.stderr).strip(), done.returncode == 0
            # Install may have added the private token. Keep it in subsequent settings saves.
            self.document = tomlkit.parse(self.path.read_text(encoding="utf-8"))
        except (OSError, subprocess.TimeoutExpired, tomlkit.exceptions.ParseError) as exc:
            output, ok = str(exc), False
        finally:
            QApplication.restoreOverrideCursor()
        summary = output.splitlines()[-1] if output else "Done."
        self.target_agent_status.setText(summary)
        (QMessageBox.information if ok else QMessageBox.critical)(self, label, output or "Done.")

    XEMU_FILES = (("exe", "Executable"), ("bootrom", "MCPX boot ROM"), ("bios", "BIOS"),
                  ("bios_128mb", "BIOS for 128 MB runs"), ("eeprom", "EEPROM"),
                  ("hdd", "Clean HDD image"))

    def target_xemu_folder(self):
        text = self.xemu_target_fields["folder"].text().strip()
        folder = Path(text).expanduser() if text else None
        return folder if folder is None or folder.is_absolute() else self.path.parent / folder

    def browse_target_xemu(self, key):
        field = self.xemu_target_fields[key]
        if key == "folder":
            selected = QFileDialog.getExistingDirectory(self, "Select xemu folder", field.text())
        else:
            selected, _ = QFileDialog.getOpenFileName(self, "Select file", field.text())
        if selected:
            field.setText(selected)

    def show_target_xemu_files(self, *_args):
        folder = self.target_xemu_folder()
        found = find_xemu_files(folder) if folder else {}
        for key, _label in self.XEMU_FILES:
            self.xemu_target_fields[key].setPlaceholderText(
                f"Found: {found[key].name}" if key in found
                else "Made by xemu" if key == "eeprom" and folder
                else "Optional" if key == "bios_128mb" else "Not found in the xemu folder"
                if folder else "")

    def download_target_xemu(self):
        folder = self.target_xemu_folder() or self.path.parent / "xemu"
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
        self.xemu_target_fields["folder"].setText(folder.as_posix())
        self.show_target_xemu_files()
        QMessageBox.information(self, "TES3X", f"Downloaded xemu {version} to {folder}. Copy your "
                                "MCPX boot ROM and BIOS there, then save the settings.")

    def addons_group(self, values):
        group, form = self.page("Add-ons", "Optional integrations for particular setups.")
        heading = QLabel("Installed add-ons")
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)
        heading.setContentsMargins(0, 12, 0, 2)
        form.addWidget(heading)
        registry = addon_registry()
        for name in registry.NAMES:
            try:
                module = registry.load(name)
            except ImportError as exc:
                form.addWidget(QLabel(f"{name}: cannot load ({exc})"))
                continue
            box = QCheckBox(module.LABEL)
            box.setChecked(bool(values.get(name)))
            self.fields["addons." + name] = box
            form.addWidget(box)
            about = QLabel(module.DESCRIPTION)
            about.setWordWrap(True)
            form.addWidget(about)
            actions = QHBoxLayout()
            for label, script, arguments in getattr(module, "SETTINGS_ACTIONS", []):
                button = QPushButton(label)
                button.clicked.connect(lambda _checked=False, label=label, script=script,
                                       arguments=arguments: self.run_addon(label, script,
                                                                           arguments))
                button.setEnabled(box.isChecked())
                box.toggled.connect(button.setEnabled)
                actions.addWidget(button)
            actions.addStretch()
            form.addLayout(actions)
        form.addStretch()
        return group

    def run_addon(self, label, script, arguments):
        """Save, then run an add-on's command against the saved settings."""
        try:
            self.save_settings()
        except (OSError, PipelineError, tomlkit.exceptions.ParseError) as exc:
            QMessageBox.critical(self, "TES3X", str(exc))
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            done = subprocess.run([sys.executable, str(script), *arguments,
                                   "--config", str(self.path)], cwd=self.path.parent,
                                  capture_output=True, text=True, timeout=120)
            output, ok = (done.stdout + done.stderr).strip(), done.returncode == 0
        except (OSError, subprocess.TimeoutExpired) as exc:
            output, ok = str(exc), False
        finally:
            QApplication.restoreOverrideCursor()
        (QMessageBox.information if ok else QMessageBox.critical)(self, label, output or "Done.")

    def download_mlox_rules(self):
        field = self.fields["paths.mlox_rules"]
        target = Path(field.text().strip() or self.path.parent / "mlox" / "mlox_base.txt")
        if not target.is_absolute():
            target = self.path.parent / target
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            size = fetch_rules(target)
        except (OSError, ValueError) as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "TES3X", f"Could not download the mlox rules: {exc}")
            return
        QApplication.restoreOverrideCursor()
        field.setText(target.as_posix())
        QMessageBox.information(self, "TES3X", f"Saved the mlox rules ({size // 1024} KB) to "
                                f"{target}. Save the settings to use them.")

    def update_table(self, section, values):
        table = self.document.get(section)
        if table is None:
            table = tomlkit.table()
            self.document[section] = table
        for key, value in values.items():
            if value == "":
                if key in table:
                    del table[key]
            else:
                table[key] = value

    def write_targets(self):
        self.store_current_target()
        if not self.use_targets:
            target = next((value for value in self.target_values.values()
                           if value.get("kind") == "xbox"), {})
            legacy_dir = target.get("legacy_install_dir", "")
            games = target.get("games_root", "").rstrip("/\\")
            values = {key: target.get(key, "") for key in
                      ("host", "port", "user", "password", "retail_root")}
            values["remote_root"] = games + "/" + legacy_dir if games and legacy_dir else ""
            self.update_table("deploy", values)
            xemu = next((value for value in self.target_values.values()
                         if value.get("kind") == "xemu"), None)
            if xemu is not None:
                self.update_table("xemu", {key: xemu.get(key, "")
                                           for key in tes3x_targets.XEMU_KEYS})
            return

        self.document.pop("deploy", None)
        tables = self.document.get("targets")
        if tables is None:
            tables = tomlkit.table()
            self.document["targets"] = tables
        for name in list(tables):
            if name not in self.target_values:
                del tables[name]
        keys = ("kind", "host", "port", "user", "password", "games_root", "retail_root",
                "dashboard", "ram", *sorted(tes3x_targets.XEMU_KEYS))
        for name, values in self.target_values.items():
            target = tables.get(name)
            if target is None:
                target = tomlkit.table()
                tables[name] = target
            kind = values.get("kind", "xbox")
            clean = {key: values.get(key) for key in keys}
            kind_keys = ({"kind", "ram", *tes3x_targets.XEMU_KEYS} if kind == "xemu" else
                         {"kind", "host", "port", "user", "password", "games_root",
                          "retail_root", "dashboard", "ram"})
            for key in list(target):
                if key in keys and (key not in kind_keys or key not in clean
                                    or clean[key] in ("", None, False)):
                    del target[key]
            target["kind"] = kind
            for key, value in clean.items():
                if key == "kind" or value in ("", None, False):
                    continue
                if kind == "xemu" and key not in ("ram", *tes3x_targets.XEMU_KEYS):
                    continue
                if kind == "xbox":
                    if key in tes3x_targets.XEMU_KEYS:
                        continue
                    if key == "ram" and value == 64:
                        continue
                target[key] = value
        if not tables:
            del self.document["targets"]
            self.document.pop("default_target", None)
        else:
            default = self.document.get("default_target")
            if default not in self.target_values:
                self.document["default_target"] = (self.current_target_name
                                                   or next(iter(self.target_values)))

    def save_settings(self):
        values = {name: (field.isChecked() if isinstance(field, QCheckBox)
                         else field.value() if isinstance(field, QSpinBox)
                         else field.text().strip())
                  for name, field in self.fields.items()}
        self.update_table("paths", {name.split(".", 1)[1]: value
                                    for name, value in values.items()
                                    if name.startswith("paths.")})
        self.write_targets()
        chosen = {name.split(".", 1)[1]: value or "" for name, value in values.items()
                  if name.startswith("addons.")}
        if any(chosen.values()) or "addons" in self.document:
            self.update_table("addons", chosen)
            if not self.document["addons"]:
                del self.document["addons"]
        text = tomlkit.dumps(self.document)
        validate_local_config(tomllib.loads(text))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text, encoding="utf-8", newline="")
        return True

    def save_and_accept(self):
        try:
            self.save_settings()
        except (OSError, PipelineError, tomlkit.exceptions.ParseError) as exc:
            QMessageBox.critical(self, "TES3X", str(exc))
            return
        self.accept()


def ini_value(text):
    for kind in (int, float):
        try:
            return kind(text)
        except ValueError:
            pass
    return {"true": True, "false": False}.get(text.casefold(), text)


# A missing movie is logged to Warnings.txt and skipped.
INTRO_MOVIES = ("Movies:Morrowind Logo", "Movies:New Game")
SKIP_MOVIE = "none.bik"


def ini_text(value):
    return str(value).lower() if isinstance(value, bool) else str(value)


def folder_name(name):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip(" .") or "mod"


def read_ini(path):
    """Morrowind.ini as {(section, key) casefolded: (section, key, value)}, first value wins."""
    values = {}
    try:
        text = Path(path).read_text(encoding="latin-1")
    except OSError:
        return values
    section = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and "]" in line:
            section = line[1:line.index("]")].strip()
        elif section and "=" in line:
            key, value = line.split("=", 1)
            values.setdefault((section.casefold(), key.strip().casefold()),
                              (section, key.strip(), value.strip()))
    return values


def trash(path):
    result = QFile.moveToTrash(str(path))
    return result[0] if isinstance(result, tuple) else bool(result)


class DragList(QTreeWidget):
    """A flat list reordered by dragging; rows never nest."""

    moved = Signal()
    dropped_files = Signal(list)

    def __init__(self, labels, accept_files=False):
        super().__init__()
        self.accept_files = accept_files
        self.setHeaderLabels(labels)
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def external(self, event):
        return self.accept_files and event.source() is not self and event.mimeData().hasUrls()

    def dragEnterEvent(self, event):
        if self.external(event):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self.external(event):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if self.external(event):
            event.acceptProposedAction()
            self.dropped_files.emit([url.toLocalFile() for url in event.mimeData().urls()
                                     if url.isLocalFile()])
            return
        super().dropEvent(event)
        self.moved.emit()

    def rows(self):
        return [self.topLevelItem(index) for index in range(self.topLevelItemCount())]

    def move_items(self, items, row):
        """Move items, in list order, to sit before row."""
        items = sorted(items, key=self.indexOfTopLevelItem)
        before = self.topLevelItem(row) if row < self.topLevelItemCount() else None
        for item in items:
            self.takeTopLevelItem(self.indexOfTopLevelItem(item))
        index = self.indexOfTopLevelItem(before) if before is not None else self.topLevelItemCount()
        for offset, item in enumerate(items):
            self.insertTopLevelItem(index + offset, item)
            item.setSelected(True)
        self.moved.emit()


ROW_FLAGS = (Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
             | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsUserCheckable)


class FilesModel(QAbstractTableModel):
    HEADERS = ("File", "Provided by", "Overrides")

    def __init__(self, headers=None):
        super().__init__()
        self.headers = headers or self.HEADERS
        self.entries = []

    def reset(self, entries):
        self.beginResetModel()
        self.entries = entries
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.entries)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and index.isValid():
            return self.entries[index.row()][index.column()]
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headers[section]
        return None


class FilesFilter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.text = ""
        self.conflicts_only = False

    def update(self, text=None, conflicts_only=None):
        if not hasattr(self, "beginFilterChange"):  # PySide6 before 6.10
            self.set_filter(text, conflicts_only)
            self.invalidateFilter()
            return
        self.beginFilterChange()
        self.set_filter(text, conflicts_only)
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def set_filter(self, text, conflicts_only):
        if text is not None:
            self.text = text.casefold()
        if conflicts_only is not None:
            self.conflicts_only = conflicts_only

    def filterAcceptsRow(self, row, _parent):
        path, owner, others = self.sourceModel().entries[row]
        if self.conflicts_only and not others:
            return False
        return not self.text or self.text in f"{path} {owner} {others}".casefold()


class InstallDialog(QDialog):
    """Choose what an unpacked mod installs: its Data Files folders and the files in them."""

    def __init__(self, unpacked, found, chosen, name, version, label, taken=None, fixed=False,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Install {label}")
        self.resize(640, 560)
        self.unpacked = Path(unpacked)
        self.roots = list(found)
        self.chosen = set(chosen)
        self.taken = taken
        self.name_field = QLineEdit(name)
        self.name_field.setReadOnly(fixed)
        self.version_field = QLineEdit(version)
        self.version_field.setReadOnly(fixed)
        self.version_field.setPlaceholderText("Optional")
        form = QFormLayout()
        form.addRow("Name", self.name_field)
        form.addRow("Version", self.version_field)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Contents", ""])
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.tree_menu)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Install")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.note)
        layout.addWidget(self.tree, 1)
        layout.addWidget(buttons)
        self.build_tree()

    def build_tree(self):
        self.tree.clear()
        top = QTreeWidgetItem([self.unpacked.name or str(self.unpacked)])
        top.setData(0, ROLE, ".")
        self.tree.addTopLevelItem(top)
        self.items = {".": top}
        self.add_children(top, self.unpacked, "")
        for rel, item in self.items.items():
            root = self.root_of(rel)
            is_dir = (self.unpacked / rel).is_dir()
            if root is None:
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                if not is_dir or not any(value.startswith(rel + "/") or rel == "."
                                         for value in self.roots):
                    item.setForeground(0, self.palette().placeholderText())
                continue
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            if is_dir:
                flags |= Qt.ItemFlag.ItemIsAutoTristate
            item.setFlags(flags)
            if rel == root:
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)
                item.setText(1, "Data Files")
        # A folder's state follows its files, so only files and empty folders are set.
        for rel, item in self.items.items():
            root = self.root_of(rel)
            if root is not None and not item.childCount():
                item.setCheckState(0, Qt.CheckState.Checked if root in self.chosen
                                   else Qt.CheckState.Unchecked)
        self.expand_to_roots()
        self.tree.resizeColumnToContents(0)
        if not self.roots:
            self.note.setText("No Data Files layout found. Right-click the folder that holds "
                              "Meshes, Textures or plugins and choose Use as Data Files.")
        elif len(self.roots) > 1:
            self.note.setText("This mod has option folders. Tick the ones to install; later "
                              "folders overwrite files from earlier ones.")
        else:
            self.note.setText("Data Files layout found. Untick anything you don't want.")

    def add_children(self, parent, path, prefix):
        entries = sorted(path.iterdir(), key=lambda entry: (entry.is_file(), entry.name.casefold()))
        for entry in entries:
            rel = prefix + entry.name
            item = QTreeWidgetItem([entry.name + ("/" if entry.is_dir() else "")])
            item.setData(0, ROLE, rel)
            parent.addChild(item)
            self.items[rel] = item
            if entry.is_dir():
                self.add_children(item, entry, rel + "/")

    def root_of(self, rel):
        for root in self.roots:
            if root == "." or rel == root or rel.startswith(root + "/"):
                return root
        return None

    def expand_to_roots(self):
        for root in self.roots:
            item = self.items.get(root)
            while item is not None:
                item.setExpanded(True)
                item = item.parent()
        self.items["."].setExpanded(True)

    def tree_menu(self, position):
        item = self.tree.itemAt(position)
        if item is None:
            return
        rel = item.data(0, ROLE)
        if not (self.unpacked / rel).is_dir():
            return
        menu = QMenu(self)
        if rel in self.roots:
            menu.addAction("Don't use as Data Files", lambda: self.set_root(rel, False))
        else:
            menu.addAction("Use as Data Files", lambda: self.set_root(rel, True))
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def set_root(self, rel, use):
        self.chosen = {root for root in self.roots
                       if self.items[root].checkState(0) != Qt.CheckState.Unchecked}
        if use:
            self.roots = [root for root in self.roots
                          if not (rel == "." or root == "." or root.startswith(rel + "/")
                                  or rel.startswith(root + "/"))]
            self.roots.append(rel)
            self.roots.sort(key=str.casefold)
            self.chosen.add(rel)
        else:
            self.roots.remove(rel)
        self.build_tree()

    def name(self):
        return self.name_field.text().strip()

    def version(self):
        return self.version_field.text().strip()

    def selection(self):
        result = []
        for root in self.roots:
            files = []
            prefix = "" if root == "." else root + "/"
            for rel, item in self.items.items():
                if (rel.startswith(prefix) and rel != root and (self.unpacked / rel).is_file()
                        and item.checkState(0) == Qt.CheckState.Checked):
                    files.append(rel[len(prefix):])
            if files:
                result.append((root, files))
        return result

    def accept(self):
        if not self.name():
            QMessageBox.warning(self, "TES3X", "Give the mod a name")
            return
        if not self.selection():
            QMessageBox.warning(self, "TES3X", "Nothing is ticked to install")
            return
        problem = self.taken(self.name()) if self.taken else None
        if problem:
            QMessageBox.warning(self, "TES3X", problem)
            return
        super().accept()


class ComponentsDialog(QDialog):
    def __init__(self, title, components, chosen, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.list = QListWidget()
        for component in components:
            item = QListWidgetItem(component["name"])
            item.setData(ROLE, component["id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if component["id"] in chosen
                               else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Optional parts to install"))
        layout.addWidget(self.list)
        layout.addWidget(buttons)

    def chosen(self):
        return [self.list.item(i).data(ROLE) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked]


class IniPanel(QWidget):
    """The Morrowind.ini a build ships: retail values, patch keys and the profile's changes."""

    HIDDEN = {"game files"}
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.retail = {}
        self.patch_keys = {}
        self.values = {}
        self.loading = False
        self.expanded = {"xbox"}
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter settings…")
        self.search.textChanged.connect(self.refresh)
        self.changed_only = QCheckBox("Changed only")
        self.changed_only.toggled.connect(self.refresh)
        add = QPushButton("Add setting…")
        add.clicked.connect(self.add_setting)
        reset = QPushButton("Reset selected")
        reset.clicked.connect(self.reset_selected)
        bar = QHBoxLayout()
        bar.addWidget(self.search, 1)
        bar.addWidget(self.changed_only)
        bar.addWidget(add)
        bar.addWidget(reset)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Setting", "Value", "From"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.itemDoubleClicked.connect(self.start_edit)
        self.tree.itemChanged.connect(self.edited)
        self.tree.itemExpanded.connect(lambda item: self.expanded.add(item.text(0).casefold()))
        self.tree.itemCollapsed.connect(
            lambda item: self.expanded.discard(item.text(0).casefold()))
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.tree.setColumnWidth(0, 280)
        self.tree.setColumnWidth(1, 220)
        note = QLabel("Double-click a value to change it. Only changed values are saved in the "
                      "profile. Patch settings appear while their patch is on; "
                      "docs/ini-keys.md explains them.")
        note.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(self.tree, 1)
        layout.addWidget(note)

    @staticmethod
    def split(name):
        section, _, key = name.partition(":")
        return section.strip().casefold(), key.strip().casefold()

    def load(self, values, retail_path=None):
        self.values = dict(values)
        self.retail = read_ini(retail_path) if retail_path else {}
        self.refresh()

    def set_patches(self, names):
        self.patch_keys = {}
        for entry in PATCH_CATALOG:
            if entry["name"] in names:
                for key, default in entry.get("ini", {}).items():
                    self.patch_keys[("xbox", key.casefold())] = ("Xbox", key, default,
                                                                entry["name"])
        self.refresh()

    def patch_owned(self, names):
        """Profile keys that only the named patches read."""
        keys = {("xbox", key.casefold()) for entry in PATCH_CATALOG if entry["name"] in names
                for key in entry.get("ini", {})}
        return [name for name in self.values
                if self.split(name) in keys and self.split(name) not in self.retail]

    def base(self, ident):
        if ident in self.retail:
            return self.retail[ident][2]
        if ident in self.patch_keys:
            return self.patch_keys[ident][2]
        return None

    def rows(self):
        rows = {}
        for ident, (section, key, _value) in self.retail.items():
            rows[ident] = (section, key)
        for ident, (section, key, _default, _patch) in self.patch_keys.items():
            rows.setdefault(ident, (section, key))
        for name in self.values:
            ident = self.split(name)
            section, _, key = name.partition(":")
            rows.setdefault(ident, (section.strip(), key.strip()))
        return rows

    def override_name(self, ident):
        return next((name for name in self.values if self.split(name) == ident), None)

    def refresh(self, *_args):
        self.loading = True
        self.tree.clear()
        query = self.search.text().casefold()
        sections = {}
        for ident, (section, key) in self.rows().items():
            if ident[0] in self.HIDDEN:
                continue
            name = self.override_name(ident)
            base = self.base(ident)
            value = ini_text(self.values[name]) if name else base or ""
            patch = self.patch_keys.get(ident)
            if name and base is None:
                source = "added"
            elif name:
                source = "changed"
            elif ident in self.retail:
                source = "retail"
            else:
                source = "default"
            if patch:
                source += f" · {patch[3]}"
            if self.changed_only.isChecked() and not name:
                continue
            if query and query not in f"{section}:{key}={value}".casefold():
                continue
            parent = sections.get(ident[0])
            if parent is None:
                parent = QTreeWidgetItem([section])
                parent.setFlags(Qt.ItemFlag.ItemIsEnabled)
                sections[ident[0]] = parent
            item = QTreeWidgetItem([key, value, source])
            item.setData(0, ROLE, ident)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                          | Qt.ItemFlag.ItemIsEditable)
            if name:
                font = item.font(0)
                font.setBold(True)
                for column in range(3):
                    item.setFont(column, font)
                parent.setFont(0, font)
            if patch:
                item.setToolTip(0, f"Read by the {patch[3]} patch")
            parent.addChild(item)
        order = {section: index for index, section in
                 enumerate(dict.fromkeys(ident[0] for ident in self.retail))}
        for ident in sorted(sections, key=lambda value: (value != "xbox",
                                                         order.get(value, len(order)), value)):
            parent = sections[ident]
            self.tree.addTopLevelItem(parent)
            parent.setExpanded(bool(query) or self.changed_only.isChecked()
                               or ident in self.expanded or parent.font(0).bold())
        self.loading = False

    def start_edit(self, item, _column):
        if item.data(0, ROLE) is not None:
            self.tree.editItem(item, 1)

    def edited(self, item, column):
        if self.loading or column != 1:
            return
        ident = item.data(0, ROLE)
        text = item.text(1).strip()
        name = self.override_name(ident)
        base = self.base(ident)
        if text == (base or "") and (base is not None or not text):
            if name:
                del self.values[name]
        else:
            section, key = self.rows()[ident]
            self.values[name or f"{section}:{key}"] = ini_value(text)
        self.changed.emit()
        QTimer.singleShot(0, self.refresh)

    def set_value(self, name, value):
        section, _, key = name.partition(":")
        if not section.strip() or not key.strip():
            raise ValueError("use Section:Key")
        ident = self.split(name)
        existing = self.rows().get(ident)
        self.values[self.override_name(ident) or
                    (f"{existing[0]}:{existing[1]}" if existing
                     else f"{section.strip()}:{key.strip()}")] = value
        self.changed.emit()
        self.refresh()

    def skips_intro(self):
        return all(str(self.values.get(self.override_name(self.split(name)))).casefold()
                   == SKIP_MOVIE for name in INTRO_MOVIES)

    def set_skip_intro(self, on):
        for name in INTRO_MOVIES:
            current = self.override_name(self.split(name))
            if on:
                self.values[current or name] = SKIP_MOVIE
            elif current and str(self.values[current]).casefold() == SKIP_MOVIE:
                del self.values[current]
        self.changed.emit()
        self.refresh()

    def add_setting(self):
        name, ok = QInputDialog.getText(self, "Add setting", "Section:Key, e.g. General:Show FPS")
        if not ok or not name.strip():
            return
        value, ok = QInputDialog.getText(self, "Add setting", f"Value for {name.strip()}")
        if not ok:
            return
        try:
            self.set_value(name.strip(), ini_value(value.strip()))
        except ValueError as exc:
            QMessageBox.warning(self, "TES3X", str(exc))

    def reset_selected(self):
        for item in self.tree.selectedItems():
            name = self.override_name(item.data(0, ROLE)) if item.data(0, ROLE) else None
            if name:
                del self.values[name]
        self.changed.emit()
        self.refresh()


class BuildSettings(QWidget):
    """Profile settings outside mods, plugins, patches and ini, written to the same TOML keys."""

    PREFERENCE_CHOICES = (("Keep the player's setting", None), ("Normal", False),
                          ("Inverted", True))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.loading = False
        self.on_change = lambda: None
        self.on_library = lambda: None

        self.title = QLineEdit()
        self.title.setPlaceholderText("Retail title")
        self.install_dir = QLineEdit()
        self.install_dir.setPlaceholderText("Profile name")
        # Kept as an attribute alias until the target toolbar lands in the next phase.
        self.remote_root = self.install_dir
        self.install_layout = QComboBox()
        self.install_layout.addItem("Full game folder", "full")
        self.install_layout.addItem("Overlay on shared retail base", "overlay")
        self.library_path = QLineEdit()
        self.library_path.setPlaceholderText("From Settings")
        self.library_path.editingFinished.connect(lambda: self.on_library())
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse_library)
        library_row = QHBoxLayout()
        library_row.setContentsMargins(0, 0, 0, 0)
        library_row.addWidget(self.library_path)
        library_row.addWidget(browse)
        library_widget = QWidget()
        library_widget.setLayout(library_row)
        self.dashboard = QCheckBox("Write XBMC4Gamers dashboard files when a title is set")
        self.skip_intro = QCheckBox("Skip the logo and New Game movies")
        self.skip_intro.setToolTip("Sets [Movies] Morrowind Logo and New Game to a missing "
                                   "file in the INI tab")
        identity = QWidget()
        identity_layout = QVBoxLayout(identity)
        identity_layout.setContentsMargins(0, 0, 0, 0)
        form = LocalSettingsDialog.form_section(identity_layout, "Profile")
        form.addRow("Dashboard title", self.title)
        form.addRow("Install folder", self.install_dir)
        form.addRow("Install layout", self.install_layout)
        form.addRow("Mod library", library_widget)
        form.addRow("", self.dashboard)
        form.addRow("", self.skip_intro)

        self.mode = QComboBox()
        for label, value in (("Delta archive (needs LLVM)", "delta-bsa"),
                             ("Merged Morrowind.bsa", "merged-bsa"),
                             ("Loose files", "loose")):
            self.mode.addItem(label, value)
        self.mode.currentIndexChanged.connect(self.update_enabled)
        self.archive_name = QLineEdit()
        self.archive_name.setPlaceholderText("tes3xmods.bsa")
        self.archive_only = QCheckBox("Read assets only from archives, never loose files")
        self.drive_letter = QComboBox()
        self.drive_letter.addItems(list("CDEFGHIJKLMNOPQRSTUVWXYZ"))
        self.loose_assets = QTextEdit()
        self.loose_assets.setPlaceholderText("One pattern per line, e.g. textures/sky/*")
        self.loose_assets.setMaximumHeight(70)
        package = QWidget()
        package_layout = QVBoxLayout(package)
        package_layout.setContentsMargins(0, 0, 0, 0)
        form = LocalSettingsDialog.form_section(package_layout, "Packaging")
        form.addRow("Mode", self.mode)
        form.addRow("Archive name", self.archive_name)
        form.addRow("", self.archive_only)
        form.addRow("Keep loose", self.loose_assets)
        form.addRow("Game drive", self.drive_letter)

        self.max_texture_size = QComboBox()
        for size in (128, 256, 512, 1024, 2048):
            self.max_texture_size.addItem(str(size), size)
        self.convert_all = QCheckBox("Convert every mod texture, not only oversized ones")
        self.max_filename = QSpinBox()
        self.max_filename.setRange(8, 42)
        self.clear_cache = QCheckBox("Clear the Xbox's X/Y/Z cache after deploying")
        self.keep_assets = QTextEdit()
        self.keep_assets.setPlaceholderText("Patterns pruning must keep, one per line")
        self.keep_assets.setMaximumHeight(60)
        self.exclude = QTextEdit()
        self.exclude.setPlaceholderText("Built-in list: " + ", ".join(DEFAULT_EXCLUDE))
        self.exclude.setMaximumHeight(60)
        rules = QWidget()
        rules_layout = QVBoxLayout(rules)
        rules_layout.setContentsMargins(0, 0, 0, 0)
        form = LocalSettingsDialog.form_section(rules_layout, "Rules")
        form.addRow("Largest texture", self.max_texture_size)
        form.addRow("", self.convert_all)
        form.addRow("Longest file name", self.max_filename)
        form.addRow("", self.clear_cache)
        form.addRow("Always keep", self.keep_assets)
        form.addRow("Leave out", self.exclude)

        self.invert_look = QComboBox()
        for label, value in self.PREFERENCE_CHOICES:
            self.invert_look.addItem(label, value)
        preferences = QWidget()
        preferences_layout = QVBoxLayout(preferences)
        preferences_layout.setContentsMargins(0, 0, 0, 0)
        form = LocalSettingsDialog.form_section(preferences_layout, "Player preferences")
        form.addRow("Look up/down", self.invert_look)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 0, 20, 20)
        for group in (identity, package, rules, preferences):
            layout.addWidget(group)
        self.reset_button = QPushButton("Reset to defaults")
        self.reset_button.clicked.connect(self.reset_defaults)
        button_row = QHBoxLayout()
        button_row.addStretch()
        button_row.addWidget(self.reset_button)
        layout.addLayout(button_row)
        layout.addStretch()

        for widget in (self.title, self.install_dir, self.archive_name):
            widget.textChanged.connect(self.changed)
        for widget in (self.dashboard, self.archive_only, self.convert_all, self.clear_cache):
            widget.toggled.connect(self.changed)
        for widget in (self.install_layout, self.mode, self.drive_letter, self.max_texture_size,
                       self.invert_look):
            widget.currentIndexChanged.connect(self.changed)
        for widget in (self.loose_assets, self.keep_assets, self.exclude):
            widget.textChanged.connect(self.changed)
        self.max_filename.valueChanged.connect(self.changed)
        self.library_path.textChanged.connect(self.changed)

    def changed(self, *_args):
        if not self.loading:
            self.on_change()

    def reset_defaults(self):
        self.skip_intro.setChecked(False)
        self.load({})
        self.on_library()
        self.on_change()

    def browse_library(self):
        selected = QFileDialog.getExistingDirectory(self, "Mod library", self.library_path.text())
        if selected:
            self.library_path.setText(selected)
            self.on_library()

    def update_enabled(self, *_args):
        mode = self.mode.currentData()
        self.archive_name.setEnabled(mode == "delta-bsa")
        self.archive_only.setEnabled(mode != "loose")
        self.loose_assets.setEnabled(mode != "loose")

    @staticmethod
    def select(combo, value):
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    @staticmethod
    def lines(widget):
        return [line.strip() for line in widget.toPlainText().splitlines() if line.strip()]

    def load(self, plain):
        self.loading = True
        identity = plain.get("profile", {})
        rules = plain.get("rules", {})
        package = plain.get("package", {})
        self.title.setText(identity.get("title", ""))
        legacy = identity.get("remote_root", "").replace("\\", "/").rstrip("/")
        self.install_dir.setText(identity.get("install_dir") or legacy.rpartition("/")[2])
        self.select(self.install_layout, identity.get("install_layout", "full"))
        self.library_path.setText(identity.get("library", ""))
        self.dashboard.setChecked("xbmc4gamers" in identity.get("dashboards", ["xbmc4gamers"]))
        self.select(self.mode, package.get("mode", "delta-bsa"))
        self.archive_name.setText(package.get("archive_name", ""))
        self.archive_only.setChecked(package.get("archive_only", False))
        self.drive_letter.setCurrentText(package.get("drive_letter", "D").upper())
        self.loose_assets.setPlainText("\n".join(package.get("loose_assets", [])))
        size = rules.get("max_texture_size", 512)
        if self.max_texture_size.findData(size) < 0:
            self.max_texture_size.addItem(str(size), size)
        self.select(self.max_texture_size, size)
        self.convert_all.setChecked(rules.get("convert_all_textures", False))
        self.max_filename.setValue(rules.get("max_filename", 42))
        self.clear_cache.setChecked(rules.get("clear_cache_partitions", False))
        self.keep_assets.setPlainText("\n".join(rules.get("keep_assets", [])))
        self.exclude.setPlainText("\n".join(rules.get("exclude", [])))
        self.select(self.invert_look, plain.get("preferences", {}).get("invert_look"))
        self.update_enabled()
        self.loading = False

    def library(self):
        return self.library_path.text().strip()

    def excluded(self):
        return self.lines(self.exclude) or DEFAULT_EXCLUDE

    def package_values(self):
        values = {"mode": self.mode.currentData()}
        if self.archive_only.isChecked() and self.mode.currentData() != "loose":
            values["archive_only"] = True
        return values

    def preference_values(self):
        value = self.invert_look.currentData()
        return {} if value is None else {"invert_look": value}

    @staticmethod
    def put(document, section, key, value, default):
        """Write a value, leaving an absent key absent while it matches the default."""
        table = document.get(section)
        if value is None or (value == default and (table is None or key not in table)):
            if table is not None and key in table:
                del table[key]
                drop_empty(document, section)
            return
        if table is None:
            table = new_table(document, section)
        if table.get(key) != value:
            table[key] = value

    def apply(self, document):
        put = lambda *args: self.put(document, *args)
        put("profile", "title", self.title.text().strip() or None, None)
        put("profile", "install_dir", self.install_dir.text().strip() or None, None)
        put("profile", "remote_root", None, None)
        put("profile", "install_layout", self.install_layout.currentData(), "full")
        put("profile", "library", self.library() or None, None)
        put("profile", "dashboards", ["xbmc4gamers"] if self.dashboard.isChecked() else [],
            ["xbmc4gamers"])
        mode = self.mode.currentData()
        put("package", "mode", mode, "delta-bsa")
        put("package", "archive_name", self.archive_name.text().strip() or None, None)
        put("package", "archive_only", self.archive_only.isChecked() and mode != "loose", False)
        put("package", "drive_letter", self.drive_letter.currentText(), "D")
        put("package", "loose_assets", self.lines(self.loose_assets), [])
        put("rules", "max_texture_size", self.max_texture_size.currentData(), 512)
        put("rules", "convert_all_textures", self.convert_all.isChecked(), False)
        put("rules", "max_filename", self.max_filename.value(), 42)
        put("rules", "clear_cache_partitions", self.clear_cache.isChecked(), False)
        put("rules", "keep_assets", self.lines(self.keep_assets), [])
        put("rules", "exclude", self.lines(self.exclude) or None, None)
        put("preferences", "invert_look", self.invert_look.currentData(), None)


class ProfileWindow(QMainWindow):
    MOD_NAME, MOD_VERSION, MOD_CONFLICTS, MOD_NOTES, MOD_PRIORITY, MOD_XBOX = range(6)
    nexus_done = Signal(str, object, object)
    in_game_event = Signal(object)

    def __init__(self, profile=None, config=None, settings=None):
        super().__init__()
        self.setWindowTitle(version_label())
        self.resize(1280, 800)
        self.profile_path = None
        self.document = None
        self.library_root = None
        self.catalog = {}
        self.library_indexed = False
        self.process = None
        self.play_process = None
        self.play_pid = None
        self.play_output_buffer = ""
        self.ftp_probe = None
        self.agent_probe = None
        self.in_game_listener = None
        self.agent_fingerprint = None
        self.agent_logs = defaultdict(list)
        self.drive_probe = None
        self.command_kind = None
        self.command_target = None
        self.check_profile_sha = None
        self.check_failed = False
        self.build_failed = False
        self.deploy_failed = False
        self.deploy_records = {}
        self.profile_plain = {}
        self.patch_modes = {}
        self.patch_categories = []
        self.patch_loading = False
        self.applied_patches = set()
        self.ini_stash = {}
        self.plugin_order = None
        self.mods_loading = False
        self.plugins_loading = False
        self.scans = {}
        self.masters = {}
        self.nexus_cache = {}
        self.nexus_links = {}
        self.nexus_pending = set()
        self.nexus_done.connect(self.nexus_finished)
        self.in_game_event.connect(self.handle_in_game_event)
        self.forget_analysis()
        try:
            self.compat = load_catalog()
        except (OSError, ValueError, CatalogError):
            self.compat = {}
        self.config_path = Path(config).resolve() if config else None
        self.settings = settings
        self.developer_mode = (self.settings.value("developer_mode", False, bool)
                               if self.settings else False)
        self.saved_text = None
        self.analysis_timer = QTimer(self)
        self.analysis_timer.setSingleShot(True)
        self.analysis_timer.setInterval(150)
        self.analysis_timer.timeout.connect(self.refresh_analysis)

        self.profile_picker = QComboBox()
        self.profile_picker.setMinimumWidth(260)
        self.profile_picker.activated.connect(self.picker_activated)
        profile_bar = QHBoxLayout()
        profile_bar.addWidget(QLabel("Profile"))
        profile_bar.addWidget(self.profile_picker)
        profile_actions = QToolButton()
        profile_actions.setText("…")
        profile_actions.setToolTip("Profile actions")
        profile_actions.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        profile_menu = QMenu(profile_actions)
        for label, handler in (("New…", self.new_profile), ("Duplicate…", self.duplicate_profile),
                               ("Rename…", self.rename_profile), ("Delete", self.delete_profile)):
            profile_menu.addAction(label, handler)
        profile_actions.setMenu(profile_menu)
        profile_bar.addWidget(profile_actions)
        profile_bar.addStretch()
        profile_bar.addWidget(QLabel("Target"))
        self.target_picker = QComboBox()
        self.target_picker.setMinimumWidth(155)
        self.target_picker.activated.connect(self.target_activated)
        profile_bar.addWidget(self.target_picker)
        self.target_states = {}
        self.target_runtime = {}
        self.target_drive_tips = {}
        self.profile_bar = profile_bar

        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.mod_details = self.create_mod_details()
        self.tabs = QTabWidget()
        self.tabs.addTab(self.create_mods_tab(), "Mods")
        self.tabs.addTab(self.create_plugins_panel(), "Plugins")
        self.archives = QTreeWidget()
        self.archives.setHeaderLabels(["Archive", "Provided by", "Loaded"])
        self.archives.setRootIsDecorated(False)
        self.archives.itemSelectionChanged.connect(self.show_context_info)
        self.tabs.addTab(self.archives, "Archives")
        self.tabs.addTab(self.create_files_panel(), "Data Files")
        self.tabs.addTab(self.create_patches_tab(), "Patches")
        self.ini = IniPanel()
        self.ini.changed.connect(self.refresh_status)
        self.ini.tree.itemSelectionChanged.connect(self.show_context_info)
        self.tabs.addTab(self.ini, "INI")
        self.tabs.addTab(self.create_resources_panel(), "Resources")
        self.build = BuildSettings()
        self.build.on_change = self.build_changed
        self.build.on_library = self.library_changed
        self.build.skip_intro.toggled.connect(self.skip_intro_toggled)
        self.ini.changed.connect(self.sync_skip_intro)
        build_scroll = QScrollArea()
        build_scroll.setWidgetResizable(True)
        build_scroll.setWidget(self.build)
        self.tabs.addTab(self.create_saves_tab(), "Saves")
        self.tabs.addTab(build_scroll, "Build")
        self.tabs.currentChanged.connect(self.saves_tab_shown)
        self.tabs.currentChanged.connect(self.show_context_info)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addLayout(profile_bar)
        self.context_info = QTextBrowser()
        self.context_info.setPlaceholderText("Select an item to see its details")
        self.details_stack = QStackedWidget()
        self.details_stack.addWidget(self.mod_details)
        self.details_stack.addWidget(self.context_info)
        self.details_container = QWidget()
        details_layout = QVBoxLayout(self.details_container)
        details_layout.setContentsMargins(0, self.tabs.tabBar().sizeHint().height(), 0, 0)
        details_layout.addWidget(self.details_stack)
        self.content_split = QSplitter(Qt.Orientation.Horizontal)
        self.content_split.addWidget(self.tabs)
        self.content_split.addWidget(self.details_container)
        self.content_split.setStretchFactor(0, 3)
        self.content_split.setStretchFactor(1, 2)
        self.content_split.setSizes([780, 500])
        self.body_split = QSplitter(Qt.Orientation.Vertical)
        self.body_split.addWidget(self.content_split)
        self.body_split.addWidget(self.output)
        self.body_split.setStretchFactor(0, 4)
        self.body_split.setStretchFactor(1, 1)
        self.body_split.setSizes([660, 120])
        layout.addWidget(self.body_split)
        self.setCentralWidget(body)
        self.setStatusBar(StatusBar())
        self.counts = QLabel()
        self.counts.setContentsMargins(0, 0, 8, 0)
        self.statusBar().addPermanentWidget(self.counts)
        self.ftp_timer = QTimer(self)
        self.ftp_timer.setInterval(60_000)
        self.ftp_timer.timeout.connect(self.refresh_ftp_status)

        self.menuBar().setFont(self.tabs.tabBar().font())
        self.menuBar().setStyleSheet("QMenuBar::item { padding: 6px 10px; }")
        file_menu = self.menuBar().addMenu("&File")
        self.action_new = QAction("&New profile…", self)
        self.action_new.setShortcut("Ctrl+N")
        self.action_new.triggered.connect(self.new_profile)
        self.action_open = QAction("&Open profile file…", self)
        self.action_open.setShortcut("Ctrl+O")
        self.action_open.triggered.connect(self.open_dialog)
        self.action_save = QAction("&Save profile", self)
        self.action_save.setShortcut("Ctrl+S")
        self.action_save.triggered.connect(self.save_profile)
        self.action_install = QAction("&Install mod…", self)
        self.action_install.setShortcut("Ctrl+I")
        self.action_install.triggered.connect(self.choose_install)
        self.action_install_folder = QAction("Install mod from &folder…", self)
        self.action_install_folder.triggered.connect(self.choose_install_folder)
        self.action_refresh = QAction("&Refresh library", self)
        self.action_refresh.setShortcut("F5")
        self.action_refresh.triggered.connect(self.reload_library)
        self.action_settings = QAction("&Settings…", self)
        self.action_settings.triggered.connect(self.edit_local_settings)
        self.action_index = QAction("Index new library folders", self)
        self.action_index.triggered.connect(self.write_library_index)
        self.action_convert = QAction("Convert folder names to library ids", self)
        self.action_convert.triggered.connect(self.convert_to_ids)
        self.action_exit = QAction("E&xit", self)
        self.action_exit.triggered.connect(self.close)
        file_menu.addActions([self.action_new, self.action_open, self.action_save])
        file_menu.addSeparator()
        file_menu.addActions([self.action_install, self.action_install_folder,
                              self.action_refresh])
        file_menu.addSeparator()
        file_menu.addActions([self.action_settings, self.action_index, self.action_convert])
        file_menu.addSeparator()
        file_menu.addAction(self.action_exit)

        view_menu = self.menuBar().addMenu("&View")
        self.action_developer_mode = QAction("&Developer mode", self)
        self.action_developer_mode.setCheckable(True)
        self.action_developer_mode.setChecked(self.developer_mode)
        self.action_developer_mode.setToolTip("Allow selecting development-channel patches")
        self.action_developer_mode.toggled.connect(self.set_developer_mode)
        view_menu.addAction(self.action_developer_mode)

        actions_menu = self.menuBar().addMenu("&Actions")
        self.action_check = QAction("&Check profile", self)
        self.action_check.setShortcut("Ctrl+Shift+C")
        self.action_check.triggered.connect(lambda: self.run_pipeline(["--check"]))
        self.action_build = QAction("&Build profile", self)
        self.action_build.setShortcut("Ctrl+B")
        self.action_build.triggered.connect(lambda: self.run_pipeline([]))
        self.action_smoke = QAction("Run &smoke test", self)
        self.action_smoke.setShortcut("Ctrl+T")
        self.action_smoke.triggered.connect(self.run_smoke_test)
        self.action_deploy = QAction("&Deploy to Xbox…", self)
        self.action_deploy.triggered.connect(self.deploy_profile)
        self.action_fetch = QAction("Pull Xbox &logs", self)
        self.action_fetch.triggered.connect(self.pull_logs)
        self.action_refresh_ftp = QAction("Refresh Xbox connection", self)
        self.action_refresh_ftp.triggered.connect(self.refresh_ftp_status)
        self.discard_after_deploy = QAction("Discard build after verified deploy", self)
        self.discard_after_deploy.setCheckable(True)
        self.action_play = QAction("&Play in xemu", self)
        self.action_play.setShortcut("F9")
        self.action_play.triggered.connect(lambda: self.play())
        self.action_stop = QAction("Stop xemu", self)
        self.action_stop.setShortcut("Shift+F9")
        self.action_stop.setEnabled(False)
        self.action_stop.triggered.connect(self.stop_play)
        self.action_reset_play = QAction("Reset xemu saves…", self)
        self.action_reset_play.triggered.connect(self.reset_play_disk)
        actions_menu.addActions([self.action_check, self.action_build, self.action_smoke])
        actions_menu.addSeparator()
        actions_menu.addActions([self.action_deploy, self.action_play, self.action_stop,
                                 self.action_reset_play])
        actions_menu.addSeparator()
        actions_menu.addActions([self.action_fetch, self.action_refresh_ftp])
        actions_menu.addSeparator()
        actions_menu.addAction(self.discard_after_deploy)
        command_group = QWidget()
        command_layout = QHBoxLayout(command_group)
        command_layout.setContentsMargins(0, 0, 0, 0)
        command_layout.setSpacing(0)
        self.command_buttons = []
        for action, theme, fallback, accent in (
                (self.action_check, None, QStyle.StandardPixmap.SP_DialogApplyButton, None),
                (self.action_build, QIcon.ThemeIcon.ViewRefresh,
                 QStyle.StandardPixmap.SP_BrowserReload, "#1976d2"),
                (self.action_deploy, QIcon.ThemeIcon.DocumentSend,
                 QStyle.StandardPixmap.SP_ArrowUp, "#d97706"),
                (self.action_play, QIcon.ThemeIcon.MediaPlaybackStart,
                 QStyle.StandardPixmap.SP_MediaPlay, "#2e7d32")):
            standard = self.style().standardIcon(fallback)
            icon = QIcon.fromTheme(theme, standard) if theme else standard
            action.setIcon(tinted_icon(icon, accent) if accent else icon)
            action.setProperty("accentColour", accent)
            button = QToolButton()
            button.setDefaultAction(action)
            button.setText(action.text().replace("&", "").replace("…", "").split()[0])
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setToolTip(action.text().replace("&", "") + (
                f" ({action.shortcut().toString()})" if not action.shortcut().isEmpty() else ""))
            if action is self.action_play:
                self.profile_bar.addWidget(command_group)
                self.profile_bar.addWidget(button)
                self.play_button = button
                self.play_menu = QMenu(self)
                self.play_menu.setToolTipsVisible(True)
                button.setMenu(self.play_menu)
                button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
            else:
                self.command_buttons.append(button)
                command_layout.addWidget(button)
                if action is self.action_check:
                    self.check_button = button
                elif action is self.action_build:
                    self.build_button = button
                elif action is self.action_deploy:
                    self.deploy_button = button
        command_group.setStyleSheet(
            "QToolButton { border: 1px solid palette(mid); padding: 4px 8px; "
            "border-radius: 0; } QToolButton:first-child { border-top-left-radius: 4px; "
            "border-bottom-left-radius: 4px; }")
        self.command_actions = (self.action_check, self.action_build, self.action_deploy,
                                self.action_play, self.action_smoke, self.action_fetch)
        self.after_command = None
        self.conflict_retry = None
        self.space_retry = None
        self.play_gdb = bool(self.settings and self.settings.value("play_gdb", False, bool))
        self.play_run = None
        self.refresh_targets()
        self.refresh_play_menu()
        self.update_build_state()
        self.state_timer = QTimer(self)
        self.state_timer.setInterval(1500)
        self.state_timer.timeout.connect(self.update_build_state)
        self.state_timer.start()
        self.refresh_profile_list()
        if profile:
            self.open_profile(Path(profile))
        else:
            self.open_initial_profile()
        if QApplication.platformName() != "offscreen":
            self.start_in_game_listener()
            if not self.local_config_path().is_file():
                QTimer.singleShot(0, self.first_run)
            QTimer.singleShot(0, self.refresh_ftp_status)
            self.ftp_timer.start()

    # Mods tab

    def create_mod_details(self):
        self.mod_info = QTextBrowser()
        self.mod_info.setOpenLinks(False)
        self.mod_info.anchorClicked.connect(self.mod_info_link)
        self.mod_info.setPlaceholderText("Select a mod to see what it is")

        self.mod_contents_summary = QLabel("Select a mod to see its contents")
        self.mod_contents_summary.setWordWrap(True)
        self.mod_plugins = QTreeWidget()
        self.mod_plugins.setHeaderLabels(["Plugin", "Build result", "Description"])
        self.mod_plugins.setRootIsDecorated(False)
        self.mod_archives = QTreeWidget()
        self.mod_archives.setHeaderLabels(["Archive", "Build result"])
        self.mod_archives.setRootIsDecorated(False)
        self.mod_files_model = FilesModel(("File", "Type", "Build result"))
        self.mod_files_filter = FilesFilter()
        self.mod_files_filter.setSourceModel(self.mod_files_model)
        search = QLineEdit()
        search.setPlaceholderText("Filter this mod's files…")
        search.textChanged.connect(lambda value: self.mod_files_filter.update(text=value))
        self.mod_files = QTableView()
        self.mod_files.setModel(self.mod_files_filter)
        self.mod_files.setSortingEnabled(True)
        self.mod_files.verticalHeader().hide()
        self.mod_files.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.mod_files.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.mod_files.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        files = QWidget()
        files_layout = QVBoxLayout(files)
        files_layout.setContentsMargins(0, 0, 0, 0)
        files_layout.addWidget(search)
        files_layout.addWidget(self.mod_files)
        self.mod_contents = QTabWidget()
        self.mod_contents.addTab(self.mod_plugins, "Plugins")
        self.mod_contents.addTab(self.mod_archives, "Archives")
        self.mod_contents.addTab(files, "Files")
        contents = QWidget()
        contents_layout = QVBoxLayout(contents)
        contents_layout.setContentsMargins(0, 0, 0, 0)
        contents_layout.addWidget(self.mod_contents_summary)
        contents_layout.addWidget(self.mod_contents, 1)
        details = QSplitter(Qt.Orientation.Vertical)
        details.addWidget(self.mod_info)
        details.addWidget(contents)
        details.setStretchFactor(0, 2)
        details.setStretchFactor(1, 3)
        details.setSizes([300, 450])
        return details

    def show_context_info(self, *_args):
        """Show details for the selected item in the current primary tab."""
        if not hasattr(self, "context_info"):
            return
        tab = self.tabs.tabText(self.tabs.currentIndex())
        if tab == "Mods":
            self.details_stack.setCurrentWidget(self.mod_details)
            self.show_mod_info()
            return
        self.details_stack.setCurrentWidget(self.context_info)
        if tab == "Saves":
            self.show_save_info()
            return
        if tab == "Plugins":
            items = self.plugin_list.selectedItems()
            if len(items) != 1:
                self.context_info.setPlainText("Select a plugin to see its details.")
                return
            item = items[0]
            key = item.data(0, ROLE)
            lines = [item.text(0), "", f"Provided by: {item.text(1)}"]
            if item.text(2):
                lines.append(f"Load index: {item.text(2)}")
            if key and key in self.analysis.get("plugins", {}):
                value = self.analysis["plugins"][key]
                lines.append("Build result: " + ("Included" if value["included"] else "Disabled"))
                author, description = plugin_header(value["path"])
                masters = self.plugin_masters(value["path"])
                if author:
                    lines += ["", f"Author: {author}"]
                if description:
                    lines += ["", description]
                lines += ["", "Masters: " + (", ".join(masters) if masters else "None")]
            elif item.text(1) == "Placeholder":
                lines += ["", "Generated expansion master placeholder."]
            problem = item.toolTip(0)
            if problem:
                lines += ["", "Warning: " + problem]
            self.context_info.setPlainText("\n".join(lines))
        elif tab == "Archives":
            items = self.archives.selectedItems()
            if len(items) != 1:
                self.context_info.setPlainText("Select an archive to see its details.")
                return
            item = items[0]
            lines = [item.text(0), "", f"Provided by: {item.text(1)}",
                     f"Build treatment: {item.text(2)}"]
            if item.toolTip(2):
                lines += ["", item.toolTip(2)]
            self.context_info.setPlainText("\n".join(lines))
        elif tab == "Data Files":
            index = self.files_view.currentIndex()
            if not index.isValid():
                self.context_info.setPlainText("Select a file to see its provider chain.")
                return
            source = self.files_filter.mapToSource(index)
            path, owner, others = self.files_model.entries[source.row()]
            key = path.replace("\\", "/").casefold()
            providers = self.analysis.get("owners", {}).get(key, [])
            active = self.analysis.get("active", [])
            lines = [path, "", f"Included from: {owner}"]
            if providers:
                winner = active[providers[-1]][0]
                lines.append("Build result: " + self.packaging_result(winner, key))
                lines += ["", "Providers, earlier to later:"]
                for number, provider in enumerate(providers, 1):
                    name = active[provider][0].text(0)
                    result = "included" if provider == providers[-1] else "overridden"
                    lines.append(f"  {number}. {name} — {result}")
            else:
                lines.append("Overrides: " + (others or "Nothing"))
            self.context_info.setPlainText("\n".join(lines))
        elif tab == "Patches":
            self.show_patch_details(self.patch_tree.currentItem(), None)
        elif tab == "INI":
            item = self.ini.tree.currentItem()
            ident = item.data(0, ROLE) if item else None
            if ident is None:
                self.context_info.setPlainText("Select an INI setting to see its details.")
                return
            section, key = self.ini.rows()[ident]
            name = self.ini.override_name(ident)
            base = self.ini.base(ident)
            value = self.ini.values[name] if name else base
            lines = [f"{section}:{key}", "", f"Effective value: {ini_text(value)}",
                     f"Source: {item.text(2)}"]
            if name and base is not None:
                lines.append(f"Base value: {base}")
            patch = self.ini.patch_keys.get(ident)
            if patch:
                lines += ["", f"Read by patch: {patch[3]}"]
            self.context_info.setPlainText("\n".join(lines))
        elif tab == "Resources":
            tree = (self.resource_budget if self.resource_tabs.currentIndex() == 0
                    else self.resource_dependencies)
            items = tree.selectedItems()
            if len(items) != 1:
                self.context_info.setPlainText(
                    "Select a budget or dependency row to see its details.")
                return
            detail = items[0].data(0, ROLE)
            self.context_info.setPlainText(detail or "\n".join(
                items[0].text(column) for column in range(items[0].columnCount())))
        else:
            self.context_info.setPlainText(self.build_preview())

    def build_preview(self):
        """Human-readable plan from the current, possibly unsaved controls."""
        active = self.analysis.get("active", [])
        plugins = [item.text(0) for item in self.plugin_list.rows()
                   if item.checkState(0) == Qt.CheckState.Checked]
        mode = self.build.mode.currentData()
        mode_text = {"delta-bsa": "Delta archive", "merged-bsa": "Rebuilt Morrowind.bsa",
                     "loose": "Loose files"}.get(mode, mode)
        destination = self.xbox_destination() or "Not set"
        title = self.build.title.text().strip() or "Retail title"
        profile = self.profile_path.stem if self.profile_path else "New"
        layout = ("Shared retail base" if self.build.install_layout.currentData() == "overlay"
                  else "Full game folder")
        lines = ["Build preview", "", f"Profile: {profile}", f"Dashboard title: {title}",
                 f"Xbox destination: {destination}", f"Install layout: {layout}",
                 f"Packaging: {mode_text}"]
        if mode == "delta-bsa":
            lines.append("Archive: " + (self.build.archive_name.text().strip()
                                         or "tes3xmods.bsa"))
        if self.build.archive_only.isChecked() and mode != "loose":
            lines.append("Asset lookup: archives only")
        lines += ["", f"Mods: {len(active)} active"]
        lines += [f"  {index}. {item.text(0)}" for index, (item, _mod) in enumerate(active, 1)]

        outcomes = defaultdict(int)
        for key, providers in self.analysis.get("owners", {}).items():
            if key.endswith((*PLUGIN_EXT, ".bsa")):
                continue
            winner = active[providers[-1]][0]
            outcomes[self.packaging_result(winner, key)] += 1
        if outcomes:
            lines += ["", "Assets:"]
            lines += [f"  {count} {result.casefold()}" for result, count in sorted(outcomes.items())]

        lines += ["", f"Plugins: {len(plugins)} active"]
        lines += [f"  {index:02X}  {name}" for index, name in enumerate(plugins[:20])]
        if len(plugins) > 20:
            lines.append(f"  … {len(plugins) - 20} more")
        archives = [self.archives.topLevelItem(i) for i in range(self.archives.topLevelItemCount())]
        lines += ["", "Archives:"]
        lines += [f"  {item.text(0)} — {item.text(2)}" for item in archives]
        patches = sorted(self.applied_patches)
        lines += ["", f"Engine patches: {len(patches)}"]
        lines += ["  " + name for name in patches[:20]]
        if len(patches) > 20:
            lines.append(f"  … {len(patches) - 20} more")
        lines += ["", "Deploy:",
                  "  Clear Xbox cache partitions" if self.build.clear_cache.isChecked()
                  else "  Keep Xbox cache partitions"]
        return "\n".join(lines)

    def create_mods_tab(self):
        install = QPushButton("Install mod…")
        install.setToolTip("Install a .zip, .7z or .rar archive, or a plugin, into the mod "
                           "library. You can also drop files on the list.")
        install.clicked.connect(self.choose_install)
        self.mod_search = QLineEdit()
        self.mod_search.setPlaceholderText("Filter mods…")
        self.mod_search.textChanged.connect(self.filter_mods)
        refresh = QAction(theme_icon(self, QIcon.ThemeIcon.ViewRefresh,
                                     QStyle.StandardPixmap.SP_BrowserReload),
                          "Refresh the mod list from the library (F5)", self)
        refresh.triggered.connect(self.reload_library)
        top = QHBoxLayout()
        top.addWidget(install)
        top.addWidget(self.mod_search, 1)
        top.addWidget(icon_button(refresh))

        self.mod_list = DragList(["Mod", "Version", "Conflicts", "Notes", "Priority", "Xbox"],
                                 accept_files=True)
        self.mod_list.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.mod_list.header().setStretchLastSection(False)
        self.mod_list.header().moveSection(self.MOD_XBOX, 1)
        self.mod_list.headerItem().setToolTip(
            self.MOD_XBOX, "\u2713 works, * works with requirements, \u2717 does not work, "
                           "? not known; from catalog.toml")
        self.mod_list.itemChanged.connect(self.mod_item_changed)
        self.mod_list.moved.connect(self.mods_moved)
        self.mod_list.dropped_files.connect(self.install_paths)
        self.mod_list.customContextMenuRequested.connect(self.mod_menu)
        self.mod_list.itemSelectionChanged.connect(self.highlight_conflicts)
        self.mod_list.itemSelectionChanged.connect(self.show_mod_info)
        self.mod_list.headerItem().setToolTip(
            self.MOD_CONFLICTS, "+N: files this mod overrides; -N: its files other mods override")
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self.mod_list, 1)
        return panel

    def create_plugins_panel(self):
        self.sort_button = QPushButton("Sort")
        self.sort_button.setToolTip("Sort the plugins with mlox now")
        self.sort_button.clicked.connect(self.sort_plugins)
        self.reset_order_button = QPushButton("Mod order")
        self.reset_order_button.setToolTip("Forget the plugin order; load plugins in mod order")
        self.reset_order_button.clicked.connect(self.reset_plugin_order)
        self.mlox_at_build = QCheckBox("Sort with mlox at every build")
        self.mlox_at_build.toggled.connect(self.mlox_toggled)
        bar = QHBoxLayout()
        bar.addWidget(self.sort_button)
        bar.addWidget(self.reset_order_button)
        bar.addWidget(self.mlox_at_build)
        bar.addStretch()
        self.plugin_list = DragList(["Plugin", "Source", "Index"])
        self.plugin_list.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.plugin_list.header().setStretchLastSection(False)
        self.plugin_list.itemChanged.connect(self.plugin_item_changed)
        self.plugin_list.moved.connect(self.plugins_moved)
        self.plugin_list.itemSelectionChanged.connect(self.show_context_info)
        self.plugin_note = QLabel()
        self.plugin_note.setWordWrap(True)
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(bar)
        layout.addWidget(self.plugin_list, 1)
        layout.addWidget(self.plugin_note)
        return panel

    def create_files_panel(self):
        self.files_model = FilesModel()
        self.files_filter = FilesFilter()
        self.files_filter.setSourceModel(self.files_model)
        search = QLineEdit()
        search.setPlaceholderText("Filter files…")
        search.textChanged.connect(lambda text: self.files_filter.update(text=text))
        conflicts = QCheckBox("Conflicts only")
        conflicts.toggled.connect(lambda value: self.files_filter.update(conflicts_only=value))
        bar = QHBoxLayout()
        bar.addWidget(search, 1)
        bar.addWidget(conflicts)
        self.files_view = QTableView()
        self.files_view.setModel(self.files_filter)
        self.files_view.setSortingEnabled(True)
        self.files_view.verticalHeader().hide()
        self.files_view.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.files_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.files_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.files_view.selectionModel().selectionChanged.connect(self.show_context_info)
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(bar)
        layout.addWidget(self.files_view, 1)
        return panel

    def create_resources_panel(self):
        self.resource_summary = QLabel("Waiting for profile analysis…")
        self.resource_summary.setWordWrap(True)
        self.resource_budget = QTreeWidget()
        self.resource_budget.setHeaderLabels(
            ["Mod", "Files", "Source size", "Textures", "Texture size", "Largest"])
        self.resource_budget.setRootIsDecorated(False)
        self.resource_budget.setAlternatingRowColors(True)
        self.resource_budget.itemSelectionChanged.connect(self.show_context_info)
        self.resource_dependencies = QTreeWidget()
        self.resource_dependencies.setHeaderLabels(["Kind", "Item", "Requires", "Status"])
        self.resource_dependencies.setRootIsDecorated(False)
        self.resource_dependencies.setAlternatingRowColors(True)
        self.resource_dependencies.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.resource_dependencies.itemSelectionChanged.connect(self.show_context_info)
        self.resource_tabs = QTabWidget()
        self.resource_tabs.addTab(self.resource_budget, "Budgets")
        self.resource_tabs.addTab(self.resource_dependencies, "Dependencies")
        self.resource_tabs.currentChanged.connect(self.show_context_info)
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.resource_summary)
        layout.addWidget(self.resource_tabs, 1)
        return panel

    def describe(self, entry):
        """Display name, version text, release and problem for a profile mod entry."""
        if "id" in entry:
            mod = self.catalog.get(entry["id"])
            if mod is None:
                return entry["id"], "", None, "not in the mod library"
            version = entry.get("version") or mod["default"] or next(iter(mod["releases"]))
            release = mod["releases"].get(version)
            if release is None:
                return mod["name"], version, None, f"version {version} is not installed"
            return mod["name"], "" if version == "unknown" else version, release, None
        name = entry["name"]
        if self.library_root is None or not (self.library_root / name).exists():
            return name, "", None, "folder not found in the mod library"
        release = next((release for mod in self.catalog.values()
                        for release in mod["releases"].values()
                        if release["folder"].casefold() == name.casefold()), None)
        version = release["version"] if release else ""
        return name, "" if version == "unknown" else version, release, None

    def entry_folders(self, entry):
        if "id" not in entry:
            return {entry["name"].casefold()}
        mod = self.catalog.get(entry["id"])
        return {release["folder"].casefold() for release in mod["releases"].values()} if mod else set()

    def library_entry(self, mod):
        if self.library_indexed:
            return {"id": mod["id"]}
        return {"name": next(iter(mod["releases"].values()))["folder"]}

    def forget_analysis(self):
        """Drop row references before rows are deleted."""
        self.analysis = {"active": [], "plugins": {}, "mod_plugins": [], "beats": [],
                         "beaten": []}

    def take_mod(self, item):
        self.forget_analysis()
        self.mod_list.takeTopLevelItem(self.mod_list.indexOfTopLevelItem(item))

    def populate_mods(self, entries):
        self.mods_loading = True
        self.forget_analysis()
        self.mod_list.clear()
        used_ids, used_folders = set(), set()
        for entry in sorted(entries, key=lambda item: item.get("order", 0)):
            self.add_mod_item(dict(entry), True)
            used_ids.add(entry.get("id"))
            used_folders |= self.entry_folders(entry)
        for mod in sorted(self.catalog.values(), key=lambda item: item["name"].casefold()):
            folders = {release["folder"].casefold() for release in mod["releases"].values()}
            if mod["id"] in used_ids or folders & used_folders:
                continue
            self.add_mod_item(self.library_entry(mod), False)
        self.mods_loading = False
        self.filter_mods()
        self.schedule_analysis()

    def add_mod_item(self, entry, in_profile, index=None):
        item = QTreeWidgetItem()
        item.setFlags(ROW_FLAGS)
        item.setData(0, ROLE, entry)
        item.setData(0, EXTRA, in_profile)
        if index is None:
            self.mod_list.addTopLevelItem(item)
        else:
            self.mod_list.insertTopLevelItem(index, item)
        self.update_mod_item(item)
        return item

    def update_mod_item(self, item):
        loading, self.mods_loading = self.mods_loading, True
        entry = item.data(0, ROLE)
        name, version, release, problem = self.describe(entry)
        item.setText(self.MOD_NAME, name)
        item.setText(self.MOD_VERSION, version)
        notes = []
        if entry.get("components"):
            notes.append(f"{len(entry['components'])} option(s)")
        if entry.get("loose"):
            notes.append("loose")
        if entry.get("optional"):
            notes.append("optional")
        if entry.get("archives") == "load":
            notes.append("archives via multi-bsa")
        if problem:
            notes.insert(0, problem)
        item.setData(self.MOD_NOTES, ROLE, notes)
        item.setText(self.MOD_NOTES, ", ".join(notes))
        on = item.data(0, EXTRA) and entry.get("enabled", True)
        item.setCheckState(self.MOD_NAME, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        colour = WARNING if problem else self.palette().text().color()
        for column in range(self.mod_list.columnCount()):
            item.setForeground(column, colour)
        source = release.get("source") if release else None
        item.setToolTip(self.MOD_NAME, problem or (f"Installed from {source}" if source else ""))
        self.update_compat(item)
        self.mods_loading = loading

    def update_compat(self, item):
        """The catalog's verdict on a mod, and any requirement this profile does not meet."""
        entry = item.data(0, ROLE)
        verdict = self.compat_verdict(item)
        status = verdict["status"] if verdict else "untested"
        symbol, colour = COMPAT.get(status, ("?", self.palette().placeholderText().color()))
        lines = [f"Xbox compatibility: {COMPAT_LABELS[status]}" if verdict
                 else "Not in the compatibility catalog"]
        if verdict:
            if catalog_needs(verdict):
                lines.append("Needs " + ", ".join(value.replace("`", "")
                                                  for value in catalog_needs(verdict)))
            off = [patch for patch in verdict.get("patches", [])
                   if patch not in self.applied_patches]
            if off and item.data(0, EXTRA) and entry.get("enabled", True):
                colour = WARNING
                lines.append("Turn on in Patches: " + ", ".join(off))
            if verdict.get("notes"):
                lines.append(verdict["notes"])
        item.setText(self.MOD_XBOX, symbol)
        item.setForeground(self.MOD_XBOX, colour)
        item.setTextAlignment(self.MOD_XBOX, Qt.AlignmentFlag.AlignCenter)
        item.setToolTip(self.MOD_XBOX, "\n".join(lines))

    def mod_rows(self):
        return self.mod_list.rows()

    def filter_mods(self, *_args):
        query = self.mod_search.text().casefold()
        for item in self.mod_rows():
            item.setHidden(bool(query) and query not in item.text(self.MOD_NAME).casefold())

    def set_entry(self, item, entry, in_profile=True):
        item.setData(0, ROLE, entry)
        item.setData(0, EXTRA, in_profile)
        self.update_mod_item(item)

    def mod_item_changed(self, item, column):
        if self.mods_loading or column != self.MOD_NAME:
            return
        entry = dict(item.data(0, ROLE))
        on = item.checkState(self.MOD_NAME) == Qt.CheckState.Checked
        if on == (bool(item.data(0, EXTRA)) and entry.get("enabled", True)):
            return
        if on:
            entry.pop("enabled", None)
            mod = self.catalog.get(entry.get("id"))
            if mod and not mod["default"] and len(mod["releases"]) > 1 and "version" not in entry:
                entry["version"] = next(iter(mod["releases"]))
        else:
            entry["enabled"] = False
        self.set_entry(item, entry)
        if on:
            self.add_dependencies(item)
            self.enable_required_patches(item)
        self.mods_changed()

    def enable_required_patches(self, item):
        """Turn on the patches the catalog says a mod needs."""
        name, _version, release, _problem = self.describe(item.data(0, ROLE))
        entry = item.data(0, ROLE)
        verdict = match_catalog(self.compat, name, entry.get("name", ""), entry.get("id", ""),
                                release["folder"] if release else "")
        off = [patch for patch in (verdict or {}).get("patches", [])
               if patch in self.patch_items and patch not in self.applied_patches]
        for patch in off:
            self.set_patch(patch, True)
        if off:
            self.statusBar().showMessage(f"{name} needs {', '.join(off)}; turned on in Patches",
                                         8000)

    def add_dependencies(self, item):
        _name, _version, release, _problem = self.describe(item.data(0, ROLE))
        for dependency in (release or {}).get("dependencies", []):
            row = next((other for other in self.mod_rows()
                        if other.data(0, ROLE).get("id") == dependency), None)
            if row is None:
                continue
            entry = dict(row.data(0, ROLE))
            entry.pop("enabled", None)
            self.set_entry(row, entry)
            if self.mod_list.indexOfTopLevelItem(row) > self.mod_list.indexOfTopLevelItem(item):
                self.mod_list.takeTopLevelItem(self.mod_list.indexOfTopLevelItem(row))
                self.mod_list.insertTopLevelItem(self.mod_list.indexOfTopLevelItem(item), row)
            self.add_dependencies(row)

    def mods_moved(self):
        for item in self.mod_list.selectedItems():
            if not item.data(0, EXTRA):
                entry = dict(item.data(0, ROLE))
                entry["enabled"] = False
                self.set_entry(item, entry)
        self.mods_changed()

    def mods_changed(self):
        self.schedule_analysis()
        self.refresh_patch_states()

    def selected_mod(self):
        items = self.mod_list.selectedItems()
        return items[0] if len(items) == 1 else None

    def mod_menu(self, position):
        item = self.mod_list.itemAt(position)
        if item is None:
            return
        if item not in self.mod_list.selectedItems():
            self.mod_list.clearSelection()
            item.setSelected(True)
        items = self.mod_list.selectedItems()
        entry = item.data(0, ROLE)
        _name, _version, release, _problem = self.describe(entry)
        menu = QMenu(self)
        menu.addAction("Enable", lambda: self.set_checked(items, True))
        menu.addAction("Disable", lambda: self.set_checked(items, False))
        menu.addSeparator()
        menu.addAction("Send to top", lambda: self.mod_list.move_items(items, 0))
        menu.addAction("Send to bottom",
                       lambda: self.mod_list.move_items(items, self.mod_list.topLevelItemCount()))
        if len(items) == 1:
            mod = self.catalog.get(entry.get("id"))
            if mod and len(mod["releases"]) > 1:
                versions = menu.addMenu("Version")
                current = entry.get("version") or mod["default"]
                for version in mod["releases"]:
                    action = versions.addAction(version,
                                                lambda value=version: self.set_version(item, value))
                    action.setCheckable(True)
                    action.setChecked(version == current)
            options = menu.addAction("Options…", lambda: self.choose_components(item))
            options.setEnabled(bool(release and release["components"] and "id" in entry))
            for key, label in (("loose", "Ship files loose"),
                               ("optional", "Skip if the folder is missing")):
                action = menu.addAction(label, lambda key=key: self.toggle_flag(item, key))
                action.setCheckable(True)
                action.setChecked(bool(entry.get(key)))
            load = menu.addAction("Load archives with multi-bsa",
                                  lambda: self.toggle_archives(item))
            load.setCheckable(True)
            load.setChecked(entry.get("archives") == "load")
            load.setToolTip("Ship this mod's .bsa files and have the multi-bsa patch open them, "
                            "instead of unpacking them into the build")
            menu.setToolTipsVisible(True)
            menu.addSeparator()
            folder = self.library_root / release["folder"] if release and self.library_root else (
                self.library_root / entry["name"] if "name" in entry and self.library_root
                else None)
            open_folder = menu.addAction(
                "Open folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))))
            open_folder.setEnabled(bool(folder and folder.exists()))
            rename = menu.addAction("Rename…", lambda: self.rename_mod(item))
            rename.setEnabled(self.library_indexed and "id" in entry
                              and entry["id"] in self.catalog)
            reinstall = menu.addAction("Reinstall…", lambda: self.reinstall_mod(item))
            reinstall.setEnabled(bool(folder and folder.is_dir()))
            page = self.mod_page(item)[0]
            web = menu.addAction("Open web page", lambda: QDesktopServices.openUrl(QUrl(page)))
            web.setEnabled(bool(page))
            menu.addAction("Find on Nexus…", lambda: self.find_on_nexus(item))
            menu.addSeparator()
            if item.data(0, EXTRA):
                menu.addAction("Remove from profile", lambda: self.remove_from_profile(item))
            delete = menu.addAction("Delete from library…", lambda: self.delete_mod(item))
            delete.setEnabled(bool(folder and folder.exists()))
        menu.exec(self.mod_list.viewport().mapToGlobal(position))

    # Mod details: what a mod is and where it comes from.

    def compat_verdict(self, item):
        entry = item.data(0, ROLE)
        name, _version, release, _problem = self.describe(entry)
        return match_catalog(self.compat, name, entry.get("name", ""), entry.get("id", ""),
                             release["folder"] if release else "")

    def mod_record(self, item):
        """The library's entry for a row, which holds its page and description."""
        entry = item.data(0, ROLE)
        if "id" in entry:
            return self.catalog.get(entry["id"])
        release = self.describe(entry)[2]
        return next((mod for mod in self.catalog.values()
                     if any(value is release for value in mod["releases"].values())), None)

    def mod_page(self, item):
        """A mod's web page and Nexus id, from the library, the catalog or its download name."""
        name, _version, release, _problem = self.describe(item.data(0, ROLE))
        mod = self.mod_record(item) or {}
        url = mod.get("url") or (self.compat_verdict(item) or {}).get("url")
        mod_id = (nexus_id(url, release.get("source") if release else None)
                  or self.nexus_links.get(name))
        return url or (nexus.page_url(mod_id) if mod_id else None), mod_id

    def show_mod_info(self):
        item = self.selected_mod()
        if item is None:
            self.mod_info.clear()
            self.populate_mod_contents(None)
            return
        entry = item.data(0, ROLE)
        name, version, _release, problem = self.describe(entry)
        mod = self.mod_record(item) or {}
        url, mod_id = self.mod_page(item)
        fetched = self.nexus_cache.get(mod_id)
        found = fetched if isinstance(fetched, dict) else {}
        summary = mod.get("summary") or found.get("summary", "")
        author = mod.get("author") or found.get("author", "")
        if mod_id and not mod.get("summary") and mod_id not in self.nexus_cache:
            self.nexus_lookup(f"id:{mod_id}", nexus.mod_info, mod_id)

        def text(value):
            return html.escape(value).replace("\r\n", "<br>").replace("\n", "<br>")

        parts = [f"<h3>{text(name)} <small>{text(version)}</small></h3>"]
        if author:
            parts.append(f"<p>by {text(author)}</p>")
        if summary:
            parts.append(f"<p>{text(summary)}</p>")
        elif f"id:{mod_id}" in self.nexus_pending:
            parts.append("<p><i>Fetching the description from Nexus…</i></p>")
        elif isinstance(fetched, Exception):
            parts.append(f"<p><i>No description from Nexus: {text(str(fetched))}</i></p>")
        links = [f"<a href='{html.escape(url)}'>{text(url)}</a>"] if url else []
        links.append("<a href='tes3x:find'>Find on Nexus…</a>")
        parts.append("<p>" + " · ".join(links) + "</p>")
        verdict = self.compat_verdict(item)
        if verdict:
            status = verdict["status"]
            parts.append(f"<p><b>Xbox compatibility:</b> {text(COMPAT_LABELS[status])}</p>")
            if verdict.get("notes"):
                parts.append(f"<p>{text(verdict['notes'])}</p>")
        if problem:
            parts.append(f"<p style='color:{WARNING.name()}'>{text(problem)}</p>")
        scanned = self.scan(entry) if self.library_root else None
        self.mod_info.setHtml("".join(parts))
        self.populate_mod_contents(item, scanned)

    def mod_build_result(self, item, key):
        """How one selected mod file reaches, or does not reach, the planned build."""
        active = self.analysis.get("active", [])
        index = next((i for i, (row, _mod) in enumerate(active) if row is item), None)
        if index is None:
            return "Not active"
        entry = item.data(0, ROLE)
        if key.endswith(PLUGIN_EXT) and "plugins" in entry:
            wanted = {name.casefold() for name in entry["plugins"]}
            if os.path.basename(key) not in wanted:
                return "Disabled"
        owners = self.analysis.get("owners", {}).get(key, [])
        if index not in owners:
            return "Not included"
        if owners[-1] != index:
            return "Overridden by " + active[owners[-1]][0].text(0)
        return self.packaging_result(item, key)

    def packaging_result(self, item, key):
        """Where an included winning file goes in the planned package."""
        entry = item.data(0, ROLE)
        if key.endswith(PLUGIN_EXT):
            return "Included loose"
        if key.endswith(".bsa"):
            return ("Loaded via multi-bsa" if entry.get("archives") == "load"
                    else "Unpacked at build")
        mode = self.build.mode.currentData()
        if mode == "loose":
            return "Included loose"
        if entry.get("loose"):
            return "Included loose by mod setting"
        path = key.replace("\\", "/")
        patterns = [pattern.casefold().replace("\\", "/")
                    for pattern in self.build.lines(self.build.loose_assets)]
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns):
            return "Included loose by path rule"
        if mode == "merged-bsa":
            return "Packed in rebuilt Morrowind.bsa"
        name = self.build.archive_name.text().strip() or "tes3xmods.bsa"
        return f"Packed in {name}"

    def populate_mod_contents(self, item, scanned=None):
        self.mod_plugins.clear()
        self.mod_archives.clear()
        self.mod_files_model.reset([])
        if item is None or not isinstance(scanned, Mod):
            self.mod_contents_summary.setText("Select a mod to see its contents")
            return
        plugins, archives, files = [], [], []
        total = 0
        for key, source in sorted(scanned.files.items()):
            try:
                total += os.path.getsize(source)
            except OSError:
                pass
            result = self.mod_build_result(item, key)
            name = scanned.relative.get(key, key)
            if key.endswith(PLUGIN_EXT):
                author, description = plugin_header(source)
                detail = (f"by {author}" if author else "")
                if description:
                    detail += (" — " if detail else "") + description
                plugins.append((name, result, detail))
            elif key.endswith(".bsa"):
                archives.append((name, result))
            else:
                parent = name.split("/", 1)[0] if "/" in name else Path(name).suffix.lstrip(".")
                files.append((name, parent.title() or "File", result))
        for row in plugins:
            self.mod_plugins.addTopLevelItem(QTreeWidgetItem(list(row)))
        for row in archives:
            self.mod_archives.addTopLevelItem(QTreeWidgetItem(list(row)))
        self.mod_files_model.reset(files)
        for tree in (self.mod_plugins, self.mod_archives):
            for column in range(tree.columnCount()):
                tree.resizeColumnToContents(column)
        state = ("Active" if any(row is item for row, _mod in self.analysis.get("active", []))
                 else "Not active")
        size = f"{total / 1048576:.1f} MB" if total >= 1048576 else f"{total / 1024:.1f} KB"
        excluded = f" · {scanned.excluded} excluded" if scanned.excluded else ""
        count = lambda values, noun: f"{len(values)} {noun}{'' if len(values) == 1 else 's'}"
        self.mod_contents_summary.setText(
            f"{state} · {count(plugins, 'plugin')} · {count(archives, 'archive')} · "
            f"{count(files, 'other file')} · {size}{excluded}")

    def mod_info_link(self, url):
        if url.scheme() != "tes3x":
            QDesktopServices.openUrl(url)
        elif self.selected_mod() is not None:
            self.find_on_nexus(self.selected_mod())

    def nexus_lookup(self, key, function, *args):
        """Run a Nexus query off the GUI thread; nexus_finished gets the answer."""
        if key in self.nexus_pending:
            return
        self.nexus_pending.add(key)

        def work():
            try:
                result, error = function(*args), None
            except (OSError, ValueError, KeyError, TypeError) as exc:
                result, error = None, exc
            self.nexus_done.emit(key, result, error)

        threading.Thread(target=work, daemon=True).start()

    def nexus_finished(self, key, result, error):
        self.nexus_pending.discard(key)
        kind, _, value = key.partition(":")
        if kind == "search":
            self.choose_nexus_match(value, result, error)
            return
        mod_id = int(value)
        self.nexus_cache[mod_id] = error or result or LookupError("Nexus has no such mod")
        if result:
            for item in self.mod_rows():
                if self.mod_page(item)[1] == mod_id:
                    self.store_nexus(item, result, replace=False)
        self.show_mod_info()

    def find_on_nexus(self, item):
        name = self.describe(item.data(0, ROLE))[0]
        text, ok = QInputDialog.getText(self, "Find on Nexus", "Mod name", text=name)
        if not ok or not text.strip():
            return
        self.nexus_search_item = item
        self.nexus_lookup("search:" + text.strip(), nexus.search, text.strip())
        self.statusBar().showMessage("Searching Nexus…", 5000)

    def choose_nexus_match(self, text, result, error):
        item = getattr(self, "nexus_search_item", None)
        if item is None or not any(row is item for row in self.mod_rows()):
            return
        if error:
            self.error(f"Nexus search failed: {error}")
            return
        if not result:
            QMessageBox.information(self, "Find on Nexus",
                                    f"No Morrowind mod on Nexus is named like “{text}”.")
            return
        result.sort(key=lambda found: found["name"].casefold() != text.casefold())
        labels = [f"{found['name']} — {found['author']} ({found['id']})" for found in result]
        label, ok = QInputDialog.getItem(self, "Find on Nexus", "Which mod is it?", labels, 0,
                                         False)
        if ok:
            self.store_nexus(item, result[labels.index(label)], replace=True)
            self.show_mod_info()

    def store_nexus(self, item, found, replace):
        """Keep a Nexus page and description in library.toml, or for this session."""
        self.nexus_cache[found["id"]] = found
        mod = self.mod_record(item)
        if not (self.library_indexed and mod):
            self.nexus_links[self.describe(item.data(0, ROLE))[0]] = found["id"]
            return
        values = {key: found[key] for key in ("url", "author", "summary")
                  if found[key] and (replace or not mod.get(key))}
        if not values:
            return

        def change(document):
            table = self.mod_table(document, mod["id"])
            for key, value in values.items():
                table[key] = value

        try:
            self.edit_catalog(change)
        except (OSError, LibraryError) as exc:
            self.error(exc)

    def set_checked(self, items, on):
        for item in items:
            item.setCheckState(self.MOD_NAME, Qt.CheckState.Checked if on
                               else Qt.CheckState.Unchecked)

    def set_version(self, item, version):
        entry = dict(item.data(0, ROLE))
        if entry.get("version") != version:
            entry["version"] = version
            entry.pop("components", None)
            entry.pop("plugins", None)
            self.set_entry(item, entry, item.data(0, EXTRA))
            self.mods_changed()

    def choose_components(self, item):
        entry = dict(item.data(0, ROLE))
        name, _version, release, _problem = self.describe(entry)
        components = list(release["components"].values())
        chosen = entry.get("components",
                           [component["id"] for component in components if component["default"]])
        dialog = ComponentsDialog(name, components, chosen, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        entry["components"] = dialog.chosen()
        try:
            resolve_selection(entry, self.library_root, self.catalog)
        except LibraryError as exc:
            self.error(exc)
            return
        self.set_entry(item, entry, item.data(0, EXTRA))
        self.mods_changed()

    def toggle_flag(self, item, key):
        entry = dict(item.data(0, ROLE))
        if entry.get(key):
            entry.pop(key)
        else:
            entry[key] = True
        self.set_entry(item, entry, item.data(0, EXTRA))
        self.mods_changed()

    def toggle_archives(self, item):
        entry = dict(item.data(0, ROLE))
        if entry.get("archives") == "load":
            entry.pop("archives")
        else:
            entry["archives"] = "load"
        self.set_entry(item, entry, item.data(0, EXTRA))
        self.mods_changed()
        self.schedule_analysis()

    def remove_from_profile(self, item):
        entry = item.data(0, ROLE)
        mod = self.catalog.get(entry.get("id"))
        if mod is None and "id" not in entry:
            mod = next((value for value in self.catalog.values()
                        if self.entry_folders(entry) & {release["folder"].casefold()
                                                        for release in value["releases"].values()}),
                       None)
        if mod is None:
            self.take_mod(item)
        else:
            self.set_entry(item, self.library_entry(mod), False)
        self.mods_changed()

    def profile_mods(self):
        mods = []
        for item in self.mod_rows():
            if item.data(0, EXTRA):
                entry = dict(item.data(0, ROLE))
                entry["order"] = (len(mods) + 1) * 10
                mods.append(entry)
        return mods

    # Analysis: which files each active mod wins, the plugins and archives they bring.

    def schedule_analysis(self):
        self.analysis_timer.start()

    def scan(self, entry):
        exclude = self.build.excluded()
        key = json.dumps([{name: entry.get(name) for name in ("id", "name", "version",
                                                              "components")}, exclude],
                         sort_keys=True)
        if key not in self.scans:
            try:
                selection = resolve_selection(entry, self.library_root, self.catalog)
                self.scans[key] = Mod(selection["name"],
                                      selection.get("layers") or selection["roots"], 0, None,
                                      exclude)
            except (LibraryError, OSError, KeyError, ValueError) as exc:
                self.scans[key] = exc
        return self.scans[key]

    def refresh_analysis(self):
        active = []
        for item in self.mod_rows():
            if item.checkState(self.MOD_NAME) != Qt.CheckState.Checked:
                continue
            mod = self.scan(item.data(0, ROLE))
            if isinstance(mod, Mod):
                active.append((item, mod))
        owners = defaultdict(list)
        plugins = {}
        retail = {}
        mod_plugins = []
        for index, (item, mod) in enumerate(active):
            entry = item.data(0, ROLE)
            wanted = ({name.casefold() for name in entry["plugins"]}
                      if "plugins" in entry else None)
            names = []
            for key, source in mod.files.items():
                if key.endswith(PLUGIN_EXT):
                    base = os.path.basename(key)
                    included = wanted is None or base in wanted
                    if base in BASE_MASTERS:
                        if included:
                            retail[base] = item.text(0)
                            owners[key].append(index)
                        continue
                    names.append(os.path.basename(source))
                    plugins[base] = {"name": os.path.basename(source), "path": source,
                                     "owner": index, "included": included}
                    if not included:
                        continue
                owners[key].append(index)
            mod_plugins.append(names)
        wins = [0] * len(active)
        loses = [0] * len(active)
        beats = [set() for _ in active]
        beaten = [set() for _ in active]
        files = []
        for key, indexes in owners.items():
            winner = indexes[-1]
            others = sorted(set(indexes[:-1]))
            if others:
                wins[winner] += 1
                for other in others:
                    loses[other] += 1
                    beats[winner].add(other)
                    beaten[other].add(winner)
            files.append((active[winner][1].relative.get(key, key), active[winner][0].text(0),
                          ", ".join(active[other][0].text(0) for other in others)))
        self.analysis = {"active": active, "owners": owners, "plugins": plugins, "retail": retail,
                         "mod_plugins": mod_plugins, "beats": beats, "beaten": beaten}
        position = {id(item): index for index, (item, _mod) in enumerate(active)}
        self.mods_loading = True
        for row, item in enumerate(self.mod_rows(), 1):
            item.setText(self.MOD_PRIORITY, str(row))
            index = position.get(id(item))
            text = ""
            if index is not None and (wins[index] or loses[index]):
                text = " ".join(part for part in (f"+{wins[index]}" if wins[index] else "",
                                                  f"-{loses[index]}" if loses[index] else "")
                                if part)
            item.setText(self.MOD_CONFLICTS, text)
            notes = list(item.data(self.MOD_NOTES, ROLE) or [])
            if index is not None and "plugins" in item.data(0, ROLE):
                names = mod_plugins[index]
                wanted = {name.casefold() for name in item.data(0, ROLE)["plugins"]}
                used = sum(1 for name in names if name.casefold() in wanted)
                if used < len(names):
                    notes.append(f"{used} of {len(names)} plugins")
            item.setText(self.MOD_NOTES, ", ".join(notes))
        self.mods_loading = False
        for column in (1, 2, 3, 4, self.MOD_XBOX):
            self.mod_list.resizeColumnToContents(column)
        self.files_model.reset(sorted(files, key=lambda row: row[0].casefold()))
        self.populate_archives(files)
        self.populate_plugins()
        self.highlight_conflicts()
        self.refresh_status()
        self.refresh_resources()
        self.show_context_info()

    @staticmethod
    def display_size(value):
        return (f"{value / 1048576:.1f} MB" if value >= 1048576
                else f"{value / 1024:.1f} KB")

    def refresh_resources(self):
        if not hasattr(self, "resource_budget"):
            return
        self.resource_budget.clear()
        self.resource_dependencies.clear()
        total_files = total_bytes = total_textures = total_texture_bytes = 0
        active_ids = {item.data(0, ROLE).get("id"): item.text(0)
                      for item, _mod in self.analysis["active"]}
        for item, mod in self.analysis["active"]:
            size = texture_size = textures = largest = 0
            for key, source in mod.files.items():
                try:
                    file_size = os.path.getsize(source)
                except OSError:
                    file_size = 0
                size += file_size
                if key.startswith("textures/") or key.endswith((".dds", ".tga", ".bmp")):
                    textures += 1
                    texture_size += file_size
                    dims = texture_dims(source)
                    if dims:
                        largest = max(largest, *dims)
            total_files += len(mod.files)
            total_bytes += size
            total_textures += textures
            total_texture_bytes += texture_size
            row = QTreeWidgetItem([item.text(0), str(len(mod.files)), self.display_size(size),
                                   str(textures), self.display_size(texture_size),
                                   str(largest) if largest else ""])
            row.setData(0, ROLE,
                        f"{item.text(0)}\n\n{len(mod.files)} source files · "
                        f"{self.display_size(size)}\n{textures} textures · "
                        f"{self.display_size(texture_size)}"
                        + (f"\nLargest texture side: {largest}" if largest else ""))
            self.resource_budget.addTopLevelItem(row)

            release = self.describe(item.data(0, ROLE))[2] or {}
            for dependency in release.get("dependencies", []):
                status = "Active" if dependency in active_ids else "Missing"
                dep = QTreeWidgetItem(["Mod", item.text(0),
                                       active_ids.get(dependency, dependency), status])
                dep.setData(0, ROLE,
                            f"Mod dependency\n\n{item.text(0)} requires {dependency}: {status}")
                if status == "Missing":
                    dep.setForeground(3, WARNING)
                self.resource_dependencies.addTopLevelItem(dep)

        loaded = [item for item in self.plugin_list.rows()
                  if item.checkState(0) == Qt.CheckState.Checked]
        positions = {(item.data(0, ROLE) or item.text(0).casefold()): index
                     for index, item in enumerate(loaded)}
        for item in loaded:
            key = item.data(0, ROLE)
            if not key or key not in self.analysis["plugins"]:
                continue
            for master in self.plugin_masters(self.analysis["plugins"][key]["path"]):
                master_key = master.casefold()
                if master_key not in positions:
                    status = "Missing"
                elif positions[master_key] > positions[key]:
                    status = "Loads later"
                else:
                    status = "Present"
                dep = QTreeWidgetItem(["Plugin", item.text(0), master, status])
                dep.setData(0, ROLE,
                            f"Plugin master\n\n{item.text(0)} requires {master}: {status}")
                if status != "Present":
                    dep.setForeground(3, WARNING)
                self.resource_dependencies.addTopLevelItem(dep)

        self.resource_summary.setText(
            f"Source inventory before conversion and packing: {total_files} files · "
            f"{self.display_size(total_bytes)} · {total_textures} textures · "
            f"{self.display_size(total_texture_bytes)} of texture sources")
        for column in range(self.resource_budget.columnCount()):
            self.resource_budget.resizeColumnToContents(column)
        for column in (0, 1, 3):
            self.resource_dependencies.resizeColumnToContents(column)

    def populate_archives(self, files):
        """The archives the build ships, and whether the engine opens each one."""
        self.archives.clear()
        mode = self.build.mode.currentData()
        assets = sum(1 for path, _owner, _others in files
                     if not path.lower().endswith((*PLUGIN_EXT, ".bsa")))
        if not self.analysis["active"]:
            mode = "retail"
        rows = [("Morrowind.bsa", "Retail", "yes")]
        if mode == "merged-bsa":
            rows = [("Morrowind.bsa", f"Retail, rebuilt with {assets} mod files", "yes")]
        elif mode == "delta-bsa":
            name = self.build.archive_name.text().strip() or "tes3xmods.bsa"
            rows.append((name, f"TES3X build: {assets} mod files", "yes, after Morrowind.bsa"))
        for row in rows:
            self.archives.addTopLevelItem(QTreeWidgetItem(list(row)))
        active = self.analysis["active"]
        for key, indexes in self.analysis["owners"].items():
            if not key.endswith(".bsa") or "/" in key:
                continue
            row, mod = active[indexes[-1]]
            try:
                named = Bsa(mod.files[key]).named
            except (OSError, ValueError, struct.error):
                named = False
            if row.data(0, ROLE).get("archives") == "load":
                loaded, why = "yes, via multi-bsa", "Listed in tes3xarch.txt for the multi-bsa patch"
            elif named:
                loaded, why = "unpacked", "Its files join the build beneath the mod's loose files"
            else:
                loaded, why = "can't unpack", ("This archive has no file names. Right-click the "
                                               "mod and choose Load archives with multi-bsa.")
            item = QTreeWidgetItem([mod.relative.get(key, key), row.text(0), loaded])
            item.setToolTip(2, why)
            if loaded == "can't unpack":
                item.setForeground(2, WARNING)
            self.archives.addTopLevelItem(item)
        for column in range(3):
            self.archives.resizeColumnToContents(column)

    def highlight_conflicts(self):
        active = self.analysis["active"]
        chosen = self.selected_mod()
        index = next((i for i, (item, _mod) in enumerate(active) if item is chosen), None)
        beats = self.analysis["beats"][index] if index is not None else set()
        beaten = self.analysis["beaten"][index] if index is not None else set()
        for i, (item, _mod) in enumerate(active):
            colour = WINS if i in beats else LOSES if i in beaten else None
            for column in range(self.mod_list.columnCount()):
                if colour is None:
                    item.setData(column, Qt.ItemDataRole.BackgroundRole, None)
                else:
                    item.setBackground(column, colour)

    def refresh_status(self):
        rows = self.mod_rows()
        active = sum(1 for item in rows if item.checkState(self.MOD_NAME) == Qt.CheckState.Checked)
        plugins = sum(1 for value in self.analysis["plugins"].values() if value["included"])
        self.counts.setText(f"Mods {active} of {len(rows)} active · "
                            f"Plugins {plugins + len(self.base_plugins())} active")

    # Plugins

    def plugin_masters(self, path):
        try:
            stamp = os.stat(path).st_mtime_ns
        except OSError:
            return []
        cached = self.masters.get(path)
        if cached is None or cached[0] != stamp:
            try:
                cached = (stamp, plugin_masters(path))
            except (OSError, ValueError):
                cached = (stamp, [])
            self.masters[path] = cached
        return cached[1]

    def plugin_sequence(self):
        """Mod plugins in load order: masters first, then the chosen order or mod order."""
        plugins = self.analysis["plugins"]
        default = sorted(plugins, key=lambda name: (not name.endswith(".esm"),
                                                    plugins[name]["owner"], name))
        preferred = default
        if self.plugin_order and not self.mlox_at_build.isChecked():
            wanted = {name.casefold(): index for index, name in enumerate(self.plugin_order)}
            preferred = sorted(default, key=lambda name: (wanted.get(name, len(wanted)),
                                                          default.index(name)))
        try:
            return dependency_order({name: Path(plugins[name]["path"]) for name in plugins},
                                    mtime=False, preferred=preferred)
        except (OSError, ValueError):
            return preferred

    def base_plugins(self):
        """Retail masters and expansion placeholders present in the planned build."""
        overrides = self.analysis.get("retail", {})
        vanilla = self.local_path("vanilla_root")
        data_files = vanilla / "Data Files" if vanilla else None
        modded = bool(self.analysis["active"])
        result = []
        for name in RETAIL_PLUGINS:
            key = name.casefold()
            source = overrides.get(key)
            retail = data_files is not None and (data_files / name).is_file()
            if source:
                result.append((name, source, False))
            elif key not in EXPANSION_PLACEHOLDERS or retail:
                result.append((name, "Retail", False))
            elif modded:
                result.append((name, "Placeholder", True))
        return result

    def populate_plugins(self):
        self.plugins_loading = True
        self.plugin_list.clear()
        plugins = self.analysis["plugins"]
        active = self.analysis["active"]
        base = self.base_plugins()
        loaded = [name.casefold() for name, _source, _placeholder in base]
        for name, source, placeholder in base:
            item = QTreeWidgetItem([name, source, ""])
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            item.setCheckState(0, Qt.CheckState.Checked)
            if placeholder:
                item.setToolTip(0, "Generated automatically for a modded build when the retail "
                                   "expansion master is absent; contains only the TES3 signature.")
            self.plugin_list.addTopLevelItem(item)
        for name in self.plugin_sequence():
            value = plugins[name]
            item = QTreeWidgetItem([value["name"], active[value["owner"]][0].text(0), ""])
            item.setFlags(ROW_FLAGS)
            item.setData(0, ROLE, name)
            item.setCheckState(0, Qt.CheckState.Checked if value["included"]
                               else Qt.CheckState.Unchecked)
            self.plugin_list.addTopLevelItem(item)
            if value["included"]:
                loaded.append(name)
        present = set(loaded)
        index = 0
        for item in self.plugin_list.rows():
            name = item.data(0, ROLE) or item.text(0).casefold()
            if name not in present:
                continue
            item.setText(2, f"{index:02X}")
            index += 1
            if name not in plugins:
                continue
            earlier = set(loaded[:loaded.index(name)])
            problems = []
            for master in self.plugin_masters(plugins[name]["path"]):
                if master.casefold() not in present:
                    problems.append(f"missing master {master}")
                elif master.casefold() not in earlier:
                    problems.append(f"master {master} loads later")
            if problems:
                for column in range(3):
                    item.setForeground(column, WARNING)
                item.setToolTip(0, "; ".join(problems))
        self.plugin_list.setDragDropMode(
            QAbstractItemView.DragDropMode.NoDragDrop if self.mlox_at_build.isChecked()
            else QAbstractItemView.DragDropMode.InternalMove)
        self.plugin_list.resizeColumnToContents(1)
        self.plugin_list.resizeColumnToContents(2)
        if self.mlox_at_build.isChecked():
            self.plugin_note.setText("mlox sorts the plugins when the profile is built.")
        elif self.plugin_order:
            self.plugin_note.setText("Custom order. Drag to change it; masters always load "
                                     "before other plugins.")
        else:
            self.plugin_note.setText("Mod order. Drag plugins or press Sort to set your own.")
        if any(placeholder for _name, _source, placeholder in base):
            self.plugin_note.setText(self.plugin_note.text() +
                                     " TES3X generates the listed four-byte expansion placeholders "
                                     "during packaging.")
        self.plugins_loading = False

    def plugins_moved(self):
        self.plugin_order = [self.analysis["plugins"][item.data(0, ROLE)]["name"]
                             for item in self.plugin_list.rows() if item.data(0, ROLE)]
        self.populate_plugins()

    def reset_plugin_order(self):
        self.plugin_order = None
        self.populate_plugins()

    def mlox_toggled(self, _value):
        if not self.plugins_loading:
            self.populate_plugins()

    def plugin_item_changed(self, item, column):
        if self.plugins_loading or column != 0 or not item.data(0, ROLE):
            return
        name = item.data(0, ROLE)
        on = item.checkState(0) == Qt.CheckState.Checked
        for (row, _mod), names in zip(self.analysis["active"], self.analysis["mod_plugins"]):
            if name not in {value.casefold() for value in names}:
                continue
            entry = dict(row.data(0, ROLE))
            wanted = {value.casefold() for value in entry.get("plugins", names)}
            if on:
                wanted.add(name)
            else:
                wanted.discard(name)
            if wanted >= {value.casefold() for value in names}:
                entry.pop("plugins", None)
            else:
                entry["plugins"] = [value for value in names if value.casefold() in wanted]
            self.set_entry(row, entry, row.data(0, EXTRA))
        self.refresh_analysis()

    def local_values(self):
        config = self.local_config_path()
        try:
            return tomllib.loads(config.read_text(encoding="utf-8")) if config.is_file() else {}
        except (OSError, tomllib.TOMLDecodeError):
            return {}

    def start_in_game_listener(self):
        """Start the shared Xbox/xemu agent endpoint after the real GUI is visible."""
        key_path = self.local_config_path().with_name("tes3x.agent.key")
        try:
            secret = load_or_create_key(key_path)
            listener = AgentListener(secret, self.in_game_event.emit)
            listener.start()
        except (OSError, ValueError, SystemExit) as exc:
            self.statusBar().showMessage(f"Could not start the in-game agent listener: {exc}",
                                         10000)
            return
        self.in_game_listener = listener
        self.agent_fingerprint = key_fingerprint(secret)

    def in_game_target(self, address):
        """Map an agent's source address to a configured target."""
        host = address[0]
        targets = tes3x_targets.targets(self.local_values())
        selected = self.target_picker.currentData()
        matching = [name for name, target in targets.items()
                    if target.get("kind") == "xbox" and target.get("host") == host]
        if selected in matching:
            return selected
        if matching:
            return matching[0]
        if host.startswith("127.") or host == "::1":
            target = targets.get(selected, {})
            if target.get("kind") == "xemu":
                return selected
        return None

    def handle_in_game_event(self, event):
        name = self.in_game_target(event["address"])
        if not name:
            return
        kind = event["kind"]
        if kind == "goodbye":
            state = self.target_runtime.setdefault(name, {})
            state.pop("game", None)
            state["game_detail"] = "In-game agent disconnected"
            self.refresh_target_item(name)
            return
        if kind == "stalled":
            self.set_target_runtime(name, game="stalled",
                                    game_detail="In-game heartbeat stopped")
            return
        detail = f"In-game agent from {event['address'][0]}:{event['address'][1]}"
        self.set_target_runtime(name, game="connected", game_detail=detail,
                                game_key=event.get("client_key"))
        if kind == "log":
            lines = event.get("payload", b"").decode("cp1252", "replace").splitlines()
            self.agent_logs[name].extend(lines)
            del self.agent_logs[name][:-2000]

    def default_target(self, kind=None):
        name = self.target_picker.currentData() if hasattr(self, "target_picker") else None
        try:
            return tes3x_targets.resolve(self.local_values(), name=name, kind=kind)
        except (tes3x_targets.TargetError, ValueError):
            return None

    def refresh_targets(self):
        """Reload machine targets while preserving status learned during this session."""
        local = self.local_values()
        available = tes3x_targets.targets(local)
        selected = self.target_picker.currentData() or tes3x_targets.default_name(local)
        self.target_picker.blockSignals(True)
        self.target_picker.clear()
        for name, target in available.items():
            kind = target.get("kind", "xbox")
            detail = target.get("host", "") if kind == "xbox" else f"{target.get('ram', 64)} MB"
            self.target_picker.addItem(dot_icon(self.target_colour(name, target)),
                                       f"{name}  ·  {detail or kind}", name)
            row = self.target_picker.count() - 1
            self.target_picker.setItemData(row, self.target_tooltip(name, target),
                                           Qt.ItemDataRole.ToolTipRole)
        index = self.target_picker.findData(selected)
        self.target_picker.setCurrentIndex(index if index >= 0 else 0)
        self.target_picker.blockSignals(False)
        self.target_selection_changed(probe=False)

    def target_runtime_state(self, name, target=None):
        state = self.target_runtime.setdefault(name, {})
        target = target or tes3x_targets.targets(self.local_values()).get(name, {})
        if target.get("kind") == "xbox":
            state["ftp"] = self.target_states.get(name, state.get("ftp", "unknown"))
        return state

    def set_target_runtime(self, name, **values):
        state = self.target_runtime.setdefault(name, {})
        state.update(values)
        if "ftp" in values:
            self.target_states[name] = values["ftp"]
        self.refresh_target_item(name)

    def target_features(self, name=None):
        name = name or self.target_picker.currentData()
        target = tes3x_targets.targets(self.local_values()).get(name)
        return target_capabilities(target, self.target_runtime_state(name, target)) \
            if target else frozenset()

    def target_colour(self, name, target=None):
        target = target or tes3x_targets.targets(self.local_values()).get(name, {})
        label = target_runtime_label(target, self.target_runtime_state(name, target))
        if label in {"In game", "Dashboard", "Dashboard reachable", "xemu running"}:
            return "#2e7d32"
        if label in {"Checking", "Checking agent", "Starting", "Stopping",
                     "Dashboard agent outdated"}:
            return "#a15c00"
        if label in {"Off", "Stalled"}:
            return "#b3261e"
        return "#616161"

    def target_tooltip(self, name, target=None):
        target = target or tes3x_targets.targets(self.local_values()).get(name, {})
        runtime = self.target_runtime_state(name, target)
        if target.get("kind") == "xemu":
            lines = [f"xemu · {target.get('ram', 64)} MB", target_runtime_label(target, runtime)]
        else:
            host = target.get("host") or "address not configured"
            lines = [f"Xbox FTP: {host}:{target.get('port', 21)}"]
            status = runtime.get("ftp")
            if status:
                lines.append({"checking": "Checking connection…", "connected": "Connected",
                              "offline": "Unreachable"}.get(status, status))
            agent = target_runtime_label(target, runtime)
            if runtime.get("game") in {"connected", "stalled"}:
                lines.append(agent)
            elif status == "connected" and agent not in {"Dashboard reachable", "Unknown"}:
                lines.append(agent)
            if runtime.get("dashboard_detail") and runtime.get("dashboard") != "current":
                lines.append(runtime["dashboard_detail"])
        if runtime.get("game_detail"):
            lines.append(runtime["game_detail"])
        if self.target_drive_tips.get(name):
            lines.append(self.target_drive_tips[name])
        return "\n".join(lines)

    def refresh_target_item(self, name):
        target = tes3x_targets.targets(self.local_values()).get(name)
        row = self.target_picker.findData(name)
        if target is None or row < 0:
            return
        self.target_picker.setItemIcon(row, dot_icon(self.target_colour(name, target)))
        tip = self.target_tooltip(name, target)
        self.target_picker.setItemData(row, tip, Qt.ItemDataRole.ToolTipRole)
        if row == self.target_picker.currentIndex():
            self.target_picker.setToolTip(tip)

    def target_activated(self, *_args):
        name = self.target_picker.currentData()
        config = self.local_config_path()
        if name and config.is_file():
            try:
                document = tomlkit.parse(config.read_text(encoding="utf-8"))
                document["default_target"] = name
                config.write_text(tomlkit.dumps(document), encoding="utf-8", newline="")
            except (OSError, tomlkit.exceptions.ParseError) as exc:
                self.error(f"Could not remember target: {exc}")
        self.target_selection_changed(probe=True)

    def target_selection_changed(self, probe=False):
        target = self.default_target()
        xbox = bool(target and target.get("kind") == "xbox")
        self.action_deploy.setEnabled(xbox and self.process is None)
        self.action_fetch.setEnabled(xbox and self.process is None)
        self.discard_after_deploy.setEnabled(xbox)
        if target:
            self.target_picker.setToolTip(self.target_tooltip(target["name"], target))
        if hasattr(self, "play_menu"):
            self.refresh_play_menu()
        self.update_deploy_state()
        if hasattr(self, "save_list"):
            self.save_target_changed(probe)
        if probe and xbox:
            self.refresh_ftp_status()

    def xbox_destination(self):
        target = self.default_target("xbox")
        if not target:
            return None
        plain = dict(self.profile_plain or {})
        identity = dict(plain.get("profile", {}))
        folder = self.build.install_dir.text().strip()
        if folder:
            identity["install_dir"] = folder
            identity.pop("remote_root", None)
        plain["profile"] = identity
        try:
            return tes3x_targets.remote_root(plain, target)
        except tes3x_targets.TargetError:
            return None

    def local_path(self, key):
        value = self.local_values().get("paths", {}).get(key)
        if not value:
            return None
        path = Path(value)
        return path if path.is_absolute() else self.work_dir() / path

    def sort_plugins(self):
        vanilla = self.local_path("vanilla_root")
        rules = self.local_path("mlox_rules")
        if vanilla is None or rules is None or not rules.is_file():
            self.error("Sorting needs the clean game root and the mlox rules; set them in "
                       "File > Settings (Download fetches the rules).")
            return
        plugins = self.analysis["plugins"]
        chosen = {name: Path(value["path"]) for name, value in plugins.items() if value["included"]}
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            with tempfile.TemporaryDirectory(prefix="tes3x-sort-") as temp:
                empty = Path(temp) / "none"
                empty.mkdir()
                files = collect(empty, vanilla / "Data Files", Path(temp) / "stubs")
                files.update(chosen)
                names, messages = sort_files(files, rules, Path(temp) / "mlox")
        except (OSError, ValueError, RuntimeError) as exc:
            QApplication.restoreOverrideCursor()
            self.error(f"mlox could not sort the plugins: {exc}")
            return
        QApplication.restoreOverrideCursor()
        sorted_names = [plugins[name]["name"] for name in names if name in plugins]
        rest = [plugins[name]["name"] for name in self.plugin_sequence() if name not in chosen]
        self.plugin_order = sorted_names + rest
        self.mlox_at_build.setChecked(False)
        self.populate_plugins()
        notes = mlox_notes(messages)
        self.output.setPlainText("\n\n".join(notes) if notes else "mlox: no warnings")
        self.statusBar().showMessage(f"Sorted {len(sorted_names)} plugins with mlox", 5000)

    # Installing and managing library mods

    def choose_install(self):
        start = self.settings.value("install_dir", "") if self.settings else ""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Install mod", start,
            "Mods (*.zip *.7z *.rar *.esp *.esm *.bsa);;All files (*)")
        if paths and self.settings is not None:
            self.settings.setValue("install_dir", str(Path(paths[0]).parent))
        self.install_paths(paths)

    def choose_install_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Install mod from folder")
        if path:
            self.install_paths([path])

    def install_paths(self, paths):
        installed = [self.install_mod(Path(path)) for path in paths]
        installed = [value for value in installed if value]
        if installed:
            self.reload_library()
            names = {name.casefold() for name in installed}
            for item in self.mod_rows():
                if item.text(self.MOD_NAME).casefold() in names:
                    item.setSelected(True)
                    self.mod_list.scrollToItem(item)

    def folder_taken(self, name):
        target = self.library_root / folder_name(name)
        if target.exists():
            return f"The mod library already has a folder named {target.name}"
        return None

    def install_mod(self, source, replace=None):
        """Install an archive, folder or plugin into the library; returns the mod's name."""
        if self.library_root is None:
            self.error("Set a mod library first, in the Build tab or File > Settings")
            return None
        work = self.library_root / f".tes3x-install-{uuid.uuid4().hex[:8]}"
        try:
            if source.is_dir():
                unpacked = source
            elif source.suffix.lower() in ARCHIVES:
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                try:
                    unpacked = extract_archive(source, work / "unpacked")
                finally:
                    QApplication.restoreOverrideCursor()
            elif source.suffix.lower() in (".esp", ".esm", ".bsa"):
                unpacked = work / "unpacked"
                unpacked.mkdir(parents=True)
                shutil.copy2(source, unpacked / source.name)
            else:
                self.error(f"{source.name}: install an archive, a folder or a plugin")
                return None
            found, chosen = install_layout(unpacked)
            name, version = guess_release(source)
            if replace:
                name, version = replace["name"], replace["version"]
            dialog = InstallDialog(unpacked, found, chosen, name, version, source.name,
                                   taken=None if replace else self.folder_taken,
                                   fixed=replace is not None, parent=self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return None
            name, version = dialog.name(), dialog.version()
            if replace:
                target = self.library_root / replace["folder"]
                staged = work / "installed"
                install_files(unpacked, dialog.selection(), staged)
                target.rename(work / "previous")
                staged.rename(target)
                if self.library_indexed and replace.get("id") and source.is_file():
                    self.edit_catalog(lambda document: self.release_table(
                        document, replace["id"], replace["version"]).__setitem__(
                            "source", str(source)))
            else:
                folder = folder_name(name)
                install_files(unpacked, dialog.selection(), self.library_root / folder)
                if self.library_indexed:
                    mod_id = free_id(self.catalog, name)
                    release = {"version": version or "unknown", "folder": folder,
                               "default": True, "roots": ["."], "dependencies": [],
                               "components": {},
                               "source": str(source) if source.is_file() else None}
                    append_mods(self.library_root, {mod_id: {
                        "id": mod_id, "name": name, "releases": {release["version"]: release}}})
            self.statusBar().showMessage(f"Installed {name}", 5000)
            return name
        except (OSError, LibraryError) as exc:
            self.error(exc)
            return None
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def reinstall_mod(self, item):
        entry = item.data(0, ROLE)
        name, version, release, _problem = self.describe(entry)
        source = Path(release["source"]) if release and release.get("source") else None
        if source is None or not source.exists():
            path, _ = QFileDialog.getOpenFileName(
                self, f"Reinstall {name}", str(source.parent) if source else "",
                "Mods (*.zip *.7z *.rar *.esp *.esm *.bsa);;All files (*)")
            if not path:
                return
            source = Path(path)
        folder = release["folder"] if release else entry["name"]
        replace = {"name": name, "version": version, "folder": folder, "id": entry.get("id")}
        if self.install_mod(source, replace):
            self.reload_library()

    @staticmethod
    def mod_table(document, mod_id):
        return next(table for table in document["mod"] if table["id"] == mod_id)

    def release_table(self, document, mod_id, version):
        releases = self.mod_table(document, mod_id)["release"]
        return next(table for table in releases if table["version"] == (version or "unknown"))

    def edit_catalog(self, change):
        path = self.library_root / CATALOG_NAME
        text = path.read_text(encoding="utf-8")
        document = tomlkit.parse(text)
        change(document)
        path.write_text(tomlkit.dumps(document), encoding="utf-8", newline="")
        try:
            self.catalog = load_library(self.library_root)
        except LibraryError:
            path.write_text(text, encoding="utf-8", newline="")
            raise

    def rename_mod(self, item):
        entry = item.data(0, ROLE)
        mod = self.catalog[entry["id"]]
        name, ok = QInputDialog.getText(self, "Rename mod", "Name", text=mod["name"])
        name = name.strip()
        if not ok or not name or name == mod["name"]:
            return
        try:
            self.edit_catalog(lambda document: self.mod_table(document, entry["id"]).__setitem__(
                "name", name))
        except (OSError, LibraryError) as exc:
            self.error(exc)
            return
        self.update_mod_item(item)
        self.schedule_analysis()

    def profiles_using(self, entry):
        """Other profiles that pick this mod."""
        users = []
        folders = self.entry_folders(entry)
        for path in self.profile_files():
            if path.resolve() == self.profile_path:
                continue
            try:
                mods = tomllib.loads(path.read_text(encoding="utf-8")).get("mods", [])
            except (OSError, tomllib.TOMLDecodeError):
                continue
            if any((entry.get("id") and mod.get("id") == entry["id"])
                   or mod.get("name", "").casefold() in folders for mod in mods):
                users.append(path.stem)
        return users

    def delete_mod(self, item):
        entry = item.data(0, ROLE)
        name, version, release, _problem = self.describe(entry)
        folder = self.library_root / (release["folder"] if release else entry["name"])
        message = f"Move {folder} to the Recycle Bin and remove {name} from the library?"
        users = self.profiles_using(entry)
        if users:
            message += "\n\nThese profiles also use it: " + ", ".join(users)
        if QMessageBox.question(self, "Delete mod", message) != QMessageBox.StandardButton.Yes:
            return
        if not trash(folder):
            self.error(f"Could not move {folder} to the Recycle Bin")
            return
        mod = self.catalog.get(entry.get("id"))
        if self.library_indexed and mod:
            def change(document):
                table = self.mod_table(document, mod["id"])
                releases = table["release"]
                if len(releases) > 1:
                    del releases[next(i for i, value in enumerate(releases)
                                      if value["version"] == release["version"])]
                else:
                    mods = document["mod"]
                    del mods[next(i for i, value in enumerate(mods) if value["id"] == mod["id"])]
            try:
                self.edit_catalog(change)
            except (OSError, LibraryError) as exc:
                self.error(exc)
        self.take_mod(item)
        self.reload_library()

    def reload_library(self):
        entries = self.profile_mods()
        try:
            self.library_root, self.catalog, self.library_indexed = self.resolve_library(
                self.build.library(), [])
        except (OSError, tomllib.TOMLDecodeError, PipelineError, LibraryError) as exc:
            self.error(exc)
            return
        self.scans.clear()
        self.populate_mods(entries)

    # Patches

    # Saves

    SAVE_NAME, SAVE_WHERE, SAVE_PLAYER, SAVE_CELL, SAVE_DATE, SAVE_SIZE, SAVE_FIT = range(7)
    SAVE_SOURCES = {"xbox": "Xbox", "xemu": "xemu", "pc": "PC copy"}

    def create_saves_tab(self):
        self.pool_combo = QComboBox()
        self.pool_combo.setMinimumWidth(280)
        self.pool_combo.activated.connect(self.pool_chosen)
        new_pool = QPushButton("New pool…")
        new_pool.clicked.connect(self.new_pool)
        self.pool_note = QLabel()
        self.pool_note.setWordWrap(True)
        top = QHBoxLayout()
        top.addWidget(QLabel("Save pool"))
        top.addWidget(self.pool_combo)
        top.addWidget(new_pool)
        top.addStretch()

        self.save_list = QTreeWidget()
        self.save_list.setHeaderLabels(["Save", "Where", "Player", "Cell", "Date", "Size",
                                        "Plugins"])
        self.save_list.setRootIsDecorated(False)
        self.save_list.setSortingEnabled(True)
        self.save_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.save_list.itemSelectionChanged.connect(self.saves_selected)
        self.save_list.itemSelectionChanged.connect(self.show_context_info)
        header = self.save_list.header()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)

        self.save_actions = {}
        for key, label, handler, tip in (
                ("pull", "Download to PC", self.pull_saves,
                 "Copy the selected Xbox or xemu saves into the PC save library"),
                ("push", "Upload to Xbox", self.push_saves,
                 "Copy the selected saves into this pool on the Xbox"),
                ("push_xemu", "Push to xemu", self.push_xemu_saves,
                 "Copy the selected saves into this pool on the profile's xemu disk"),
                ("copy", "Copy to pool…", lambda: self.transfer_saves(False),
                 "Copy the selected saves into another pool, where they are"),
                ("move", "Move to pool…", lambda: self.transfer_saves(True),
                 "Move the selected saves into another pool, where they are. Each original is "
                 "deleted once its copy is complete, and a save leaving the Xbox keeps a PC "
                 "copy"),
                ("delete", "Delete…", self.delete_saves, "Delete the selected saves"),
                ("refresh", "Refresh", lambda: self.refresh_saves(True),
                 "List this pool's saves again, including the Xbox"),
                ("folder", "Open PC folder", self.open_save_folder,
                 "Open this pool's folder in the PC save library")):
            action = QAction(label, self)
            action.setToolTip(f"{label.rstrip('…')}: {tip}")
            action.triggered.connect(handler)
            self.save_actions[key] = action
        self.save_actions["delete"].setShortcut(QKeySequence(QKeySequence.StandardKey.Delete))
        self.save_actions["delete"].setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        self.save_list.addAction(self.save_actions["delete"])
        self.save_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.save_list.customContextMenuRequested.connect(self.save_menu)

        pixmap = QStyle.StandardPixmap
        buttons = QHBoxLayout()
        for key, theme, fallback in (
                ("refresh", QIcon.ThemeIcon.ViewRefresh, pixmap.SP_BrowserReload),
                ("folder", QIcon.ThemeIcon.FolderOpen, pixmap.SP_DirOpenIcon),
                (None, None, None),
                ("pull", QIcon.ThemeIcon.GoDown, pixmap.SP_ArrowDown),
                ("push", QIcon.ThemeIcon.GoUp, pixmap.SP_ArrowUp),
                ("push_xemu", QIcon.ThemeIcon.DocumentSend, pixmap.SP_ArrowRight),
                (None, None, None),
                ("copy", QIcon.ThemeIcon.EditCopy, pixmap.SP_FileIcon),
                ("move", QIcon.ThemeIcon.GoNext, pixmap.SP_ArrowForward),
                ("delete", QIcon.ThemeIcon.EditDelete, pixmap.SP_TrashIcon)):
            if key is None:
                buttons.addSpacing(12)
                continue
            self.save_actions[key].setIcon(theme_icon(self, theme, fallback))
            buttons.addWidget(icon_button(self.save_actions[key]))
        buttons.addStretch()
        self.saves_status = QLabel()
        buttons.addWidget(self.saves_status)

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addLayout(top)
        layout.addWidget(self.pool_note)
        layout.addWidget(self.save_list)
        layout.addLayout(buttons)
        self.save_pool = None
        self.saves_probe = None
        self.saves_local = []
        self.saves_xbox = []
        self.xbox_listing = None
        self.xbox_checked = set()
        self.saves_selected()
        return widget

    def save_library(self):
        return self.work_dir() / "build" / "saves"

    def current_pool(self):
        """(title ID, name) of this profile's pool; the shared pool has no name."""
        if not self.save_pool:
            return tes3x_savepool.SHARED_ID, None
        return (tes3x_savepool.pool_id(self.save_pool["name"], self.save_pool.get("id")),
                self.save_pool["name"])

    def known_pools(self):
        """{title ID: {"name", "id", "users"}}: every profile's pool, and those the save library
        has seen, on the Xbox or made here."""
        pools = {}
        for path in self.profile_files():
            try:
                identity = tomllib.loads(path.read_text(encoding="utf-8")).get("profile", {})
                name = identity.get("save_pool")
                value = tes3x_savepool.pool_id(name, identity.get("save_pool_id")) \
                    if name else None
            except (OSError, tomllib.TOMLDecodeError, ValueError):
                continue
            if value:
                pool = pools.setdefault(value, {"name": name, "id": identity.get("save_pool_id"),
                                                "users": []})
                pool["users"].append(identity.get("name") or path.stem)
        for hex_id, name in saves_tool.read_index(self.save_library())["pools"].items():
            pools.setdefault(int(hex_id, 16), {"name": name, "id": hex_id, "users": []})
        value, name = self.current_pool()
        if name:
            pools.setdefault(value, {"name": name, "id": self.save_pool.get("id"), "users": []})
        pools.pop(tes3x_savepool.SHARED_ID, None)
        return pools

    def populate_pools(self):
        self.pool_combo.clear()
        self.pool_combo.addItem("Shared: the retail game's saves", tes3x_savepool.SHARED_ID)
        pools = self.known_pools()
        for value, pool in sorted(pools.items(), key=lambda item: item[1]["name"].casefold()):
            self.pool_combo.addItem(f"{pool['name']}  ({value:08X})", value)
        current, _name = self.current_pool()
        self.pool_combo.setCurrentIndex(max(0, self.pool_combo.findData(current)))
        me = self.profile_plain.get("profile", {}).get("name")
        others = [user for user in pools.get(current, {}).get("users", []) if user != me]
        if current == tes3x_savepool.SHARED_ID:
            note = ("Saves go to E:\\UDATA\\42530005 with the retail game and every profile "
                    "without a pool of its own.")
        else:
            note = (f"Saves go to E:\\UDATA\\{current:08X}, apart from other builds. "
                    + (f"Also used by: {', '.join(others)}. " if others else "")
                    + "A change of pool takes effect from the next build.")
        self.pool_note.setText(note)

    def load_save_pool(self, plain):
        identity = plain.get("profile", {})
        self.save_pool = ({"name": identity["save_pool"], "id": identity.get("save_pool_id")}
                          if identity.get("save_pool") else None)
        self.pool_changed()

    def apply_save_pool(self, document):
        put = lambda *args: BuildSettings.put(document, *args)
        put("profile", "save_pool", self.save_pool["name"] if self.save_pool else None, None)
        put("profile", "save_pool_id",
            self.save_pool.get("id") if self.save_pool else None, None)

    def pool_changed(self):
        self.populate_pools()
        self.update_build_state()
        self.save_list.clear()
        self.saves_local, self.saves_xbox, self.xbox_listing = [], [], None
        if self.saves_tab_visible():
            self.refresh_saves()

    def save_target_changed(self, probe=False):
        """Drop state from the old target and show saves belonging to the selected one."""
        if self.saves_probe is not None:
            probe_process, self.saves_probe = self.saves_probe, None
            probe_process.kill()
        self.saves_xbox = []
        self.xbox_listing = None
        if self.saves_tab_visible() and self.profile_path:
            self.refresh_saves(None if probe else False)

    def saves_tab_visible(self):
        return self.tabs.tabText(self.tabs.currentIndex()) == "Saves"

    def saves_tab_shown(self, _index):
        if self.saves_tab_visible():
            self.refresh_saves()

    def pool_chosen(self, index):
        value = self.pool_combo.itemData(index)
        if value == self.current_pool()[0]:
            return
        if value == tes3x_savepool.SHARED_ID:
            self.save_pool = None
        else:
            pool = self.known_pools()[value]
            self.save_pool = {"name": pool["name"], "id": pool["id"] or f"{value:08X}"}
        self.pool_changed()

    def new_pool(self):
        name, ok = QInputDialog.getText(self, "New save pool",
                                        "Name of the new save pool (shown on the dashboard):")
        name = name.strip()
        if not ok or not name:
            return
        pools = self.known_pools()
        if any(pool["name"].casefold() == name.casefold() for pool in pools.values()):
            self.error(f"A pool named {name!r} exists already; choose it from the list")
            return
        value = tes3x_savepool.pool_id(name)
        taken = set(pools) | {tes3x_savepool.SHARED_ID}
        while value in taken:
            value = tes3x_savepool.PREFIX << 16 | ((value + 1) & 0xFFFF or 1)
        saves_tool.remember_pool(self.save_library(), value, name)
        self.save_pool = {"name": name, "id": f"{value:08X}"}
        self.pool_changed()

    def refresh_saves(self, xbox=None):
        """List the PC library and the selected target's saves.

        Show an Xbox target's last listing, then ask it again once per pool and session, or
        whenever `xbox` is true. An xemu target reads this profile's persistent play disk.
        """
        if not self.profile_path:
            return
        value, _name = self.current_pool()
        target = self.default_target()
        self.saves_local = []
        try:
            self.saves_local += saves_tool.library_saves(self.save_library(), value)
            if target and target.get("kind") == "xemu" and self.play_disk().is_file():
                self.saves_local += saves_tool.Disk(self.play_disk()).saves(value)
        except (OSError, ValueError, struct.error) as exc:
            self.statusBar().showMessage(f"Could not read the PC saves: {exc}", 8000)
        xbox_target = target if target and target.get("kind") == "xbox" else None
        if xbox_target and self.xbox_listing is None:
            cached = saves_tool.target_listing(
                saves_tool.read_index(self.save_library()), xbox_target["name"], value)
            self.saves_xbox = cached.get("saves", [])
            self.xbox_listing = f"listed {cached['time']}" if cached.get("time") else ""
        elif not xbox_target:
            self.saves_xbox = []
            self.xbox_listing = ""
        self.show_saves()
        if not xbox_target or not xbox_target.get("host"):
            return
        checked = (xbox_target["name"], value)
        if xbox or (xbox is None and checked not in self.xbox_checked):
            self.list_xbox_saves(value, xbox_target["name"])

    def list_xbox_saves(self, value, target_name=None):
        if self.saves_probe is not None:
            probe, self.saves_probe = self.saves_probe, None
            probe.kill()
        process = QProcess(self)
        process.setWorkingDirectory(str(self.work_dir()))
        process.setProgram(sys.executable)
        target_name = target_name or self.target_picker.currentData()
        selected = (["--target", target_name] if target_name else [])
        process.setArguments([str(ROOT / "tools" / "tes3x_saves.py"), "list", "--pool",
                              f"{value:08X}", "--xbox", "--library", str(self.save_library()),
                              "--config", str(self.local_config_path()), *selected])
        process.finished.connect(lambda code, _status, process=process, value=value,
                                 target_name=target_name:
                                 self.xbox_saves_listed(process, value, target_name, code))
        self.saves_probe = process
        self.xbox_checked.add((target_name, value))
        self.show_saves_status(f"listing {target_name}…")
        process.start()

    def xbox_saves_listed(self, process, value, target_name, code):
        # A listing started for a pool since left, or replaced by a newer one, is dropped.
        if process is not self.saves_probe:
            return
        self.saves_probe = None
        output = bytes(process.readAllStandardOutput()).decode("utf-8", "replace").strip()
        try:
            result = json.loads(output.splitlines()[-1])
        except (IndexError, ValueError):
            error = bytes(process.readAllStandardError()).decode("utf-8", "replace").strip()
            self.show_saves_status("could not list the Xbox", error or output or f"exit {code}")
            return
        if value != self.current_pool()[0] or target_name != self.target_picker.currentData():
            return
        self.saves_xbox = result["saves"]
        if result.get("xbox") == "ok":
            self.xbox_listing = ""
            self.populate_pools()
            self.show_saves()
        else:
            self.xbox_listing = (f"Xbox offline, listed {result['time']}" if result.get("time")
                                 else "Xbox offline")
            self.show_saves(result.get("xbox") or "")

    def show_saves(self, tip=""):
        self.populate_saves(self.saves_xbox + self.saves_local)
        self.show_saves_status(None, tip)

    def show_saves_status(self, doing=None, tip=""):
        counts = collections.Counter(save["source"] for save in self.saves_xbox + self.saves_local)
        target = self.default_target()
        xbox = bool(target and target.get("kind") == "xbox" and target.get("host"))
        parts = [f"{self.SAVE_SOURCES[key]} {counts[key]}" for key in ("xbox", "xemu", "pc")
                 if counts[key] or (key == "xbox" and xbox)]
        parts.append(doing or self.xbox_listing or "")
        self.saves_status.setText(" · ".join(part for part in parts if part)
                                  or "No saves in this pool")
        self.saves_status.setToolTip(tip)

    def load_order(self):
        names = [name for name, _source, _placeholder in self.base_plugins()]
        names += [name for name, value in self.analysis.get("plugins", {}).items()
                  if value.get("included")]
        return {name.casefold() for name in names}

    def save_fit(self, masters):
        """('ok' | 'missing', text) for a save's masters against this profile. The engine
        matches a save's masters by name, so their order does not matter."""
        loaded = self.load_order()
        # GOTY merged the expansions into Morrowind.esm; their files are empty placeholders.
        missing = [name for name in masters if name.casefold() not in loaded
                   and name.casefold() not in EXPANSION_PLACEHOLDERS]
        if missing:
            return "missing", "Missing " + ", ".join(missing)
        return "ok", "Compatible"

    def populate_saves(self, saves):
        selected = {(save["source"], save["folder"]) for save in self.selected_saves()}
        self.save_list.setSortingEnabled(False)
        self.save_list.clear()
        colours = {"ok": QColor("#2e7d32"), "missing": QColor("#b3261e")}
        fits = collections.Counter()
        for save in saves:
            fit, text = self.save_fit(save.get("masters", []))
            fits[fit] += 1
            item = QTreeWidgetItem([
                save.get("name") or save.get("title") or save["folder"],
                self.SAVE_SOURCES.get(save["source"], save["source"]),
                save.get("player") or "", save.get("cell") or "", save.get("date") or "",
                f"{save['size'] / 1048576:.1f} MB", text])
            item.setData(0, ROLE, save)
            item.setForeground(self.SAVE_FIT, colours[fit])
            item.setToolTip(self.SAVE_FIT, "\n".join(save.get("masters", [])))
            self.save_list.addTopLevelItem(item)
            item.setSelected((save["source"], save["folder"]) in selected)
        self.save_list.setSortingEnabled(True)
        self.save_list.sortByColumn(self.SAVE_DATE, Qt.SortOrder.DescendingOrder)
        note = self.pool_note.text().split("  ⚠")[0]
        if fits["missing"]:
            note += (f"  ⚠ {fits['missing']} of {len(saves)} saves need plugins this profile "
                     "does not load.")
        self.pool_note.setText(note)
        self.saves_selected()

    def selected_saves(self):
        return [item.data(0, ROLE) for item in self.save_list.selectedItems()]

    def saves_selected(self):
        sources = {save["source"] for save in self.selected_saves()}
        idle = self.process is None
        target = self.default_target()
        kind = target.get("kind") if target else None
        disk = self.profile_path is not None and self.play_disk().is_file()
        for key, wanted in (("pull", {kind} if kind in {"xbox", "xemu"} else set()),
                            ("push", {"pc"} if kind == "xbox" else set()),
                            ("push_xemu", {"pc"} if kind == "xemu" and disk else set()),
                            ("copy", {"xbox", "pc", "xemu"}), ("move", {"xbox", "pc", "xemu"}),
                            ("delete", {"xbox", "pc", "xemu"})):
            self.save_actions[key].setEnabled(idle and bool(sources & wanted))

    def save_menu(self, position):
        if not self.selected_saves():
            return
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        actions = self.save_actions
        menu.addActions([actions["pull"], actions["push"], actions["push_xemu"]])
        menu.addSeparator()
        menu.addActions([actions["copy"], actions["move"]])
        menu.addSeparator()
        menu.addAction(actions["delete"])
        menu.exec(self.save_list.viewport().mapToGlobal(position))

    def saves_command(self, command, folders, *extra):
        value, _name = self.current_pool()
        return (ROOT / "tools" / "tes3x_saves.py",
                [command, *folders, "--pool", f"{value:08X}", "--library",
                 str(self.save_library()), "--config", str(self.local_config_path()),
                 *(["--target", self.target_picker.currentData()]
                   if self.target_picker.currentData() else []), *extra])

    def pull_steps(self, saves):
        steps = []
        for source in ("xbox", "xemu"):
            folders = [save["folder"] for save in saves if save["source"] == source]
            if folders:
                extra = ["--from", "xemu", "--disk", str(self.play_disk())] \
                    if source == "xemu" else []
                script, arguments = self.saves_command("pull", folders, *extra)
                steps.append((script, arguments, f"Copying {len(folders)} save(s) from "
                                                 f"{self.SAVE_SOURCES[source]} to the PC…"))
        return steps

    def run_save_steps(self, steps):
        if not steps:
            return
        if self.process is not None:
            self.error("A TES3X command is already running")
            return
        self.run_steps(steps, then=lambda: self.refresh_saves(True))

    def pull_saves(self):
        self.run_save_steps(self.pull_steps(self.selected_saves()))

    def push_steps(self, saves):
        """Pull the xemu saves first, then push the PC copies into this pool on the Xbox."""
        _value, name = self.current_pool()
        wanted = [save for save in saves if save["source"] in ("pc", "xemu")]
        steps = self.pull_steps([save for save in wanted if save["source"] == "xemu"])
        folders = sorted({save["folder"] for save in wanted})
        if folders:
            script, arguments = self.saves_command(
                "push", folders, *(["--pool-name", name] if name else []))
            steps.append((script, arguments, f"Copying {len(folders)} save(s) to the Xbox…"))
        return steps

    def push_saves(self):
        self.run_save_steps(self.push_steps(self.selected_saves()))

    def push_xemu_steps(self, saves):
        """Pull the Xbox saves first, then write the PC copies into this pool on the xemu
        disk."""
        _value, name = self.current_pool()
        wanted = [save for save in saves if save["source"] in ("pc", "xbox")]
        steps = self.pull_steps([save for save in wanted if save["source"] == "xbox"])
        folders = sorted({save["folder"] for save in wanted})
        if folders:
            script, arguments = self.saves_command(
                "push", folders, "--where", "xemu", "--disk", str(self.play_disk()),
                *(["--pool-name", name] if name else []))
            steps.append((script, arguments, f"Copying {len(folders)} save(s) to xemu…"))
        return steps

    def push_xemu_saves(self):
        self.run_save_steps(self.push_xemu_steps(self.selected_saves()))

    def transfer_steps(self, saves, target, name, move):
        """Copy or move saves into pool `target`, each where it is."""
        steps = []
        for where in ("xbox", "pc", "xemu"):
            folders = [save["folder"] for save in saves if save["source"] == where]
            if not folders:
                continue
            extra = ["--to", f"{target:08X}", "--where", where]
            extra += ["--to-name", name] if name else []
            extra += ["--move"] if move else []
            extra += ["--disk", str(self.play_disk())] if where == "xemu" else []
            script, arguments = self.saves_command("copy", folders, *extra)
            steps.append((script, arguments,
                          f"{'Moving' if move else 'Copying'} {len(folders)} "
                          f"{self.SAVE_SOURCES[where]} save(s) to another pool…"))
        return steps

    def transfer_saves(self, move):
        current, _name = self.current_pool()
        pools = {tes3x_savepool.SHARED_ID: {"name": None}, **self.known_pools()}
        choices = {("Shared: the retail game's saves" if pool["name"] is None
                    else f"{pool['name']}  ({value:08X})"): value
                   for value, pool in pools.items() if value != current}
        if not choices:
            self.error("There is no other pool; make one with New pool…")
            return
        verb = "Move" if move else "Copy"
        label, ok = QInputDialog.getItem(
            self, f"{verb} saves",
            f"{verb} the selected saves into this pool. Xbox saves stay on the Xbox and PC "
            "copies on the PC" + (", and each original is deleted once its copy is complete:"
                                  if move else ":"),
            list(choices), 0, False)
        if ok:
            target = choices[label]
            self.run_save_steps(self.transfer_steps(self.selected_saves(), target,
                                                    pools[target]["name"], move))

    def delete_saves(self):
        saves = self.selected_saves()
        if not saves or self.process is not None:
            return
        names = "\n".join(f"• {save.get('name') or save['folder']} "
                          f"({self.SAVE_SOURCES[save['source']]})" for save in saves)
        answer = QMessageBox.question(self, "Delete saves",
                                      f"Delete these saves for good?\n\n{names}")
        if answer != QMessageBox.StandardButton.Yes:
            return
        current, _name = self.current_pool()
        for save in saves:
            if save["source"] == "pc":
                folder = self.save_library() / f"{current:08X}" / save["folder"]
                if not trash(folder):
                    shutil.rmtree(folder, ignore_errors=True)
        steps = []
        for where, extra in (("xbox", []), ("xemu", ["--where", "xemu", "--disk",
                                                    str(self.play_disk())])):
            folders = [save["folder"] for save in saves if save["source"] == where]
            if folders:
                script, arguments = self.saves_command("delete", folders, *extra)
                steps.append((script, arguments,
                              f"Deleting {len(folders)} {self.SAVE_SOURCES[where]} save(s)…"))
        if steps:
            self.run_save_steps(steps)
        else:
            self.refresh_saves(False)

    def open_save_folder(self):
        current, _name = self.current_pool()
        folder = self.save_library() / f"{current:08X}"
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def show_save_info(self):
        saves = self.selected_saves()
        if len(saves) != 1:
            self.context_info.setPlainText("Select a save to see its details.")
            return
        save = saves[0]
        loaded = self.load_order()
        lines = [save.get("name") or save["folder"], "",
                 f"Where: {self.SAVE_SOURCES.get(save['source'], save['source'])}",
                 f"Folder: {save['folder']}", f"Player: {save.get('player') or '?'}",
                 f"Cell: {save.get('cell') or '?'}", f"Saved: {save.get('date') or '?'}", "",
                 f"Plugins ({self.save_fit(save.get('masters', []))[1]}):"]
        lines += [f"  {name}" + ("" if name.casefold() in loaded
                                 or name.casefold() in EXPANSION_PLACEHOLDERS
                                 else "   — not in this profile")
                  for name in save.get("masters", [])]
        self.context_info.setPlainText("\n".join(lines))

    def create_patches_tab(self):
        self.patch_preset = QComboBox()
        self.patch_preset.addItems(["minimal", "recommended", "testing"])
        self.patch_preset.setToolTip("minimal: no optional patches; recommended: release fixes; "
                                     "testing: release and preview fixes, diagnostics and console")
        self.patch_preset.currentTextChanged.connect(self.refresh_patch_states)
        self.patch_search = QLineEdit()
        self.patch_search.setPlaceholderText("Filter patches…")
        self.patch_search.textChanged.connect(self.filter_patches)
        top = QHBoxLayout()
        top.addWidget(QLabel("Start from"))
        top.addWidget(self.patch_preset)
        top.addWidget(self.patch_search, 1)

        self.patch_tree = QTreeWidget()
        self.patch_tree.setHeaderLabels(["Title", "Patch key", "Status", "Included by"])
        self.patch_tree.setAlternatingRowColors(True)
        self.patch_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.patch_tree.header().setStretchLastSection(False)
        self.patch_tree.itemChanged.connect(self.patch_item_changed)
        self.patch_tree.currentItemChanged.connect(self.show_context_info)
        self.patch_items = {}
        self.patch_groups = {}
        for category in PATCH_CATEGORIES:
            entries = [entry for entry in PATCH_CATALOG if entry["category"] == category]
            if not entries:
                continue
            group = QTreeWidgetItem([category])
            group.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            group.setData(0, ROLE, None)
            self.patch_tree.addTopLevelItem(group)
            self.patch_groups[category] = group
            for entry in entries:
                item = QTreeWidgetItem([entry["title"], patch_spec(entry), entry["channel"], ""])
                item.setData(0, ROLE, entry["name"])
                item.setToolTip(0, entry["summary"])
                flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                if entry["selection"] == "preset" and (entry["channel"] != "dev"
                                                         or self.developer_mode):
                    flags |= Qt.ItemFlag.ItemIsUserCheckable
                else:
                    item.setForeground(0, self.palette().placeholderText())
                item.setFlags(flags)
                group.addChild(item)
                self.patch_items[entry["name"]] = item
            group.setExpanded(True)
        self.patch_tree.resizeColumnToContents(0)
        self.patch_tree.resizeColumnToContents(2)
        self.patch_tree.resizeColumnToContents(3)
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addLayout(top)
        layout.addWidget(self.patch_tree, 1)
        return tab

    def patch_configuration(self, modes=None):
        modes = self.patch_modes if modes is None else modes
        order = [entry["name"] for entry in PATCH_CATALOG]

        def selected(mode):
            names = [name for name, value in modes.items() if value == mode]
            return sorted(names, key=lambda name: (order.index(name) if name in order
                                                   else len(order), name))

        return {"preset": self.patch_preset.currentText(), "categories": list(self.patch_categories),
                "enable": selected("enable"), "disable": selected("disable")}

    def effective_patches(self, modes=None):
        profile = {"patches": self.patch_configuration(modes),
                   "profile": {"install_layout": self.build.install_layout.currentData()}
                   if hasattr(self, "build") else {},
                   "mods": self.profile_mods() if hasattr(self, "mod_list") else [],
                   "package": self.build.package_values() if hasattr(self, "build") else {},
                   "preferences": (self.build.preference_values()
                                   if hasattr(self, "build") else {})}
        try:
            applied = set(resolve_patch_plan(profile)["applied"])
        except PipelineError:
            applied = set(profile["patches"]["enable"])
        applied.update(entry["name"] for entry in PATCH_CATALOG if entry["selection"] == "always")
        return applied

    def populate_patches(self, config):
        self.patch_preset.blockSignals(True)
        preset = {"standard": "recommended", "dev": "testing"}.get(
            config.get("preset", "recommended"), config.get("preset", "recommended"))
        self.patch_preset.setCurrentText(preset)
        self.patch_preset.blockSignals(False)
        self.patch_categories = list(config.get("categories", []))
        self.patch_modes = {name: "enable" for name in config.get("enable", [])}
        self.patch_modes.update({name: "disable" for name in config.get("disable", [])})
        self.applied_patches = self.effective_patches()
        self.ini_stash = {}
        self.refresh_patch_states()

    def refresh_patch_states(self, *_args):
        if not hasattr(self, "patch_tree"):
            return
        applied = self.effective_patches()
        by_name = {entry["name"]: entry for entry in PATCH_CATALOG}
        preset = self.patch_preset.currentText()
        required_by = {
            name: [owner["name"] for owner in PATCH_CATALOG
                   if owner["name"] in applied and name in owner.get("requires", [])]
            for name in self.patch_items
        }
        overlay_layout = self.build.install_layout.currentData() == "overlay"
        self.patch_loading = True
        for name, item in self.patch_items.items():
            entry = by_name[name]
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if entry["selection"] == "preset" and (entry["channel"] != "dev"
                                                     or self.developer_mode) \
                    and not required_by[name] \
                    and not (name == "data-overlay" and overlay_layout):
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            item.setFlags(flags)
            on = name in applied
            item.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
            mode = self.patch_modes.get(name)
            why = ""
            if entry["selection"] == "always":
                reason, why = "Always included", "Every build includes this patch"
            elif entry["selection"] == "packaging":
                reason = "Delta-BSA packaging" if on else ""
                why = ("The delta-bsa package mode needs this patch" if on
                       else "Only delta-bsa builds need this patch")
            elif name == "data-overlay" and overlay_layout:
                reason, why = "Overlay install layout", "The shared-base layout needs this patch"
            elif required_by[name]:
                reason = ", ".join(required_by[name])
                why = "Included because " + ", ".join(required_by[name]) + " needs this patch"
            elif mode == "enable":
                reason = "Profile"
            elif mode == "disable":
                reason = "Disabled in profile"
            elif on and entry["category"] in self.patch_categories:
                reason = f"Category: {entry['category']}"
            elif entry["selection"] == "option":
                reason = "Build settings" if on else "Build option"
            else:
                reason = f"{preset} preset" if on else ""
            item.setText(3, reason)
            item.setToolTip(3, why)
            item.setForeground(3, WARNING if entry["selection"] == "always"
                               else self.palette().text().color())
        self.patch_loading = False
        self.patch_tree.resizeColumnToContents(3)
        self.sync_patch_ini(applied)
        self.filter_patches()
        self.patch_loading = True
        for group in self.patch_groups.values():
            states = {group.child(i).checkState(0) for i in range(group.childCount())
                      if not group.child(i).isHidden()
                      and group.child(i).flags() & Qt.ItemFlag.ItemIsUserCheckable}
            group.setCheckState(0, Qt.CheckState.Checked if states == {Qt.CheckState.Checked}
                                else Qt.CheckState.Unchecked if Qt.CheckState.Checked not in states
                                else Qt.CheckState.PartiallyChecked)
        self.patch_loading = False
        if hasattr(self, "mod_list"):
            loading, self.mods_loading = self.mods_loading, True
            for row in self.mod_rows():
                self.update_compat(row)
            self.mods_loading = loading

    def sync_patch_ini(self, applied):
        """Show the ini keys of patches that are on; drop profile values only dead patches read."""
        if not hasattr(self, "ini"):
            return
        gone = self.applied_patches - applied
        back = applied - self.applied_patches
        still = {("xbox", key.casefold()) for entry in PATCH_CATALOG if entry["name"] in applied
                 for key in entry.get("ini", {})}
        for name in self.ini.patch_owned(gone):
            if self.ini.split(name) not in still:
                self.ini_stash[name] = self.ini.values.pop(name)
        for name in self.ini_stash.copy():
            owners = {entry["name"] for entry in PATCH_CATALOG
                      if ("xbox", self.ini.split(name)[1]) in
                      {("xbox", key.casefold()) for key in entry.get("ini", {})}}
            if owners & back:
                self.ini.values[name] = self.ini_stash.pop(name)
        self.applied_patches = applied
        self.ini.set_patches(applied)

    def patch_item_changed(self, item, column):
        if self.patch_loading or column != 0:
            return
        name = item.data(0, ROLE)
        on = item.checkState(0) == Qt.CheckState.Checked
        if name is None:
            names = [item.child(i).data(0, ROLE) for i in range(item.childCount())
                     if not item.child(i).isHidden()
                     and item.child(i).flags() & Qt.ItemFlag.ItemIsUserCheckable]
            on = item.checkState(0) != Qt.CheckState.Unchecked
        else:
            names = [name]
        for name in names:
            modes = {key: value for key, value in self.patch_modes.items() if key != name}
            if (name in self.effective_patches(modes)) == on:
                self.patch_modes = modes
            else:
                self.patch_modes = {**modes, name: "enable" if on else "disable"}
        self.refresh_patch_states()

    def set_patch(self, name, on):
        self.patch_items[name].setCheckState(0, Qt.CheckState.Checked if on
                                             else Qt.CheckState.Unchecked)

    def filter_patches(self, text=None):
        query = (self.patch_search.text() if text is None else text).casefold()
        by_name = {entry["name"]: entry for entry in PATCH_CATALOG}
        for group in self.patch_groups.values():
            visible = 0
            for i in range(group.childCount()):
                child = group.child(i)
                entry = by_name[child.data(0, ROLE)]
                name = entry["name"]
                hidden = bool(query) and query not in (name + " " + entry["title"]
                                                       + " " + entry["summary"]).casefold()
                child.setHidden(hidden)
                visible += not hidden
            group.setHidden(visible == 0)

    def set_developer_mode(self, enabled):
        self.developer_mode = enabled
        if self.settings is not None:
            self.settings.setValue("developer_mode", enabled)
        self.refresh_patch_states()

    def show_patch_details(self, item, _previous):
        name = item.data(0, ROLE) if item else None
        entry = next((value for value in PATCH_CATALOG if value["name"] == name), None)
        if not entry:
            self.context_info.clear()
            return
        origin = entry.get("origin", {"source": "TES3X"})
        source = SOURCES.get(origin.get("source"), {})
        origin_text = source.get("name", origin.get("source", "TES3X"))
        if "id" in origin:
            origin_text += " #" + str(origin["id"])
        lines = [f"{entry['title']} — {patch_spec(entry)}", "", entry["summary"], "",
                 f"Category: {entry['category']}", f"Status: {entry['channel']}",
                 f"Origin: {origin_text}"]
        selection = {"always": "every build", "packaging": "package mode",
                     "preset": "preset or profile", "option": "build option"}
        lines.append(f"Selection: {selection[entry['selection']]}")
        if entry.get("ini"):
            lines += ["", "Settings (INI tab, [Xbox]):"]
            lines += [f"  {key}" + (f" = {value}" if value else "")
                      for key, value in entry["ini"].items()]
        self.context_info.setPlainText("\n".join(lines))

    def error(self, message):
        QMessageBox.critical(self, "TES3X", str(message))

    def open_dialog(self):
        name, _ = QFileDialog.getOpenFileName(self, "Open TES3X profile",
                                              str(self.profiles_dir()), "TOML (*.toml)")
        if name and self.maybe_save():
            self.open_profile(Path(name))

    def local_config_path(self):
        return self.config_path or default_config_path()

    def work_dir(self):
        return self.local_config_path().parent

    def profiles_dir(self):
        config = self.local_config_path()
        try:
            local = tomllib.loads(config.read_text(encoding="utf-8")) if config.is_file() else {}
        except (OSError, tomllib.TOMLDecodeError):
            local = {}
        folder = Path(local.get("paths", {}).get("profiles", "profiles"))
        return (folder if folder.is_absolute() else config.parent / folder).resolve()

    def profile_files(self):
        folder = self.profiles_dir()
        if not folder.is_dir():
            return []
        return sorted(folder.glob("*.toml"), key=lambda path: path.stem.casefold())

    def refresh_profile_list(self):
        folder = self.profiles_dir()
        paths = [path.resolve() for path in self.profile_files()]
        if self.profile_path and self.profile_path not in paths:
            paths.append(self.profile_path)
        self.profile_picker.blockSignals(True)
        self.profile_picker.clear()
        for path in paths:
            self.profile_picker.addItem(path.stem if path.parent == folder else str(path), str(path))
        self.profile_picker.setCurrentIndex(
            self.profile_picker.findData(str(self.profile_path)) if self.profile_path else -1)
        self.profile_picker.blockSignals(False)

    def open_initial_profile(self):
        last = self.settings.value("last_profile", "") if self.settings else ""
        files = self.profile_files()
        candidates = ([Path(last)] if last and Path(last).is_file() else []) + files
        # A profile the GUI cannot open should not stop it opening the next one.
        if not any(self.open_profile(path, quiet=True) for path in candidates):
            self.statusBar().showMessage(
                f"No profile in {self.profiles_dir()} could be opened; use New… to create one"
                if files else f"No profiles in {self.profiles_dir()}; use New… to create one")

    def picker_activated(self, index):
        path = Path(self.profile_picker.itemData(index))
        if path != self.profile_path and self.maybe_save():
            self.open_profile(path)
        self.refresh_profile_list()

    def is_dirty(self):
        if self.document is None:
            return False
        try:
            return self.profile_text() != self.saved_text
        except (PipelineError, LibraryError, tomlkit.exceptions.ParseError):
            return True

    def maybe_save(self):
        if not self.is_dirty():
            return True
        buttons = QMessageBox.StandardButton
        answer = QMessageBox.question(
            self, "Unsaved changes", f"Save changes to {self.profile_path.stem}?",
            buttons.Save | buttons.Discard | buttons.Cancel)
        if answer == buttons.Save:
            return self.save_profile()
        return answer == buttons.Discard

    def closeEvent(self, event):
        if self.maybe_save():
            if self.in_game_listener is not None:
                self.in_game_listener.close()
                self.in_game_listener = None
            event.accept()
        else:
            event.ignore()

    def ask_profile_name(self, title, default=""):
        name, ok = QInputDialog.getText(self, title, "Profile name", text=default)
        name = name.strip()
        if not ok or not name:
            return None
        if not PROFILE_NAME.fullmatch(name):
            self.error("Use letters, digits, dots, underscores and hyphens, "
                       "starting with a letter or digit")
            return None
        if (self.profiles_dir() / f"{name}.toml").exists():
            self.error(f"A profile named {name} already exists")
            return None
        return name

    def write_new_profile(self, name, document):
        if "profile" not in document:
            document["profile"] = tomlkit.table()
        document["profile"]["name"] = name
        path = self.profiles_dir() / f"{name}.toml"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(tomlkit.dumps(document), encoding="utf-8", newline="")
        except OSError as exc:
            self.error(exc)
            return False
        self.open_profile(path)
        return True

    def new_profile(self):
        if not self.maybe_save():
            return
        name = self.ask_profile_name("New profile")
        if not name:
            return
        lines = TEMPLATE.read_text(encoding="utf-8").splitlines()
        text = "\n".join(line for line in lines if not line.startswith("# Copy this file"))
        document = tomlkit.parse(text.lstrip() + "\n")
        document["profile"].pop("library", None)
        document.pop("mods", None)
        self.write_new_profile(name, document)

    def duplicate_profile(self):
        if self.profile_path is None or not self.maybe_save():
            return
        name = self.ask_profile_name("Duplicate profile", self.profile_path.stem + "-copy")
        if name:
            self.write_new_profile(name, tomlkit.parse(self.profile_path.read_text(encoding="utf-8")))

    def rename_profile(self):
        if self.profile_path is None or not self.maybe_save():
            return
        old = self.profile_path
        name = self.ask_profile_name("Rename profile", old.stem)
        if name and self.write_new_profile(name, tomlkit.parse(old.read_text(encoding="utf-8"))):
            old.unlink()
            self.refresh_profile_list()

    def delete_profile(self):
        if self.profile_path is None:
            return
        answer = QMessageBox.question(
            self, "Delete profile", f"Delete {self.profile_path}?\n\nBuilds are not affected.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.profile_path.unlink()
        except OSError as exc:
            self.error(exc)
            return
        self.profile_path = None
        self.document = None
        self.saved_text = None
        self.forget_analysis()
        self.mod_list.clear()
        files = self.profile_files()
        if files:
            self.open_profile(files[0])
        self.refresh_profile_list()

    def first_run(self):
        QMessageBox.information(
            self, "TES3X", "Set where your retail game files are, and how to reach your Xbox.")
        self.edit_local_settings()

    def edit_local_settings(self):
        try:
            dialog = LocalSettingsDialog(self.local_config_path(), self)
        except PipelineError as exc:
            self.error(exc)
            return
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.config_path = dialog.path
        if self.profile_path:
            self.open_profile(self.profile_path)
        self.refresh_profile_list()
        self.refresh_targets()
        self.refresh_ftp_status()
        self.statusBar().showMessage(f"Saved {dialog.path}", 5000)

    def open_profile(self, path, quiet=False):
        try:
            text = path.read_text(encoding="utf-8")
            document = tomlkit.parse(text)
            plain = tomllib.loads(tomlkit.dumps(document))
            validate_profile(plain)
            library_root, catalog, indexed = self.resolve_library(
                plain["profile"].get("library"), plain.get("mods", []))
        except (OSError, tomlkit.exceptions.ParseError, tomllib.TOMLDecodeError,
                PipelineError, LibraryError) as exc:
            if not quiet:
                self.error(f"{path.name}: {exc}")
            return False
        self.profile_path = path.resolve()
        self.check_profile_sha = None
        self.check_failed = False
        self.build_failed = False
        self.deploy_failed = False
        self.deploy_records = {}
        self.document = document
        self.profile_plain = plain
        self.library_root = library_root
        self.catalog = catalog
        self.library_indexed = indexed
        self.scans.clear()
        self.build.load(plain)
        self.load_save_pool(plain)
        self.plugins_loading = True
        self.mlox_at_build.setChecked(plain.get("rules", {}).get("plugin_order") == "mlox")
        self.plugins_loading = False
        self.plugin_order = plain.get("plugins", {}).get("order") or None
        self.populate_mods(plain.get("mods", []))
        self.populate_patches(plain.get("patches", {}))
        vanilla = self.local_path("vanilla_root")
        self.ini.load(plain.get("ini", {}), vanilla / "Morrowind.ini" if vanilla else None)
        self.ini.set_patches(self.applied_patches)
        self.sync_skip_intro()
        try:
            self.saved_text = self.profile_text()
        except (PipelineError, LibraryError, tomlkit.exceptions.ParseError):
            self.saved_text = None
        self.load_remembered_states()
        if self.settings is not None:
            self.settings.setValue("last_profile", str(self.profile_path))
        self.refresh_profile_list()
        self.statusBar().clearMessage()
        if library_root and not indexed:
            self.statusBar().showMessage(
                "The mod library has no library.toml, so mods are added by folder name", 8000)
        self.update_build_state()
        return True

    def resolve_library(self, value, mods):
        """The library a profile uses, its catalog, and whether library.toml indexes it."""
        config = self.local_config_path()
        local = tomllib.loads(config.read_text(encoding="utf-8")) if config.is_file() else {}
        value = value or local.get("paths", {}).get("mod_library")
        if not value:
            if mods:
                raise PipelineError("set a mod library to manage mods")
            return None, {}, False
        root = Path(value)
        root = (root if root.is_absolute() else self.work_dir() / root).resolve()
        if not root.is_dir():
            raise PipelineError(f"mod library not found: {root}")
        indexed = (root / CATALOG_NAME).is_file()
        catalog = load_library(root) if indexed else discover_library(root)
        if any("id" in entry for entry in mods) and not indexed:
            raise PipelineError("mods chosen by id need library.toml in the mod library")
        return root, catalog, indexed

    def library_changed(self):
        value = self.build.library()
        try:
            root, catalog, indexed = self.resolve_library(value, self.profile_mods())
        except (OSError, tomllib.TOMLDecodeError, PipelineError, LibraryError) as exc:
            self.error(exc)
            return
        if root == self.library_root:
            return
        self.library_root, self.catalog, self.library_indexed = root, catalog, indexed
        self.scans.clear()
        self.populate_mods(self.profile_mods())

    def build_changed(self):
        self.refresh_patch_states()
        self.schedule_analysis()

    def skip_intro_toggled(self, on):
        if on != self.ini.skips_intro():
            self.ini.set_skip_intro(on)

    def sync_skip_intro(self):
        self.build.skip_intro.blockSignals(True)
        self.build.skip_intro.setChecked(self.ini.skips_intro())
        self.build.skip_intro.blockSignals(False)

    def write_library_index(self):
        if not self.library_root:
            return
        try:
            path, added = index_library(self.library_root)
            self.catalog = load_library(self.library_root)
            self.library_indexed = True
        except (OSError, LibraryError) as exc:
            self.error(exc)
            return
        self.populate_mods(self.profile_mods())
        self.statusBar().showMessage(f"Added {len(added)} mods to {path}", 5000)

    def convert_to_ids(self):
        if self.profile_path is None or not self.save_profile():
            return
        if not self.library_indexed:
            self.error("Index the library first (File > Index new library folders)")
            return
        with open(self.profile_path, encoding="utf-8", newline="") as stream:
            text = stream.read()
        new_text, converted, skipped = convert_profile(text, self.catalog)
        if converted:
            with open(self.profile_path, "w", encoding="utf-8", newline="") as stream:
                stream.write(new_text)
            self.open_profile(self.profile_path)
        lines = [f"Converted {len(converted)} mods to library ids."]
        lines += [f"Kept {name}: {reason}" for name, reason in skipped]
        QMessageBox.information(self, "TES3X", "\n".join(lines))

    def profile_text(self):
        """Write the editor state into the document and return it, validated."""
        mods = tomlkit.aot()
        for values in self.profile_mods():
            table = tomlkit.table()
            for key in ("name", "id", "version", "components", "order", "enabled",
                        "optional", "plugins", "loose", "archives"):
                if key in values:
                    table.add(key, values[key])
            mods.append(table)
        if len(mods):
            self.document["mods"] = mods
        else:
            self.document.pop("mods", None)
        self.build.apply(self.document)
        self.apply_save_pool(self.document)
        BuildSettings.put(self.document, "rules", "plugin_order",
                          "mlox" if self.mlox_at_build.isChecked() else "mods", "mods")
        order = None if self.mlox_at_build.isChecked() else self.plugin_order
        plugins = self.document.get("plugins")
        if order:
            if plugins is None:
                plugins = new_table(self.document, "plugins")
            if plugins.get("order") != order:
                listed = tomlkit.array()
                listed.extend(order)
                plugins["order"] = listed.multiline(True)
        elif plugins is not None:
            plugins.pop("order", None)
            if not plugins:
                self.document.pop("plugins")
        values = dict(self.ini.values)
        table = self.document.get("ini")
        if table is None and values:
            table = new_table(self.document, "ini")
        if table is not None:
            for key in [key for key in table if key not in values]:
                del table[key]
            for key, value in values.items():
                if table.get(key) != value:
                    table[key] = value
            drop_empty(self.document, "ini")
        patches = self.document.get("patches")
        if patches is None:
            patches = new_table(self.document, "patches")
        for key, value in self.patch_configuration().items():
            # Leave unchanged values alone so their comments and layout survive.
            if isinstance(value, list) and not value and key not in patches:
                continue
            if isinstance(value, list) and set(patches.get(key, [])) == set(value):
                continue
            if patches.get(key) != value:
                patches[key] = value
        text = tomlkit.dumps(self.document).rstrip("\n") + "\n"
        validate_profile(tomllib.loads(text))
        return text

    def save_profile(self):
        if not self.profile_path or self.document is None:
            return False
        try:
            text = self.profile_text()
            plain = tomllib.loads(text)
            self.profile_path.write_text(text, encoding="utf-8", newline="")
        except (OSError, PipelineError, LibraryError, tomlkit.exceptions.ParseError) as exc:
            self.error(exc)
            return False
        self.profile_plain = plain
        self.saved_text = text
        self.statusBar().showMessage(f"Saved {self.profile_path}", 5000)
        self.update_build_state()
        return True

    def run_pipeline(self, extra):
        if self.process is not None:
            self.error("A TES3X command is already running")
            return
        requested = ("check" if "--check" in extra else
                     "deploy" if "--deploy" in extra else "build")
        if requested == "build" and self.play_process is not None:
            self.error("Stop xemu before replacing the build it is running")
            return
        if not self.save_profile():
            return
        self.command_kind = requested
        self.command_target = self.target_picker.currentData()
        self.start_command(ROOT / "tools" / "tes3x_pipeline.py", [
            str(self.profile_path),
            *(["--config", str(self.local_config_path())]
              if self.local_config_path().is_file() else []),
            *(["--target", self.target_picker.currentData()]
              if self.target_picker.currentData() else []),
            *extra,
        ], "Running TES3X pipeline…")

    def build_output(self):
        root = self.local_path("build_root") or self.work_dir() / "build"
        return root / self.profile_plain["profile"]["name"]

    @staticmethod
    def read_state(path, fallback):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, type(fallback)) else fallback
        except (OSError, ValueError):
            return fallback

    @staticmethod
    def write_state(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        staged = path.with_name(path.name + ".tmp")
        staged.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        staged.replace(path)

    def load_remembered_states(self):
        check = self.read_state(self.build_output() / CHECK_MARKER, {})
        self.check_profile_sha = check.get("profile_sha256")
        self.check_failed = bool(check and check.get("result") != "pass")
        self.deploy_records = self.read_state(self.build_output() / DEPLOYS_MARKER, {})

    def remember_check(self, passed):
        try:
            profile_sha = sha256_file(self.profile_path)
            self.write_state(self.build_output() / CHECK_MARKER, {
                "profile_sha256": profile_sha,
                "time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "result": "pass" if passed else "fail",
            })
            self.check_profile_sha = profile_sha
        except OSError:
            self.check_profile_sha = None
        self.check_failed = not passed

    def remember_deploy(self, target_name):
        if not target_name:
            return
        marker = self.build_output() / PIPELINE_MARKER
        try:
            record = {
                "profile_sha256": sha256_file(self.profile_path),
                "build_sha256": sha256_file(marker),
                "time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            records = self.read_state(self.build_output() / DEPLOYS_MARKER, {})
            records[target_name] = record
            self.write_state(self.build_output() / DEPLOYS_MARKER, records)
            self.deploy_records = records
        except OSError:
            return

    def build_status(self):
        """'built', 'stale' or 'missing', and why, for the saved profile's pipeline output."""
        if not self.profile_path:
            return "missing", "No profile"
        marker = self.build_output() / PIPELINE_MARKER
        try:
            record = json.loads(marker.read_text(encoding="utf-8"))
            current = record.get("profile_sha256") == sha256_file(self.profile_path)
        except (OSError, ValueError):
            return "missing", "Not built yet; Play builds it first"
        if not current or self.is_dirty():
            return "stale", "The profile changed since the last build; Play rebuilds it first"
        stamp = datetime.datetime.fromtimestamp(marker.stat().st_mtime)
        return "built", (f"Built {stamp:%Y-%m-%d %H:%M}. Changes to mod files since then are not "
                         "detected; Build to pick them up")

    def update_build_state(self):
        self.update_check_state()
        self.update_deploy_state()
        if self.command_kind == "build":
            self.set_action_state(self.build_button, "Build", "running",
                                  "The profile is being built")
            return
        if self.build_failed:
            self.set_action_state(self.build_button, "Build", "failed",
                                  "The last build command failed; see the output")
            return
        state, tip = self.build_status()
        self.set_action_state(self.build_button, "Build",
                              {"built": "current", "stale": "stale",
                               "missing": "idle"}[state], tip)

    @staticmethod
    def set_action_state(button, label, state, tip):
        colours = {"idle": "#616161", "current": "#2e7d32", "stale": "#a15c00",
                   "running": "#a15c00", "failed": "#b3261e"}
        button.setText(label)
        button.setIcon(dot_icon(colours[state]))
        button.setProperty("state", state)
        button.setToolTip(tip)

    def update_check_state(self):
        if not hasattr(self, "check_button"):
            return
        if self.command_kind == "check":
            state, tip = "running", "Checking the saved profile"
        elif self.check_failed:
            state, tip = "failed", "The last check failed; see the output"
        elif self.check_profile_sha is None:
            state, tip = "idle", "Run Check profile"
        else:
            try:
                current = sha256_file(self.profile_path) == self.check_profile_sha
            except OSError:
                current = False
            if current and not self.is_dirty():
                state, tip = ("current", "The saved profile passed Check. Changes inside mod "
                              "folders are not detected")
            else:
                state, tip = "stale", "The profile changed since Check"
        self.set_action_state(self.check_button, "Check", state, tip)

    def update_deploy_state(self):
        if not hasattr(self, "deploy_button"):
            return
        target = self.default_target()
        if not self.profile_path:
            state, tip = "idle", "Open a profile before deploying"
        elif not target or target.get("kind") != "xbox":
            state, tip = "idle", "Select an Xbox target to deploy"
        elif self.command_kind == "deploy":
            state, tip = "running", "Synchronizing the build to the Xbox"
        elif self.deploy_failed:
            state, tip = "failed", "The last deploy failed; see the output"
        else:
            record = self.deploy_records.get(target["name"], {})
            try:
                marker = self.build_output() / PIPELINE_MARKER
                current = (record.get("profile_sha256") == sha256_file(self.profile_path)
                           and record.get("build_sha256") == sha256_file(marker))
            except OSError:
                current = False
            if current and not self.is_dirty():
                when = record.get("time", "an earlier session")
                state = "current"
                tip = (f"This build was deployed to {target['name']} at {when}. Changes inside "
                       "mod folders are not detected")
            elif record:
                state, tip = "stale", f"The build deployed to {target['name']} is out of date"
            else:
                state, tip = "stale", f"This build has not been deployed to {target['name']}"
        self.set_action_state(self.deploy_button, "Deploy", state, tip)

    def refresh_play_menu(self):
        """Actions that apply to the target selected in the toolbar."""
        self.play_addons = {key: module for module in enabled_addons(self.local_values()).values()
                            for key in module.PLAY}
        target = self.default_target()
        xemu = bool(target and target.get("kind") == "xemu")
        xbox = bool(target and target.get("kind") == "xbox")
        self.play_menu.clear()
        gdb = self.play_menu.addAction("Debug with GDB", self.set_play_gdb)
        gdb.setCheckable(True)
        gdb.setChecked(self.play_gdb)
        gdb.setEnabled(xemu)
        gdb.setToolTip("Open xemu's GDB stub; the status bar shows the port to attach to")
        reset = self.play_menu.addAction("Reset xemu saves…", self.reset_play_disk)
        reset.setEnabled(xemu)
        self.play_menu.addSeparator()
        pull = self.play_menu.addAction("Pull logs", self.pull_logs)
        pull.setEnabled(xbox and self.process is None)
        refresh = self.play_menu.addAction("Refresh connection", self.refresh_ftp_status)
        refresh.setEnabled(xbox)
        name = target["name"] if target else "a target"
        if self.play_process is not None:
            self.action_play.setText("&Stop")
            self.play_button.setText("Stop")
            self.play_button.setToolTip("Stop the xemu process started by this session")
            self.action_play.setEnabled(self.play_pid is not None)
            self.action_stop.setEnabled(self.play_pid is not None)
            self.action_build.setEnabled(False)
            return
        reason = self.play_available()
        self.action_play.setText("&Play")
        self.play_button.setText("Play")
        self.play_button.setToolTip(reason or f"Play on {name} (F9)")
        self.action_play.setEnabled(not reason and self.process is None)
        self.action_stop.setEnabled(False)
        self.action_build.setEnabled(self.process is None)

    def set_play_gdb(self):
        self.play_gdb = not self.play_gdb
        if self.settings is not None:
            self.settings.setValue("play_gdb", self.play_gdb)

    def play_available(self):
        """Why the selected target cannot run, or None when it can."""
        local = self.local_values()
        target = self.default_target()
        if target is None:
            return "Configure and select a target in File > Settings"
        if target.get("kind") == "xbox":
            if not target.get("host"):
                return "Set the Xbox target's address in File > Settings"
            module = self.play_addons.get("xbox")
            if module is None:
                return "Enable the Xbox dashboard agent add-on in File > Settings"
            selected = dict(local)
            selected["default_target"] = target["name"]
            return module.status(selected)
        xemu = resolve_xemu(target, self.local_config_path().parent)
        if not xemu.get("exe"):
            return "Set the xemu folder in File > Settings"
        if target.get("ram", 64) == 128 and not xemu.get("bios_128mb"):
            return "Set a 128 MB BIOS in File > Settings"
        return None

    def play(self):
        if self.play_process is not None:
            self.stop_play()
            return
        reason = self.play_available()
        if reason:
            self.error(reason)
            return
        if self.process is not None:
            self.error("A TES3X command is already running")
            return
        if not self.save_profile():
            return
        if self.build_status()[0] == "built":
            self.start_play()
            return
        self.run_pipeline([])
        if self.process is not None:
            self.after_command = self.start_play

    def play_iso(self):
        """The ISO an earlier play made of the current build, so it is not packed again."""
        built = (self.build_output() / PIPELINE_MARKER).stat().st_mtime
        runs = self.work_dir() / "build" / "xemu"
        name = self.target_picker.currentData()
        isos = sorted(runs.glob(f"play-{self.profile_path.stem}-{name}-*/game.iso"),
                      key=lambda path: path.stat().st_mtime)
        current = isos[-1] if isos and isos[-1].stat().st_mtime >= built else None
        for iso in isos:
            if iso != current:
                iso.unlink(missing_ok=True)
        return current

    def play_context(self):
        target = self.default_target()
        return {"profile": self.profile_path, "config": self.local_config_path(),
                "deploy": self.build_output() / "deploy", "plain": self.profile_plain,
                "local": self.local_values(), "target": target.get("name") if target else None}

    DEPLOY_QUESTION = ("Deploy over existing files?", "This deploy would write over:",
                       "Files in the game folder that the build does not have are deleted. "
                       "Deploy anyway?")
    PUSH_QUESTION = ("Replace saves?", "These saves are already on the Xbox:",
                     "Replace them with the copies from the PC?")

    def run_steps(self, steps, first=True, then=None):
        """Run commands one after another, stopping at the first that fails; `then` runs after
        the last one succeeds."""
        script, arguments, message = steps[0]
        if Path(script).name == "tes3x_deploy.py":
            self.command_kind = "deploy"
            self.command_target = self.target_picker.currentData()
        self.start_command(script, arguments, message, clear=first)
        question = {"tes3x_deploy.py": self.DEPLOY_QUESTION,
                    "tes3x_saves.py": self.PUSH_QUESTION}.get(Path(script).name)
        if question and "--replace" not in arguments:
            self.conflict_retry = (lambda: self.run_steps(
                [(script, [*arguments, "--replace"], message), *steps[1:]], False, then),
                question)
        if Path(script).name == "tes3x_deploy.py" and "--ignore-space" not in arguments:
            self.space_retry = lambda: self.run_steps(
                [(script, [*arguments, "--ignore-space"], message), *steps[1:]], False, then)
        if len(steps) > 1:
            self.after_command = lambda: self.run_steps(steps[1:], False, then)
        elif then:
            self.after_command = then

    def start_play(self):
        target = self.default_target()
        if target is None:
            self.error("Select a target")
            return
        module = self.play_addons.get(target.get("kind"))
        if module is not None:
            context = self.play_context()
            try:
                steps = module.play_steps(target.get("kind"), context)
            except ValueError as exc:
                self.error(exc)
                return
            question = module.confirm(target.get("kind"), context) \
                if hasattr(module, "confirm") else None
            key = f"confirmed/{target['name']}/{self.profile_path}"
            if question and not (self.settings and self.settings.value(key, False, bool)):
                if QMessageBox.question(self, "TES3X", question) != \
                        QMessageBox.StandardButton.Yes:
                    return
                if self.settings is not None:
                    self.settings.setValue(key, True)
            self.run_steps(steps)
            return
        running = running_xemu()
        if running and QMessageBox.question(
                self, "TES3X", f"xemu is already running (PID {', '.join(running)}). "
                "Start another session?") != QMessageBox.StandardButton.Yes:
            return
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        iso = self.play_iso()
        source = (["--iso", str(iso)] if iso
                  else ["--deploy", str(self.build_output() / "deploy"), "--keep-iso"])
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("TES3X_CONFIG", str(self.local_config_path()))
        name = f"play-{self.profile_path.stem}-{target['name']}-{stamp}"
        self.play_run = self.work_dir() / "build" / "xemu" / name
        self.start_play_process(ROOT / "tools" / "tes3x_xemu.py",
                                [name, *source, "--target", target["name"],
                                 "--config", str(self.local_config_path()),
                                 *(["--gdb"] if self.play_gdb else []),
                                 "--disk", str(self.play_disk())],
                                f"Playing in {target['name']}…", environment)

    def play_disk(self):
        """The profile's own xemu disk, which keeps its saves between plays."""
        return self.work_dir() / "build" / "play" / self.profile_path.stem / "hdd.qcow2"

    def reset_play_disk(self):
        if self.process is not None or self.play_process is not None:
            self.error("Stop running TES3X commands and xemu before resetting its saves")
            return
        disk = self.play_disk() if self.profile_path else None
        if disk is None or not disk.is_file():
            QMessageBox.information(self, "TES3X", "This profile has no xemu saves yet.")
            return
        answer = QMessageBox.question(
            self, "Reset xemu saves",
            f"Delete this profile's xemu disk and every save on it?\n\n{disk}")
        if answer == QMessageBox.StandardButton.Yes:
            # A save pool added to a kept disk stacks it on hdd-N.qcow2 layers.
            for layer in disk.parent.glob(f"{disk.stem}-*{disk.suffix}"):
                layer.unlink()
            disk.unlink()
            self.statusBar().showMessage("Deleted the xemu saves; the next Play starts clean", 5000)

    def run_smoke_test(self):
        if self.process is not None:
            self.error("A TES3X command is already running")
            return
        if not self.save_profile():
            return
        self.start_command(ROOT / "tools" / "tes3x_test.py", [
            str(self.profile_path),
            *(["--config", str(self.local_config_path())]
              if self.local_config_path().is_file() else []),
            "--record",
        ], "Running profile smoke test…")

    def deploy_profile(self):
        if self.process is not None:
            self.error("A TES3X command is already running")
            return
        overlay = self.build.install_layout.currentData() == "overlay"
        target = self.default_target("xbox") or {}
        retail = target.get("retail_root")
        overlay_note = (f"\n\nThe shared retail base at {retail or '<not configured>'} will be "
                        "installed or synchronized first." if overlay else "")
        answer = QMessageBox.question(
            self, "Deploy profile",
            "Build this profile and synchronize it to the configured Xbox destination?\n\n"
            "Files absent from the build are removed from that destination. Uploaded files are "
            "verified by size before the command succeeds." + overlay_note)
        if answer != QMessageBox.StandardButton.Yes:
            return
        arguments = ["--deploy", "--verify-deploy", "size"]
        if overlay:
            arguments.append("--install-retail-base")
        if self.discard_after_deploy.isChecked():
            arguments.append("--discard-build")
        self.run_pipeline(arguments)
        if self.process is not None:
            self.conflict_retry = (self.deploy_built, self.DEPLOY_QUESTION)
            self.space_retry = lambda: self.deploy_built("--ignore-space")

    def deploy_built(self, *extra):
        """Deploy the finished build with --replace, after a conflict or a shortage of space
        stopped the pipeline's."""
        target = self.default_target("xbox") or {}
        remote = self.xbox_destination()
        if not remote:
            self.error("Configure an Xbox target before deploying")
            return
        arguments = [str(self.build_output() / "deploy"), "--remote", remote,
                     "--config", str(self.local_config_path()), "--verify", "size", "--replace",
                     *extra]
        if target.get("name"):
            arguments += ["--target", target["name"]]
        if self.profile_plain.get("rules", {}).get("clear_cache_partitions", False):
            arguments.append("--clear-cache")
        self.run_steps([(ROOT / "tools" / "tes3x_deploy.py", arguments, f"Deploying to {remote}…")],
                       first=False)
        if self.discard_after_deploy.isChecked():
            self.after_command = lambda: shutil.rmtree(self.build_output(), ignore_errors=True)

    def pull_logs(self):
        if self.process is not None:
            self.error("A TES3X command is already running")
            return
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        destination = self.work_dir() / "build" / "xbox-logs" / timestamp
        config = self.local_config_path()
        self.start_command(ROOT / "tools" / "tes3x_fetch.py", [
            "E:/tes3x*", "--out", str(destination),
            *(["--config", str(config)] if config.is_file() else []),
            *(["--target", self.target_picker.currentData()]
              if self.target_picker.currentData() else []),
        ], f"Pulling Xbox logs to {destination}…")

    def start_command(self, program, arguments, message, environment=None, clear=True):
        if clear:
            self.output.clear()
        process = QProcess(self)
        process.setWorkingDirectory(str(self.work_dir()))
        if environment is not None:
            process.setProcessEnvironment(environment)
        process.setProgram(sys.executable)
        process.setArguments([str(program), *arguments])
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self.append_process_output)
        process.finished.connect(self.command_finished)
        self.process = process
        self.target_picker.setEnabled(False)
        for action in self.command_actions:
            if action is not self.action_play or self.play_process is None:
                action.setEnabled(False)
        self.statusBar().spinner.start()
        self.update_build_state()
        process.start()
        self.statusBar().showMessage(message)

    def start_play_process(self, program, arguments, message, environment=None):
        """Start the long-lived xemu wrapper without occupying the command process slot."""
        self.output.clear()
        process = QProcess(self)
        process.setWorkingDirectory(str(self.work_dir()))
        if environment is not None:
            process.setProcessEnvironment(environment)
        process.setProgram(sys.executable)
        process.setArguments([str(program), *arguments])
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self.append_play_output)
        process.finished.connect(self.play_finished)
        self.play_process = process
        self.play_target = self.target_picker.currentData()
        self.play_pid = None
        self.play_output_buffer = ""
        self.statusBar().spinner.start()
        if self.play_target:
            self.set_target_runtime(self.play_target, process="starting")
        self.refresh_play_menu()
        process.start()
        self.statusBar().showMessage(message)

    def append_play_output(self):
        if self.play_process is None:
            return
        text = bytes(self.play_process.readAllStandardOutput()).decode(errors="replace")
        self.output.moveCursor(QTextCursor.MoveOperation.End)
        self.output.insertPlainText(text)
        self.play_output_buffer = (self.play_output_buffer + text)[-512:]
        match = re.search(r"xemu: started, pid (\d+)", self.play_output_buffer)
        if match and self.play_pid is None:
            self.play_pid = int(match.group(1))
            if getattr(self, "play_target", None):
                self.set_target_runtime(self.play_target, process="running")
            self.statusBar().spinner.stop()
            port = self.play_run / "gdb.port" if self.play_gdb and self.play_run else None
            if port is not None and port.is_file():
                value = port.read_text().strip()
                attach = f'gdb -ex "target remote 127.0.0.1:{value}"'
                self.output.insertPlainText(f"\nGDB stub on 127.0.0.1:{value}; attach with "
                                            f"{attach}\n")
                self.statusBar().showMessage(f"Playing · GDB :{value}")
            else:
                self.statusBar().showMessage(f"Playing in xemu · PID {self.play_pid}")
            self.refresh_play_menu()

    def stop_play(self):
        if self.play_process is None:
            return
        if self.play_pid is None:
            self.error("xemu is still starting; wait for its PID before stopping it")
            return
        try:
            os.kill(self.play_pid, signal.SIGTERM)
        except OSError as exc:
            self.error(f"Could not stop xemu PID {self.play_pid}: {exc}")
            return
        self.statusBar().showMessage(f"Stopping xemu PID {self.play_pid}; recovering its log…")
        if getattr(self, "play_target", None):
            self.set_target_runtime(self.play_target, process="stopping")
        self.action_play.setEnabled(False)
        self.action_stop.setEnabled(False)

    def play_finished(self, code, _status):
        self.append_play_output()
        if getattr(self, "play_target", None):
            self.set_target_runtime(self.play_target, process="stopped")
        self.play_process = None
        self.play_pid = None
        self.play_output_buffer = ""
        self.statusBar().spinner.stop()
        self.statusBar().showMessage(f"xemu session exited {code}; log recovery finished", 5000)
        self.refresh_play_menu()
        self.update_build_state()
        self.refresh_saves(False)

    def refresh_ftp_status(self):
        if self.ftp_probe is not None:
            return
        config = self.local_config_path()
        try:
            local = tomllib.loads(config.read_text(encoding="utf-8")) if config.is_file() else {}
        except (OSError, tomllib.TOMLDecodeError) as exc:
            name = self.target_picker.currentData()
            if name:
                self.set_target_runtime(name, ftp="offline", dashboard="unknown")
                self.target_drive_tips[name] = f"Configuration error: {exc}"
            return
        try:
            target = tes3x_targets.resolve(local, self.target_picker.currentData(), kind="xbox")
        except tes3x_targets.TargetError:
            target = None
        host = target.get("host") if target else None
        if not host:
            if target:
                self.set_target_runtime(target["name"], ftp="offline", dashboard="unknown")
                self.target_drive_tips[target["name"]] = "Set the Xbox host in File > Settings"
            return
        process = QProcess(self)
        process.setWorkingDirectory(str(self.work_dir()))
        process.setProgram(sys.executable)
        process.setArguments([str(ROOT / "tools" / "tes3x_fetch.py"), "E:/", "--list",
                              "--config", str(config), "--target", target["name"]])
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.finished.connect(self.ftp_probe_finished)
        self.ftp_probe = process
        self.ftp_probe_target = target["name"]
        self.set_target_runtime(target["name"], ftp="checking", dashboard="unknown")
        process.start()

    def ftp_probe_finished(self, code, _status):
        output = ""
        if self.ftp_probe is not None:
            output = bytes(self.ftp_probe.readAllStandardOutput()).decode(errors="replace").strip()
        name = getattr(self, "ftp_probe_target", self.target_picker.currentData())
        self.set_target_runtime(name, ftp="connected" if code == 0 else "offline",
                                dashboard="checking" if code == 0 else "unknown")
        if code != 0:
            self.target_drive_tips[name] = output or f"FTP probe exited {code}"
        self.ftp_probe = None
        if code == 0 and name == self.target_picker.currentData():
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
        name = name or self.target_picker.currentData()
        if command is None or not name or self.agent_probe is not None:
            if command is None and name:
                self.set_target_runtime(name, dashboard="unknown")
            return
        script, arguments, expected = command
        process = QProcess(self)
        process.setWorkingDirectory(str(self.work_dir()))
        process.setProgram(sys.executable)
        process.setArguments([str(script), *arguments, "--config", str(self.local_config_path()),
                              "--target", name])
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.finished.connect(self.dashboard_probe_finished)
        self.agent_probe = process
        self.agent_probe_target = name
        self.agent_probe_expected = expected
        self.set_target_runtime(name, dashboard="checking")
        process.start()

    def dashboard_probe_finished(self, code, _status):
        output = bytes(self.agent_probe.readAllStandardOutput()).decode(errors="replace").strip()
        name = getattr(self, "agent_probe_target", self.target_picker.currentData())
        expected = getattr(self, "agent_probe_expected", 0)
        self.agent_probe = None
        state, detail = dashboard_agent_state(output, code, expected)
        self.set_target_runtime(name, dashboard=state, dashboard_detail=detail)
        if state == "current" and name == self.target_picker.currentData():
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
        process = QProcess(self)
        process.setWorkingDirectory(str(self.work_dir()))
        process.setProgram(sys.executable)
        name = self.target_picker.currentData()
        process.setArguments([str(script), *arguments, "--config", str(self.local_config_path()),
                              *(["--target", name] if name else [])])
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.finished.connect(self.drive_probe_finished)
        self.drive_probe = process
        self.drive_probe_target = name
        process.start()

    def drive_probe_finished(self, code, _status):
        output = bytes(self.drive_probe.readAllStandardOutput()).decode(errors="replace").strip()
        self.drive_probe = None
        drives = parse_drives(output) if code == 0 else {}
        name = getattr(self, "drive_probe_target", self.target_picker.currentData())
        if not drives:
            self.target_drive_tips[name] = ("The dashboard agent did not report drive space: "
                                            + (output or f"exit {code}"))
            self.refresh_target_item(name)
            return
        remote = self.xbox_destination() or "F:"
        target = remote[0].upper()
        lines = []
        for drive, (free, total) in sorted(drives.items()):
            if free is None:
                continue
            used = f", {100 - 100 * free // total}% used" if total else ""
            lines.append(f"{drive}: {free / 1024:.1f} GB free of "
                         f"{(total or 0) / 1024:.1f} GB{used}")
        self.target_drive_tips[name] = "\n".join(lines or ["No drive reported its space"])
        self.refresh_target_item(name)

    def append_process_output(self):
        if self.process is None:
            return
        text = bytes(self.process.readAllStandardOutput()).decode(errors="replace")
        shown = text.replace("\r\n", "\n").replace("\r", "\n")
        self.output.moveCursor(QTextCursor.MoveOperation.End)
        self.output.insertPlainText(shown)
        if XEMU_STARTED in text:
            self.statusBar().spinner.stop()
            port = self.play_run / "gdb.port" if self.play_gdb and self.play_run else None
            if port is not None and port.is_file():
                port = port.read_text().strip()
                attach = f'gdb -ex "target remote 127.0.0.1:{port}"'
                self.output.insertPlainText(f"\nGDB stub on 127.0.0.1:{port}; attach with "
                                            f"{attach}\n")
                self.statusBar().showMessage(f"Playing · GDB :{port}")
            else:
                self.statusBar().showMessage("Playing in xemu")

    def command_finished(self, code, _status):
        self.append_process_output()
        self.statusBar().showMessage(f"TES3X exited {code}", 5000)
        kind, self.command_kind = self.command_kind, None
        target, self.command_target = self.command_target, None
        if kind == "check":
            self.remember_check(code == 0)
        elif kind == "build":
            self.build_failed = code != 0
        elif kind == "deploy":
            self.deploy_failed = code != 0
            if code == 0:
                self.remember_deploy(target)
        self.process = None
        self.statusBar().spinner.stop()
        self.target_picker.setEnabled(True)
        for action in self.command_actions:
            action.setEnabled(True)
        self.target_selection_changed(probe=False)
        self.update_build_state()
        follow, self.after_command = self.after_command, None
        retry, self.conflict_retry = self.conflict_retry, None
        space, self.space_retry = self.space_retry, None
        self.saves_selected()
        if code == DEPLOY_NO_SPACE and space:
            if kind == "build":
                self.build_failed = False
            elif kind == "deploy":
                self.deploy_failed = False
            self.update_build_state()
            self.confirm_space(space)
            return
        if code == DEPLOY_CONFLICT and retry:
            if kind == "build":
                self.build_failed = False
            elif kind == "deploy":
                self.deploy_failed = False
            self.update_build_state()
            self.confirm_replace(*retry)
            return
        if follow and code == 0:
            follow()

    def confirm_space(self, retry):
        """The Xbox's drive looked too small for the deploy; ask before trying anyway."""
        lines = [line.strip() for line in self.output.toPlainText().splitlines()
                 if line.strip().startswith(("space:", "nothing changed:"))]
        answer = QMessageBox.warning(
            self, "Not enough space on the Xbox",
            "\n".join(lines[-2:]) + "\n\nThe deploy may fail partway and leave the game folder "
            "incomplete. Deploy anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            retry()

    def confirm_replace(self, retry, question):
        """A command stopped rather than overwrite something; ask before repeating it."""
        title, lead, ask = question
        lines = [line.split("conflict: ", 1)[1] for line in self.output.toPlainText().splitlines()
                 if "conflict: " in line]
        answer = QMessageBox.warning(
            self, title, lead + "\n\n" + "\n".join(f"• {line}" for line in lines)
            + "\n\n" + ask,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            retry()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", nargs="?", help="profile to open instead of the last one")
    parser.add_argument("--config", help="local TES3X config")
    args = parser.parse_args(argv)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("TES3X")
    window = ProfileWindow(args.profile, args.config, QSettings("TES3X", "TES3X"))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
