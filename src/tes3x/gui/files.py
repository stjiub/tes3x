"""FilesPanel: profile window files."""

from .common import (FilesFilter, FilesModel, QAbstractItemView, QCheckBox, QHBoxLayout,
                     QHeaderView, QLineEdit, QTableView, QVBoxLayout, QWidget)


class FilesPanel(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window

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
        self.files_view.selectionModel().selectionChanged.connect(self.window.show_context_info)
        panel = self
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(bar)
        layout.addWidget(self.files_view, 1)
        return panel
