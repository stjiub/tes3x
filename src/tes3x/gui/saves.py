"""SavesTab: profile window saves."""

from .common import (BuildSettings, EXPANSION_PLACEHOLDERS, EXTRA, QAbstractItemView, QAction,
                     QColor, QComboBox, QDesktopServices, QHBoxLayout, QHeaderView, QIcon,
                     QInputDialog, QKeySequence, QLabel, QMenu, QMessageBox, QProcess,
                     QPushButton, QStyle, QTreeWidget, QTreeWidgetItem, QUrl, QVBoxLayout,
                     QWidget, Qt, ROLE, collections, icon_button, json, saves_tool, shutil,
                     struct, sys, tes3x_savepool, tes3x_targets, theme_icon, tomllib, trash)


class SavesTab(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window

    SAVE_NAME, SAVE_PLAYER, SAVE_CELL, SAVE_DATE, SAVE_SIZE, SAVE_FIT = range(6)

    def create_saves_tab(self):
        self.pool_combo = QComboBox()
        self.pool_combo.setMinimumWidth(280)
        self.pool_combo.activated.connect(self.pool_chosen)
        new_pool = QPushButton("New pool…")
        new_pool.clicked.connect(self.new_pool)
        self.pool_note = QLabel()
        self.pool_note.setWordWrap(True)
        top = QHBoxLayout()
        top.addWidget(QLabel("Save pool"))
        top.addWidget(self.pool_combo)
        top.addWidget(new_pool)
        top.addStretch()

        self.save_list = QTreeWidget()
        self.save_list.setHeaderLabels(["Save", "Player", "Cell", "Date", "Size", "Plugins"])
        self.save_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.save_list.itemSelectionChanged.connect(self.saves_selected)
        self.save_list.itemSelectionChanged.connect(self.window.show_context_info)
        header = self.save_list.header()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)

        self.save_actions = {}
        for key, label, handler, tip in (
                ("send", "Copy to…", self.send_saves,
                 "Copy the selected saves, in this pool, to the PC, an Xbox or the xemu disk"),
                ("copy", "Copy to pool…", lambda: self.transfer_saves(False),
                 "Copy the selected saves into another pool, where they are"),
                ("move", "Move to pool…", lambda: self.transfer_saves(True),
                 "Move the selected saves into another pool, where they are. Each original is "
                 "deleted once its copy is complete, and a save leaving an Xbox keeps a PC "
                 "copy"),
                ("delete", "Delete…", self.delete_saves, "Delete the selected saves"),
                ("refresh", "Refresh", lambda: self.refresh_saves(True),
                 "List this pool's saves again, on every Xbox too"),
                ("folder", "Open PC folder", self.open_save_folder,
                 "Open this pool's folder in the PC save library")):
            action = QAction(label, self)
            action.setToolTip(f"{label.rstrip('…')}: {tip}")
            action.triggered.connect(handler)
            self.save_actions[key] = action
        self.save_actions["delete"].setShortcut(QKeySequence(QKeySequence.StandardKey.Delete))
        self.save_actions["delete"].setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        self.save_list.addAction(self.save_actions["delete"])
        self.save_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.save_list.customContextMenuRequested.connect(self.save_menu)

        pixmap = QStyle.StandardPixmap
        buttons = QHBoxLayout()
        for key, theme, fallback in (
                ("refresh", QIcon.ThemeIcon.ViewRefresh, pixmap.SP_BrowserReload),
                ("folder", QIcon.ThemeIcon.FolderOpen, pixmap.SP_DirOpenIcon),
                (None, None, None),
                ("send", QIcon.ThemeIcon.DocumentSend, pixmap.SP_ArrowRight),
                (None, None, None),
                ("copy", QIcon.ThemeIcon.EditCopy, pixmap.SP_FileIcon),
                ("move", QIcon.ThemeIcon.GoNext, pixmap.SP_ArrowForward),
                ("delete", QIcon.ThemeIcon.EditDelete, pixmap.SP_TrashIcon)):
            if key is None:
                buttons.addSpacing(12)
                continue
            self.save_actions[key].setIcon(theme_icon(self, theme, fallback))
            buttons.addWidget(icon_button(self.save_actions[key]))
        buttons.addStretch()
        self.saves_status = QLabel()
        buttons.addWidget(self.saves_status)

        widget = self
        layout = QVBoxLayout(widget)
        layout.addLayout(top)
        layout.addWidget(self.pool_note)
        layout.addWidget(self.save_list)
        layout.addLayout(buttons)
        self.save_pool = None
        self.saves_probes = {}
        self.saves_local = []
        self.saves_disk = []
        self.saves_xbox = {}
        self.xbox_listing = {}
        self.xbox_checked = set()
        self.saves_selected()
        return widget

    def save_library(self):
        return self.window.work_dir() / "build" / "saves"

    def current_pool(self):
        """(title ID, name) of this profile's pool; the shared pool has no name."""
        if not self.save_pool:
            return tes3x_savepool.SHARED_ID, None
        return (tes3x_savepool.pool_id(self.save_pool["name"], self.save_pool.get("id")),
                self.save_pool["name"])

    def known_pools(self):
        """{title ID: {"name", "id", "users"}}: every profile's pool, and those the save library
        has seen, on an Xbox or made here."""
        pools = {}
        for path in self.window.profile_files():
            try:
                identity = tomllib.loads(path.read_text(encoding="utf-8")).get("profile", {})
                name = identity.get("save_pool")
                value = tes3x_savepool.pool_id(name, identity.get("save_pool_id")) \
                    if name else None
            except (OSError, tomllib.TOMLDecodeError, ValueError):
                continue
            if value:
                pool = pools.setdefault(value, {"name": name, "id": identity.get("save_pool_id"),
                                                "users": []})
                pool["users"].append(identity.get("name") or path.stem)
        for hex_id, name in saves_tool.read_index(self.save_library())["pools"].items():
            pools.setdefault(int(hex_id, 16), {"name": name, "id": hex_id, "users": []})
        value, name = self.current_pool()
        if name:
            pools.setdefault(value, {"name": name, "id": self.save_pool.get("id"), "users": []})
        pools.pop(tes3x_savepool.SHARED_ID, None)
        return pools

    def populate_pools(self):
        self.pool_combo.clear()
        self.pool_combo.addItem("Shared: the retail game's saves", tes3x_savepool.SHARED_ID)
        pools = self.known_pools()
        for value, pool in sorted(pools.items(), key=lambda item: item[1]["name"].casefold()):
            self.pool_combo.addItem(f"{pool['name']}  ({value:08X})", value)
        current, _name = self.current_pool()
        self.pool_combo.setCurrentIndex(max(0, self.pool_combo.findData(current)))
        me = self.window.profile_plain.get("profile", {}).get("name")
        others = [user for user in pools.get(current, {}).get("users", []) if user != me]
        if current == tes3x_savepool.SHARED_ID:
            note = ("Saves go to E:\\UDATA\\42530005 with the retail game and every profile "
                    "without a pool of its own.")
        else:
            note = (f"Saves go to E:\\UDATA\\{current:08X}, apart from other builds. "
                    + (f"Also used by: {', '.join(others)}. " if others else "")
                    + "A change of pool takes effect from the next build.")
        self.pool_note.setText(note)

    def load_save_pool(self, plain):
        identity = plain.get("profile", {})
        self.save_pool = ({"name": identity["save_pool"], "id": identity.get("save_pool_id")}
                          if identity.get("save_pool") else None)
        self.pool_changed()

    def apply_save_pool(self, document):
        put = lambda *args: BuildSettings.put(document, *args)
        put("profile", "save_pool", self.save_pool["name"] if self.save_pool else None, None)
        put("profile", "save_pool_id",
            self.save_pool.get("id") if self.save_pool else None, None)

    def pool_changed(self):
        self.populate_pools()
        self.window.update_build_state()
        self.save_list.clear()
        self.saves_local, self.saves_disk, self.saves_xbox, self.xbox_listing = [], [], {}, {}
        if self.saves_tab_visible():
            self.refresh_saves()

    def save_target_changed(self, probe=False):
        """The inventory covers every target; a changed target list may add or drop one."""
        if self.saves_tab_visible() and self.window.profile_path:
            self.refresh_saves(None if probe else False)

    def saves_tab_visible(self):
        return self.window.tabs.tabText(self.window.tabs.currentIndex()) == "Saves"

    def saves_tab_shown(self, _index):
        if self.saves_tab_visible():
            self.refresh_saves()

    def pool_chosen(self, index):
        value = self.pool_combo.itemData(index)
        if value == self.current_pool()[0]:
            return
        if value == tes3x_savepool.SHARED_ID:
            self.save_pool = None
        else:
            pool = self.known_pools()[value]
            self.save_pool = {"name": pool["name"], "id": pool["id"] or f"{value:08X}"}
        self.pool_changed()

    def new_pool(self):
        name, ok = QInputDialog.getText(self, "New save pool",
                                        "Name of the new save pool (shown on the dashboard):")
        name = name.strip()
        if not ok or not name:
            return
        pools = self.known_pools()
        if any(pool["name"].casefold() == name.casefold() for pool in pools.values()):
            self.window.error(f"A pool named {name!r} exists already; choose it from the list")
            return
        value = tes3x_savepool.pool_id(name)
        taken = set(pools) | {tes3x_savepool.SHARED_ID}
        while value in taken:
            value = tes3x_savepool.PREFIX << 16 | ((value + 1) & 0xFFFF or 1)
        saves_tool.remember_pool(self.save_library(), value, name)
        self.save_pool = {"name": name, "id": f"{value:08X}"}
        self.pool_changed()

    def save_xboxes(self):
        """{name: target} for every Xbox target the inventory lists."""
        return {
            name: target
            for name, target in tes3x_targets.targets(self.window.local_values()).items()
            if target.get("kind") == "xbox" and target.get("host")
        }

    def save_devices(self):
        """[(device, label)] in display order: the PC, this profile's xemu disk, each Xbox."""
        devices = [("pc", "PC library")]
        if self.window.profile_path and self.window.play_controller.play_disk().is_file():
            devices.append(("xemu", "xemu disk (this profile)"))
        devices += [(f"xbox:{name}", f"Xbox · {name}") for name in sorted(self.save_xboxes())]
        return devices

    @staticmethod
    def save_device(save):
        return f"xbox:{save['target']}" if save["source"] == "xbox" else save["source"]

    def refresh_saves(self, xbox=None):
        """List this pool on the PC, the profile's xemu disk and every Xbox.

        Each Xbox shows its last listing, then is asked again once per pool and session, or
        whenever `xbox` is true.
        """
        if not self.window.profile_path:
            return
        value, _name = self.current_pool()
        self.saves_local, self.saves_disk = [], []
        try:
            self.saves_local = saves_tool.library_saves(self.save_library(), value)
            if self.window.play_controller.play_disk().is_file():
                self.saves_disk = saves_tool.Disk(self.window.play_controller.play_disk()).saves(
                    value
                )
        except (OSError, ValueError, struct.error) as exc:
            self.window.statusBar().showMessage(f"Could not read the PC saves: {exc}", 8000)
        xboxes = self.save_xboxes()
        index = saves_tool.read_index(self.save_library())
        for name in list(self.saves_xbox):
            if name not in xboxes:
                del self.saves_xbox[name]
                self.xbox_listing.pop(name, None)
        for name in xboxes:
            if name not in self.saves_xbox:
                cached = saves_tool.target_listing(index, name, value)
                self.saves_xbox[name] = [dict(save, target=name)
                                         for save in cached.get("saves", [])]
                self.xbox_listing[name] = f"listed {cached['time']}" if cached.get("time") \
                    else "not listed"
        self.show_saves()
        for name in xboxes:
            if xbox or (xbox is None and (name, value) not in self.xbox_checked):
                self.list_xbox_saves(value, name)

    def list_xbox_saves(self, value, target_name):
        if target_name in self.saves_probes:
            self.saves_probes.pop(target_name).kill()
        process = QProcess(self)
        process.setWorkingDirectory(str(self.window.work_dir()))
        process.setProgram(sys.executable)
        process.setArguments(["-m", "tes3x", "saves", "list", "--pool", f"{value:08X}",
                              "--xbox", "--library", str(self.save_library()),
                              "--config", str(self.window.local_config_path()),
                              "--target", target_name])
        process.finished.connect(lambda code, _status, process=process, value=value,
                                 target_name=target_name:
                                 self.xbox_saves_listed(process, value, target_name, code))
        self.saves_probes[target_name] = process
        self.xbox_checked.add((target_name, value))
        self.xbox_listing[target_name] = "listing…"
        self.show_saves_status()
        process.start()

    def xbox_saves_listed(self, process, value, target_name, code):
        # A listing started for a pool since left, or replaced by a newer one, is dropped.
        if self.saves_probes.get(target_name) is not process:
            return
        del self.saves_probes[target_name]
        if value != self.current_pool()[0]:
            return
        output = bytes(process.readAllStandardOutput()).decode("utf-8", "replace").strip()
        try:
            result = json.loads(output.splitlines()[-1])
        except (IndexError, ValueError):
            error = bytes(process.readAllStandardError()).decode("utf-8", "replace").strip()
            self.xbox_listing[target_name] = "could not list"
            self.show_saves(error or output or f"exit {code}")
            return
        self.saves_xbox[target_name] = [dict(save, target=target_name)
                                        for save in result["saves"]]
        if result.get("xbox") == "ok":
            self.xbox_listing[target_name] = ""
            self.populate_pools()
            self.show_saves()
        else:
            self.xbox_listing[target_name] = (f"offline, listed {result['time']}"
                                              if result.get("time") else "offline")
            self.show_saves(result.get("xbox") or "")

    def all_saves(self):
        return self.saves_local + self.saves_disk + [
            save for saves in self.saves_xbox.values() for save in saves]

    def show_saves(self, tip=""):
        self.populate_saves(self.all_saves())
        self.show_saves_status(tip)

    def show_saves_status(self, tip=""):
        total = len(self.all_saves())
        waiting = [name for name, text in self.xbox_listing.items() if text == "listing…"]
        text = f"{total} save{'s' if total != 1 else ''} in this pool"
        if waiting:
            text += " · listing " + ", ".join(sorted(waiting)) + "…"
        self.saves_status.setText(text)
        self.saves_status.setToolTip(tip)

    def load_order(self):
        names = [name for name, _source, _placeholder in self.window.plugins.base_plugins()]
        names += [name for name, value in self.window.mods.analysis.get("plugins", {}).items()
                  if value.get("included")]
        return {name.casefold() for name in names}

    def save_fit(self, masters):
        """('ok' | 'missing', text) for a save's masters against this profile. The engine
        matches a save's masters by name, so their order does not matter."""
        loaded = self.load_order()
        # GOTY merged the expansions into Morrowind.esm; their files are empty placeholders.
        missing = [name for name in masters if name.casefold() not in loaded
                   and name.casefold() not in EXPANSION_PLACEHOLDERS]
        if missing:
            return "missing", "Missing " + ", ".join(missing)
        return "ok", "Compatible"

    def populate_saves(self, saves):
        """One group per device, newest save first, each device shown even when empty."""
        selected = {(self.save_device(save), save["folder"]) for save in self.selected_saves()}
        self.save_list.clear()
        colours = {"ok": QColor("#2e7d32"), "missing": QColor("#b3261e")}
        fits = collections.Counter()
        groups = {}
        for device, label in self.save_devices():
            group = QTreeWidgetItem([label])
            group.setData(0, ROLE, None)
            group.setData(0, EXTRA, device)
            group.setFlags(Qt.ItemFlag.ItemIsEnabled)
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            self.save_list.addTopLevelItem(group)
            groups[device] = group
        for save in sorted(saves, key=lambda save: save.get("date") or "", reverse=True):
            group = groups.get(self.save_device(save))
            if group is None:
                continue
            fit, text = self.save_fit(save.get("masters", []))
            fits[fit] += 1
            item = QTreeWidgetItem([
                save.get("name") or save.get("title") or save["folder"],
                save.get("player") or "", save.get("cell") or "", save.get("date") or "",
                f"{save['size'] / 1048576:.1f} MB", text])
            item.setData(0, ROLE, save)
            item.setForeground(self.SAVE_FIT, colours[fit])
            item.setToolTip(self.SAVE_FIT, "\n".join(save.get("masters", [])))
            group.addChild(item)
            item.setSelected((self.save_device(save), save["folder"]) in selected)
        for device, group in groups.items():
            count = group.childCount()
            detail = f"{count} save{'s' if count != 1 else ''}"
            if device.startswith("xbox:") and self.xbox_listing.get(device[5:]):
                detail += f" · {self.xbox_listing[device[5:]]}"
            group.setText(self.SAVE_PLAYER, detail)
            group.setExpanded(True)
        note = self.pool_note.text().split("  ⚠")[0]
        if fits["missing"]:
            note += (f"  ⚠ {fits['missing']} of {len(saves)} saves need plugins this profile "
                     "does not load.")
        self.pool_note.setText(note)
        self.saves_selected()

    def selected_saves(self):
        return [item.data(0, ROLE) for item in self.save_list.selectedItems()
                if item.data(0, ROLE) is not None]

    def saves_selected(self):
        selected = bool(self.selected_saves())
        idle = self.window.process is None
        for key in ("send", "copy", "move", "delete"):
            self.save_actions[key].setEnabled(idle and selected)

    def save_menu(self, position):
        if not self.selected_saves():
            return
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        sources = {self.save_device(save) for save in self.selected_saves()}
        send = menu.addMenu("Copy to")
        for device, label in self.save_devices():
            action = send.addAction(label, lambda device=device: self.send_saves(device))
            action.setEnabled(self.window.process is None and sources != {device})
        menu.addSeparator()
        menu.addActions([self.save_actions["copy"], self.save_actions["move"]])
        menu.addSeparator()
        menu.addAction(self.save_actions["delete"])
        menu.exec(self.save_list.viewport().mapToGlobal(position))

    def saves_command(self, command, folders, *extra, target=None):
        value, _name = self.current_pool()
        return ("saves",
                [command, *folders, "--pool", f"{value:08X}", "--library",
                 str(self.save_library()), "--config", str(self.window.local_config_path()),
                 *(["--target", target] if target else []), *extra])

    def device_groups(self, saves):
        """{device: [folders]} in display order."""
        groups = {}
        for save in saves:
            groups.setdefault(self.save_device(save), []).append(save["folder"])
        order = [device for device, _label in self.save_devices()]
        return {device: groups[device] for device in sorted(
            groups, key=lambda device: order.index(device) if device in order else len(order))}

    def device_label(self, device):
        return dict(self.save_devices()).get(device, device)

    def pull_steps(self, saves):
        """Copy the saves that are not on the PC into the PC library."""
        steps = []
        for device, folders in self.device_groups(saves).items():
            if device == "pc":
                continue
            if device == "xemu":
                script, arguments = self.saves_command(
                    "pull",
                    folders,
                    "--from",
                    "xemu",
                    "--disk",
                    str(self.window.play_controller.play_disk()),
                )
            else:
                script, arguments = self.saves_command("pull", folders, target=device[5:])
            steps.append((script, arguments, f"Copying {len(folders)} save(s) from "
                                             f"{self.device_label(device)} to the PC…"))
        return steps

    def send_steps(self, saves, destination):
        """Copy saves to `destination`, through the PC library: pull, then push."""
        saves = [save for save in saves if self.save_device(save) != destination]
        steps = self.pull_steps(saves)
        folders = sorted({save["folder"] for save in saves})
        if destination == "pc" or not folders:
            return steps
        _value, name = self.current_pool()
        pool_name = ["--pool-name", name] if name else []
        if destination == "xemu":
            script, arguments = self.saves_command(
                "push",
                folders,
                "--where",
                "xemu",
                "--disk",
                str(self.window.play_controller.play_disk()),
                *pool_name,
            )
        else:
            script, arguments = self.saves_command("push", folders, *pool_name,
                                                   target=destination[5:])
        steps.append((script, arguments, f"Copying {len(folders)} save(s) to "
                                         f"{self.device_label(destination)}…"))
        return steps

    def run_save_steps(self, steps):
        if not steps:
            return
        if self.window.process is not None:
            self.window.error("A TES3X command is already running")
            return
        self.window.run_steps(steps, then=lambda: self.refresh_saves(True))

    def send_saves(self, destination=None):
        saves = self.selected_saves()
        if not saves:
            return
        if destination is None:
            sources = {self.save_device(save) for save in saves}
            choices = {label: device for device, label in self.save_devices()
                       if sources != {device}}
            label, ok = QInputDialog.getItem(self, "Copy saves",
                                             "Copy the selected saves, in this pool, to:",
                                             list(choices), 0, False)
            if not ok:
                return
            destination = choices[label]
        self.run_save_steps(self.send_steps(saves, destination))

    def transfer_steps(self, saves, target, name, move):
        """Copy or move saves into pool `target`, each where it is."""
        steps = []
        for device, folders in self.device_groups(saves).items():
            where = device.split(":")[0]
            extra = ["--to", f"{target:08X}", "--where", where]
            extra += ["--to-name", name] if name else []
            extra += ["--move"] if move else []
            extra += (
                ["--disk", str(self.window.play_controller.play_disk())] if where == "xemu" else []
            )
            script, arguments = self.saves_command(
                "copy", folders, *extra, target=device[5:] if where == "xbox" else None)
            steps.append((script, arguments,
                          f"{'Moving' if move else 'Copying'} {len(folders)} "
                          f"{self.device_label(device)} save(s) to another pool…"))
        return steps

    def transfer_saves(self, move):
        current, _name = self.current_pool()
        pools = {tes3x_savepool.SHARED_ID: {"name": None}, **self.known_pools()}
        choices = {("Shared: the retail game's saves" if pool["name"] is None
                    else f"{pool['name']}  ({value:08X})"): value
                   for value, pool in pools.items() if value != current}
        if not choices:
            self.window.error("There is no other pool; make one with New pool…")
            return
        verb = "Move" if move else "Copy"
        label, ok = QInputDialog.getItem(
            self, f"{verb} saves",
            f"{verb} the selected saves into this pool. Xbox saves stay on their Xbox and PC "
            "copies on the PC" + (", and each original is deleted once its copy is complete:"
                                  if move else ":"),
            list(choices), 0, False)
        if ok:
            target = choices[label]
            self.run_save_steps(self.transfer_steps(self.selected_saves(), target,
                                                    pools[target]["name"], move))

    def delete_saves(self):
        saves = self.selected_saves()
        if not saves or self.window.process is not None:
            return
        names = "\n".join(f"• {save.get('name') or save['folder']} "
                          f"({self.device_label(self.save_device(save))})" for save in saves)
        answer = QMessageBox.question(self, "Delete saves",
                                      f"Delete these saves for good?\n\n{names}")
        if answer != QMessageBox.StandardButton.Yes:
            return
        current, _name = self.current_pool()
        steps = []
        for device, folders in self.device_groups(saves).items():
            if device == "pc":
                for folder in folders:
                    path = self.save_library() / f"{current:08X}" / folder
                    if not trash(path):
                        shutil.rmtree(path, ignore_errors=True)
                continue
            if device == "xemu":
                script, arguments = self.saves_command(
                    "delete",
                    folders,
                    "--where",
                    "xemu",
                    "--disk",
                    str(self.window.play_controller.play_disk()),
                )
            else:
                script, arguments = self.saves_command("delete", folders, target=device[5:])
            steps.append((script, arguments, f"Deleting {len(folders)} "
                                             f"{self.device_label(device)} save(s)…"))
        if steps:
            self.run_save_steps(steps)
        else:
            self.refresh_saves(False)

    def open_save_folder(self):
        current, _name = self.current_pool()
        folder = self.save_library() / f"{current:08X}"
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def show_save_info(self):
        saves = self.selected_saves()
        if len(saves) != 1:
            self.window.context_info.setPlainText("Select a save to see its details.")
            return
        save = saves[0]
        loaded = self.load_order()
        lines = [save.get("name") or save["folder"], "",
                 f"Where: {self.device_label(self.save_device(save))}",
                 f"Folder: {save['folder']}", f"Player: {save.get('player') or '?'}",
                 f"Cell: {save.get('cell') or '?'}", f"Saved: {save.get('date') or '?'}", "",
                 f"Plugins ({self.save_fit(save.get('masters', []))[1]}):"]
        lines += [f"  {name}" + ("" if name.casefold() in loaded
                                 or name.casefold() in EXPANSION_PLACEHOLDERS
                                 else "   — not in this profile")
                  for name in save.get("masters", [])]
        self.window.context_info.setPlainText("\n".join(lines))
