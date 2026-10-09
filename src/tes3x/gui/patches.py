"""PatchesTab: profile window patches."""

from .common import (CATEGORY_ACCENTS, CHANNEL_BADGES, PATCH_CATALOG, PATCH_CATEGORIES,
                     PipelineError, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel,
                     QLineEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, Qt, ROLE,
                     SOURCES, WARNING, dot_icon, patch_spec, resolve_patch_plan)


class PatchesTab(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window

    def create_patches_tab(self):
        self.patch_preset = QComboBox()
        self.patch_preset.addItems(["minimal", "recommended", "testing"])
        self.patch_preset.setToolTip("minimal: no optional patches; recommended: release fixes; "
                                     "testing: release and preview fixes, diagnostics and console")
        self.patch_preset.currentTextChanged.connect(self.refresh_patch_states)
        self.patch_search = QLineEdit()
        self.patch_search.setPlaceholderText("Filter patches…")
        self.patch_search.textChanged.connect(self.filter_patches)
        self.patch_only_enabled = QCheckBox("Only enabled")
        self.patch_only_enabled.toggled.connect(lambda _on: self.filter_patches())
        top = QHBoxLayout()
        top.addWidget(QLabel("Start from"))
        top.addWidget(self.patch_preset)
        top.addWidget(self.patch_search, 1)
        top.addWidget(self.patch_only_enabled)

        self.patch_tree = QTreeWidget()
        self.patch_tree.setHeaderLabels(["Title", "Patch key", "Status", "Included by"])
        self.patch_tree.setAlternatingRowColors(True)
        self.patch_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.patch_tree.header().setStretchLastSection(False)
        self.patch_tree.itemChanged.connect(self.patch_item_changed)
        self.patch_tree.currentItemChanged.connect(self.window.show_context_info)
        self.patch_items = {}
        self.patch_groups = {}
        for category in PATCH_CATEGORIES:
            entries = [entry for entry in PATCH_CATALOG if entry["category"] == category]
            if not entries:
                continue
            group = QTreeWidgetItem([category])
            group.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            group.setData(0, ROLE, None)
            group.setIcon(0, dot_icon(CATEGORY_ACCENTS.get(category, "#7a8794")))
            self.patch_tree.addTopLevelItem(group)
            self.patch_groups[category] = group
            for entry in entries:
                item = QTreeWidgetItem([entry["title"], patch_spec(entry), entry["channel"], ""])
                item.setData(0, ROLE, entry["name"])
                item.setToolTip(0, entry["summary"])
                badge = CHANNEL_BADGES.get(entry["channel"])
                if badge:
                    item.setBackground(2, badge)
                    item.setTextAlignment(2, Qt.AlignmentFlag.AlignCenter)
                flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                if entry["selection"] == "preset" and (entry["channel"] != "dev"
                                                         or self.window.developer_mode):
                    flags |= Qt.ItemFlag.ItemIsUserCheckable
                else:
                    item.setForeground(0, self.window.palette().placeholderText())
                item.setFlags(flags)
                group.addChild(item)
                self.patch_items[entry["name"]] = item
            group.setExpanded(True)
        self.patch_tree.resizeColumnToContents(0)
        self.patch_tree.resizeColumnToContents(2)
        self.patch_tree.resizeColumnToContents(3)
        tab = self
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
        profile = {
            "patches": self.patch_configuration(modes),
            "profile": (
                {"install_layout": self.window.build.install_layout.currentData()}
                if hasattr(self.window, "build")
                else {}
            ),
            "mods": (
                self.window.mods.profile_mods() if hasattr(self.window.mods, "mod_list") else []
            ),
            "package": self.window.build.package_values() if hasattr(self.window, "build") else {},
            "preferences": (
                self.window.build.preference_values() if hasattr(self.window, "build") else {}
            ),
        }
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
        overlay_layout = self.window.build.install_layout.currentData() == "overlay"
        self.patch_loading = True
        for name, item in self.patch_items.items():
            entry = by_name[name]
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if entry["selection"] == "preset" and (entry["channel"] != "dev"
                                                     or self.window.developer_mode) \
                    and not required_by[name] \
                    and not (name == "data-overlay" and overlay_layout):
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            item.setFlags(flags)
            item.setData(0, Qt.ItemDataRole.ForegroundRole,
                         None if flags & Qt.ItemFlag.ItemIsUserCheckable
                         else self.window.palette().placeholderText())
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
            item.setData(3, Qt.ItemDataRole.ForegroundRole,
                         WARNING if entry["selection"] == "always" else None)
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
        if hasattr(self.window.mods, "mod_list"):
            loading, self.window.mods.mods_loading = self.window.mods.mods_loading, True
            for row in self.window.mods.mod_rows():
                self.window.mods.update_compat(row)
            self.window.mods.mods_loading = loading

    def sync_patch_ini(self, applied):
        """Show the ini keys of patches that are on; drop profile values only dead patches read."""
        if not hasattr(self.window, "ini"):
            return
        gone = self.applied_patches - applied
        back = applied - self.applied_patches
        still = {("xbox", key.casefold()) for entry in PATCH_CATALOG if entry["name"] in applied
                 for key in entry.get("ini", {})}
        for name in self.window.ini.patch_owned(gone):
            if self.window.ini.split(name) not in still:
                self.ini_stash[name] = self.window.ini.values.pop(name)
        for name in self.ini_stash.copy():
            owners = {entry["name"] for entry in PATCH_CATALOG
                      if ("xbox", self.window.ini.split(name)[1]) in
                      {("xbox", key.casefold()) for key in entry.get("ini", {})}}
            if owners & back:
                self.window.ini.values[name] = self.ini_stash.pop(name)
        self.applied_patches = applied
        self.window.ini.set_patches(applied)

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
                if (
                    entry["channel"] not in self.window.shown_channels
                    and child.checkState(0) != Qt.CheckState.Checked
                ):
                    hidden = True
                if self.patch_only_enabled.isChecked() and child.checkState(0) != Qt.CheckState.Checked:
                    hidden = True
                child.setHidden(hidden)
                visible += not hidden
            group.setHidden(visible == 0)

    def show_patch_details(self, item, _previous):
        name = item.data(0, ROLE) if item else None
        entry = next((value for value in PATCH_CATALOG if value["name"] == name), None)
        if not entry:
            self.window.context_info.clear()
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
        self.window.context_info.setPlainText("\n".join(lines))
