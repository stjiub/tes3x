#!/usr/bin/env python3
"""TES3X profile editor. The GUI edits the same TOML consumed by the command-line tools."""

import argparse
import datetime
from pathlib import Path
import re
import sys
import tomllib

try:
    import tomlkit
    from PySide6.QtCore import QProcess, QSettings, QTimer, Qt
    from PySide6.QtGui import QAction, QTextCursor
    from PySide6.QtWidgets import (
        QAbstractItemView, QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
        QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
        QListWidget, QScrollArea, QTableWidget, QTableWidgetItem,
        QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QSpinBox, QSplitter, QStatusBar,
        QTabWidget, QTextEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
    )
except ImportError as exc:
    raise SystemExit(
        "TES3X GUI dependencies are not installed; run "
        "`python -m pip install -r requirements-gui.txt`"
    ) from exc

from tes3x_build import DEFAULT_EXCLUDE, find_data_root
from tes3x_library import (CATALOG_NAME, LibraryError, available_plugins, convert_profile,
                           dependency_order, discover_library, index_library, load_library,
                           resolve_selection)
from tes3x_patches import CATEGORIES as PATCH_CATEGORIES, PATCHES as PATCH_CATALOG, SOURCES
from tes3x_plugins import fetch_rules
from tes3x_pipeline import (PipelineError, resolve_patch_plan, validate_local_config,
                            validate_profile)


ROOT = Path(__file__).resolve().parents[1]
ROLE = Qt.ItemDataRole.UserRole
TEMPLATE = ROOT / "examples" / "profile.toml"
PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def default_config_path():
    local = Path.cwd() / "tes3x.local.toml"
    return (local if local.is_file() else ROOT / "tes3x.local.toml").resolve()


class LocalSettingsDialog(QDialog):
    """Edit the machine-local TOML without discarding comments or private xemu settings."""

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = Path(path).resolve()
        self.setWindowTitle("TES3X local settings")
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


