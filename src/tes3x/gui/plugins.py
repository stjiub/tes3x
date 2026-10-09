"""PluginsPanel: profile window plugins."""

from .common import (DragList, EXPANSION_PLACEHOLDERS, EXTRA, Path, QAbstractItemView,
                     QApplication, QCheckBox, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                     QTreeWidgetItem, QVBoxLayout, QWidget, Qt, RETAIL_PLUGINS, ROLE, ROW_FLAGS,
                     WARNING, collect, dependency_order, mlox_notes, os, plugin_masters,
                     rules_file, sort_files, tempfile)


class PluginsPanel(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window

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
        self.plugin_list.itemSelectionChanged.connect(self.window.show_context_info)
        self.plugin_note = QLabel()
        self.plugin_note.setWordWrap(True)
        panel = self
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(bar)
        layout.addWidget(self.plugin_list, 1)
        layout.addWidget(self.plugin_note)
        return panel

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
        plugins = self.window.mods.analysis["plugins"]
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
        overrides = self.window.mods.analysis.get("retail", {})
        vanilla = self.window.local_path("vanilla_root")
        data_files = vanilla / "Data Files" if vanilla else None
        modded = bool(self.window.mods.analysis["active"])
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
        plugins = self.window.mods.analysis["plugins"]
        active = self.window.mods.analysis["active"]
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
        self.plugin_order = [self.window.mods.analysis["plugins"][item.data(0, ROLE)]["name"]
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
        for (row, _mod), names in zip(
            self.window.mods.analysis["active"], self.window.mods.analysis["mod_plugins"]
        ):
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
            self.window.mods.set_entry(row, entry, row.data(0, EXTRA))
        self.window.mods.refresh_analysis()

    def sort_plugins(self):
        vanilla = self.window.local_path("vanilla_root")
        if vanilla is None:
            self.window.error("Sorting needs the clean game root; set it in File > Settings.")
            return
        plugins = self.window.mods.analysis["plugins"]
        chosen = {name: Path(value["path"]) for name, value in plugins.items() if value["included"]}
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            rules = rules_file(self.window.local_path("mlox_rules"))
            with tempfile.TemporaryDirectory(prefix="tes3x-sort-") as temp:
                empty = Path(temp) / "none"
                empty.mkdir()
                files = collect(empty, vanilla / "Data Files", Path(temp) / "stubs")
                files.update(chosen)
                names, messages = sort_files(files, rules, Path(temp) / "mlox")
        except (OSError, ValueError, RuntimeError) as exc:
            QApplication.restoreOverrideCursor()
            self.window.error(f"mlox could not sort the plugins: {exc}")
            return
        QApplication.restoreOverrideCursor()
        sorted_names = [plugins[name]["name"] for name in names if name in plugins]
        rest = [plugins[name]["name"] for name in self.plugin_sequence() if name not in chosen]
        self.plugin_order = sorted_names + rest
        self.mlox_at_build.setChecked(False)
        self.populate_plugins()
        notes = mlox_notes(messages)
        self.window.output.setPlainText("\n\n".join(notes) if notes else "mlox: no warnings")
        self.window.statusBar().showMessage(f"Sorted {len(sorted_names)} plugins with mlox", 5000)
