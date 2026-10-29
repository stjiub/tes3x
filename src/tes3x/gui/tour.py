"""Pick the places the memory tour visits."""

from tes3x import locations

from .common import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QTreeWidget,
                     QTreeWidgetItem, QVBoxLayout, Qt)

PRESETS = (("Major cities", "cities"), ("Dense interiors", "dense"),
           ("Expansions", "expansions"), ("All", "all"))


class TourDialog(QDialog):
    """A checklist of locations.toml, grouped, with preset buttons."""

    def __init__(self, checked, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Memory tour")
        self.resize(460, 560)
        self.locations = locations.load()
        self.presets = locations.presets(self.locations)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Visit these places and log free memory at each:"))
        row = QHBoxLayout()
        for label, preset in PRESETS:
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, name=preset: self.choose(self.presets[name]))
            row.addWidget(button)
        none = QPushButton("None")
        none.clicked.connect(lambda: self.choose([]))
        row.addWidget(none)
        layout.addLayout(row)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Location", "Refs"])
        self.tree.setRootIsDecorated(True)
        self.items = {}
        groups = {}
        for entry in self.locations.values():
            group = groups.get(entry["group"])
            if group is None:
                group = groups[entry["group"]] = QTreeWidgetItem(self.tree, [entry["group"]])
                group.setFlags(group.flags() | Qt.ItemFlag.ItemIsAutoTristate
                               | Qt.ItemFlag.ItemIsUserCheckable)
                group.setExpanded(True)
            item = QTreeWidgetItem(group, [entry["name"], str(entry.get("refs", ""))])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            self.items[entry["id"]] = item
        self.tree.header().resizeSection(0, 340)
        layout.addWidget(self.tree, 1)
        self.count = QLabel()
        layout.addWidget(self.count)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Run tour")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.choose(checked if checked is not None else self.presets["cities"])
        self.tree.itemChanged.connect(lambda *_: self.update_count())

    def choose(self, ids):
        self.tree.blockSignals(True)
        for name, item in self.items.items():
            item.setCheckState(0, Qt.CheckState.Checked if name in ids
                               else Qt.CheckState.Unchecked)
        self.tree.blockSignals(False)
        self.update_count()

    def selected(self):
        return [name for name, item in self.items.items()
                if item.checkState(0) == Qt.CheckState.Checked]

    def update_count(self):
        count = len(self.selected())
        self.count.setText(f"{count} selected")
