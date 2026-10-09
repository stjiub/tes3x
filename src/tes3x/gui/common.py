#!/usr/bin/env python3
"""TES3X GUI. It edits the same TOML the command-line tools read."""

import argparse
import collections
from collections import defaultdict
import datetime
import fnmatch
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
import time
import tomllib
import uuid

try:
    import tomlkit
    from PySide6.QtCore import (QAbstractTableModel, QEvent, QFile, QModelIndex, QProcess,
                                QProcessEnvironment, QPointF, QSettings, QSortFilterProxyModel, QTimer, Qt,
                                QUrl, Signal)
    from PySide6.QtGui import (QAction, QActionGroup, QColor, QPolygonF, QDesktopServices, QIcon,
                               QKeySequence, QPainter, QPainterPath, QPen, QPixmap,
                               QTextCursor, QTransform)
    from PySide6.QtWidgets import (
        QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
        QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
        QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
        QPushButton, QScrollArea, QSpinBox, QSplitter, QStackedWidget, QStatusBar, QStyle,
        QTabBar, QTableView, QTabWidget, QTextBrowser, QTextEdit, QToolButton, QTreeWidget, QTreeWidgetItem,
        QVBoxLayout, QWidget,
    )
except ImportError as exc:
    raise SystemExit(
        "The TES3X GUI needs PySide6 and tomlkit: use a portable folder, or install TES3X with "
        "its GUI (`python -m pip install -e .[gui]` in a checkout)"
    ) from exc

from tes3x.build import DEFAULT_EXCLUDE, PLUGIN_EXT, Mod, plugin_masters, texture_dims
from tes3x.bsa import Bsa
from tes3x.library import (ARCHIVES, CATALOG_NAME, LibraryError, append_mods, convert_profile,
                           discover_library, extract_archive, free_id, guess_release,
                           index_library, install_files, install_layout, load_library, nexus_id,
                           resolve_selection)
from tes3x.catalog import STATUS_LABELS as COMPAT_LABELS, CatalogError, load as load_catalog
from tes3x.catalog import match as match_catalog, needs as catalog_needs
from tes3x.patches import (CATEGORIES as PATCH_CATEGORIES, PATCHES as PATCH_CATALOG, SOURCES,
                           patch_spec)
from tes3x.plugins import (BASE_MASTERS, collect, default_rules, dependency_order, fetch_rules,
                           rules_file, sort_files, warnings as mlox_notes)
from tes3x.pipeline import (CHECK_MARKER, DEPLOY_CONFLICT, DEPLOY_NO_SPACE, MARKER as PIPELINE_MARKER, PipelineError,
                            resolve_patch_plan, validate_local_config, validate_profile)
from tes3x.records import records, subrecords
from tes3x.deploy import parse_drives
from tes3x.agent import (AgentListener, Fetch, OP_REBOOT, console_request, is_manager,
                         key_fingerprint, load_or_create_key)
from tes3x.gui_pages import ServerPage, TargetsPage, fetch_destination
import tes3x.nexus as nexus
import tes3x.saves as saves_tool
import tes3x.savepool as tes3x_savepool
import tes3x.targets as tes3x_targets
from tes3x.paths import bundled, checkout, docs_url, local_config, resource
from tes3x.xemu_setup import resolve as resolve_xemu
import tes3x


VERSION = tes3x.__version__
ROLE = Qt.ItemDataRole.UserRole
EXTRA = Qt.ItemDataRole.UserRole + 1
TEMPLATE = resource("examples", "profile.toml")
PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
RETAIL_PLUGINS = ("Morrowind.esm", "Tribunal.esm", "Bloodmoon.esm")
EXPANSION_PLACEHOLDERS = {"tribunal.esm", "bloodmoon.esm"}
WINS = QColor(60, 170, 60, 60)
LOSES = QColor(210, 60, 60, 60)
WARNING = QColor(200, 40, 40)
CHANNEL_BADGES = {"dev": QColor(215, 150, 20, 110), "preview": QColor(50, 130, 220, 100),
                  "release": QColor(60, 170, 60, 100)}
COMPAT = {"works": ("\u2713", QColor(60, 170, 60)),
          "works-with-requirements": ("*", QColor(215, 150, 20)),
          "broken": ("\u2717", WARNING), "not-possible": ("\u2717", WARNING)}
XEMU_STARTED = "xemu: started"
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

    if kind == "xbox" and ("ftp" in capabilities or (
            game == "connected" and runtime.get("game_kind") == "manager")):
        capabilities.add("install_manager")
    if game == "connected" and runtime.get("game_kind") == "manager":
        capabilities.update({"manager_agent", "agent_fetch", "pull_logs"})
    elif game == "connected":
        capabilities.update({"in_game_agent", "live_status", "live_logs", "commands",
                             "agent_fetch", "pull_logs"})
    elif game == "stalled":
        capabilities.update({"in_game_agent", "stalled"})
    return frozenset(capabilities)