class BuildSettings(QWidget):
    """Every profile setting outside mods and patches, written back to the same TOML keys."""

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
        self.remote_root.setPlaceholderText("From local settings")
        self.library_path = QLineEdit()
        self.library_path.setPlaceholderText("From local settings")
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
        self.plugin_order = QComboBox()
        self.plugin_order.addItem("Masters first, then mod order", "mods")
        self.plugin_order.addItem("Sort with mlox", "mlox")
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
        form.addRow("Plugin order", self.plugin_order)
        form.addRow("Always keep", self.keep_assets)
        form.addRow("Leave out", self.exclude)

        self.invert_look = QComboBox()
        for label, value in self.PREFERENCE_CHOICES:
            self.invert_look.addItem(label, value)
        preferences = QGroupBox("Player preferences")
        form = QFormLayout(preferences)
        form.addRow("Look up/down", self.invert_look)

        self.ini = QTableWidget(0, 2)
        self.ini.setHorizontalHeaderLabels(["Section:Key", "Value"])
        self.ini.horizontalHeader().setStretchLastSection(True)
        self.ini.itemChanged.connect(self.changed)
        add = QPushButton("Add")
        add.clicked.connect(self.add_ini_row)
        remove = QPushButton("Remove")
        remove.clicked.connect(self.remove_ini_row)
        ini_buttons = QHBoxLayout()
        ini_buttons.addWidget(add)
        ini_buttons.addWidget(remove)
        ini_buttons.addStretch()
        ini = QGroupBox("Morrowind.ini settings (docs/ini-keys.md lists the [Xbox] keys)")
        ini_layout = QVBoxLayout(ini)
        ini_layout.addWidget(self.ini)
        ini_layout.addLayout(ini_buttons)

        left = QVBoxLayout()
        left.addWidget(identity)
        left.addWidget(package)
        left.addWidget(preferences)
        left.addStretch()
        right = QVBoxLayout()
        right.addWidget(rules)
        right.addWidget(ini, 1)
        layout = QHBoxLayout(self)
        layout.addLayout(left, 1)
        layout.addLayout(right, 1)

        for widget in (self.title, self.remote_root, self.archive_name):
            widget.textChanged.connect(self.changed)
        for widget in (self.dashboard, self.archive_only, self.convert_all, self.clear_cache):
            widget.toggled.connect(self.changed)
        for widget in (self.mode, self.drive_letter, self.max_texture_size, self.plugin_order,
                       self.invert_look):
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

    def add_ini_row(self, key="", value=""):
        row = self.ini.rowCount()
        self.ini.insertRow(row)
        self.ini.setItem(row, 0, QTableWidgetItem(key))
        self.ini.setItem(row, 1, QTableWidgetItem(value))
        if not key:
            self.ini.setCurrentCell(row, 0)
            self.ini.editItem(self.ini.item(row, 0))

    def remove_ini_row(self):
        row = self.ini.currentRow()
        if row >= 0:
            self.ini.removeRow(row)
            self.changed()

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
        self.select(self.plugin_order, rules.get("plugin_order", "mods"))
        self.keep_assets.setPlainText("\n".join(rules.get("keep_assets", [])))
        self.exclude.setPlainText("\n".join(rules.get("exclude", [])))
        self.select(self.invert_look, plain.get("preferences", {}).get("invert_look"))
        self.ini.setRowCount(0)
        for key, value in plain.get("ini", {}).items():
            self.add_ini_row(key, str(value).lower() if isinstance(value, bool) else str(value))
        self.update_enabled()
        self.loading = False

    def library(self):
        return self.library_path.text().strip()

    def package_values(self):
        values = {"mode": self.mode.currentData()}
        if self.archive_only.isChecked() and self.mode.currentData() != "loose":
            values["archive_only"] = True
        return values

    def preference_values(self):
        value = self.invert_look.currentData()
        return {} if value is None else {"invert_look": value}

    @staticmethod
    def ini_value(text):
        for kind in (int, float):
            try:
                return kind(text)
            except ValueError:
                pass
        return {"true": True, "false": False}.get(text.casefold(), text)

    def ini_values(self):
        values = {}
        for row in range(self.ini.rowCount()):
            key = (self.ini.item(row, 0).text() if self.ini.item(row, 0) else "").strip()
            if key:
                text = self.ini.item(row, 1).text().strip() if self.ini.item(row, 1) else ""
                values[key] = self.ini_value(text)
        return values

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
        put("rules", "plugin_order", self.plugin_order.currentData(), "mods")
        put("rules", "keep_assets", self.lines(self.keep_assets), [])
        put("rules", "exclude", self.lines(self.exclude) or None, None)
        put("preferences", "invert_look", self.invert_look.currentData(), None)
        values = self.ini_values()
        table = document.get("ini")
        if table is None and values:
            table = tomlkit.table()
            document["ini"] = table
        if table is not None:
            for key in [key for key in table if key not in values]:
                del table[key]
            for key, value in values.items():
                if table.get(key) != value:
                    table[key] = value


