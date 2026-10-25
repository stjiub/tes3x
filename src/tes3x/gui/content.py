"""The GUI's standalone content library and server controls."""

import json
import sys
from pathlib import Path

import tomlkit
from PySide6.QtCore import QProcess, Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTabWidget,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from tes3x import content as tes3x_content
from tes3x import manifest


class ContentPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.process = None
        self.manual = []
        self.output_pending = ""
        self.activity_rows = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 4)
        top = QHBoxLayout()
        self.status = QLabel()
        self.status.setMinimumWidth(180)
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top.addWidget(self.status)
        self.start_button = QPushButton("Start")
        self.start_button.clicked.connect(self.start)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop)
        top.addWidget(self.start_button)
        top.addWidget(self.stop_button)
        top.addStretch()
        layout.addLayout(top)
        layout.addWidget(QLabel("Content library"))
        form = QFormLayout()
        self.all_builds = QCheckBox("Always share every completed build")
        self.all_builds.setToolTip("Publishes deploy folders under the configured build root "
                                   "while the content server runs.")
        self.bind = QLineEdit("0.0.0.0")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(26510)
        self.retail = QCheckBox("Serve retail files")
        self.xbe = QCheckBox("Serve whole XBEs")
        self.access = QComboBox()
        self.access.addItem("Private", "private")
        self.access.addItem("Public", "public")
        self.access.setToolTip("Private: approved Xboxes only. Public: any Xbox that reaches "
                               "the port.")
        self.client_rate = QSpinBox()
        self.client_rate.setRange(0, 10000)
        self.client_rate.setSuffix(" Mbit/s")
        self.client_rate.setSpecialValueText("Unlimited")
        self.with_multiplayer = QCheckBox("Start content server with multiplayer server")
        self.with_multiplayer.setToolTip("Starts this library when a local multiplayer server "
                                        "starts. Each server still stops independently.")
        form.addRow("Library", self.all_builds)
        form.addRow("", self.with_multiplayer)
        form.addRow("Files", self.retail)
        form.addRow("", self.xbe)
        network = QFormLayout()
        network.addRow("Listen address", self.bind)
        network.addRow("Port", self.port)
        network.addRow("Access", self.access)
        network.addRow("Per Xbox", self.client_rate)
        columns = QHBoxLayout()
        columns.addLayout(form)
        columns.addSpacing(24)
        columns.addLayout(network)
        columns.addStretch()
        layout.addLayout(columns)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        layout.addWidget(self.splitter, 1)
        builds_panel = QWidget()
        builds_layout = QVBoxLayout(builds_panel)
        builds_layout.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Build", "Revision", "Layout", "Source", "Status"])
        self.tree.setRootIsDecorated(False)
        builds_layout.addWidget(self.tree, 1)
        buttons = QHBoxLayout()
        self.add_button = QPushButton("Add completed build…")
        self.add_button.clicked.connect(self.add_build)
        self.remove_button = QPushButton("Remove selected")
        self.remove_button.clicked.connect(self.remove_build)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        for button in (self.add_button, self.remove_button, self.refresh_button):
            buttons.addWidget(button)
        buttons.addStretch()
        builds_layout.addLayout(buttons)
        self.tabs = QTabWidget()
        self.tabs.addTab(builds_panel, "Sources")
        self.tabs.addTab(self.revisions_panel(), "Revisions")
        self.tabs.addTab(self.xboxes_panel(), "Xboxes")
        self.tabs.currentChanged.connect(lambda _index: self.refresh_library())
        self.splitter.addWidget(self.tabs)
        activity_panel = QWidget()
        activity_layout = QVBoxLayout(activity_panel)
        activity_layout.setContentsMargins(0, 0, 0, 0)
        activity_layout.addWidget(QLabel("Recent install activity"))
        self.activity_tree = QTreeWidget()
        self.activity_tree.setHeaderLabels(["Build", "Client", "Stage", "Tickets",
                                            "Progress", "Sent", "File"])
        self.activity_tree.setRootIsDecorated(False)
        self.activity_tree.setMaximumHeight(190)
        activity_layout.addWidget(self.activity_tree)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setMaximumHeight(150)
        activity_layout.addWidget(self.log)
        self.splitter.addWidget(activity_panel)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([450, 180])
        self.load()
        self.all_builds.toggled.connect(self.save)
        self.all_builds.toggled.connect(lambda _checked: self.refresh())
        self.bind.editingFinished.connect(self.save)
        self.port.editingFinished.connect(self.save)
        self.retail.toggled.connect(self.save)
        self.xbe.toggled.connect(self.save)
        self.with_multiplayer.toggled.connect(self.save)
        self.access.currentIndexChanged.connect(lambda _index: self.save())
        self.client_rate.editingFinished.connect(self.save)
        self.library_timer = QTimer(self)
        self.library_timer.setInterval(5000)
        self.library_timer.timeout.connect(self.refresh_library)
        self.set_status("Stopped")

    def revisions_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        self.revisions = QTreeWidget()
        self.revisions.setHeaderLabels(["Entry", "Name", "Revision", "Build", "State",
                                        "Description"])
        self.revisions.setRootIsDecorated(False)
        layout.addWidget(self.revisions, 1)
        buttons = QHBoxLayout()
        self.edit_button = QPushButton("Edit…")
        self.edit_button.clicked.connect(self.edit_entry)
        self.withdraw_button = QPushButton("Withdraw")
        self.withdraw_button.clicked.connect(lambda: self.set_listed(False))
        self.restore_button = QPushButton("Restore")
        self.restore_button.clicked.connect(lambda: self.set_listed(True))
        self.prune_button = QPushButton("Delete withdrawn…")
        self.prune_button.clicked.connect(self.prune)
        for button in (self.edit_button, self.withdraw_button, self.restore_button,
                       self.prune_button):
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)
        return panel

    def xboxes_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        self.xboxes = QTreeWidget()
        self.xboxes.setHeaderLabels(["Fingerprint", "State", "Label", "Since"])
        self.xboxes.setRootIsDecorated(False)
        layout.addWidget(self.xboxes, 1)
        buttons = QHBoxLayout()
        self.approve_button = QPushButton("Approve…")
        self.approve_button.clicked.connect(self.approve)
        self.revoke_button = QPushButton("Revoke")
        self.revoke_button.clicked.connect(self.revoke)
        buttons.addWidget(self.approve_button)
        buttons.addWidget(self.revoke_button)
        buttons.addStretch()
        layout.addLayout(buttons)
        return panel

    def state_dir(self):
        return self.window.work_dir() / "content-state"

    def library(self):
        return tes3x_content.Library(self.state_dir(), self.state_dir(), [], False, ("build",))

    def refresh_library(self):
        state = self.state_dir()
        try:
            history = tes3x_content.read_publications(state)
            access = tes3x_content.read_access(state)
            pending = tes3x_content.read_pending(state)
        except (OSError, ValueError) as error:
            self.log.appendPlainText(f"content library: {error}")
            return
        selected = self.revisions.currentItem()
        selected = selected.data(0, 0x0100) if selected else None
        self.revisions.clear()
        for entry_id, rows in history.items():
            for row in reversed(rows):
                item = QTreeWidgetItem([entry_id, row["name"], row["revision"][:12],
                                        row["build"][:12],
                                        "Listed" if row["listed"] else "Withdrawn",
                                        row["description"]])
                item.setData(0, 0x0100, (entry_id, row["revision"]))
                item.setToolTip(2, row["revision"])
                self.revisions.addTopLevelItem(item)
                if (entry_id, row["revision"]) == selected:
                    self.revisions.setCurrentItem(item)
        for column in range(5):
            self.revisions.resizeColumnToContents(column)
        selected = self.xboxes.currentItem()
        selected = selected.data(0, 0x0100) if selected else None
        self.xboxes.clear()
        rows = [(key, "Approved", row["label"], row.get("added", ""))
                for key, row in access["clients"].items()]
        rows += [(key, "Waiting", row.get("address", ""), row.get("seen", ""))
                 for key, row in pending.items() if key not in access["clients"]]
        for key, state_name, label, since in rows:
            item = QTreeWidgetItem([tes3x_content.fingerprint(bytes.fromhex(key)), state_name,
                                    label, since])
            item.setData(0, 0x0100, key)
            self.xboxes.addTopLevelItem(item)
            if key == selected:
                self.xboxes.setCurrentItem(item)
        for column in range(4):
            self.xboxes.resizeColumnToContents(column)
        waiting = sum(state_name == "Waiting" for _, state_name, _, _ in rows)
        self.tabs.setTabText(2, f"Xboxes ({waiting} waiting)" if waiting else "Xboxes")

    def chosen_revision(self):
        item = self.revisions.currentItem()
        return item.data(0, 0x0100) if item else None

    def edit_entry(self):
        chosen = self.chosen_revision()
        if chosen is None:
            return
        row = next(row for row in tes3x_content.read_publications(self.state_dir())[chosen[0]]
                   if row["revision"] == chosen[1])
        dialog = QDialog(self)
        dialog.setWindowTitle(chosen[0])
        form = QFormLayout(dialog)
        name = QLineEdit(row["name"])
        name.setMaxLength(47)
        description = QLineEdit(row["description"])
        description.setMaxLength(191)
        form.addRow("Name", name)
        form.addRow("Description", description)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.library().set_metadata(chosen[0], name.text(), description.text())
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "TES3X", str(error))
        self.refresh_library()

    def set_listed(self, listed):
        chosen = self.chosen_revision()
        if chosen is None:
            return
        try:
            self.library().set_listed(chosen[0], chosen[1], listed)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "TES3X", str(error))
        self.refresh_library()

    def prune(self):
        if self.process is not None:
            QMessageBox.information(self, "TES3X", "Stop the content server first.")
            return
        held = tes3x_content.serving_lock(self.state_dir())
        if held is None:
            QMessageBox.information(self, "TES3X", "Another content server uses this library.")
            return
        try:
            history = tes3x_content.read_publications(self.state_dir())
            withdrawn = sum(not row["listed"] for rows in history.values() for row in rows)
            if not withdrawn:
                QMessageBox.information(self, "TES3X", "No withdrawn revisions.")
                return
            if QMessageBox.question(
                    self, "TES3X", f"Delete {withdrawn} withdrawn revision(s)? Revisions a "
                    "multiplayer world uses are kept. Deleted revisions cannot be restored.") \
                    != QMessageBox.StandardButton.Yes:
                return
            removed = self.library().prune()
            self.log.appendPlainText(f"deleted {len(removed)} withdrawn revision(s)")
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "TES3X", str(error))
        finally:
            held.close()
        self.refresh_library()

    def approve(self):
        item = self.xboxes.currentItem()
        dialog = QDialog(self)
        dialog.setWindowTitle("Approve Xbox")
        form = QFormLayout(dialog)
        which = QLineEdit(item.text(0) if item else "")
        label = QLineEdit(item.text(2) if item and item.text(1) == "Waiting" else "")
        label.setMaxLength(47)
        form.addRow("Fingerprint", which)
        form.addRow("Label", label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            tes3x_content.approve(self.state_dir(), which.text(), label.text().strip())
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "TES3X", str(error))
        self.refresh_library()

    def revoke(self):
        item = self.xboxes.currentItem()
        if item is None or item.text(1) != "Approved":
            return
        try:
            tes3x_content.revoke(self.state_dir(), item.data(0, 0x0100))
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "TES3X", str(error))
        self.refresh_library()

    def build_root(self):
        return self.window.local_path("build_root") or self.window.work_dir() / "build"

    def load(self):
        saved = self.window.local_values().get("content", {})
        self.manual = [(Path(value) if Path(value).is_absolute()
                        else self.window.work_dir() / value).resolve()
                       for value in saved.get("build", [])]
        self.all_builds.setChecked(bool(saved.get("all_builds", False)))
        self.bind.setText(str(saved.get("bind", "0.0.0.0")))
        self.port.setValue(int(saved.get("port", 26510)))
        self.retail.setChecked(bool(saved.get("retail", False)))
        self.xbe.setChecked(bool(saved.get("xbe", False)))
        self.with_multiplayer.setChecked(bool(saved.get("with_multiplayer", False)))
        self.access.setCurrentIndex(max(0, self.access.findData(saved.get("access", "private"))))
        self.client_rate.setValue(int(saved.get("client_rate", 0)))
        self.refresh()
        self.refresh_library()

    def save(self):
        path = self.window.local_config_path()
        try:
            document = tomlkit.parse(path.read_text(encoding="utf-8")) if path.is_file() \
                else tomlkit.document()
            values = {
                "all_builds": self.all_builds.isChecked(), "build": [str(p) for p in self.manual],
                "bind": self.bind.text().strip() or "0.0.0.0", "port": self.port.value(),
                "retail": self.retail.isChecked(), "xbe": self.xbe.isChecked(),
                "with_multiplayer": self.with_multiplayer.isChecked(),
                "access": self.access.currentData(), "client_rate": self.client_rate.value(),
            }
            table = document.get("content")
            if table is None:
                table = tomlkit.table()
                document["content"] = table
            for key, value in values.items():
                table[key] = value
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(tomlkit.dumps(document), encoding="utf-8", newline="")
        except OSError as error:
            QMessageBox.warning(self, "TES3X", f"Could not save content settings: {error}")
            return False
        return True

    def sources(self):
        folders = list(self.manual)
        if self.all_builds.isChecked():
            folders += sorted(self.build_root().glob("*/deploy"))
        return list(dict.fromkeys(path.resolve() for path in folders))

    def refresh(self):
        self.tree.clear()
        for source in self.sources():
            try:
                data = manifest.load(source)
                if data is None:
                    raise ValueError("No completed build manifest")
                name = str(data.get("profile") or source.parent.name)
                revision = str(data.get("build") or manifest.build_id(data))[:12]
                layout = str(data.get("install_layout", "full"))
                status = "Ready"
            except (OSError, ValueError, KeyError) as error:
                name, revision, layout, status = source.parent.name, "", "", str(error)
            row = QTreeWidgetItem([name, revision, layout, str(source), status])
            row.setData(0, 0x0100, str(source))
            self.tree.addTopLevelItem(row)
        for column in range(3):
            self.tree.resizeColumnToContents(column)

    def add_build(self):
        chosen = QFileDialog.getExistingDirectory(self, "Completed deploy folder",
                                                   str(self.build_root()))
        if chosen:
            path = Path(chosen).resolve()
            if path not in self.manual:
                self.manual.append(path)
            self.save()
            self.refresh()

    def remove_build(self):
        item = self.tree.currentItem()
        if item is None:
            return
        source = Path(item.data(0, 0x0100))
        if source in self.manual:
            self.manual.remove(source)
            self.save()
        elif self.all_builds.isChecked():
            QMessageBox.information(self, "TES3X", "This build is shared by 'Always share'. "
                                    "Turn that option off to stop sharing automatic builds.")
        self.refresh()

    def start(self):
        if self.process is not None or not self.save():
            return
        if not self.all_builds.isChecked() and not self.manual:
            QMessageBox.warning(self, "TES3X", "Choose completed builds or turn on sharing all builds")
            return
        args = ["-u", "-m", "tes3x", "content", "serve", "--state",
                str(self.state_dir()), "--build-root",
                str(self.build_root()), "--bind", self.bind.text().strip(),
                "--port", str(self.port.value()), "--access", self.access.currentData(),
                "--client-rate", str(self.client_rate.value())]
        if self.all_builds.isChecked():
            args.append("--all-builds")
        for path in self.manual:
            args += ["--build", str(path)]
        if self.retail.isChecked() or self.xbe.isChecked():
            args += ["--serve-origin", "build"]
        if self.retail.isChecked():
            args += ["--serve-origin", "retail"]
        if self.xbe.isChecked():
            args += ["--serve-origin", "xbe"]
        if self.retail.isChecked() and self.window.local_path("vanilla_root"):
            args += ["--retail-base", str(self.window.local_path("vanilla_root"))]
        process = QProcess(self)
        process.setProgram(sys.executable)
        process.setArguments(args)
        process.setWorkingDirectory(str(self.window.work_dir()))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self.read_output)
        process.finished.connect(self.finished)
        process.errorOccurred.connect(self.process_error)
        self.process = process
        self.output_pending = ""
        self.activity_rows.clear()
        self.activity_tree.clear()
        process.start()
        self.library_timer.start()
        self.set_status("Starting… copying new builds")

    def read_output(self):
        if self.process is not None:
            output = bytes(self.process.readAllStandardOutput()).decode("utf-8", "replace")
            self.output_pending += output
            while "\n" in self.output_pending:
                line, self.output_pending = self.output_pending.split("\n", 1)
                if line.startswith("activity "):
                    try:
                        self.show_activity(json.loads(line[9:]))
                    except (ValueError, KeyError, TypeError):
                        continue
                else:
                    self.log.appendPlainText(line)
                    if line.startswith("content server on "):
                        self.set_status("Running")
                        self.refresh_library()
                    elif line.startswith("access pending "):
                        self.refresh_library()
                        self.tabs.setCurrentIndex(2)

    def show_activity(self, activity):
        key = activity["attempt"]
        row = self.activity_rows.get(key)
        if row is None:
            row = QTreeWidgetItem()
            self.activity_rows[key] = row
            self.activity_tree.insertTopLevelItem(0, row)
            if len(self.activity_rows) > 128:
                oldest = self.activity_tree.takeTopLevelItem(self.activity_tree.topLevelItemCount() - 1)
                self.activity_rows.pop(oldest.data(0, 0x0100), None)
            row.setData(0, 0x0100, key)
        done, total = activity["done"], activity["total"]
        progress = f"{done * 100 // total}%" if total else "—"
        row.setText(0, activity["entry"])
        row.setText(1, activity["client"])
        row.setText(2, activity["stage"])
        row.setText(3, str(activity["tickets"]))
        row.setText(4, progress)
        row.setText(5, f"{activity['wire'] / (1 << 20):.1f} MB")
        row.setText(6, activity["file"])

    def finished(self, code, _status):
        self.read_output()
        self.log.appendPlainText(f"content server exited ({code})")
        self.process = None
        self.library_timer.stop()
        self.set_status("Stopped")

    def process_error(self, _error):
        if self.process is not None:
            self.log.appendPlainText(self.process.errorString())
            if self.process.error() == QProcess.ProcessError.FailedToStart:
                self.process = None
                self.set_status("Stopped")

    def set_status(self, value):
        color = ("#2e7d32" if value.startswith("Running") else
                 "#b26a00" if value.startswith(("Starting", "Stopping")) else "#616161")
        self.status.setText(value)
        self.status.setStyleSheet(f"background: {color}; color: white; font-weight: bold; "
                                  "border-radius: 4px; padding: 4px 10px;")
        self.start_button.setEnabled(self.process is None)
        self.stop_button.setEnabled(self.process is not None)

    def stop(self):
        if self.process is not None:
            self.set_status("Stopping…")
            self.process.kill()

    def shutdown(self):
        self.stop()