def target_runtime_label(target, runtime=None):
    """Short state label shared by target badges and capability-driven pages."""
    runtime = runtime or {}
    if runtime.get("game") == "connected":
        return "Manager" if runtime.get("game_kind") == "manager" else "In game"
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


def ftp_error(line):
    """A failed FTP probe's last output line, without the exception class and errno."""
    if "10061" in line or "ConnectionRefusedError" in line:
        return "connection refused (no FTP server running)"
    if "timed out" in line or "10060" in line or "TimeoutError" in line:
        return "timed out"
    return re.sub(r"^\w+(Error|Exception): (\[\w+ \d+\] )?", "", line)


MEMORY_LOG_SECONDS = 5


class PlaySession:
    """One xemu run this window started: its wrapper process, xemu's PID and output so far."""

    def __init__(self, target):
        self.target = target
        self.process = self.pid = self.run = None
        self.output = ""


def heartbeat_text(beat):
    """The in-game agent's last heartbeat: free memory, frame time, dropped log lines."""
    text = f"{beat['free_kb'] / 1024:.1f} MB free, frame {beat['frame_us'] / 1000:.1f} ms"
    return text + (f", {beat['dropped']} log lines dropped" if beat["dropped"] else "")


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
    """The packaged version, or the base version with the commit when run from a checkout."""
    packaged = bundled("VERSION")
    if packaged.is_file():
        return "TES3X " + packaged.read_text(encoding="utf-8").strip()
    try:
        commit = subprocess.run(["git", "-C", str(checkout()), "describe", "--always",
                                 "--dirty"], capture_output=True, text=True,
                                timeout=5).stdout.strip()
    except OSError:
        commit = ""
    return f"TES3X {VERSION}" + (f" ({commit})" if commit else "")


def launch(program, arguments):
    """QProcess arguments for sys.executable: a tes3x command by name, or a script by path."""
    head = ["-m", "tes3x", program] if isinstance(program, str) else [str(program)]
    return [*head, *arguments]


def play_icon(colour="#2ea043"):
    """A solid triangle that stays visible on dark and light backgrounds."""
    result = QPixmap(18, 18)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(colour))
    painter.drawPolygon(QPolygonF([QPointF(3.5, 1.5), QPointF(16, 9), QPointF(3.5, 16.5)]))
    painter.end()
    return QIcon(result)


def stop_icon(colour="#d32f2f"):
    result = QPixmap(18, 18)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(colour))
    painter.drawRoundedRect(3, 3, 12, 12, 1.5, 1.5)
    painter.end()
    return QIcon(result)


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


CATEGORY_ACCENTS = {"core": "#3d85d6", "correctness": "#d9534f", "compat": "#8a63d2",
                    "performance": "#2ea043", "qol": "#2aa198", "balance": "#c9822b",
                    "instrumentation": "#7a8794", "infrastructure": "#d9a21b"}


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


def gear_icon(colour):
    """A small cog for the menus that manage profiles and targets."""
    scale = 2
    pixmap = QPixmap(16 * scale, 16 * scale)
    pixmap.setDevicePixelRatio(scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    tooth = QPainterPath()
    tooth.addRect(-1.6, -7.5, 3.2, 3.5)
    gear = QPainterPath()
    gear.addEllipse(-5.2, -5.2, 10.4, 10.4)
    for step in range(8):
        gear = gear.united(QTransform().rotate(step * 45).map(tooth))
    hole = QPainterPath()
    hole.addEllipse(-2.2, -2.2, 4.4, 4.4)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(8, 8)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(colour))
    painter.drawPath(gear.subtracted(hole))
    painter.end()
    return QIcon(pixmap)


class GearButton(QToolButton):
    """A cog that is redrawn in the palette's text colour when the theme changes."""

    def __init__(self):
        super().__init__()
        self.paint_icon()

    def paint_icon(self):
        self.setIcon(gear_icon(self.palette().buttonText().color()))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self.paint_icon()


def menu_button(tip, actions):
    """A cog button that opens a menu of (label, handler) actions."""
    button = GearButton()
    button.setToolTip(tip)
    button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    button.setStyleSheet("QToolButton::menu-indicator { image: none; }")
    menu = QMenu(button)
    for label, handler in actions:
        menu.addAction(label, handler)
    button.setMenu(menu)
    return button


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
    if str(resource()) not in sys.path:
        sys.path.insert(0, str(resource()))
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
    return local_config().resolve()


