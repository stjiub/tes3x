#!/usr/bin/env python3
"""TES3X GUI. It edits the same TOML the command-line tools read."""

import argparse
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
import struct
import subprocess
import sys
import tempfile
import threading
import tomllib
import uuid

try:
    import tomlkit
    from PySide6.QtCore import (QAbstractTableModel, QFile, QModelIndex, QProcess,
                                QProcessEnvironment, QSettings, QSortFilterProxyModel, QTimer, Qt,
                                QUrl, Signal)
    from PySide6.QtGui import QAction, QActionGroup, QColor, QDesktopServices, QIcon, QTextCursor
    from PySide6.QtWidgets import (
        QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
        QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
        QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton,
        QScrollArea, QSpinBox, QSplitter, QStackedWidget, QStatusBar, QStyle, QTableView, QTabWidget,
        QTextBrowser, QTextEdit, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
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
from tes3x_patches import CATEGORIES as PATCH_CATEGORIES, PATCHES as PATCH_CATALOG, SOURCES
from tes3x_plugins import (BASE_MASTERS, collect, dependency_order, fetch_rules, sort_files,
                           warnings as mlox_notes)
from tes3x_pipeline import (MARKER as PIPELINE_MARKER, PipelineError, resolve_patch_plan,
                            validate_local_config, validate_profile)
from tes3x_records import records, subrecords
import tes3x_nexus as nexus


ROOT = Path(__file__).resolve().parents[1]
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
# Where Play runs a build: menu label, button suffix and tes3x_xemu.py options.
PLAY_TARGETS = {"xemu-64": ("xemu (64 MB)", "", []),
                "xemu-128": ("xemu (128 MB)", " 128 MB", ["--ram", "128", "--bios", "128mb"])}


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


def default_config_path():
    local = Path.cwd() / "tes3x.local.toml"
    return (local if local.is_file() else ROOT / "tes3x.local.toml").resolve()


class LocalSettingsDialog(QDialog):
    """Edit the machine-local TOML without discarding comments or private xemu settings."""

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = Path(path).resolve()
        self.setWindowTitle("TES3X settings")
        self.resize(720, 500)
        try:
            text = self.path.read_text(encoding="utf-8") if self.path.is_file() else ""
            self.document = tomlkit.parse(text)
        except (OSError, tomlkit.exceptions.ParseError) as exc:
            raise PipelineError(str(exc)) from exc

        plain = tomllib.loads(tomlkit.dumps(self.document))
        paths = plain.get("paths", {})
        deploy = plain.get("deploy", {})
        xemu = plain.get("xemu", {})
        self.fields = {}

        layout = QVBoxLayout(self)
        layout.addWidget(self.path_group(paths))
        layout.addWidget(self.deploy_group(deploy))
        layout.addWidget(self.xemu_group(xemu))
        layout.addWidget(self.addons_group(plain.get("addons", {})))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

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
        group = QGroupBox("Project paths")
        form = QFormLayout(group)
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
        return group

    def deploy_group(self, values):
        group = QGroupBox("Xbox FTP and deployment")
        form = QFormLayout(group)
        for key, label, default in (("host", "Host", ""), ("user", "User", "xbox"),
                                    ("password", "Password", "xbox"),
                                    ("remote_root", "Game destination", "")):
            field = self.line(values.get(key, default), password=key == "password")
            self.fields["deploy." + key] = field
            form.addRow(label, field)
        port = QSpinBox()
        port.setRange(1, 65535)
        port.setValue(values.get("port", 21))
        self.fields["deploy.port"] = port
        form.insertRow(1, "Port", port)
        return group

    def xemu_group(self, values):
        group = QGroupBox("xemu (for test runs)")
        form = QFormLayout(group)
        values = dict(values)
        values.setdefault("bios_128mb", values.get("cerbios", ""))
        for key, label in (("exe", "Executable"), ("bootrom", "MCPX boot ROM"),
                           ("bios", "BIOS"), ("bios_128mb", "BIOS for 128 MB runs"),
                           ("eeprom", "EEPROM"), ("hdd", "Clean HDD image"),
                           ("extract_xiso", "extract-xiso")):
            form.addRow(label, self.browse_row("xemu." + key, values.get(key, ""), files=True))
        return group

    def addons_group(self, values):
        group = QGroupBox("Add-ons")
        form = QVBoxLayout(group)
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

    def save_settings(self):
        values = {name: (field.isChecked() if isinstance(field, QCheckBox)
                         else field.value() if isinstance(field, QSpinBox)
                         else field.text().strip())
                  for name, field in self.fields.items()}
        xemu = self.document.get("xemu")
        if xemu is not None and "cerbios" in xemu and values.get("xemu.bios_128mb"):
            del xemu["cerbios"]
        for section in ("paths", "deploy", "xemu"):
            self.update_table(section, {name.split(".", 1)[1]: value
                                        for name, value in values.items()
                                        if name.startswith(section + ".")})
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
        if text is not None:
            self.text = text.casefold()
        if conflicts_only is not None:
            self.conflicts_only = conflicts_only
        self.invalidateFilter()

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
        self.remote_root = QLineEdit()
        self.remote_root.setPlaceholderText("From Settings")
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
        identity = QGroupBox("Build")
        form = QFormLayout(identity)
        form.addRow("Dashboard title", self.title)
        form.addRow("Xbox game folder", self.remote_root)
        form.addRow("Mod library", library_widget)
        form.addRow("", self.dashboard)

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
        package = QGroupBox("Packaging")
        form = QFormLayout(package)
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
        rules = QGroupBox("Rules")
        form = QFormLayout(rules)
        form.addRow("Largest texture", self.max_texture_size)
        form.addRow("", self.convert_all)
        form.addRow("Longest file name", self.max_filename)
        form.addRow("", self.clear_cache)
        form.addRow("Always keep", self.keep_assets)
        form.addRow("Leave out", self.exclude)

        self.invert_look = QComboBox()
        for label, value in self.PREFERENCE_CHOICES:
            self.invert_look.addItem(label, value)
        preferences = QGroupBox("Player preferences")
        form = QFormLayout(preferences)
        form.addRow("Look up/down", self.invert_look)

        left = QVBoxLayout()
        left.addWidget(identity)
        left.addWidget(package)
        left.addStretch()
        right = QVBoxLayout()
        right.addWidget(rules)
        right.addWidget(preferences)
        right.addStretch()
        layout = QHBoxLayout(self)
        layout.addLayout(left, 1)
        layout.addLayout(right, 1)

        for widget in (self.title, self.remote_root, self.archive_name):
            widget.textChanged.connect(self.changed)
        for widget in (self.dashboard, self.archive_only, self.convert_all, self.clear_cache):
            widget.toggled.connect(self.changed)
        for widget in (self.mode, self.drive_letter, self.max_texture_size, self.invert_look):
            widget.currentIndexChanged.connect(self.changed)
        for widget in (self.loose_assets, self.keep_assets, self.exclude):
            widget.textChanged.connect(self.changed)
        self.max_filename.valueChanged.connect(self.changed)
        self.library_path.textChanged.connect(self.changed)

    def changed(self, *_args):
        if not self.loading:
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
        self.remote_root.setText(identity.get("remote_root", ""))
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
            return
        if table is None:
            table = tomlkit.table()
            document[section] = table
        if table.get(key) != value:
            table[key] = value

    def apply(self, document):
        put = lambda *args: self.put(document, *args)
        put("profile", "title", self.title.text().strip() or None, None)
        put("profile", "remote_root", self.remote_root.text().strip() or None, None)
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

    def __init__(self, profile=None, config=None, settings=None):
        super().__init__()
        self.setWindowTitle("TES3X")
        self.resize(1280, 800)
        self.profile_path = None
        self.document = None
        self.library_root = None
        self.catalog = {}
        self.library_indexed = False
        self.process = None
        self.ftp_probe = None
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
        self.forget_analysis()
        try:
            self.compat = load_catalog()
        except (OSError, ValueError, CatalogError):
            self.compat = {}
        self.config_path = Path(config).resolve() if config else None
        self.settings = settings
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
        for label, handler in (("New…", self.new_profile), ("Duplicate…", self.duplicate_profile),
                               ("Rename…", self.rename_profile), ("Delete", self.delete_profile)):
            button = QPushButton(label)
            button.clicked.connect(handler)
            profile_bar.addWidget(button)
        profile_bar.addStretch()
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
        self.tabs.addTab(self.create_health_panel(), "Health")
        self.tabs.addTab(self.create_resources_panel(), "Resources")
        self.build = BuildSettings()
        self.build.on_change = self.build_changed
        self.build.on_library = self.library_changed
        build_scroll = QScrollArea()
        build_scroll.setWidgetResizable(True)
        build_scroll.setWidget(self.build)
        self.tabs.addTab(build_scroll, "Build")
        self.tabs.currentChanged.connect(self.show_context_info)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addLayout(profile_bar)
        self.context_info = QTextBrowser()
        self.context_info.setPlaceholderText("Select an item to see its details")
        self.details_stack = QStackedWidget()
        self.details_stack.addWidget(self.mod_details)
        self.details_stack.addWidget(self.context_info)
        self.content_split = QSplitter(Qt.Orientation.Horizontal)
        self.content_split.addWidget(self.tabs)
        self.content_split.addWidget(self.details_stack)
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
        self.setStatusBar(QStatusBar())
        self.counts = QLabel()
        self.statusBar().addPermanentWidget(self.counts)
        self.ftp_status = QPushButton("Xbox: not checked")
        self.ftp_status.setFlat(True)
        self.ftp_status.setToolTip("Click to check the configured Xbox FTP connection")
        self.ftp_status.clicked.connect(self.refresh_ftp_status)
        self.statusBar().addPermanentWidget(self.ftp_status)
        self.ftp_timer = QTimer(self)
        self.ftp_timer.setInterval(60_000)
        self.ftp_timer.timeout.connect(self.refresh_ftp_status)

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
        self.action_reset_play = QAction("Reset xemu saves…", self)
        self.action_reset_play.triggered.connect(self.reset_play_disk)
        actions_menu.addActions([self.action_check, self.action_build, self.action_play,
                                 self.action_reset_play, self.action_smoke])
        actions_menu.addSeparator()
        actions_menu.addActions([self.action_deploy, self.action_fetch, self.action_refresh_ftp])
        actions_menu.addSeparator()
        actions_menu.addAction(self.discard_after_deploy)
        for action, theme, fallback in (
                (self.action_check, None, QStyle.StandardPixmap.SP_DialogApplyButton),
                (self.action_build, QIcon.ThemeIcon.ViewRefresh,
                 QStyle.StandardPixmap.SP_BrowserReload),
                (self.action_play, QIcon.ThemeIcon.MediaPlaybackStart,
                 QStyle.StandardPixmap.SP_MediaPlay),
                (self.action_deploy, QIcon.ThemeIcon.DocumentSend,
                 QStyle.StandardPixmap.SP_ArrowUp)):
            standard = self.style().standardIcon(fallback)
            action.setIcon(QIcon.fromTheme(theme, standard) if theme else standard)
            button = QToolButton()
            button.setDefaultAction(action)
            button.setText(action.text().replace("&", "").replace("…", "").split()[0])
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setToolTip(action.text().replace("&", "") + (
                f" ({action.shortcut().toString()})" if not action.shortcut().isEmpty() else ""))
            self.profile_bar.addWidget(button)
            if action is self.action_play:
                self.play_button = button
                self.play_menu = QMenu(self)
                self.play_menu.setToolTipsVisible(True)
                self.play_targets = {}
                button.setMenu(self.play_menu)
                button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
                self.build_state = QLabel()
                self.profile_bar.addWidget(self.build_state)
        self.command_actions = (self.action_check, self.action_build, self.action_play,
                                self.action_smoke, self.action_deploy, self.action_fetch)
        self.after_command = None
        self.play_target = (self.settings.value("play_target", "xemu-64") if self.settings
                            else "xemu-64")
        self.refresh_play_menu()
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
        elif tab == "Health":
            items = self.health_tree.selectedItems()
            if len(items) != 1:
                self.context_info.setPlainText(
                    "Profile Health checks the current selection before a build.")
                return
            severity, subject, problem = items[0].data(0, ROLE)
            self.context_info.setPlainText(f"{severity}: {subject}\n\n{problem}")
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
            self.context_info.setPlainText(
                "Build-wide profile settings. Changes here affect packaging, deployment and "
                "player preferences.")

    def create_mods_tab(self):
        install = QPushButton("Install mod…")
        install.setToolTip("Install a .zip, .7z or .rar archive, or a plugin, into the mod "
                           "library. You can also drop files on the list.")
        install.clicked.connect(self.choose_install)
        self.mod_search = QLineEdit()
        self.mod_search.setPlaceholderText("Filter mods…")
        self.mod_search.textChanged.connect(self.filter_mods)
        top = QHBoxLayout()
        top.addWidget(install)
        top.addWidget(self.mod_search, 1)

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

    def create_health_panel(self):
        self.health_summary = QLabel("Waiting for profile analysis…")
        self.health_summary.setWordWrap(True)
        self.health_tree = QTreeWidget()
        self.health_tree.setHeaderLabels(["Severity", "Item", "Problem"])
        self.health_tree.setRootIsDecorated(False)
        self.health_tree.setAlternatingRowColors(True)
        self.health_tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.health_tree.itemSelectionChanged.connect(self.show_context_info)
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.health_summary)
        layout.addWidget(self.health_tree, 1)
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
        self.refresh_health(files)
        self.refresh_resources()
        self.show_mod_info()

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

    def refresh_health(self, files=None):
        """Summarise problems that can be found without running a build."""
        if not hasattr(self, "health_tree") or not hasattr(self, "build"):
            return
        issues = []

        def add(severity, subject, problem):
            issues.append((severity, subject, problem))

        active_ids = {item.data(0, ROLE).get("id") for item, _mod in self.analysis["active"]}
        for item, _mod in self.analysis["active"]:
            entry = item.data(0, ROLE)
            name, _version, release, problem = self.describe(entry)
            if problem:
                add("Error", name, problem)
                continue
            verdict = self.compat_verdict(item)
            if verdict is None or verdict["status"] == "untested":
                add("Notice", name, "Xbox compatibility is unknown.")
            elif verdict["status"] == "passes-automated":
                add("Notice", name, "Only an automated test has passed; compatibility is unknown.")
            elif verdict["status"] in ("broken", "not-possible"):
                add("Error", name, f"Xbox compatibility: {COMPAT_LABELS[verdict['status']]}")
            if verdict:
                off = [patch for patch in verdict.get("patches", [])
                       if patch not in self.applied_patches]
                if off:
                    add("Error", name, "Required patches are off: " + ", ".join(off))
                requirements = []
                if verdict.get("ram") == 128:
                    requirements.append("128 MB RAM")
                if verdict.get("bios"):
                    requirements.append(verdict["bios"].title() + " BIOS")
                requirements += verdict.get("requirements", [])
                if requirements:
                    add("Notice", name, "Requires " + ", ".join(requirements))
            missing = [mod_id for mod_id in (release or {}).get("dependencies", [])
                       if mod_id not in active_ids]
            if missing:
                add("Error", name, "Missing mod dependencies: " + ", ".join(missing))

        for item in self.plugin_list.rows():
            if (item.data(0, ROLE) and item.checkState(0) == Qt.CheckState.Checked
                    and item.toolTip(0)):
                add("Error", item.text(0), item.toolTip(0))
        plugin_count = sum(1 for item in self.plugin_list.rows()
                           if item.checkState(0) == Qt.CheckState.Checked)
        if plugin_count > 256:
            add("Error", "Plugins", f"{plugin_count} plugins exceed the 256 load-index limit.")

        for item in self.archives.findItems("can't unpack", Qt.MatchFlag.MatchExactly, 2):
            add("Error", item.text(0), item.toolTip(2) or "Archive cannot be unpacked.")

        current_files = files if files is not None else self.files_model.entries
        limit = self.build.max_filename.value()
        long_names = [(path, owner) for path, owner, _others in current_files
                      if len(Path(path).name) > limit]
        if long_names:
            examples = ", ".join(path for path, _owner in long_names[:3])
            if len(long_names) > 3:
                examples += f" and {len(long_names) - 3} more"
            add("Error", "Data Files",
                f"{len(long_names)} filenames exceed the configured {limit}-character limit: "
                + examples)

        rank = {"Error": 0, "Notice": 1}
        issues.sort(key=lambda row: (rank[row[0]], row[1].casefold(), row[2].casefold()))
        self.health_tree.clear()
        for issue in issues:
            item = QTreeWidgetItem(list(issue))
            item.setData(0, ROLE, issue)
            if issue[0] == "Error":
                item.setForeground(0, WARNING)
            self.health_tree.addTopLevelItem(item)
        errors = sum(severity == "Error" for severity, _subject, _problem in issues)
        notices = len(issues) - errors
        if not issues:
            self.health_summary.setText("No problems found in the current profile.")
        else:
            parts = [f"{errors} error{'' if errors == 1 else 's'}" if errors else "",
                     f"{notices} notice{'' if notices == 1 else 's'}" if notices else ""]
            self.health_summary.setText(" · ".join(part for part in parts if part))
        self.health_tree.resizeColumnToContents(0)
        self.health_tree.resizeColumnToContents(1)

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

    def create_patches_tab(self):
        self.patch_preset = QComboBox()
        self.patch_preset.addItems(["minimal", "standard", "development"])
        self.patch_preset.setToolTip("minimal: no optional patches; standard: tested fixes and "
                                     "the console; development: everything, untested included")
        self.patch_preset.currentTextChanged.connect(self.refresh_patch_states)
        self.patch_search = QLineEdit()
        self.patch_search.setPlaceholderText("Filter patches…")
        self.patch_search.textChanged.connect(self.filter_patches)
        top = QHBoxLayout()
        top.addWidget(QLabel("Start from"))
        top.addWidget(self.patch_preset)
        top.addWidget(self.patch_search, 1)

        self.patch_tree = QTreeWidget()
        self.patch_tree.setHeaderLabels(["Patch", "Channel", "Why"])
        self.patch_tree.setAlternatingRowColors(True)
        self.patch_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.patch_tree.header().setStretchLastSection(False)
        self.patch_tree.itemChanged.connect(self.patch_item_changed)
        self.patch_tree.currentItemChanged.connect(self.show_context_info)
        self.patch_items = {}
        self.patch_groups = {}
        for category in PATCH_CATEGORIES:
            entries = [entry for entry in PATCH_CATALOG
                       if entry["category"] == category and entry["selection"] != "option"]
            if not entries:
                continue
            group = QTreeWidgetItem([category])
            group.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            group.setData(0, ROLE, None)
            self.patch_tree.addTopLevelItem(group)
            self.patch_groups[category] = group
            for entry in entries:
                item = QTreeWidgetItem([entry["name"], entry["channel"], ""])
                item.setData(0, ROLE, entry["name"])
                item.setToolTip(0, entry["summary"])
                flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                if entry["selection"] == "preset":
                    flags |= Qt.ItemFlag.ItemIsUserCheckable
                else:
                    item.setForeground(0, self.palette().placeholderText())
                item.setFlags(flags)
                group.addChild(item)
                self.patch_items[entry["name"]] = item
            group.setExpanded(True)
        self.patch_tree.resizeColumnToContents(1)
        self.patch_tree.resizeColumnToContents(2)
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
        self.patch_preset.setCurrentText(config.get("preset", "standard"))
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
        self.patch_loading = True
        for name, item in self.patch_items.items():
            entry = by_name[name]
            on = name in applied
            item.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
            mode = self.patch_modes.get(name)
            why = ""
            if entry["selection"] == "always":
                reason, why = "Required", "Every build needs this patch"
            elif entry["selection"] == "packaging":
                reason = "Required" if on else ""
                why = ("The delta-bsa package mode needs this patch" if on
                       else "Only delta-bsa builds need this patch")
            elif mode == "enable":
                reason = "added"
            elif mode == "disable":
                reason = "removed"
            elif on and entry["category"] in self.patch_categories:
                reason = "category"
            else:
                reason = f"{preset} preset" if on else ""
            item.setText(2, reason)
            item.setToolTip(2, why)
            item.setForeground(2, WARNING if reason == "Required"
                               else self.palette().text().color())
        for group in self.patch_groups.values():
            states = {group.child(i).checkState(0) for i in range(group.childCount())
                      if group.child(i).flags() & Qt.ItemFlag.ItemIsUserCheckable}
            group.setCheckState(0, Qt.CheckState.Checked if states == {Qt.CheckState.Checked}
                                else Qt.CheckState.Unchecked if Qt.CheckState.Checked not in states
                                else Qt.CheckState.PartiallyChecked)
        self.patch_loading = False
        self.patch_tree.resizeColumnToContents(2)
        self.sync_patch_ini(applied)
        if hasattr(self, "mod_list"):
            loading, self.mods_loading = self.mods_loading, True
            for row in self.mod_rows():
                self.update_compat(row)
            self.mods_loading = loading
        self.refresh_health()

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
                     if item.child(i).flags() & Qt.ItemFlag.ItemIsUserCheckable]
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

    def filter_patches(self, text):
        query = text.casefold()
        by_name = {entry["name"]: entry for entry in PATCH_CATALOG}
        for group in self.patch_groups.values():
            visible = 0
            for i in range(group.childCount()):
                child = group.child(i)
                entry = by_name[child.data(0, ROLE)]
                hidden = bool(query) and query not in (entry["name"] + " "
                                                       + entry["summary"]).casefold()
                child.setHidden(hidden)
                visible += not hidden
            group.setHidden(visible == 0)

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
        lines = [entry["name"], "", entry["summary"], "",
                 f"Category: {entry['category']}", f"Channel: {entry['channel']}",
                 f"Origin: {origin_text}"]
        if entry["selection"] != "preset":
            lines.append("Chosen automatically from the build settings")
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
        self.setWindowTitle("TES3X")
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
        self.refresh_ftp_status()
        self.refresh_play_menu()
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
        self.document = document
        self.profile_plain = plain
        self.library_root = library_root
        self.catalog = catalog
        self.library_indexed = indexed
        self.scans.clear()
        self.build.load(plain)
        self.plugins_loading = True
        self.mlox_at_build.setChecked(plain.get("rules", {}).get("plugin_order") == "mlox")
        self.plugins_loading = False
        self.plugin_order = plain.get("plugins", {}).get("order") or None
        self.populate_mods(plain.get("mods", []))
        self.populate_patches(plain.get("patches", {}))
        vanilla = self.local_path("vanilla_root")
        self.ini.load(plain.get("ini", {}), vanilla / "Morrowind.ini" if vanilla else None)
        self.ini.set_patches(self.applied_patches)
        try:
            self.saved_text = self.profile_text()
        except (PipelineError, LibraryError, tomlkit.exceptions.ParseError):
            self.saved_text = None
        if self.settings is not None:
            self.settings.setValue("last_profile", str(self.profile_path))
        self.refresh_profile_list()
        self.setWindowTitle(f"TES3X — {self.profile_path.stem}")
        message = str(self.profile_path)
        if library_root and not indexed:
            message += " — no library.toml, so mods are added by folder name"
        self.statusBar().showMessage(message)
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
        BuildSettings.put(self.document, "rules", "plugin_order",
                          "mlox" if self.mlox_at_build.isChecked() else "mods", "mods")
        order = None if self.mlox_at_build.isChecked() else self.plugin_order
        plugins = self.document.get("plugins")
        if order:
            if plugins is None:
                plugins = tomlkit.table()
                self.document["plugins"] = plugins
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
            table = tomlkit.table()
            self.document["ini"] = table
        if table is not None:
            for key in [key for key in table if key not in values]:
                del table[key]
            for key, value in values.items():
                if table.get(key) != value:
                    table[key] = value
        patches = self.document.get("patches")
        if patches is None:
            patches = tomlkit.table()
            self.document["patches"] = patches
        for key, value in self.patch_configuration().items():
            # Leave unchanged values alone so their comments and layout survive.
            if isinstance(value, list) and not value and key not in patches:
                continue
            if isinstance(value, list) and set(patches.get(key, [])) == set(value):
                continue
            if patches.get(key) != value:
                patches[key] = value
        text = tomlkit.dumps(self.document)
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
        if not self.save_profile():
            return
        self.start_command(ROOT / "tools" / "tes3x_pipeline.py", [
            str(self.profile_path),
            *(["--config", str(self.local_config_path())]
              if self.local_config_path().is_file() else []),
            *extra,
        ], "Running TES3X pipeline…")

    def build_output(self):
        root = self.local_path("build_root") or self.work_dir() / "build"
        return root / self.profile_plain["profile"]["name"]

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
        state, tip = self.build_status()
        text, colour = {"built": ("Built", QColor(60, 170, 60)),
                        "stale": ("Out of date", QColor(215, 150, 20)),
                        "missing": ("Not built", self.palette().placeholderText().color())}[state]
        self.build_state.setText(f"<span style='color:{colour.name()}'>●</span> {text}")
        self.build_state.setToolTip(tip)

    def refresh_play_menu(self):
        """xemu's targets, then those of the enabled add-ons."""
        self.play_addons = {key: module for module in enabled_addons(self.local_values()).values()
                            for key in module.PLAY}
        self.play_labels = {key: (label, suffix) for key, (label, suffix, _options)
                            in PLAY_TARGETS.items()}
        self.play_labels.update({key: module.PLAY[key] for key, module in self.play_addons.items()})
        self.play_menu.clear()
        group = QActionGroup(self.play_menu)
        self.play_targets = {}
        for key, (label, _suffix) in self.play_labels.items():
            if key == next(iter(self.play_addons), None):
                self.play_menu.addSeparator()
            target = self.play_menu.addAction(label, lambda key=key: self.play(key))
            target.setCheckable(True)
            group.addAction(target)
            self.play_targets[key] = target
        self.update_play_targets()

    def play_available(self):
        """Each Play target, and why it cannot run when it cannot."""
        local = self.local_values()
        xemu = local.get("xemu", {})
        missing = "Set the xemu files in File > Settings" if not xemu.get("exe") else None
        available = {"xemu-64": missing,
                     "xemu-128": missing or (None if xemu.get("bios_128mb") or xemu.get("cerbios")
                                             else "Set a 128 MB BIOS in File > Settings")}
        for key, module in self.play_addons.items():
            available[key] = module.status(local)
        return available

    def update_play_targets(self):
        available = self.play_available()
        if self.play_target not in available or available[self.play_target]:
            self.play_target = "xemu-64"
        for key, target in self.play_targets.items():
            target.setEnabled(not available[key])
            target.setToolTip(available[key] or "")
            target.setChecked(key == self.play_target)
        label, suffix = self.play_labels[self.play_target]
        self.action_play.setText(f"&Play in {label}")
        self.play_button.setText("Play" + suffix)
        self.play_button.setToolTip(f"Play in {label} (F9); the arrow picks where")

    def play(self, target=None):
        reason = self.play_available().get(target or self.play_target, "Not available")
        if reason:
            self.error(reason)
            return
        if target is not None:
            self.play_target = target
            if self.settings is not None:
                self.settings.setValue("play_target", target)
            self.update_play_targets()
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
        isos = sorted(runs.glob(f"play-{self.profile_path.stem}-{self.play_target}-*/game.iso"),
                      key=lambda path: path.stat().st_mtime)
        current = isos[-1] if isos and isos[-1].stat().st_mtime >= built else None
        for iso in isos:
            if iso != current:
                iso.unlink(missing_ok=True)
        return current

    def play_context(self):
        return {"profile": self.profile_path, "config": self.local_config_path(),
                "deploy": self.build_output() / "deploy", "plain": self.profile_plain,
                "local": self.local_values()}

    def run_steps(self, steps, first=True):
        """Run commands one after another, stopping at the first that fails."""
        script, arguments, message = steps[0]
        self.start_command(script, arguments, message, clear=first)
        if len(steps) > 1:
            self.after_command = lambda: self.run_steps(steps[1:], first=False)

    def start_play(self):
        module = self.play_addons.get(self.play_target)
        if module is not None:
            context = self.play_context()
            try:
                steps = module.play_steps(self.play_target, context)
            except ValueError as exc:
                self.error(exc)
                return
            question = module.confirm(self.play_target, context) \
                if hasattr(module, "confirm") else None
            key = f"confirmed/{self.play_target}/{self.profile_path}"
            if question and not (self.settings and self.settings.value(key, False, bool)):
                if QMessageBox.question(self, "TES3X", question) != \
                        QMessageBox.StandardButton.Yes:
                    return
                if self.settings is not None:
                    self.settings.setValue(key, True)
            self.run_steps(steps)
            return
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        iso = self.play_iso()
        source = (["--iso", str(iso)] if iso
                  else ["--deploy", str(self.build_output() / "deploy"), "--keep-iso"])
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("TES3X_CONFIG", str(self.local_config_path()))
        label, _suffix, options = PLAY_TARGETS[self.play_target]
        self.start_command(ROOT / "tools" / "tes3x_xemu.py",
                           [f"play-{self.profile_path.stem}-{self.play_target}-{stamp}", *source,
                            *options, "--disk", str(self.play_disk())],
                           f"Playing in {label}…", environment)

    def play_disk(self):
        """The profile's own xemu disk, which keeps its saves between plays."""
        return self.work_dir() / "build" / "play" / self.profile_path.stem / "hdd.qcow2"

    def reset_play_disk(self):
        if self.process is not None:
            self.error("A TES3X command is already running")
            return
        disk = self.play_disk() if self.profile_path else None
        if disk is None or not disk.is_file():
            QMessageBox.information(self, "TES3X", "This profile has no xemu saves yet.")
            return
        answer = QMessageBox.question(
            self, "Reset xemu saves",
            f"Delete this profile's xemu disk and every save on it?\n\n{disk}")
        if answer == QMessageBox.StandardButton.Yes:
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
        answer = QMessageBox.question(
            self, "Deploy profile",
            "Build this profile and synchronize it to the configured Xbox destination?\n\n"
            "Files absent from the build are removed from that destination. Uploaded files are "
            "verified by size before the command succeeds.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        arguments = ["--deploy", "--verify-deploy", "size"]
        if self.discard_after_deploy.isChecked():
            arguments.append("--discard-build")
        self.run_pipeline(arguments)

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
        for action in self.command_actions:
            action.setEnabled(False)
        process.start()
        self.statusBar().showMessage(message)

    def refresh_ftp_status(self):
        if self.ftp_probe is not None:
            return
        config = self.local_config_path()
        try:
            local = tomllib.loads(config.read_text(encoding="utf-8")) if config.is_file() else {}
        except (OSError, tomllib.TOMLDecodeError) as exc:
            self.ftp_status.setText("Xbox: config error")
            self.ftp_status.setToolTip(str(exc))
            return
        host = local.get("deploy", {}).get("host")
        if not host:
            self.ftp_status.setText("Xbox: not configured")
            self.ftp_status.setToolTip("Set the Xbox host in File > Settings")
            return
        process = QProcess(self)
        process.setWorkingDirectory(str(self.work_dir()))
        process.setProgram(sys.executable)
        process.setArguments([str(ROOT / "tools" / "tes3x_fetch.py"), "E:/", "--list",
                              "--config", str(config)])
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.finished.connect(self.ftp_probe_finished)
        self.ftp_probe = process
        self.ftp_probe_host = host
        self.ftp_status.setText(f"Xbox: checking {host}…")
        process.start()

    def ftp_probe_finished(self, code, _status):
        output = ""
        if self.ftp_probe is not None:
            output = bytes(self.ftp_probe.readAllStandardOutput()).decode(errors="replace").strip()
        host = getattr(self, "ftp_probe_host", "")
        self.ftp_status.setText(f"Xbox: {'connected' if code == 0 else 'offline'} — {host}")
        self.ftp_status.setToolTip(output or f"FTP probe exited {code}")
        self.ftp_probe = None

    def append_process_output(self):
        if self.process is None:
            return
        self.output.moveCursor(QTextCursor.MoveOperation.End)
        self.output.insertPlainText(bytes(self.process.readAllStandardOutput()).decode(errors="replace"))

    def command_finished(self, code, _status):
        self.append_process_output()
        self.statusBar().showMessage(f"TES3X exited {code}", 5000)
        self.process = None
        for action in self.command_actions:
            action.setEnabled(True)
        self.update_build_state()
        follow, self.after_command = self.after_command, None
        if follow and code == 0:
            follow()


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
