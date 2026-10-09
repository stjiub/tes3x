"""ModsTab: profile window mods."""

from .common import (ARCHIVES, BASE_MASTERS, CATALOG_NAME, COMPAT, COMPAT_LABELS,
                     ComponentsDialog, DragList, EXTRA, FilesFilter, FilesModel, InstallDialog,
                     LOSES, LibraryError, Mod, PLUGIN_EXT, Path, PipelineError, QAbstractItemView,
                     QAction, QApplication, QDesktopServices, QDialog, QFileDialog, QHBoxLayout,
                     QHeaderView, QIcon, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox,
                     QPushButton, QSplitter, QStyle, QTabWidget, QTableView, QTextBrowser,
                     QTreeWidget, QTreeWidgetItem, QUrl, QVBoxLayout, QWidget, Qt, ROLE,
                     ROW_FLAGS, WARNING, WINS, append_mods, catalog_needs, defaultdict,
                     extract_archive, fnmatch, folder_name, free_id, guess_release, html,
                     icon_button, install_files, install_layout, json, load_library,
                     match_catalog, nexus, nexus_id, os, plugin_header, resolve_selection, shutil,
                     theme_icon, threading, tomlkit, tomllib, trash, uuid)


class ModsTab(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window

    MOD_NAME, MOD_VERSION, MOD_CONFLICTS, MOD_NOTES, MOD_PRIORITY, MOD_XBOX = range(6)

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

    def create_mods_tab(self):
        install = QPushButton("Install mod…")
        install.setToolTip("Install a .zip, .7z or .rar archive, or a plugin, into the mod "
                           "library. You can also drop files on the list.")
        install.clicked.connect(self.choose_install)
        self.mod_search = QLineEdit()
        self.mod_search.setPlaceholderText("Filter mods…")
        self.mod_search.textChanged.connect(self.filter_mods)
        refresh = QAction(theme_icon(self, QIcon.ThemeIcon.ViewRefresh,
                                     QStyle.StandardPixmap.SP_BrowserReload),
                          "Refresh the mod list from the library (F5)", self)
        refresh.triggered.connect(self.reload_library)
        top = QHBoxLayout()
        top.addWidget(install)
        top.addWidget(self.mod_search, 1)
        top.addWidget(icon_button(refresh))

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
        panel = self
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self.mod_list, 1)
        return panel

    def describe(self, entry):
        """Display name, version text, release and problem for a profile mod entry."""
        if "id" in entry:
            mod = self.window.catalog.get(entry["id"])
            if mod is None:
                return entry["id"], "", None, "not in the mod library"
            version = entry.get("version") or mod["default"] or next(iter(mod["releases"]))
            release = mod["releases"].get(version)
            if release is None:
                return mod["name"], version, None, f"version {version} is not installed"
            return mod["name"], "" if version == "unknown" else version, release, None
        name = entry["name"]
        if self.window.library_root is None or not (self.window.library_root / name).exists():
            return name, "", None, "folder not found in the mod library"
        release = next((release for mod in self.window.catalog.values()
                        for release in mod["releases"].values()
                        if release["folder"].casefold() == name.casefold()), None)
        version = release["version"] if release else ""
        return name, "" if version == "unknown" else version, release, None

    def entry_folders(self, entry):
        if "id" not in entry:
            return {entry["name"].casefold()}
        mod = self.window.catalog.get(entry["id"])
        return {release["folder"].casefold() for release in mod["releases"].values()} if mod else set()

    def library_entry(self, mod):
        if self.window.library_indexed:
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
        for mod in sorted(self.window.catalog.values(), key=lambda item: item["name"].casefold()):
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
        for column in range(self.mod_list.columnCount()):
            item.setData(column, Qt.ItemDataRole.ForegroundRole, WARNING if problem else None)
        source = release.get("source") if release else None
        item.setToolTip(self.MOD_NAME, problem or (f"Installed from {source}" if source else ""))
        self.update_compat(item)
        self.mods_loading = loading

    def update_compat(self, item):
        """The catalog's verdict on a mod, and any requirement this profile does not meet."""
        entry = item.data(0, ROLE)
        verdict = self.compat_verdict(item)
        status = verdict["status"] if verdict else "untested"
        symbol, colour = COMPAT.get(status, ("?", None))
        lines = [f"Xbox compatibility: {COMPAT_LABELS[status]}" if verdict
                 else "Not in the compatibility catalog"]
        if verdict:
            if catalog_needs(verdict):
                lines.append("Needs " + ", ".join(value.replace("`", "")
                                                  for value in catalog_needs(verdict)))
            off = [patch for patch in verdict.get("patches", [])
                   if patch not in self.window.patches.applied_patches]
            if off and item.data(0, EXTRA) and entry.get("enabled", True):
                colour = WARNING
                lines.append("Turn on in Patches: " + ", ".join(off))
            if verdict.get("notes"):
                lines.append(verdict["notes"])
        item.setText(self.MOD_XBOX, symbol)
        item.setData(self.MOD_XBOX, Qt.ItemDataRole.ForegroundRole, colour)
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
            mod = self.window.catalog.get(entry.get("id"))
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
        verdict = match_catalog(
            self.window.compat,
            name,
            entry.get("name", ""),
            entry.get("id", ""),
            release["folder"] if release else "",
        )
        off = [
            patch
            for patch in (verdict or {}).get("patches", [])
            if patch in self.window.patches.patch_items
            and patch not in self.window.patches.applied_patches
        ]
        for patch in off:
            self.window.patches.set_patch(patch, True)
        if off:
            self.window.statusBar().showMessage(
                f"{name} needs {', '.join(off)}; turned on in Patches", 8000
            )

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
        self.window.patches.refresh_patch_states()

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
            mod = self.window.catalog.get(entry.get("id"))
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
            folder = (
                self.window.library_root / release["folder"]
                if release and self.window.library_root
                else (
                    self.window.library_root / entry["name"]
                    if "name" in entry and self.window.library_root
                    else None
                )
            )
            open_folder = menu.addAction(
                "Open folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))))
            open_folder.setEnabled(bool(folder and folder.exists()))
            rename = menu.addAction("Rename…", lambda: self.rename_mod(item))
            rename.setEnabled(self.window.library_indexed and "id" in entry
                              and entry["id"] in self.window.catalog)
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

    def compat_verdict(self, item):
        entry = item.data(0, ROLE)
        name, _version, release, _problem = self.describe(entry)
        return match_catalog(self.window.compat, name, entry.get("name", ""), entry.get("id", ""),
                             release["folder"] if release else "")

    def mod_record(self, item):
        """The library's entry for a row, which holds its page and description."""
        entry = item.data(0, ROLE)
        if "id" in entry:
            return self.window.catalog.get(entry["id"])
        release = self.describe(entry)[2]
        return next((mod for mod in self.window.catalog.values()
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
        scanned = self.scan(entry) if self.window.library_root else None
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
        mode = self.window.build.mode.currentData()
        if mode == "loose":
            return "Included loose"
        if entry.get("loose"):
            return "Included loose by mod setting"
        path = key.replace("\\", "/")
        patterns = [pattern.casefold().replace("\\", "/")
                    for pattern in self.window.build.lines(self.window.build.loose_assets)]
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns):
            return "Included loose by path rule"
        if mode == "merged-bsa":
            return "Packed in rebuilt Morrowind.bsa"
        name = self.window.build.archive_name.text().strip() or "tes3xmods.bsa"
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
            self.window.nexus_done.emit(key, result, error)

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
        self.window.statusBar().showMessage("Searching Nexus…", 5000)

    def choose_nexus_match(self, text, result, error):
        item = getattr(self, "nexus_search_item", None)
        if item is None or not any(row is item for row in self.mod_rows()):
            return
        if error:
            self.window.error(f"Nexus search failed: {error}")
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
        if not (self.window.library_indexed and mod):
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
            self.window.error(exc)

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
            resolve_selection(entry, self.window.library_root, self.window.catalog)
        except LibraryError as exc:
            self.window.error(exc)
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
        mod = self.window.catalog.get(entry.get("id"))
        if mod is None and "id" not in entry:
            mod = next((value for value in self.window.catalog.values()
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

    def schedule_analysis(self):
        self.analysis_timer.start()

    def scan(self, entry):
        exclude = self.window.build.excluded()
        key = json.dumps([{name: entry.get(name) for name in ("id", "name", "version",
                                                              "components")}, exclude],
                         sort_keys=True)
        if key not in self.scans:
            try:
                selection = resolve_selection(entry, self.window.library_root, self.window.catalog)
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
        self.window.files.files_model.reset(sorted(files, key=lambda row: row[0].casefold()))
        self.window.populate_archives(files)
        self.window.plugins.populate_plugins()
        self.highlight_conflicts()
        self.window.refresh_status()
        self.window.resources.refresh_resources()
        self.window.show_context_info()

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

    def choose_install(self):
        start = self.window.settings.value("install_dir", "") if self.window.settings else ""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Install mod", start,
            "Mods (*.zip *.7z *.rar *.esp *.esm *.bsa);;All files (*)")
        if paths and self.window.settings is not None:
            self.window.settings.setValue("install_dir", str(Path(paths[0]).parent))
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
        target = self.window.library_root / folder_name(name)
        if target.exists():
            return f"The mod library already has a folder named {target.name}"
        return None

    def install_mod(self, source, replace=None):
        """Install an archive, folder or plugin into the library; returns the mod's name."""
        if self.window.library_root is None:
            self.window.error("Set a mod library first, in the Build tab or File > Settings")
            return None
        work = self.window.library_root / f".tes3x-install-{uuid.uuid4().hex[:8]}"
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
                self.window.error(f"{source.name}: install an archive, a folder or a plugin")
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
                target = self.window.library_root / replace["folder"]
                staged = work / "installed"
                install_files(unpacked, dialog.selection(), staged)
                target.rename(work / "previous")
                staged.rename(target)
                if self.window.library_indexed and replace.get("id") and source.is_file():
                    self.edit_catalog(lambda document: self.release_table(
                        document, replace["id"], replace["version"]).__setitem__(
                            "source", str(source)))
            else:
                folder = folder_name(name)
                install_files(unpacked, dialog.selection(), self.window.library_root / folder)
                if self.window.library_indexed:
                    mod_id = free_id(self.window.catalog, name)
                    release = {"version": version or "unknown", "folder": folder,
                               "default": True, "roots": ["."], "dependencies": [],
                               "components": {},
                               "source": str(source) if source.is_file() else None}
                    append_mods(self.window.library_root, {mod_id: {
                        "id": mod_id, "name": name, "releases": {release["version"]: release}}})
            self.window.statusBar().showMessage(f"Installed {name}", 5000)
            return name
        except (OSError, LibraryError) as exc:
            self.window.error(exc)
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
        path = self.window.library_root / CATALOG_NAME
        text = path.read_text(encoding="utf-8")
        document = tomlkit.parse(text)
        change(document)
        path.write_text(tomlkit.dumps(document), encoding="utf-8", newline="")
        try:
            self.window.catalog = load_library(self.window.library_root)
        except LibraryError:
            path.write_text(text, encoding="utf-8", newline="")
            raise

    def rename_mod(self, item):
        entry = item.data(0, ROLE)
        mod = self.window.catalog[entry["id"]]
        name, ok = QInputDialog.getText(self, "Rename mod", "Name", text=mod["name"])
        name = name.strip()
        if not ok or not name or name == mod["name"]:
            return
        try:
            self.edit_catalog(lambda document: self.mod_table(document, entry["id"]).__setitem__(
                "name", name))
        except (OSError, LibraryError) as exc:
            self.window.error(exc)
            return
        self.update_mod_item(item)
        self.schedule_analysis()

    def profiles_using(self, entry):
        """Other profiles that pick this mod."""
        users = []
        folders = self.entry_folders(entry)
        for path in self.window.profile_files():
            if path.resolve() == self.window.profile_path:
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
        folder = self.window.library_root / (release["folder"] if release else entry["name"])
        message = f"Move {folder} to the Recycle Bin and remove {name} from the library?"
        users = self.profiles_using(entry)
        if users:
            message += "\n\nThese profiles also use it: " + ", ".join(users)
        if QMessageBox.question(self, "Delete mod", message) != QMessageBox.StandardButton.Yes:
            return
        if not trash(folder):
            self.window.error(f"Could not move {folder} to the Recycle Bin")
            return
        mod = self.window.catalog.get(entry.get("id"))
        if self.window.library_indexed and mod:
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
                self.window.error(exc)
        self.take_mod(item)
        self.reload_library()

    def reload_library(self):
        entries = self.profile_mods()
        try:
            self.window.library_root, self.window.catalog, self.window.library_indexed = (
                self.window.resolve_library(self.window.build.library(), [])
            )
        except (OSError, tomllib.TOMLDecodeError, PipelineError, LibraryError) as exc:
            self.window.error(exc)
            return
        self.scans.clear()
        self.populate_mods(entries)
