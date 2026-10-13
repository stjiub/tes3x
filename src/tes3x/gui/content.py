"""The GUI's standalone content library and server controls."""

import sys
from pathlib import Path

import tomlkit
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from tes3x import manifest


class ContentPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.process = None
        self.manual = []
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Content library"))
        form = QFormLayout()
        self.all_builds = QCheckBox("Always share every completed build")
        self.all_builds.setToolTip("Publishes deploy folders under the configured build root "
                                   "when the Dash requests the catalog.")
        self.bind = QLineEdit("0.0.0.0")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(26510)
        self.retail = QCheckBox("Serve retail files")
        self.xbe = QCheckBox("Serve whole XBEs")
        form.addRow("Library", self.all_builds)
        form.addRow("Listen address", self.bind)
        form.addRow("Port", self.port)
        form.addRow("Files", self.retail)
        form.addRow("", self.xbe)
        layout.addLayout(form)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Build", "Revision", "Layout", "Source", "Status"])
        self.tree.setRootIsDecorated(False)
        layout.addWidget(self.tree, 1)
        buttons = QHBoxLayout()
        self.add_button = QPushButton("Add completed build…")
        self.add_button.clicked.connect(self.add_build)
        self.remove_button = QPushButton("Remove selected")
        self.remove_button.clicked.connect(self.remove_build)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        self.start_button = QPushButton("Start content server")
        self.start_button.clicked.connect(self.start)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop)
        for button in (self.add_button, self.remove_button, self.refresh_button,
                       self.start_button, self.stop_button):
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.status = QLabel("Stopped")
        layout.addWidget(self.status)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setMaximumHeight(150)
        layout.addWidget(self.log)
        self.load()
        self.all_builds.toggled.connect(self.save)
        self.all_builds.toggled.connect(lambda _checked: self.refresh())
        self.bind.editingFinished.connect(self.save)
        self.port.editingFinished.connect(self.save)
        self.retail.toggled.connect(self.save)
        self.xbe.toggled.connect(self.save)

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
        self.refresh()

    def save(self):
        path = self.window.local_config_path()
        try:
            document = tomlkit.parse(path.read_text(encoding="utf-8")) if path.is_file() \
                else tomlkit.document()
            values = {
                "all_builds": self.all_builds.isChecked(), "build": [str(p) for p in self.manual],
                "bind": self.bind.text().strip() or "0.0.0.0", "port": self.port.value(),
                "retail": self.retail.isChecked(), "xbe": self.xbe.isChecked(),
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
                str(self.window.work_dir() / "content-state"), "--build-root",
                str(self.build_root()), "--bind", self.bind.text().strip(),
                "--port", str(self.port.value())]
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
        self.process = process
        process.start()
        self.status.setText("Starting…")
        self.start_button.setEnabled(False)

    def read_output(self):
        if self.process is not None:
            text = bytes(self.process.readAllStandardOutput()).decode("utf-8", "replace")
            self.log.insertPlainText(text)
            if "content server on " in text:
                self.status.setText("Running")

    def finished(self, code, _status):
        self.read_output()
        self.log.appendPlainText(f"content server exited ({code})")
        self.process = None
        self.status.setText("Stopped")
        self.start_button.setEnabled(True)

    def stop(self):
        if self.process is not None:
            self.process.kill()

    def shutdown(self):
        self.stop()