class ProfileWindow(QMainWindow):
    def __init__(self, profile=None, config=None, settings=None):
        super().__init__()
        self.setWindowTitle("TES3X Profile Manager")
        self.resize(1180, 760)
        self.profile_path = None
        self.document = None
        self.library_root = None
        self.catalog = {}
        self.library_indexed = False
        self.process = None
        self.ftp_probe = None
        self.profile_plain = {}
        self.patch_modes = {}
        self.patch_combos = {}
        self.config_path = Path(config).resolve() if config else None
        self.settings = settings
        self.saved_text = None

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

        self.library = QTreeWidget()
        self.library.setHeaderLabels(["Library", "Version"])
        self.library.setAlternatingRowColors(True)
        self.selected = QListWidget()
        self.selected.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.selected.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.selected.currentRowChanged.connect(self.show_options)
        self.selected.model().rowsMoved.connect(lambda *_: self.renumber())

        add = QPushButton("Add →")
        add.clicked.connect(self.add_selected_release)
        remove = QPushButton("Remove")
        remove.clicked.connect(self.remove_selected)
        up = QPushButton("Move up")
        up.clicked.connect(lambda: self.move_selected(-1))
        down = QPushButton("Move down")
        down.clicked.connect(lambda: self.move_selected(1))
        middle_buttons = QVBoxLayout()
        middle_buttons.addStretch()
        for button in (add, remove, up, down):
            middle_buttons.addWidget(button)
        middle_buttons.addStretch()
        middle = QWidget()
        middle.setLayout(middle_buttons)

        self.options_title = QLabel("Select a profile mod")
        self.components = QListWidget()
        self.components.itemChanged.connect(self.components_changed)
        self.plugins = QListWidget()
        self.plugins.itemChanged.connect(self.plugins_changed)
        options = QWidget()
        options_layout = QVBoxLayout(options)
        options_layout.addWidget(self.options_title)
        options_layout.addWidget(QLabel("Components"))
        options_layout.addWidget(self.components)
        options_layout.addWidget(QLabel("Plugins"))
        options_layout.addWidget(self.plugins)
        self.mod_switches = {}
        for key, label, default in (("enabled", "Enabled", True),
                                    ("optional", "Skip if the mod folder is missing", False),
                                    ("loose", "Ship this mod's files loose", False)):
            box = QCheckBox(label)
            box.setEnabled(False)
            box.toggled.connect(lambda value, key=key, default=default:
                                self.mod_switch_changed(key, value, default))
            self.mod_switches[key] = (box, default)
            options_layout.addWidget(box)

        choices = QSplitter()
        choices.addWidget(self.library)
        choices.addWidget(middle)
        choices.addWidget(self.selected)
        choices.addWidget(options)
        choices.setStretchFactor(0, 3)
        choices.setStretchFactor(2, 3)
        choices.setStretchFactor(3, 3)

        mods_tab = QWidget()
        mods_layout = QVBoxLayout(mods_tab)
        mods_layout.addWidget(choices)

        patches_tab = self.create_patches_tab()

        self.output = QTextEdit()
        self.output.setReadOnly(True)

        self.tabs = QTabWidget()
        self.tabs.addTab(mods_tab, "Mods")
        self.tabs.addTab(patches_tab, "Patches")
        self.build = BuildSettings()
        self.build.on_change = self.build_changed
        self.build.on_library = self.library_changed
        build_scroll = QScrollArea()
        build_scroll.setWidgetResizable(True)
        build_scroll.setWidget(self.build)
        self.tabs.addTab(build_scroll, "Build")

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addLayout(profile_bar)
        self.body_split = QSplitter(Qt.Orientation.Vertical)
        self.body_split.addWidget(self.tabs)
        self.body_split.addWidget(self.output)
        self.body_split.setStretchFactor(0, 4)
        self.body_split.setStretchFactor(1, 1)
        self.body_split.setSizes([640, 140])
        layout.addWidget(self.body_split)
        self.setCentralWidget(body)
        self.setStatusBar(QStatusBar())
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
        self.action_settings = QAction("Local &settings…", self)
        self.action_settings.triggered.connect(self.edit_local_settings)
        self.action_index = QAction("Index new library folders", self)
        self.action_index.triggered.connect(self.write_library_index)
        self.action_convert = QAction("Convert folder names to library ids", self)
        self.action_convert.triggered.connect(self.convert_to_ids)
        self.action_exit = QAction("E&xit", self)
        self.action_exit.triggered.connect(self.close)
        file_menu.addActions([self.action_new, self.action_open, self.action_save])
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
        actions_menu.addActions([self.action_check, self.action_build, self.action_smoke])
        actions_menu.addSeparator()
        actions_menu.addActions([self.action_deploy, self.action_fetch, self.action_refresh_ftp])
        actions_menu.addSeparator()
        actions_menu.addAction(self.discard_after_deploy)
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

    def create_patches_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Preset"))
        self.patch_preset = QComboBox()
        self.patch_preset.addItems(["minimal", "standard", "development"])
        self.patch_preset.currentTextChanged.connect(self.patch_selection_changed)
        preset_row.addWidget(self.patch_preset)
        preset_row.addWidget(QLabel(
            "Explicit choices below override the preset and selected categories."))
        preset_row.addStretch()
        layout.addLayout(preset_row)

        self.patch_categories = QListWidget()
        self.patch_categories.setFlow(QListWidget.Flow.LeftToRight)
        self.patch_categories.setWrapping(True)
        self.patch_categories.setMaximumHeight(62)
        for category in PATCH_CATEGORIES:
            item = QListWidgetItem(category)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.patch_categories.addItem(item)
        self.patch_categories.itemChanged.connect(self.patch_selection_changed)
        layout.addWidget(QLabel("Enable whole categories"))
        layout.addWidget(self.patch_categories)

        self.patch_summary = QTreeWidget()
        self.patch_summary.setHeaderLabels(["Profile patches", "Selected by"])
        self.patch_summary.setMaximumHeight(180)
        layout.addWidget(self.patch_summary)

        filters = QHBoxLayout()
        self.patch_search = QLineEdit()
        self.patch_search.setPlaceholderText("Filter by patch name or description…")
        self.patch_search.textChanged.connect(self.refresh_patch_catalog)
        self.patch_category_filter = QComboBox()
        self.patch_category_filter.addItems(["All categories", *PATCH_CATEGORIES])
        self.patch_category_filter.currentTextChanged.connect(self.refresh_patch_catalog)
        self.patch_channel_filter = QComboBox()
        self.patch_channel_filter.addItems(["All channels", "release", "development"])
        self.patch_channel_filter.currentTextChanged.connect(self.refresh_patch_catalog)
        filters.addWidget(self.patch_search, 1)
        filters.addWidget(self.patch_category_filter)
        filters.addWidget(self.patch_channel_filter)
        layout.addLayout(filters)

        self.patch_catalog = QTreeWidget()
        self.patch_catalog.setHeaderLabels(["Patch", "Channel", "Selected by", "Profile choice"])
        self.patch_catalog.setAlternatingRowColors(True)
        self.patch_catalog.currentItemChanged.connect(self.show_patch_details)
        self.patch_details = QTextEdit()
        self.patch_details.setReadOnly(True)
        self.patch_details.setPlaceholderText("Select a patch to see its catalog information.")
        lower = QSplitter()
        lower.addWidget(self.patch_catalog)
        lower.addWidget(self.patch_details)
        lower.setStretchFactor(0, 3)
        lower.setStretchFactor(1, 2)
        layout.addWidget(lower, 1)
        return tab

    def patch_configuration(self):
        categories = [self.patch_categories.item(index).text()
                      for index in range(self.patch_categories.count())
                      if self.patch_categories.item(index).checkState() == Qt.CheckState.Checked]
        order = [entry["name"] for entry in PATCH_CATALOG]

        def selected(mode):
            names = [name for name, value in self.patch_modes.items() if value == mode]
            return sorted(names, key=lambda name: (order.index(name) if name in order else len(order),
                                                   name))

        return {"preset": self.patch_preset.currentText(), "categories": categories,
                "enable": selected("enable"), "disable": selected("disable")}

    def populate_patches(self, config):
        self.patch_preset.blockSignals(True)
        self.patch_preset.setCurrentText(config.get("preset", "standard"))
        self.patch_preset.blockSignals(False)
        categories = set(config.get("categories", []))
        self.patch_categories.blockSignals(True)
        for index in range(self.patch_categories.count()):
            item = self.patch_categories.item(index)
            item.setCheckState(Qt.CheckState.Checked if item.text() in categories
                               else Qt.CheckState.Unchecked)
        self.patch_categories.blockSignals(False)
        self.patch_modes = {name: "enable" for name in config.get("enable", [])}
        self.patch_modes.update({name: "disable" for name in config.get("disable", [])})
        self.refresh_patch_catalog()
        self.refresh_patch_summary()

    def refresh_patch_catalog(self):
        if not hasattr(self, "patch_catalog"):
            return
        query = self.patch_search.text().casefold()
        category_filter = self.patch_category_filter.currentText()
        channel_filter = self.patch_channel_filter.currentText()
        self.patch_catalog.clear()
        self.patch_combos = {}
        groups = {}
        for entry in PATCH_CATALOG:
            text = (entry["name"] + " " + entry["summary"]).casefold()
            if query and query not in text:
                continue
            if category_filter != "All categories" and entry["category"] != category_filter:
                continue
            if channel_filter != "All channels" and entry["channel"] != channel_filter:
                continue
            parent = groups.get(entry["category"])
            if parent is None:
                parent = QTreeWidgetItem([entry["category"]])
                self.patch_catalog.addTopLevelItem(parent)
                groups[entry["category"]] = parent
            item = QTreeWidgetItem([entry["name"], entry["channel"], entry["selection"], ""])
            item.setData(0, ROLE, entry["name"])
            item.setToolTip(0, entry["summary"])
            parent.addChild(item)
            choice = QComboBox()
            if entry["selection"] == "preset":
                choice.addItems(["Default", "Enable", "Disable"])
                choice.setCurrentText(self.patch_modes.get(entry["name"], "default").title())
                choice.currentTextChanged.connect(
                    lambda value, name=entry["name"]: self.patch_mode_changed(name, value))
                self.patch_combos[entry["name"]] = choice
            else:
                choice.addItem("Automatic")
                choice.setEnabled(False)
            self.patch_catalog.setItemWidget(item, 3, choice)
        for parent in groups.values():
            parent.setExpanded(True)
        self.patch_catalog.resizeColumnToContents(0)

    def patch_mode_changed(self, name, value):
        mode = value.casefold()
        if mode == "default":
            self.patch_modes.pop(name, None)
        else:
            self.patch_modes[name] = mode
        self.refresh_patch_summary()

    def patch_selection_changed(self, *_args):
        self.refresh_patch_summary()

    def refresh_patch_summary(self):
        if not hasattr(self, "patch_summary"):
            return
        self.patch_summary.clear()
        config = self.patch_configuration()
        profile = {
            "patches": config,
            "mods": self.profile_mods() if hasattr(self, "selected") else [],
            "package": self.build.package_values() if hasattr(self, "build") else {},
            "preferences": self.build.preference_values() if hasattr(self, "build") else {},
        }
        try:
            plan = resolve_patch_plan(profile)
            effective = set(plan["applied"])
        except PipelineError:
            effective = set(config["enable"])
        effective.update(entry["name"] for entry in PATCH_CATALOG
                         if entry["selection"] == "always")
        by_name = {entry["name"]: entry for entry in PATCH_CATALOG}
        root = QTreeWidgetItem([f"Applied by this profile ({len(effective)})"])
        self.patch_summary.addTopLevelItem(root)
        for entry in PATCH_CATALOG:
            name = entry["name"]
            if name not in effective:
                continue
            if name in config["enable"]:
                reason = "explicit enable"
            elif entry["category"] in config["categories"]:
                reason = "category"
            elif entry["selection"] in {"always", "packaging"}:
                reason = entry["selection"]
            else:
                reason = config["preset"] + " preset"
            child = QTreeWidgetItem([name, reason])
            child.setToolTip(0, entry["summary"])
            root.addChild(child)
        if "build-preferences" in effective and "build-preferences" not in by_name:
            root.addChild(QTreeWidgetItem(["build-preferences", "profile preferences"]))
        root.setExpanded(True)
        disabled = QTreeWidgetItem([f"Explicitly disabled ({len(config['disable'])})"])
        self.patch_summary.addTopLevelItem(disabled)
        for name in config["disable"]:
            disabled.addChild(QTreeWidgetItem([name, "explicit disable"]))
        disabled.setExpanded(bool(config["disable"]))
        self.patch_summary.resizeColumnToContents(0)

    def show_patch_details(self, item, _previous):
        name = item.data(0, ROLE) if item else None
        entry = next((value for value in PATCH_CATALOG if value["name"] == name), None)
        if not entry:
            self.patch_details.clear()
            return
        origin = entry.get("origin", {"source": "TES3X"})
        source = SOURCES.get(origin.get("source"), {})
        origin_text = source.get("name", origin.get("source", "TES3X"))
        if "id" in origin:
            origin_text += " #" + str(origin["id"])
        lines = [entry["name"], "", entry["summary"], "",
                 f"Category: {entry['category']}", f"Channel: {entry['channel']}",
                 f"Selected by: {entry['selection']}", f"Origin: {origin_text}"]
        if entry.get("takes"):
            lines.append("Value: " + entry["takes"])
        self.patch_details.setPlainText("\n".join(lines))

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
        self.library.clear()
        self.selected.clear()
        self.setWindowTitle("TES3X Profile Manager")
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
        self.populate_library()
        self.populate_profile(plain.get("mods", []))
        self.populate_patches(plain.get("patches", {}))
        self.build.load(plain)
        try:
            self.saved_text = self.profile_text()
        except (PipelineError, LibraryError, tomlkit.exceptions.ParseError):
            self.saved_text = None
        if self.settings is not None:
            self.settings.setValue("last_profile", str(self.profile_path))
        self.refresh_profile_list()
        self.setWindowTitle(f"TES3X Profile Manager — {self.profile_path.stem}")
        message = str(self.profile_path)
        if library_root and not indexed:
            message += " — no library.toml, so mods are added by folder name"
        self.statusBar().showMessage(message)
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
        for entry in mods:
            resolve_selection(entry, root, catalog)
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
        self.populate_library()
        self.show_options(self.selected.currentRow())

    def build_changed(self):
        self.refresh_patch_summary()

    def mod_switch_changed(self, key, value, default):
        row = self.selected.currentRow()
        if row < 0:
            return
        item = self.selected.item(row)
        data = dict(item.data(ROLE))
        if value == default:
            data.pop(key, None)
        else:
            data[key] = value
        item.setData(ROLE, data)
        font = item.font()
        font.setStrikeOut(not data.get("enabled", True))
        item.setFont(font)
        self.refresh_patch_summary()

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
        self.populate_library()
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

    def populate_library(self):
        self.library.clear()
        for mod in sorted(self.catalog.values(), key=lambda item: item["name"].casefold()):
            parent = QTreeWidgetItem([mod["name"], mod["id"]])
            self.library.addTopLevelItem(parent)
            for release in mod["releases"].values():
                child = QTreeWidgetItem([release["version"], "default" if release["default"] else ""])
                child.setData(0, ROLE, (mod["id"], release["version"]))
                parent.addChild(child)
            parent.setExpanded(True)

    def populate_profile(self, mods):
        self.selected.clear()
        for index, mod in enumerate(sorted(mods, key=lambda item: item.get("order", 0)), 1):
            data = dict(mod)
            data["order"] = index * 10
            self.add_profile_item(data)
        if self.selected.count():
            self.selected.setCurrentRow(0)

    def entry_folder(self, data):
        """The library folder a profile mod reads, for spotting the same mod added twice."""
        if "id" not in data:
            return data["name"].casefold()
        mod = self.catalog.get(data["id"])
        if not mod:
            return None
        version = data.get("version") or mod["default"] or next(iter(mod["releases"]))
        release = mod["releases"].get(version)
        return release["folder"].casefold() if release else None

    def add_profile_item(self, data):
        if "id" in data:
            if data["id"] not in self.catalog:
                raise LibraryError(f"mod id is not installed: {data['id']}")
            mod = self.catalog[data["id"]]
            version = data.get("version") or mod["default"] or next(iter(mod["releases"]), "?")
            label = f"{mod['name']}  {version}"
        else:
            label = f"{data['name']}  (folder)"
        item = QListWidgetItem(label)
        item.setData(ROLE, data)
        font = item.font()
        font.setStrikeOut(not data.get("enabled", True))
        item.setFont(font)
        self.selected.addItem(item)

    def add_selected_release(self):
        item = self.library.currentItem()
        identity = item.data(0, ROLE) if item else None
        if not identity:
            return
        mod_id, version = identity
        folders = {self.entry_folder(self.selected.item(i).data(ROLE) or {})
                   for i in range(self.selected.count())}
        if self.catalog[mod_id]["releases"][version]["folder"].casefold() in folders:
            self.error(f"{self.catalog[mod_id]['name']} is already in this profile")
            return
        if not self.library_indexed:
            # Without library.toml a mod is its folder, as in a hand-written profile.
            self.add_profile_item({"name": self.catalog[mod_id]["releases"][version]["folder"],
                                   "order": (self.selected.count() + 1) * 10})
            self.selected.setCurrentRow(self.selected.count() - 1)
            self.refresh_patch_summary()
            return
        present = {(self.selected.item(i).data(ROLE) or {}).get("id")
                   for i in range(self.selected.count())}
        for selected_id in dependency_order(mod_id, self.catalog, version):
            if selected_id in present:
                continue
            selected_mod = self.catalog[selected_id]
            selected_version = (version if selected_id == mod_id else selected_mod["default"] or
                                next(iter(selected_mod["releases"])))
            release = selected_mod["releases"][selected_version]
            components = [entry["id"] for entry in release["components"].values()
                          if entry["default"]]
            self.add_profile_item({"id": selected_id, "version": selected_version,
                                   "components": components,
                                   "order": (self.selected.count() + 1) * 10})
            present.add(selected_id)
        self.selected.setCurrentRow(self.selected.count() - 1)
        self.refresh_patch_summary()

    def remove_selected(self):
        row = self.selected.currentRow()
        if row >= 0:
            self.selected.takeItem(row)
            self.renumber()
            self.refresh_patch_summary()

    def move_selected(self, delta):
        row = self.selected.currentRow()
        target = row + delta
        if row < 0 or not 0 <= target < self.selected.count():
            return
        item = self.selected.takeItem(row)
        self.selected.insertItem(target, item)
        self.selected.setCurrentRow(target)
        self.renumber()

    def renumber(self):
        for index in range(self.selected.count()):
            item = self.selected.item(index)
            data = dict(item.data(ROLE))
            data["order"] = (index + 1) * 10
            item.setData(ROLE, data)

    def show_options(self, row):
        self.components.blockSignals(True)
        self.plugins.blockSignals(True)
        self.components.clear()
        self.plugins.clear()
        for box, default in self.mod_switches.values():
            box.blockSignals(True)
            box.setEnabled(row >= 0)
            box.setChecked(default)
        if row < 0:
            self.options_title.setText("Select a profile mod")
            self.components.blockSignals(False)
            self.plugins.blockSignals(False)
            for box, _default in self.mod_switches.values():
                box.blockSignals(False)
            return
        data = dict(self.selected.item(row).data(ROLE))
        for key, (box, default) in self.mod_switches.items():
            box.setChecked(data.get(key, default))
            box.blockSignals(False)
        if "id" in data:
            mod = self.catalog[data["id"]]
            version = data.get("version") or mod["default"] or next(iter(mod["releases"]))
            release = mod["releases"][version]
            self.options_title.setText(f"{mod['name']} {version}")
            component_list = release["components"].values()
        else:
            self.options_title.setText(f"{data['name']} (whole folder)")
            component_list = []
        chosen = set(data.get("components", []))
        for component in component_list:
            item = QListWidgetItem(component["name"])
            item.setData(ROLE, component["id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if component["id"] in chosen
                               else Qt.CheckState.Unchecked)
            self.components.addItem(item)
        try:
            selection = resolve_selection(data, self.library_root, self.catalog)
            discovered = available_plugins(selection, find_data_root)
        except LibraryError as exc:
            discovered = []
            self.statusBar().showMessage(str(exc))
        selected_plugins = set(name.casefold() for name in data.get("plugins", discovered))
        for name in discovered:
            item = QListWidgetItem(name)
            item.setData(ROLE, name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if name.casefold() in selected_plugins
                               else Qt.CheckState.Unchecked)
            self.plugins.addItem(item)
        self.components.blockSignals(False)
        self.plugins.blockSignals(False)

    def components_changed(self, _item):
        row = self.selected.currentRow()
        if row < 0:
            return
        target = self.selected.item(row)
        data = dict(target.data(ROLE))
        data["components"] = [self.components.item(i).data(ROLE)
                              for i in range(self.components.count())
                              if self.components.item(i).checkState() == Qt.CheckState.Checked]
        try:
            resolve_selection(data, self.library_root, self.catalog)
        except LibraryError as exc:
            self.statusBar().showMessage(str(exc))
        target.setData(ROLE, data)
        self.show_options(row)

    def plugins_changed(self, _item):
        row = self.selected.currentRow()
        if row < 0:
            return
        target = self.selected.item(row)
        data = dict(target.data(ROLE))
        data["plugins"] = [self.plugins.item(i).data(ROLE)
                           for i in range(self.plugins.count())
                           if self.plugins.item(i).checkState() == Qt.CheckState.Checked]
        target.setData(ROLE, data)

    def profile_mods(self):
        self.renumber()
        return [dict(self.selected.item(i).data(ROLE)) for i in range(self.selected.count())]

    def profile_text(self):
        """Write the editor state into the document and return it, validated."""
        mods = tomlkit.aot()
        for values in self.profile_mods():
            table = tomlkit.table()
            for key in ("name", "id", "version", "components", "order", "enabled",
                        "optional", "plugins", "loose"):
                if key in values:
                    table.add(key, values[key])
            mods.append(table)
        if len(mods):
            self.document["mods"] = mods
        else:
            self.document.pop("mods", None)
        self.build.apply(self.document)
        patches = self.document.get("patches")
        if patches is None:
            patches = tomlkit.table()
            self.document["patches"] = patches
        for key, value in self.patch_configuration().items():
            # Leave unchanged values alone so their comments and layout survive.
            if patches.get(key) != value:
                patches[key] = value
        text = tomlkit.dumps(self.document)
        validate_profile(tomllib.loads(text))
        for values in self.profile_mods():
            resolve_selection(values, self.library_root, self.catalog)
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

    def start_command(self, program, arguments, message):
        self.output.clear()
        process = QProcess(self)
        process.setWorkingDirectory(str(self.work_dir()))
        process.setProgram(sys.executable)
        process.setArguments([str(program), *arguments])
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self.append_process_output)
        process.finished.connect(self.command_finished)
        self.process = process
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
            self.ftp_status.setToolTip("Set deploy.host in local settings")
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