class LocalSettingsDialog(QDialog):
    """Edit the machine-local TOML without discarding comments or private xemu settings."""

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

        layout = QVBoxLayout(self)
        content = QHBoxLayout()
        self.categories = QListWidget()
        self.categories.setFixedWidth(155)
        self.pages = QStackedWidget()
        content.addWidget(self.categories)
        content.addWidget(self.pages, 1)
        layout.addLayout(content, 1)
        for label, page in (("Paths", self.path_group(paths)),
                            ("Advanced", self.advanced_group(paths)),
                            ("Add-ons", self.addons_group(plain.get("addons", {})))):
            self.add_page(label, page)
        self.categories.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.categories.setCurrentRow(0)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

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

    def browse_row(self, key, value, files=False, default=None):
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
        if default is not None:
            field.setPlaceholderText(default)
            reset = QPushButton("Use default")
            reset.clicked.connect(field.clear)
            reset.setEnabled(bool(field.text()))
            field.textChanged.connect(lambda text: reset.setEnabled(bool(text)))
            row_layout.addWidget(reset)
            container = QWidget()
            layout = QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(row)
            hint = QLabel("Default: " + default)
            hint.setWordWrap(True)
            hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(hint)
            return container
        return row

    def path_group(self, values):
        group, layout = self.page("Paths", "Folders and tools shared by every profile.")
        form = self.form_section(layout, "Your files")
        for key, label in (("vanilla_root", "Clean game root"),
                           ("mod_library", "Mod library")):
            form.addRow(label, self.browse_row("paths." + key, values.get(key, "")))
        form = self.form_section(layout, "TES3X folders and tools")
        for key, label, folder in (("profiles", "Profiles", "profiles"),
                                   ("build_root", "Build output", "build")):
            form.addRow(label, self.browse_row("paths." + key, values.get(key, ""),
                                              default=str(self.path.parent / folder)))
        llvm_bin = bundled("externals", "llvm", "bin")
        suffix = ".exe" if os.name == "nt" else ""
        llvm_default = (f"Bundled LLVM: {llvm_bin}"
                        if all((llvm_bin / (name + suffix)).is_file()
                               for name in ("clang", "lld-link"))
                        else "Automatically find LLVM on this PC")
        form.addRow("LLVM tools (optional)",
                    self.browse_row("paths.llvm", values.get("llvm", ""), default=llvm_default))
        layout.addStretch()
        return group

    def advanced_group(self, values):
        group, layout = self.page("Advanced", "Optional overrides and file handling.")
        form = self.form_section(layout, "External tools")
        form.addRow("mlox rules (optional)",
                    self.browse_row("paths.mlox_rules", values.get("mlox_rules", ""), files=True,
                                    default="Downloaded automatically when first needed"))
        form.addRow("TES3Merge (optional)",
                    self.browse_row("paths.tes3merge", values.get("tes3merge", ""), files=True))
        form = self.form_section(layout, "File handling")
        hardlink = QCheckBox("Hardlink unchanged retail files")
        hardlink.setChecked(values.get("hardlink_retail", False))
        self.fields["paths.hardlink_retail"] = hardlink
        form.addRow("", hardlink)
        layout.addStretch()
        return group

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

    def save_settings(self):
        values = {name: (field.isChecked() if isinstance(field, QCheckBox)
                         else field.value() if isinstance(field, QSpinBox)
                         else field.text().strip())
                  for name, field in self.fields.items()}
        self.update_table("paths", {name.split(".", 1)[1]: value
                                    for name, value in values.items()
                                    if name.startswith("paths.")})
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
                      f'<a href="{docs_url("ini-keys.md")}">INI keys</a> explains them.')
        note.setWordWrap(True)
        note.setOpenExternalLinks(True)
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
        for label, value in (("Delta archive", "delta-bsa"),
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
        self.tes3merge = QCheckBox("Ship a TES3Merge conflict patch (needs TES3Merge in Paths)")
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
        form.addRow("", self.tes3merge)
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
        for widget in (self.dashboard, self.archive_only, self.convert_all, self.clear_cache,
                       self.tes3merge):
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
        self.tes3merge.setChecked(rules.get("tes3merge", False))
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
        put("rules", "tes3merge", self.tes3merge.isChecked(), False)
        put("rules", "keep_assets", self.lines(self.keep_assets), [])
        put("rules", "exclude", self.lines(self.exclude) or None, None)
        put("preferences", "invert_look", self.invert_look.currentData(), None)

__all__ = [
    'ARCHIVES',
    'AgentListener',
    'BASE_MASTERS',
    'Bsa',
    'BuildSettings',
    'CATALOG_NAME',
    'CATEGORY_ACCENTS',
    'CHANNEL_BADGES',
    'CHECK_MARKER',
    'COMPAT',
    'COMPAT_LABELS',
    'CatalogError',
    'ComponentsDialog',
    'DEFAULT_EXCLUDE',
    'DEPLOYS_MARKER',
    'DEPLOY_CONFLICT',
    'DEPLOY_NO_SPACE',
    'DragList',
    'EXPANSION_PLACEHOLDERS',
    'EXTRA',
    'Fetch',
    'FilesFilter',
    'FilesModel',
    'GearButton',
    'INTRO_MOVIES',
    'IniPanel',
    'InstallDialog',
    'LOSES',
    'LibraryError',
    'LocalSettingsDialog',
    'MEMORY_LOG_SECONDS',
    'Mod',
    'OP_REBOOT',
    'PATCH_CATALOG',
    'PATCH_CATEGORIES',
    'PIPELINE_MARKER',
    'PLUGIN_EXT',
    'PROFILE_NAME',
    'Path',
    'PipelineError',
    'PlaySession',
    'QAbstractItemView',
    'QAbstractTableModel',
    'QAction',
    'QActionGroup',
    'QApplication',
    'QCheckBox',
    'QColor',
    'QComboBox',
    'QDesktopServices',
    'QDialog',
    'QDialogButtonBox',
    'QEvent',
    'QFile',
    'QFileDialog',
    'QFormLayout',
    'QHBoxLayout',
    'QHeaderView',
    'QIcon',
    'QInputDialog',
    'QKeySequence',
    'QLabel',
    'QLineEdit',
    'QListWidget',
    'QListWidgetItem',
    'QMainWindow',
    'QMenu',
    'QMessageBox',
    'QModelIndex',
    'QPainter',
    'QPainterPath',
    'QPen',
    'QPixmap',
    'QPointF',
    'QPolygonF',
    'QProcess',
    'QProcessEnvironment',
    'QPushButton',
    'QScrollArea',
    'QSettings',
    'QSortFilterProxyModel',
    'QSpinBox',
    'QSplitter',
    'QStackedWidget',
    'QStatusBar',
    'QStyle',
    'QTabBar',
    'QTabWidget',
    'QTableView',
    'QTextBrowser',
    'QTextCursor',
    'QTextEdit',
    'QTimer',
    'QToolButton',
    'QTransform',
    'QTreeWidget',
    'QTreeWidgetItem',
    'QUrl',
    'QVBoxLayout',
    'QWidget',
    'Qt',
    'RETAIL_PLUGINS',
    'ROLE',
    'ROW_FLAGS',
    'SKIP_MOVIE',
    'SOURCES',
    'ServerPage',
    'Signal',
    'Spinner',
    'StatusBar',
    'TEMPLATE',
    'TargetsPage',
    'VERSION',
    'WARNING',
    'WINS',
    'XEMU_STARTED',
    'addon_registry',
    'append_mods',
    'argparse',
    'bundled',
    'catalog_needs',
    'checkout',
    'collect',
    'collections',
    'console_request',
    'convert_profile',
    'dashboard_agent_state',
    'datetime',
    'default_config_path',
    'default_rules',
    'defaultdict',
    'dependency_order',
    'discover_library',
    'docs_url',
    'dot_icon',
    'drop_empty',
    'enabled_addons',
    'extract_archive',
    'fetch_destination',
    'fetch_rules',
    'fnmatch',
    'folder_name',
    'free_id',
    'ftp_error',
    'gear_icon',
    'guess_release',
    'hashlib',
    'heartbeat_text',
    'html',
    'icon_button',
    'index_library',
    'ini_text',
    'ini_value',
    'install_files',
    'install_layout',
    'is_manager',
    'json',
    'key_fingerprint',
    'launch',
    'load_catalog',
    'load_library',
    'load_or_create_key',
    'local_config',
    'match_catalog',
    'menu_button',
    'mlox_notes',
    'new_table',
    'nexus',
    'nexus_id',
    'os',
    'parse_drives',
    'patch_spec',
    'play_icon',
    'plugin_header',
    'plugin_masters',
    're',
    'read_ini',
    'records',
    'resolve_patch_plan',
    'resolve_selection',
    'resolve_xemu',
    'resource',
    'rules_file',
    'running_xemu',
    'saves_tool',
    'sha256_file',
    'shutil',
    'signal',
    'sort_files',
    'stop_icon',
    'struct',
    'subprocess',
    'subrecords',
    'sys',
    'target_capabilities',
    'target_runtime_label',
    'tempfile',
    'tes3x',
    'tes3x_savepool',
    'tes3x_targets',
    'texture_dims',
    'theme_icon',
    'threading',
    'time',
    'tinted_icon',
    'tomlkit',
    'tomllib',
    'trash',
    'uuid',
    'validate_local_config',
    'validate_profile',
    'version_label',
]
