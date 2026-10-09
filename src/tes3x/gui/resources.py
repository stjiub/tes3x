"""ResourcesPanel: profile window resources."""

from .common import (QHeaderView, QLabel, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                     QWidget, Qt, ROLE, WARNING, os, texture_dims)


class ResourcesPanel(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window

    def create_resources_panel(self):
        self.resource_summary = QLabel("Waiting for profile analysis…")
        self.resource_summary.setWordWrap(True)
        self.resource_budget = QTreeWidget()
        self.resource_budget.setHeaderLabels(
            ["Mod", "Files", "Source size", "Textures", "Texture size", "Largest"])
        self.resource_budget.setRootIsDecorated(False)
        self.resource_budget.setAlternatingRowColors(True)
        self.resource_budget.itemSelectionChanged.connect(self.window.show_context_info)
        self.resource_dependencies = QTreeWidget()
        self.resource_dependencies.setHeaderLabels(["Kind", "Item", "Requires", "Status"])
        self.resource_dependencies.setRootIsDecorated(False)
        self.resource_dependencies.setAlternatingRowColors(True)
        self.resource_dependencies.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.resource_dependencies.itemSelectionChanged.connect(self.window.show_context_info)
        self.resource_tabs = QTabWidget()
        self.resource_tabs.addTab(self.resource_budget, "Budgets")
        self.resource_tabs.addTab(self.resource_dependencies, "Dependencies")
        self.resource_tabs.currentChanged.connect(self.window.show_context_info)
        panel = self
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.resource_summary)
        layout.addWidget(self.resource_tabs, 1)
        return panel

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
                      for item, _mod in self.window.mods.analysis["active"]}
        for item, mod in self.window.mods.analysis["active"]:
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

            release = self.window.mods.describe(item.data(0, ROLE))[2] or {}
            for dependency in release.get("dependencies", []):
                status = "Active" if dependency in active_ids else "Missing"
                dep = QTreeWidgetItem(["Mod", item.text(0),
                                       active_ids.get(dependency, dependency), status])
                dep.setData(0, ROLE,
                            f"Mod dependency\n\n{item.text(0)} requires {dependency}: {status}")
                if status == "Missing":
                    dep.setForeground(3, WARNING)
                self.resource_dependencies.addTopLevelItem(dep)

        loaded = [item for item in self.window.plugins.plugin_list.rows()
                  if item.checkState(0) == Qt.CheckState.Checked]
        positions = {(item.data(0, ROLE) or item.text(0).casefold()): index
                     for index, item in enumerate(loaded)}
        for item in loaded:
            key = item.data(0, ROLE)
            if not key or key not in self.window.mods.analysis["plugins"]:
                continue
            for master in self.window.plugins.plugin_masters(
                self.window.mods.analysis["plugins"][key]["path"]
            ):
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
