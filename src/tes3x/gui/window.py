"""TES3X GUI. It edits the same TOML the command-line tools read."""

from html import escape

from .common import (Bsa, BuildSettings, CATALOG_NAME, CHECK_MARKER, CatalogError, DEPLOYS_MARKER,
                     DEPLOY_CONFLICT, DEPLOY_NO_SPACE, IniPanel, LibraryError,
                     LocalSettingsDialog, PIPELINE_MARKER, PLUGIN_EXT, PROFILE_NAME, Path,
                     PipelineError, QAction, QActionGroup, QApplication, QComboBox, QDialog,
                     QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMainWindow, QMenu,
                     QMessageBox, QProcess, QScrollArea, QSettings, QSplitter, QStackedWidget,
                     QTabBar, QTabWidget, QTextBrowser, QTextCursor, QTextEdit, QTimer,
                     QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, Qt, ROLE,
                     ServerPage, Signal, StatusBar, TEMPLATE, TargetsPage, WARNING, XEMU_STARTED,
                     argparse, convert_profile, datetime, default_config_path, defaultdict,
                     discover_library, dot_icon, drop_empty, enabled_addons, heartbeat_text,
                     index_library, ini_text, json, launch, load_catalog, load_library,
                     menu_button, new_table, play_icon, plugin_header, sha256_file, shutil,
                     stop_icon, struct, sys, target_capabilities, target_runtime_label,
                     tes3x_targets, tomlkit, tomllib, validate_profile, version_label)
from .saves import SavesTab
from .patches import PatchesTab
from .resources import ResourcesPanel
from .plugins import PluginsPanel
from .files import FilesPanel
from .mods import ModsTab
from .play_controller import PlayController
from tes3x.paths import docs_url


class ProfileWindow(QMainWindow):
    nexus_done = Signal(str, object, object)

    in_game_event = Signal(object)

    agent_lines = Signal(str, object)

    def __init__(self, profile=None, config=None, settings=None):
        super().__init__()
        self.saves = SavesTab(self)
        self.patches = PatchesTab(self)
        self.resources = ResourcesPanel(self)
        self.plugins = PluginsPanel(self)
        self.files = FilesPanel(self)
        self.mods = ModsTab(self)
        self.play_controller = PlayController(self)
        self.setWindowTitle(version_label())
        self.resize(1280, 800)
        self.profile_path = None
        self.document = None
        self.library_root = None
        self.catalog = {}
        self.library_indexed = False
        self.process = None
        self.play_controller.plays = {}  # target name -> PlaySession; xemu targets run side by side
        self.play_controller.ftp_probes = {}
        self.play_controller.agent_probes = {}
        self.play_controller.in_game_listener = None
        self.play_controller.agent_fingerprint = None
        self.play_controller.listener_lent = False
        self.deploy_agent = False
        self.play_controller.agent_logs = defaultdict(list)
        self.play_controller.log_memory = False
        self.play_controller.agent_keys = {}  # in-game agent key -> xemu target
        self.play_controller.memory_logged = {}
        self.play_controller.agent_replies = {}
        self.play_controller.agent_fetches = []
        self.target_builds = {}
        self.play_controller.drive_probe = None
        self.command_kind = None
        self.command_target = None
        self.check_profile_sha = None
        self.check_failed = False
        self.build_failed = False
        self.deploy_failed = False
        self.deploy_records = {}
        self.profile_plain = {}
        self.patches.patch_modes = {}
        self.patches.patch_categories = []
        self.patches.patch_loading = False
        self.patches.applied_patches = set()
        self.patches.ini_stash = {}
        self.plugins.plugin_order = None
        self.mods.mods_loading = False
        self.plugins.plugins_loading = False
        self.mods.scans = {}
        self.plugins.masters = {}
        self.mods.nexus_cache = {}
        self.mods.nexus_links = {}
        self.mods.nexus_pending = set()
        self.nexus_done.connect(self.mods.nexus_finished)
        self.in_game_event.connect(self.play_controller.handle_in_game_event)
        self.mods.forget_analysis()
        try:
            self.compat = load_catalog()
        except (OSError, ValueError, CatalogError):
            self.compat = {}
        self.config_path = Path(config).resolve() if config else None
        self.settings = settings
        saved = (str(self.settings.value("shown_channels", "preview,release"))
                 if self.settings else "preview,release")
        self.shown_channels = {name for name in saved.split(",") if name}
        self.developer_mode = "dev" in self.shown_channels
        self.saved_text = None
        self.mods.analysis_timer = QTimer(self)
        self.mods.analysis_timer.setSingleShot(True)
        self.mods.analysis_timer.setInterval(150)
        self.mods.analysis_timer.timeout.connect(self.mods.refresh_analysis)

        self.profile_picker = QComboBox()
        self.profile_picker.setMinimumWidth(260)
        self.profile_picker.activated.connect(self.picker_activated)
        profile_bar = QHBoxLayout()
        self.workspace_bar = QTabBar()
        self.workspace_bar.setDrawBase(False)
        self.workspace_bar.setExpanding(False)
        self.workspace_bar.setUsesScrollButtons(False)
        self.workspace_bar.setElideMode(Qt.TextElideMode.ElideNone)
        for label in ("Profile", "Target", "Server"):
            self.workspace_bar.addTab(label)
        profile_bar.addWidget(self.workspace_bar)
        profile_bar.addSpacing(16)
        profile_bar.addWidget(QLabel("Profile"))
        profile_bar.addWidget(self.profile_picker)
        profile_actions = menu_button("Manage profiles", (
            ("New…", self.new_profile), ("Duplicate…", self.duplicate_profile),
            ("Rename…", self.rename_profile), ("Delete", self.delete_profile)))
        profile_bar.addWidget(profile_actions)
        profile_bar.addStretch()
        profile_bar.addWidget(QLabel("Target"))
        self.target_picker = QComboBox()
        self.target_picker.setMinimumWidth(155)
        self.target_picker.activated.connect(self.target_activated)
        profile_bar.addWidget(self.target_picker)
        target_actions = menu_button("Manage targets", (
            ("Add…", lambda: self.targets_page.setup.add_target()),
            ("Duplicate…", lambda: self.targets_page.setup.duplicate_target()),
            ("Set up…", self.show_target_setup),
            ("Remove", lambda: self.targets_page.setup.remove_target())))
        profile_bar.addWidget(target_actions)
        self.target_actions = target_actions
        self.target_states = {}
        self.target_runtime = {}
        self.target_drive_tips = {}
        self.profile_bar = profile_bar

        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.mods.mod_details = self.mods.create_mod_details()
        self.tabs = QTabWidget()
        self.tabs.addTab(self.mods.create_mods_tab(), "Mods")
        self.tabs.addTab(self.plugins.create_plugins_panel(), "Plugins")
        self.archives = QTreeWidget()
        self.archives.setHeaderLabels(["Archive", "Provided by", "Loaded"])
        self.archives.setRootIsDecorated(False)
        self.archives.itemSelectionChanged.connect(self.show_context_info)
        self.tabs.addTab(self.archives, "Archives")
        self.tabs.addTab(self.files.create_files_panel(), "Data Files")
        self.tabs.addTab(self.patches.create_patches_tab(), "Patches")
        self.ini = IniPanel()
        self.ini.changed.connect(self.refresh_status)
        self.ini.tree.itemSelectionChanged.connect(self.show_context_info)
        self.tabs.addTab(self.ini, "INI")
        self.tabs.addTab(self.resources.create_resources_panel(), "Resources")
        self.build = BuildSettings()
        self.build.on_change = self.build_changed
        self.build.on_library = self.library_changed
        self.build.skip_intro.toggled.connect(self.skip_intro_toggled)
        self.ini.changed.connect(self.sync_skip_intro)
        build_scroll = QScrollArea()
        build_scroll.setWidgetResizable(True)
        build_scroll.setWidget(self.build)
        self.tabs.addTab(self.saves.create_saves_tab(), "Saves")
        self.tabs.addTab(build_scroll, "Build")
        self.tabs.currentChanged.connect(self.saves.saves_tab_shown)
        self.tabs.currentChanged.connect(self.show_context_info)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addLayout(profile_bar)
        self.context_info = QTextBrowser()
        self.context_info.setPlaceholderText("Select an item to see its details")
        self.details_stack = QStackedWidget()
        self.details_stack.addWidget(self.mods.mod_details)
        self.details_stack.addWidget(self.context_info)
        self.details_container = QWidget()
        details_layout = QVBoxLayout(self.details_container)
        details_layout.setContentsMargins(0, self.tabs.tabBar().sizeHint().height(), 0, 0)
        details_layout.addWidget(self.details_stack)
        self.content_split = QSplitter(Qt.Orientation.Horizontal)
        self.content_split.addWidget(self.tabs)
        self.content_split.addWidget(self.details_container)
        self.content_split.setStretchFactor(0, 3)
        self.content_split.setStretchFactor(1, 2)
        self.content_split.setSizes([780, 500])
        self.targets_page = TargetsPage(self)
        self.agent_lines.connect(self.targets_page.append_lines)
        self.server_page = ServerPage(self)
        self.workspaces = QStackedWidget()
        for page in (self.content_split, self.targets_page, self.server_page):
            self.workspaces.addWidget(page)
        self.workspace_bar.currentChanged.connect(self.workspace_changed)
        self.body_split = QSplitter(Qt.Orientation.Vertical)
        self.body_split.addWidget(self.workspaces)
        self.body_split.addWidget(self.output)
        self.body_split.setStretchFactor(0, 4)
        self.body_split.setStretchFactor(1, 1)
        self.body_split.setSizes([660, 120])
        layout.addWidget(self.body_split)
        self.setCentralWidget(body)
        self.setStatusBar(StatusBar())
        self.counts = QLabel()
        self.counts.setContentsMargins(0, 0, 8, 0)
        self.build_state_label = QLabel()
        self.build_state_label.setContentsMargins(8, 0, 8, 0)
        self.statusBar().addPermanentWidget(self.build_state_label)
        self.statusBar().addPermanentWidget(self.counts)
        self.ftp_timer = QTimer(self)
        self.ftp_timer.setInterval(60_000)
        self.ftp_timer.timeout.connect(self.play_controller.refresh_all_targets)

        self.menuBar().setFont(self.tabs.tabBar().font())
        self.menuBar().setStyleSheet("QMenuBar::item { padding: 6px 10px; }")
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
        self.action_install = QAction("&Install mod…", self)
        self.action_install.setShortcut("Ctrl+I")
        self.action_install.triggered.connect(self.mods.choose_install)
        self.action_install_folder = QAction("Install mod from &folder…", self)
        self.action_install_folder.triggered.connect(self.mods.choose_install_folder)
        self.action_refresh = QAction("&Refresh library", self)
        self.action_refresh.setShortcut("F5")
        self.action_refresh.triggered.connect(self.mods.reload_library)
        self.action_settings = QAction("&Settings…", self)
        self.action_settings.triggered.connect(self.edit_local_settings)
        self.action_index = QAction("Index new library folders", self)
        self.action_index.triggered.connect(self.write_library_index)
        self.action_convert = QAction("Convert folder names to library ids", self)
        self.action_convert.triggered.connect(self.convert_to_ids)
        self.action_exit = QAction("E&xit", self)
        self.action_exit.triggered.connect(self.close)
        file_menu.addActions([self.action_new, self.action_open, self.action_save])
        file_menu.addSeparator()
        file_menu.addActions([self.action_install, self.action_install_folder,
                              self.action_refresh])
        file_menu.addSeparator()
        file_menu.addActions([self.action_settings, self.action_index, self.action_convert])
        file_menu.addSeparator()
        file_menu.addAction(self.action_exit)

        view_menu = self.menuBar().addMenu("&View")
        theme_menu = view_menu.addMenu("&Theme")
        self.theme_actions = {}
        theme_group = QActionGroup(self)
        saved_theme = str(self.settings.value("theme", "system")) if self.settings else "system"
        for key, label in (("system", "System"), ("light", "Light"), ("dark", "Dark")):
            action = QAction(label, self, checkable=True, checked=key == saved_theme)
            action.triggered.connect(lambda _checked, key=key: self.set_theme(key))
            theme_group.addAction(action)
            theme_menu.addAction(action)
            self.theme_actions[key] = action
        self.set_theme(saved_theme, save=False)
        channels_menu = view_menu.addMenu("Patch &channels")
        self.channel_actions = {}
        for channel in ("dev", "preview", "release"):
            action = QAction(channel.capitalize(), self, checkable=True,
                             checked=channel in self.shown_channels)
            action.setToolTip(f"List {channel}-channel patches")
            action.toggled.connect(lambda on, channel=channel: self.set_channel(channel, on))
            channels_menu.addAction(action)
            self.channel_actions[channel] = action
        self.action_output = QAction("&Output panel", self, checkable=True)
        self.action_output.setChecked(
            bool(self.settings.value("show_output", True, bool)) if self.settings else True)
        self.action_output.toggled.connect(self.set_output_visible)
        view_menu.addAction(self.action_output)
        self.set_output_visible(self.action_output.isChecked(), save=False)
        columns_menu = view_menu.addMenu("&Mod columns")
        hidden = (str(self.settings.value("hidden_mod_columns", "")) if self.settings
                  else "").split(",")
        for column in range(1, self.mods.mod_list.columnCount()):
            title = self.mods.mod_list.headerItem().text(column)
            action = QAction(title, self, checkable=True, checked=str(column) not in hidden)
            action.toggled.connect(lambda on, column=column: self.set_mod_column(column, on))
            columns_menu.addAction(action)
            self.mods.mod_list.setColumnHidden(column, not action.isChecked())

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
        self.action_manager = QAction("Install console &manager…", self)
        self.action_manager.triggered.connect(self.install_manager)
        self.action_fetch = QAction("Pull Xbox &logs", self)
        self.action_fetch.triggered.connect(self.pull_logs)
        self.action_refresh_ftp = QAction("Refresh Xbox connection", self)
        self.action_refresh_ftp.triggered.connect(self.play_controller.refresh_ftp_status)
        self.discard_after_deploy = QAction("Discard build after verified deploy", self)
        self.discard_after_deploy.setCheckable(True)
        self.action_play = QAction("&Play in xemu", self)
        self.action_play.setShortcut("F9")
        self.action_play.triggered.connect(lambda: self.play_controller.play())
        self.action_stop = QAction("Stop xemu", self)
        self.action_stop.setShortcut("Shift+F9")
        self.action_stop.setEnabled(False)
        self.action_stop.triggered.connect(self.play_controller.stop_play)
        self.action_reset_play = QAction("Reset xemu saves…", self)
        self.action_reset_play.triggered.connect(self.play_controller.reset_play_disk)
        actions_menu.addActions([self.action_check, self.action_build, self.action_smoke])
        actions_menu.addSeparator()
        actions_menu.addActions([self.action_deploy, self.action_play, self.action_stop,
                                 self.action_reset_play])
        actions_menu.addSeparator()
        actions_menu.addActions([self.action_manager, self.action_fetch,
                                 self.action_refresh_ftp])
        actions_menu.addSeparator()
        actions_menu.addAction(self.discard_after_deploy)
        help_menu = self.menuBar().addMenu("&Help")
        self.action_about = QAction("&About TES3X…", self)
        self.action_about.triggered.connect(self.show_about)
        help_menu.addAction(self.action_about)
        self.play_icon, self.stop_icon = play_icon(), stop_icon()
        self.action_play.setIcon(self.play_icon)
        self.run_menu = QMenu(self)
        self.run_menu.setToolTipsVisible(True)
        self.run_menu.addActions([self.action_check, self.action_build, self.action_deploy])
        self.run_button = QToolButton()
        self.run_button.setMenu(self.run_menu)
        self.run_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.run_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.run_button.clicked.connect(lambda: self.run_default().trigger())
        self.profile_bar.addWidget(self.run_button)
        self.play_button = QToolButton()
        self.play_button.setDefaultAction(self.action_play)
        self.play_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.profile_bar.addWidget(self.play_button)
        self.play_menu = QMenu(self)
        self.play_menu.setToolTipsVisible(True)
        self.play_button.setMenu(self.play_menu)
        self.play_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.style_toolbar_buttons()
        QApplication.styleHints().colorSchemeChanged.connect(
            lambda _scheme: QTimer.singleShot(0, self.style_toolbar_buttons))
        self.check_button, self.build_button, self.deploy_button = (
            self.action_check, self.action_build, self.action_deploy)
        for action in (self.action_check, self.action_build, self.action_deploy):
            action.changed.connect(self.sync_run_button)
        self.command_actions = (self.action_check, self.action_build, self.action_deploy,
                                self.action_play, self.action_smoke, self.action_fetch,
                                self.action_manager)
        self.after_command = None
        self.conflict_retry = None
        self.space_retry = None
        self.play_controller.play_gdb = bool(
            self.settings and self.settings.value("play_gdb", False, bool)
        )
        self.refresh_targets()
        self.refresh_play_menu()
        self.update_build_state()
        self.state_timer = QTimer(self)
        self.state_timer.setInterval(1500)
        self.state_timer.timeout.connect(self.update_build_state)
        self.state_timer.start()
        self.refresh_profile_list()
        if profile:
            self.open_profile(Path(profile))
        else:
            self.open_initial_profile()
        if self.settings:
            self.workspace_bar.setCurrentIndex(self.settings.value("workspace", 0, int))
        if QApplication.platformName() != "offscreen":
            self.play_controller.start_in_game_listener()
            if not self.local_config_path().is_file():
                QTimer.singleShot(0, self.first_run)
            QTimer.singleShot(0, self.play_controller.refresh_all_targets)
            self.ftp_timer.start()

    def show_about(self):
        project = "https://github.com/stjiub/tes3x"
        links = (
            ("Documentation", docs_url("index.md")),
            ("Project on GitHub", project),
            ("Release notes", project + "/blob/main/CHANGELOG.md"),
            ("Report an issue", project + "/issues"),
        )
        dialog = QMessageBox(self)
        dialog.setWindowTitle("About TES3X")
        dialog.setTextFormat(Qt.TextFormat.RichText)
        dialog.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        dialog.setText(
            f"<h3>{escape(version_label())}</h3>"
            "<p>Modding and patching toolkit for Morrowind GOTY on original Xbox.</p>"
            "<p>Author: stjiub<br>"
            f'<a href="{project}/blob/main/LICENSE">GPL-3.0-or-later</a></p>'
            "<p>" + "<br>".join(f'<a href="{url}">{label}</a>' for label, url in links)
            + "</p>"
        )
        dialog.setStandardButtons(QMessageBox.StandardButton.Close)
        dialog.exec()

    def show_context_info(self, *_args):
        """Show details for the selected item in the current primary tab."""
        if not hasattr(self, "context_info"):
            return
        tab = self.tabs.tabText(self.tabs.currentIndex())
        if tab == "Mods":
            self.details_stack.setCurrentWidget(self.mods.mod_details)
            self.mods.show_mod_info()
            return
        self.details_stack.setCurrentWidget(self.context_info)
        if tab == "Saves":
            self.saves.show_save_info()
            return
        if tab == "Plugins":
            items = self.plugins.plugin_list.selectedItems()
            if len(items) != 1:
                self.context_info.setPlainText("Select a plugin to see its details.")
                return
            item = items[0]
            key = item.data(0, ROLE)
            lines = [item.text(0), "", f"Provided by: {item.text(1)}"]
            if item.text(2):
                lines.append(f"Load index: {item.text(2)}")
            if key and key in self.mods.analysis.get("plugins", {}):
                value = self.mods.analysis["plugins"][key]
                lines.append("Build result: " + ("Included" if value["included"] else "Disabled"))
                author, description = plugin_header(value["path"])
                masters = self.plugins.plugin_masters(value["path"])
                if author:
                    lines += ["", f"Author: {author}"]
                if description:
                    lines += ["", description]
                lines += ["", "Masters: " + (", ".join(masters) if masters else "None")]
            elif item.text(1) == "Placeholder":
                lines += ["", "Generated expansion master placeholder."]
            problem = item.toolTip(0)
            if problem:
                lines += ["", "Warning: " + problem]
            self.context_info.setPlainText("\n".join(lines))
        elif tab == "Archives":
            items = self.archives.selectedItems()
            if len(items) != 1:
                self.context_info.setPlainText("Select an archive to see its details.")
                return
            item = items[0]
            lines = [item.text(0), "", f"Provided by: {item.text(1)}",
                     f"Build treatment: {item.text(2)}"]
            if item.toolTip(2):
                lines += ["", item.toolTip(2)]
            self.context_info.setPlainText("\n".join(lines))
        elif tab == "Data Files":
            index = self.files.files_view.currentIndex()
            if not index.isValid():
                self.context_info.setPlainText("Select a file to see its provider chain.")
                return
            source = self.files.files_filter.mapToSource(index)
            path, owner, others = self.files.files_model.entries[source.row()]
            key = path.replace("\\", "/").casefold()
            providers = self.mods.analysis.get("owners", {}).get(key, [])
            active = self.mods.analysis.get("active", [])
            lines = [path, "", f"Included from: {owner}"]
            if providers:
                winner = active[providers[-1]][0]
                lines.append("Build result: " + self.mods.packaging_result(winner, key))
                lines += ["", "Providers, earlier to later:"]
                for number, provider in enumerate(providers, 1):
                    name = active[provider][0].text(0)
                    result = "included" if provider == providers[-1] else "overridden"
                    lines.append(f"  {number}. {name} — {result}")
            else:
                lines.append("Overrides: " + (others or "Nothing"))
            self.context_info.setPlainText("\n".join(lines))
        elif tab == "Patches":
            self.patches.show_patch_details(self.patches.patch_tree.currentItem(), None)
        elif tab == "INI":
            item = self.ini.tree.currentItem()
            ident = item.data(0, ROLE) if item else None
            if ident is None:
                self.context_info.setPlainText("Select an INI setting to see its details.")
                return
            section, key = self.ini.rows()[ident]
            name = self.ini.override_name(ident)
            base = self.ini.base(ident)
            value = self.ini.values[name] if name else base
            lines = [f"{section}:{key}", "", f"Effective value: {ini_text(value)}",
                     f"Source: {item.text(2)}"]
            if name and base is not None:
                lines.append(f"Base value: {base}")
            patch = self.ini.patch_keys.get(ident)
            if patch:
                lines += ["", f"Read by patch: {patch[3]}"]
            self.context_info.setPlainText("\n".join(lines))
        elif tab == "Resources":
            tree = (
                self.resources.resource_budget
                if self.resources.resource_tabs.currentIndex() == 0
                else self.resources.resource_dependencies
            )
            items = tree.selectedItems()
            if len(items) != 1:
                self.context_info.setPlainText(
                    "Select a budget or dependency row to see its details.")
                return
            detail = items[0].data(0, ROLE)
            self.context_info.setPlainText(detail or "\n".join(
                items[0].text(column) for column in range(items[0].columnCount())))
        else:
            self.context_info.setPlainText(self.build_preview())

    def build_preview(self):
        """Human-readable plan from the current, possibly unsaved controls."""
        active = self.mods.analysis.get("active", [])
        plugins = [item.text(0) for item in self.plugins.plugin_list.rows()
                   if item.checkState(0) == Qt.CheckState.Checked]
        mode = self.build.mode.currentData()
        mode_text = {"delta-bsa": "Delta archive", "merged-bsa": "Rebuilt Morrowind.bsa",
                     "loose": "Loose files"}.get(mode, mode)
        destination = self.xbox_destination() or "Not set"
        title = self.build.title.text().strip() or "Retail title"
        profile = self.profile_path.stem if self.profile_path else "New"
        layout = ("Shared retail base" if self.build.install_layout.currentData() == "overlay"
                  else "Full game folder")
        lines = ["Build preview", "", f"Profile: {profile}", f"Dashboard title: {title}",
                 f"Xbox destination: {destination}", f"Install layout: {layout}",
                 f"Packaging: {mode_text}"]
        if mode == "delta-bsa":
            lines.append("Archive: " + (self.build.archive_name.text().strip()
                                         or "tes3xmods.bsa"))
        if self.build.archive_only.isChecked() and mode != "loose":
            lines.append("Asset lookup: archives only")
        lines += ["", f"Mods: {len(active)} active"]
        lines += [f"  {index}. {item.text(0)}" for index, (item, _mod) in enumerate(active, 1)]

        outcomes = defaultdict(int)
        for key, providers in self.mods.analysis.get("owners", {}).items():
            if key.endswith((*PLUGIN_EXT, ".bsa")):
                continue
            winner = active[providers[-1]][0]
            outcomes[self.mods.packaging_result(winner, key)] += 1
        if outcomes:
            lines += ["", "Assets:"]
            lines += [f"  {count} {result.casefold()}" for result, count in sorted(outcomes.items())]

        lines += ["", f"Plugins: {len(plugins)} active"]
        lines += [f"  {index:02X}  {name}" for index, name in enumerate(plugins[:20])]
        if len(plugins) > 20:
            lines.append(f"  … {len(plugins) - 20} more")
        archives = [self.archives.topLevelItem(i) for i in range(self.archives.topLevelItemCount())]
        lines += ["", "Archives:"]
        lines += [f"  {item.text(0)} — {item.text(2)}" for item in archives]
        patches = sorted(self.patches.applied_patches)
        lines += ["", f"Engine patches: {len(patches)}"]
        lines += ["  " + name for name in patches[:20]]
        if len(patches) > 20:
            lines.append(f"  … {len(patches) - 20} more")
        lines += ["", "Deploy:",
                  "  Clear Xbox cache partitions" if self.build.clear_cache.isChecked()
                  else "  Keep Xbox cache partitions"]
        return "\n".join(lines)

    def populate_archives(self, files):
        """The archives the build ships, and whether the engine opens each one."""
        self.archives.clear()
        mode = self.build.mode.currentData()
        assets = sum(1 for path, _owner, _others in files
                     if not path.lower().endswith((*PLUGIN_EXT, ".bsa")))
        if not self.mods.analysis["active"]:
            mode = "retail"
        rows = [("Morrowind.bsa", "Retail", "yes")]
        if mode == "merged-bsa":
            rows = [("Morrowind.bsa", f"Retail, rebuilt with {assets} mod files", "yes")]
        elif mode == "delta-bsa":
            name = self.build.archive_name.text().strip() or "tes3xmods.bsa"
            rows.append((name, f"TES3X build: {assets} mod files", "yes, after Morrowind.bsa"))
        for row in rows:
            self.archives.addTopLevelItem(QTreeWidgetItem(list(row)))
        active = self.mods.analysis["active"]
        for key, indexes in self.mods.analysis["owners"].items():
            if not key.endswith(".bsa") or "/" in key:
                continue
            row, mod = active[indexes[-1]]
            try:
                named = Bsa(mod.files[key]).named
            except (OSError, ValueError, struct.error):
                named = False
            if row.data(0, ROLE).get("archives") == "load":
                loaded, why = "yes, via multi-bsa", "Listed in tes3xarch.txt for the multi-bsa patch"
            elif named:
                loaded, why = "unpacked", "Its files join the build beneath the mod's loose files"
            else:
                loaded, why = "can't unpack", ("This archive has no file names. Right-click the "
                                               "mod and choose Load archives with multi-bsa.")
            item = QTreeWidgetItem([mod.relative.get(key, key), row.text(0), loaded])
            item.setToolTip(2, why)
            if loaded == "can't unpack":
                item.setForeground(2, WARNING)
            self.archives.addTopLevelItem(item)
        for column in range(3):
            self.archives.resizeColumnToContents(column)

    def refresh_status(self):
        rows = self.mods.mod_rows()
        active = sum(
            1 for item in rows if item.checkState(self.mods.MOD_NAME) == Qt.CheckState.Checked
        )
        plugins = sum(1 for value in self.mods.analysis["plugins"].values() if value["included"])
        self.counts.setText(f"Mods {active} of {len(rows)} active · "
                            f"Plugins {plugins + len(self.plugins.base_plugins())} active")

    def local_values(self):
        config = self.local_config_path()
        try:
            return tomllib.loads(config.read_text(encoding="utf-8")) if config.is_file() else {}
        except (OSError, tomllib.TOMLDecodeError):
            return {}

    def target_settings(self, name):
        return tes3x_targets.targets(self.local_values()).get(name, {}) if name else {}

    def target_label(self, name):
        target = self.target_settings(name)
        return target_runtime_label(target, self.target_runtime_state(name, target)) \
            if target else "Unknown"

    def profile_name(self):
        return (self.profile_plain or {}).get("profile", {}).get("name") or (
            self.profile_path.stem if self.profile_path else None)

    def workspace_changed(self, index):
        self.workspaces.setCurrentIndex(index)
        if self.settings:
            self.settings.setValue("workspace", index)
        if index == 1:
            self.targets_page.set_target(self.target_picker.currentData())

    def default_target(self, kind=None):
        name = self.target_picker.currentData() if hasattr(self, "target_picker") else None
        try:
            return tes3x_targets.resolve(self.local_values(), name=name, kind=kind)
        except (tes3x_targets.TargetError, ValueError):
            return None

    def refresh_targets(self, selected=None):
        """Reload machine targets while preserving status learned during this session."""
        local = self.local_values()
        available = tes3x_targets.targets(local)
        selected = selected or self.target_picker.currentData() or             tes3x_targets.default_name(local)
        self.target_picker.blockSignals(True)
        self.target_picker.clear()
        for name, target in available.items():
            kind = target.get("kind", "xbox")
            detail = target.get("host", "") if kind == "xbox" else f"{target.get('ram', 64)} MB"
            self.target_picker.addItem(dot_icon(self.target_colour(name, target)),
                                       f"{name}  ·  {detail or kind}", name)
            row = self.target_picker.count() - 1
            self.target_picker.setItemData(row, self.target_tooltip(name, target),
                                           Qt.ItemDataRole.ToolTipRole)
        index = self.target_picker.findData(selected)
        self.target_picker.setCurrentIndex(index if index >= 0 else 0)
        self.target_picker.blockSignals(False)
        self.target_selection_changed(probe=False)

    def target_runtime_state(self, name, target=None):
        state = self.target_runtime.setdefault(name, {})
        target = target or tes3x_targets.targets(self.local_values()).get(name, {})
        if target.get("kind") == "xbox":
            state["ftp"] = self.target_states.get(name, state.get("ftp", "unknown"))
        return state

    def set_target_runtime(self, name, **values):
        state = self.target_runtime.setdefault(name, {})
        state.update(values)
        if "ftp" in values:
            self.target_states[name] = values["ftp"]
        self.refresh_target_item(name)

    def target_features(self, name=None):
        name = name or self.target_picker.currentData()
        target = tes3x_targets.targets(self.local_values()).get(name)
        return target_capabilities(target, self.target_runtime_state(name, target)) \
            if target else frozenset()

    def target_colour(self, name, target=None):
        target = target or tes3x_targets.targets(self.local_values()).get(name, {})
        label = target_runtime_label(target, self.target_runtime_state(name, target))
        if label in {"In game", "Manager", "Dashboard", "Dashboard reachable",
                     "xemu running"}:
            return "#2e7d32"
        if label in {"Checking", "Checking agent", "Starting", "Stopping",
                     "Dashboard agent outdated"}:
            return "#a15c00"
        if label in {"Off", "Stalled"}:
            return "#b3261e"
        return "#616161"

    def target_tooltip(self, name, target=None):
        target = target or tes3x_targets.targets(self.local_values()).get(name, {})
        runtime = self.target_runtime_state(name, target)
        if target.get("kind") == "xemu":
            lines = [f"xemu · {target.get('ram', 64)} MB", target_runtime_label(target, runtime)]
        else:
            host = target.get("host") or "address not configured"
            lines = [f"Xbox FTP: {host}:{target.get('port', 21)}"]
            status = runtime.get("ftp")
            if status:
                lines.append({"checking": "Checking connection…", "connected": "Connected",
                              "offline": "Unreachable"}.get(status, status)
                             + (f": {runtime['ftp_detail']}" if status == "offline"
                                and runtime.get("ftp_detail") else ""))
            agent = target_runtime_label(target, runtime)
            if runtime.get("game") in {"connected", "stalled"}:
                lines.append(agent)
            elif status == "connected" and agent not in {"Dashboard reachable", "Unknown"}:
                lines.append(agent)
            if runtime.get("dashboard_detail") and runtime.get("dashboard") != "current":
                lines.append(runtime["dashboard_detail"])
        if runtime.get("game_detail"):
            lines.append(runtime["game_detail"])
        if runtime.get("game") == "connected" and runtime.get("heartbeat"):
            lines.append(heartbeat_text(runtime["heartbeat"]))
        if self.target_drive_tips.get(name):
            lines.append(self.target_drive_tips[name])
        return "\n".join(lines)

    def refresh_target_item(self, name):
        target = tes3x_targets.targets(self.local_values()).get(name)
        row = self.target_picker.findData(name)
        if target is None or row < 0:
            return
        self.target_picker.setItemIcon(row, dot_icon(self.target_colour(name, target)))
        tip = self.target_tooltip(name, target)
        self.target_picker.setItemData(row, tip, Qt.ItemDataRole.ToolTipRole)
        if row == self.target_picker.currentIndex():
            self.target_picker.setToolTip(tip)
            if hasattr(self, "targets_page"):
                self.targets_page.refresh()

    def targets_changed(self, selected=None):
        """The target list in tes3x.local.toml changed; select `selected` if it exists."""
        self.refresh_targets(selected)
        if selected and self.target_picker.currentData() == selected:
            self.target_activated()

    def show_target_setup(self):
        self.workspace_bar.setCurrentIndex(1)
        self.targets_page.tabs.setCurrentWidget(self.targets_page.setup)

    def target_activated(self, *_args):
        name = self.target_picker.currentData()
        config = self.local_config_path()
        if name and config.is_file():
            try:
                document = tomlkit.parse(config.read_text(encoding="utf-8"))
                document["default_target"] = name
                config.write_text(tomlkit.dumps(document), encoding="utf-8", newline="")
            except (OSError, tomlkit.exceptions.ParseError) as exc:
                self.error(f"Could not remember target: {exc}")
        self.target_selection_changed(probe=True)

    def target_selection_changed(self, probe=False):
        target = self.default_target()
        xbox = bool(target and target.get("kind") == "xbox")
        self.action_deploy.setEnabled(xbox and self.process is None)
        self.action_fetch.setEnabled(xbox and self.process is None)
        self.discard_after_deploy.setEnabled(xbox)
        if hasattr(self, "run_button"):
            self.sync_run_button()
        if target:
            self.target_picker.setToolTip(self.target_tooltip(target["name"], target))
        if hasattr(self, "play_menu"):
            self.refresh_play_menu()
        self.update_deploy_state()
        if hasattr(self.saves, "save_list"):
            self.saves.save_target_changed(probe)
        if hasattr(self, "targets_page"):
            self.targets_page.set_target(self.target_picker.currentData())
        if probe and xbox:
            self.play_controller.refresh_ftp_status()

    def xbox_destination(self):
        target = self.default_target("xbox")
        if not target:
            return None
        plain = dict(self.profile_plain or {})
        identity = dict(plain.get("profile", {}))
        folder = self.build.install_dir.text().strip()
        if folder:
            identity["install_dir"] = folder
            identity.pop("remote_root", None)
        plain["profile"] = identity
        try:
            return tes3x_targets.remote_root(plain, target)
        except tes3x_targets.TargetError:
            return None

    def local_path(self, key):
        value = self.local_values().get("paths", {}).get(key)
        if not value:
            return None
        path = Path(value)
        return path if path.is_absolute() else self.work_dir() / path

    def style_toolbar_buttons(self):
        """Stylesheets freeze a button's colours; set them again after a theme change."""
        for button in (self.run_button, self.play_button):
            button.setStyleSheet("")
            button.setStyleSheet("QToolButton { border: 1px solid palette(mid); "
                                 "color: palette(button-text); "
                                 "padding: 4px 8px; border-radius: 4px; }")
            button.style().unpolish(button)
            button.style().polish(button)

    def set_output_visible(self, visible, save=True):
        self.output.setVisible(visible)
        if save and self.settings is not None:
            self.settings.setValue("show_output", visible)

    def set_mod_column(self, column, visible):
        self.mods.mod_list.setColumnHidden(column, not visible)
        if self.settings is not None:
            hidden = [str(value) for value in range(1, self.mods.mod_list.columnCount())
                      if self.mods.mod_list.isColumnHidden(value)]
            self.settings.setValue("hidden_mod_columns", ",".join(hidden))

    def set_theme(self, key, save=True):
        scheme = {"light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}.get(
            key, Qt.ColorScheme.Unknown)
        QApplication.styleHints().setColorScheme(scheme)
        if hasattr(self, "play_button"):
            self.style_toolbar_buttons()
        if save and self.settings is not None:
            self.settings.setValue("theme", key)

    def set_channel(self, channel, shown):
        self.shown_channels.discard(channel)
        if shown:
            self.shown_channels.add(channel)
        self.developer_mode = "dev" in self.shown_channels
        if self.settings is not None:
            self.settings.setValue("shown_channels", ",".join(sorted(self.shown_channels)))
        self.patches.refresh_patch_states()

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
            self.server_page.shutdown()
            if self.play_controller.in_game_listener is not None:
                self.play_controller.in_game_listener.close()
                self.play_controller.in_game_listener = None
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
        self.mods.forget_analysis()
        self.mods.mod_list.clear()
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
        self.refresh_targets()
        self.play_controller.refresh_ftp_status()
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
        self.check_profile_sha = None
        self.check_failed = False
        self.build_failed = False
        self.deploy_failed = False
        self.deploy_records = {}
        self.document = document
        self.profile_plain = plain
        self.library_root = library_root
        self.catalog = catalog
        self.library_indexed = indexed
        self.mods.scans.clear()
        self.build.load(plain)
        self.saves.load_save_pool(plain)
        self.plugins.plugins_loading = True
        self.plugins.mlox_at_build.setChecked(plain.get("rules", {}).get("plugin_order") == "mlox")
        self.plugins.plugins_loading = False
        self.plugins.plugin_order = plain.get("plugins", {}).get("order") or None
        self.mods.populate_mods(plain.get("mods", []))
        self.patches.populate_patches(plain.get("patches", {}))
        vanilla = self.local_path("vanilla_root")
        self.ini.load(plain.get("ini", {}), vanilla / "Morrowind.ini" if vanilla else None)
        self.ini.set_patches(self.patches.applied_patches)
        self.sync_skip_intro()
        try:
            self.saved_text = self.profile_text()
        except (PipelineError, LibraryError, tomlkit.exceptions.ParseError):
            self.saved_text = None
        self.load_remembered_states()
        if self.settings is not None:
            self.settings.setValue("last_profile", str(self.profile_path))
        self.refresh_profile_list()
        self.statusBar().clearMessage()
        if library_root and not indexed:
            self.statusBar().showMessage(
                "The mod library has no library.toml, so mods are added by folder name", 8000)
        self.update_build_state()
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
        return root, catalog, indexed

    def library_changed(self):
        value = self.build.library()
        try:
            root, catalog, indexed = self.resolve_library(value, self.mods.profile_mods())
        except (OSError, tomllib.TOMLDecodeError, PipelineError, LibraryError) as exc:
            self.error(exc)
            return
        if root == self.library_root:
            return
        self.library_root, self.catalog, self.library_indexed = root, catalog, indexed
        self.mods.scans.clear()
        self.mods.populate_mods(self.mods.profile_mods())

    def build_changed(self):
        self.patches.refresh_patch_states()
        self.mods.schedule_analysis()

    def skip_intro_toggled(self, on):
        if on != self.ini.skips_intro():
            self.ini.set_skip_intro(on)

    def sync_skip_intro(self):
        self.build.skip_intro.blockSignals(True)
        self.build.skip_intro.setChecked(self.ini.skips_intro())
        self.build.skip_intro.blockSignals(False)

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
        self.mods.populate_mods(self.mods.profile_mods())
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

    def profile_text(self):
        """Write the editor state into the document and return it, validated."""
        mods = tomlkit.aot()
        for values in self.mods.profile_mods():
            table = tomlkit.table()
            for key in ("name", "id", "version", "components", "order", "enabled",
                        "optional", "plugins", "loose", "archives"):
                if key in values:
                    table.add(key, values[key])
            mods.append(table)
        if len(mods):
            self.document["mods"] = mods
        else:
            self.document.pop("mods", None)
        self.build.apply(self.document)
        self.saves.apply_save_pool(self.document)
        BuildSettings.put(self.document, "rules", "plugin_order",
                          "mlox" if self.plugins.mlox_at_build.isChecked() else "mods", "mods")
        order = None if self.plugins.mlox_at_build.isChecked() else self.plugins.plugin_order
        plugins = self.document.get("plugins")
        if order:
            if plugins is None:
                plugins = new_table(self.document, "plugins")
            if plugins.get("order") != order:
                listed = tomlkit.array()
                listed.extend(order)
                plugins["order"] = listed.multiline(True)
        elif plugins is not None:
            plugins.pop("order", None)
            if not plugins:
                self.document.pop("plugins")
        values = dict(self.ini.values)
        table = self.document.get("ini")
        if table is None and values:
            table = new_table(self.document, "ini")
        if table is not None:
            for key in [key for key in table if key not in values]:
                del table[key]
            for key, value in values.items():
                if table.get(key) != value:
                    table[key] = value
            drop_empty(self.document, "ini")
        patches = self.document.get("patches")
        if patches is None:
            patches = new_table(self.document, "patches")
        for key, value in self.patches.patch_configuration().items():
            # Leave unchanged values alone so their comments and layout survive.
            if isinstance(value, list) and not value and key not in patches:
                continue
            if isinstance(value, list) and set(patches.get(key, [])) == set(value):
                continue
            if patches.get(key) != value:
                patches[key] = value
        text = tomlkit.dumps(self.document).rstrip("\n") + "\n"
        validate_profile(tomllib.loads(text))
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
        self.update_build_state()
        return True

    def run_pipeline(self, extra):
        if self.process is not None:
            self.error("A TES3X command is already running")
            return
        requested = ("check" if "--check" in extra else
                     "deploy" if "--deploy" in extra else "build")
        if requested == "build" and any(
            s.process is not None for s in self.play_controller.plays.values()
        ):
            self.error("Stop xemu before replacing the build it is running")
            return
        if not self.save_profile():
            return
        self.command_kind = requested
        self.command_target = self.target_picker.currentData()
        self.start_command("pipeline", [
            str(self.profile_path),
            *(["--config", str(self.local_config_path())]
              if self.local_config_path().is_file() else []),
            *(["--target", self.target_picker.currentData()]
              if self.target_picker.currentData() else []),
            *extra,
        ], "Running TES3X pipeline…")

    def build_output(self):
        root = self.local_path("build_root") or self.work_dir() / "build"
        return root / self.profile_plain["profile"]["name"]

    @staticmethod
    def read_state(path, fallback):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, type(fallback)) else fallback
        except (OSError, ValueError):
            return fallback

    @staticmethod
    def write_state(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        staged = path.with_name(path.name + ".tmp")
        staged.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        staged.replace(path)

    def load_remembered_states(self):
        check = self.read_state(self.build_output() / CHECK_MARKER, {})
        self.check_profile_sha = check.get("profile_sha256")
        self.check_failed = bool(check and check.get("result") != "pass")
        self.deploy_records = self.read_state(self.build_output() / DEPLOYS_MARKER, {})

    def remember_check(self, passed):
        try:
            profile_sha = sha256_file(self.profile_path)
            self.write_state(self.build_output() / CHECK_MARKER, {
                "profile_sha256": profile_sha,
                "time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "result": "pass" if passed else "fail",
            })
            self.check_profile_sha = profile_sha
        except OSError:
            self.check_profile_sha = None
        self.check_failed = not passed

    def remember_deploy(self, target_name):
        if not target_name:
            return
        marker = self.build_output() / PIPELINE_MARKER
        try:
            record = {
                "profile_sha256": sha256_file(self.profile_path),
                "build_sha256": sha256_file(marker),
                "time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            records = self.read_state(self.build_output() / DEPLOYS_MARKER, {})
            records[target_name] = record
            self.write_state(self.build_output() / DEPLOYS_MARKER, records)
            self.deploy_records = records
        except OSError:
            return

    def build_status(self):
        """'built', 'stale' or 'missing', and why, for the saved profile's pipeline output."""
        if not self.profile_path:
            return "missing", "No profile"
        marker = self.build_output() / PIPELINE_MARKER
        try:
            record = json.loads(marker.read_text(encoding="utf-8"))
            current = record.get("profile_sha256") == sha256_file(self.profile_path)
        except (OSError, ValueError):
            return "missing", "Not built yet; Play builds it first"
        if not current or self.is_dirty():
            return "stale", "The profile changed since the last build; Play rebuilds it first"
        stamp = datetime.datetime.fromtimestamp(marker.stat().st_mtime)
        return "built", (f"Built {stamp:%Y-%m-%d %H:%M}. Changes to mod files since then are not "
                         "detected; Build to pick them up")

    def update_build_state(self):
        self.update_check_state()
        self.update_deploy_state()
        self.sync_run_button()
        if self.command_kind == "build":
            self.set_action_state(self.build_button, "Build", "running",
                                  "The profile is being built")
            return
        if self.build_failed:
            self.set_action_state(self.build_button, "Build", "failed",
                                  "The last build command failed; see the output")
            return
        state, tip = self.build_status()
        self.set_action_state(self.build_button, "Build",
                              {"built": "current", "stale": "stale",
                               "missing": "idle"}[state], tip)

    def update_build_label(self):
        state = self.build_button.property("state") or "idle"
        text = {"idle": "Not built", "current": "Build current", "stale": "Build out of date",
                "running": "Building…", "failed": "Build failed"}[state]
        colour = self.build_button.property("stateColour") or "#616161"
        self.build_state_label.setText(f'<span style="color:{colour}">●</span> {text}')
        self.build_state_label.setToolTip(self.build_button.toolTip())

    def set_action_state(self, action, label, state, tip):
        colours = {"idle": "#616161", "current": "#2e7d32", "stale": "#a15c00",
                   "running": "#a15c00", "failed": "#b3261e"}
        action.setIcon(dot_icon(colours[state]))
        action.setProperty("state", state)
        action.setProperty("stateColour", colours[state])
        action.setToolTip(tip)
        if action is getattr(self, "build_button", None):
            self.update_build_label()

    def run_default(self):
        """Deploy for an Xbox target, Build otherwise."""
        target = self.default_target()
        return self.action_deploy if target and target.get("kind") == "xbox" else self.action_build

    def sync_run_button(self):
        action = self.run_default()
        self.run_button.setText(action.text().replace("&", "").replace("…", "").split()[0])
        self.run_button.setIcon(action.icon())
        self.run_button.setToolTip(action.toolTip())
        self.run_button.setEnabled(action.isEnabled())

    def update_check_state(self):
        if not hasattr(self, "check_button"):
            return
        if self.command_kind == "check":
            state, tip = "running", "Checking the saved profile"
        elif self.check_failed:
            state, tip = "failed", "The last check failed; see the output"
        elif self.check_profile_sha is None:
            state, tip = "idle", "Run Check profile"
        else:
            try:
                current = sha256_file(self.profile_path) == self.check_profile_sha
            except OSError:
                current = False
            if current and not self.is_dirty():
                state, tip = ("current", "The saved profile passed Check. Changes inside mod "
                              "folders are not detected")
            else:
                state, tip = "stale", "The profile changed since Check"
        self.set_action_state(self.check_button, "Check", state, tip)

    def update_deploy_state(self):
        if not hasattr(self, "deploy_button"):
            return
        target = self.default_target()
        if not self.profile_path:
            state, tip = "idle", "Open a profile before deploying"
        elif not target or target.get("kind") != "xbox":
            state, tip = "idle", "Select an Xbox target to deploy"
        elif self.command_kind == "deploy":
            state, tip = "running", "Synchronizing the build to the Xbox"
        elif self.deploy_failed:
            state, tip = "failed", "The last deploy failed; see the output"
        else:
            record = self.deploy_records.get(target["name"], {})
            try:
                marker = self.build_output() / PIPELINE_MARKER
                current = (record.get("profile_sha256") == sha256_file(self.profile_path)
                           and record.get("build_sha256") == sha256_file(marker))
            except OSError:
                current = False
            if current and not self.is_dirty():
                when = record.get("time", "an earlier session")
                state = "current"
                tip = (f"This build was deployed to {target['name']} at {when}. Changes inside "
                       "mod folders are not detected")
            elif record:
                state, tip = "stale", f"The build deployed to {target['name']} is out of date"
            else:
                state, tip = "stale", f"This build has not been deployed to {target['name']}"
        self.set_action_state(self.deploy_button, "Deploy", state, tip)

    def refresh_play_menu(self):
        """Actions that apply to the target selected in the toolbar."""
        self.play_addons = {key: module for module in enabled_addons(self.local_values()).values()
                            for key in module.PLAY}
        target = self.default_target()
        xemu = bool(target and target.get("kind") == "xemu")
        xbox = bool(target and target.get("kind") == "xbox")
        self.play_menu.clear()
        gdb = self.play_menu.addAction("Debug with GDB", self.play_controller.set_play_gdb)
        gdb.setCheckable(True)
        gdb.setChecked(self.play_controller.play_gdb)
        gdb.setEnabled(xemu)
        gdb.setToolTip("Open xemu's GDB stub; the status bar shows the port to attach to")
        self.play_menu.addAction(self.action_smoke)
        launch = self.play_menu.addAction("Launch manager", self.play_controller.launch_manager)
        launch.setEnabled(self.process is None and self.play_controller.play_process is None and (
            xemu or (xbox and "xbox" in self.play_addons)))
        launch.setToolTip("Start the TES3X manager: on the Xbox through the dashboard agent, "
                          "or as an xemu session that keeps its own disk")
        name = target["name"] if target else "a target"
        if self.play_controller.play_process is not None:
            self.action_play.setText("&Stop")
            self.action_play.setIcon(self.stop_icon)
            self.play_button.setToolTip("Stop the xemu process started by this session")
            self.action_play.setEnabled(self.play_controller.play_pid is not None)
            self.action_stop.setEnabled(self.play_controller.play_pid is not None)
            self.action_build.setEnabled(False)
            return
        reason = self.play_controller.play_available()
        self.action_play.setText("&Play")
        self.action_play.setIcon(self.play_icon)
        self.play_button.setToolTip(reason or f"Play on {name} (F9)")
        self.action_play.setEnabled(not reason and self.process is None)
        self.action_stop.setEnabled(False)
        self.action_build.setEnabled(self.process is None)

    DEPLOY_QUESTION = ("Deploy over existing files?", "This deploy would write over:",
                       "Files in the game folder that the build does not have are deleted. "
                       "Deploy anyway?")

    PUSH_QUESTION = ("Replace saves?", "These saves are already on the Xbox:",
                     "Replace them with the copies from the PC?")

    def run_steps(self, steps, first=True, then=None):
        """Run commands one after another, stopping at the first that fails; `then` runs after
        the last one succeeds."""
        script, arguments, message = steps[0]
        if script == "deploy":
            self.command_kind = "deploy"
            self.command_target = self.target_picker.currentData()
        self.start_command(script, arguments, message, clear=first)
        question = {"deploy": self.DEPLOY_QUESTION,
                    "saves": self.PUSH_QUESTION}.get(script)
        if question and "--replace" not in arguments:
            self.conflict_retry = (lambda: self.run_steps(
                [(script, [*arguments, "--replace"], message), *steps[1:]], False, then),
                question)
        if script == "deploy" and "--ignore-space" not in arguments:
            self.space_retry = lambda: self.run_steps(
                [(script, [*arguments, "--ignore-space"], message), *steps[1:]], False, then)
        if len(steps) > 1:
            self.after_command = lambda: self.run_steps(steps[1:], False, then)
        elif then:
            self.after_command = then

    def run_smoke_test(self):
        if self.process is not None:
            self.error("A TES3X command is already running")
            return
        if not self.save_profile():
            return
        self.start_command("test", [
            str(self.profile_path),
            *(["--config", str(self.local_config_path())]
              if self.local_config_path().is_file() else []),
            "--record",
        ], "Running profile smoke test…")

    def deploy_profile(self):
        if self.process is not None:
            self.error("A TES3X command is already running")
            return
        overlay = self.build.install_layout.currentData() == "overlay"
        target = self.default_target("xbox") or {}
        retail = target.get("retail_root")
        overlay_note = (f"\n\nThe shared retail base at {retail or '<not configured>'} will be "
                        "installed or synchronized first." if overlay else "")
        manager = self.play_controller.manager_paired(target.get("name"))
        manager_note = ("\n\nThe files go through the TES3X manager running on the Xbox."
                        if manager else "")
        answer = QMessageBox.question(
            self, "Deploy profile",
            "Build this profile and synchronize it to the configured Xbox destination?\n\n"
            "Files absent from the build are removed from that destination. Uploaded files are "
            "verified by size before the command succeeds." + overlay_note + manager_note)
        if answer != QMessageBox.StandardButton.Yes:
            return
        arguments = ["--deploy", "--verify-deploy", "size"]
        if overlay:
            arguments.append("--install-retail-base")
        if self.discard_after_deploy.isChecked():
            arguments.append("--discard-build")
        self.deploy_agent = manager
        if manager:
            arguments.append("--deploy-agent")
        self.run_pipeline(arguments)
        if self.process is not None:
            if manager:
                self.play_controller.lend_listener()
            self.conflict_retry = (self.deploy_built, self.DEPLOY_QUESTION)
            self.space_retry = lambda: self.deploy_built("--ignore-space")

    def deploy_built(self, *extra):
        """Deploy the finished build with --replace, after a conflict or a shortage of space
        stopped the pipeline's."""
        target = self.default_target("xbox") or {}
        remote = self.xbox_destination()
        if not remote:
            self.error("Configure an Xbox target before deploying")
            return
        arguments = [str(self.build_output() / "deploy"), "--remote", remote,
                     "--config", str(self.local_config_path()), "--verify", "size", "--replace",
                     *extra]
        if target.get("name"):
            arguments += ["--target", target["name"]]
        if self.profile_plain.get("rules", {}).get("clear_cache_partitions", False):
            arguments.append("--clear-cache")
        if (self.build_output() / "console.ini").is_file():
            arguments += ["--console-ini", str(self.build_output() / "console.ini")]
        # The manager re-pairs with the GUI only seconds after the first attempt ends.
        if self.deploy_agent or self.play_controller.manager_paired(target.get("name")):
            arguments.append("--agent")
            self.play_controller.lend_listener()
        self.run_steps([("deploy", arguments,
                         f"Deploying to {remote}…")], first=False)
        if self.discard_after_deploy.isChecked():
            self.after_command = lambda: shutil.rmtree(self.build_output(), ignore_errors=True)

    MANAGER_QUESTION = ("Install over existing files?", "The manager's folder holds:",
                        "Files there that the manager does not have are deleted. "
                        "Install anyway?")

    def install_manager(self):
        if self.process is not None:
            self.error("A TES3X command is already running")
            return
        target = self.default_target("xbox")
        if not target:
            self.error("Configure an Xbox target before installing the manager")
            return
        name = target["name"]
        manager = self.play_controller.manager_paired(name)
        answer = QMessageBox.question(
            self, "Install console manager",
            f"Install or update the TES3X console manager on {name}, in "
            f"{target.get('games_root') or '<games_root>'}/TES3XManager?\n\n"
            + ("The files go through the manager running on the Xbox; start the new version "
               "from the dashboard or the manager's build list afterwards." if manager else
               "The files go over the dashboard's FTP server."))
        if answer != QMessageBox.StandardButton.Yes:
            return
        out = self.work_dir() / "build" / "manager" / "install"
        config = ["--config", str(self.local_config_path()), "--target", name]
        self.run_steps([("manager", ["stage", str(out), *config],
                         "Preparing the console manager…")],
                       then=lambda: self.deploy_manager(out, name, manager))

    def deploy_manager(self, out, name, agent, *extra):
        stage = Path(out)
        try:
            record = json.loads((stage / PIPELINE_MARKER).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.error(f"The manager was not staged: {exc}")
            return
        remote, version = record["remote"], record.get("version")
        arguments = [str(stage / "deploy"), "--remote", remote, "--verify", "size",
                     "--console-ini", str(stage / "console.ini"),
                     "--config", str(self.local_config_path()), "--target", name, *extra]
        if agent:
            arguments.append("--agent")
            self.play_controller.lend_listener()
        self.run_steps([("deploy", arguments,
                         f"Installing manager {version} to {remote}…")], first=False)
        self.command_kind = "manager"
        if "--replace" not in extra:
            self.conflict_retry = (lambda: self.deploy_manager(out, name, agent, *extra,
                                                               "--replace"),
                                   self.MANAGER_QUESTION)
        if "--ignore-space" not in extra:
            self.space_retry = lambda: self.deploy_manager(out, name, agent, *extra,
                                                           "--ignore-space")

    def pull_logs(self):
        if self.process is not None:
            self.error("A TES3X command is already running")
            return
        name = self.target_picker.currentData()
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        destination = self.work_dir() / "build" / "xbox-logs" / (name or "xbox") / timestamp
        features = self.target_features(name)
        if "ftp" not in features and "agent_fetch" in features:
            self.play_controller.agent_fetch(name, "E:\\tes3xlog.txt", destination / "tes3xlog.txt")
            self.write_pull_record(destination, name)
            return
        self.write_pull_record(destination, name)
        config = self.local_config_path()
        self.start_command("fetch", [
            "E:/tes3x*", "--out", str(destination),
            *(["--config", str(config)] if config.is_file() else []),
            *(["--target", self.target_picker.currentData()]
              if self.target_picker.currentData() else []),
        ], f"Pulling Xbox logs to {destination}…")

    def write_pull_record(self, destination, name):
        """What a pulled log folder came from, for the Targets page's log catalog."""
        try:
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "pull.json").write_text(json.dumps({
                "target": name, "profile": self.profile_name(),
                "time": datetime.datetime.now().isoformat(timespec="seconds")},
                indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            self.error(f"Could not record the pull: {exc}")

    def start_command(self, program, arguments, message, environment=None, clear=True):
        if clear:
            self.output.clear()
        process = QProcess(self)
        process.setWorkingDirectory(str(self.work_dir()))
        if environment is not None:
            process.setProcessEnvironment(environment)
        process.setProgram(sys.executable)
        process.setArguments(launch(program, arguments))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self.append_process_output)
        process.finished.connect(self.command_finished)
        self.process = process
        self.target_picker.setEnabled(False)
        self.target_actions.setEnabled(False)
        for action in self.command_actions:
            if action is not self.action_play or self.play_controller.play_process is None:
                action.setEnabled(False)
        self.statusBar().spinner.start()
        self.update_build_state()
        self.targets_page.refresh()
        process.start()
        self.statusBar().showMessage(message)

    def append_process_output(self):
        if self.process is None:
            return
        text = bytes(self.process.readAllStandardOutput()).decode(errors="replace")
        shown = text.replace("\r\n", "\n").replace("\r", "\n")
        self.output.moveCursor(QTextCursor.MoveOperation.End)
        self.output.insertPlainText(shown)
        if XEMU_STARTED in text:
            self.statusBar().spinner.stop()
            port = (
                self.play_controller.play_run / "gdb.port"
                if self.play_controller.play_gdb and self.play_controller.play_run
                else None
            )
            if port is not None and port.is_file():
                port = port.read_text().strip()
                attach = f'gdb -ex "target remote 127.0.0.1:{port}"'
                self.output.insertPlainText(f"\nGDB stub on 127.0.0.1:{port}; attach with "
                                            f"{attach}\n")
                self.statusBar().showMessage(f"Playing · GDB :{port}")
            else:
                self.statusBar().showMessage("Playing in xemu")

    def command_finished(self, code, _status):
        self.append_process_output()
        self.statusBar().showMessage(f"TES3X exited {code}", 5000)
        kind, self.command_kind = self.command_kind, None
        target, self.command_target = self.command_target, None
        if kind == "check":
            self.remember_check(code == 0)
        elif kind == "build":
            self.build_failed = code != 0
        elif kind == "deploy":
            self.deploy_failed = code != 0
            if code == 0:
                self.remember_deploy(target)
        self.process = None
        self.statusBar().spinner.stop()
        self.target_picker.setEnabled(True)
        self.target_actions.setEnabled(True)
        for action in self.command_actions:
            action.setEnabled(True)
        self.target_selection_changed(probe=False)
        self.update_build_state()
        follow, self.after_command = self.after_command, None
        retry, self.conflict_retry = self.conflict_retry, None
        space, self.space_retry = self.space_retry, None
        self.play_controller.restore_listener()
        self.saves.saves_selected()
        if code == DEPLOY_NO_SPACE and space:
            if kind == "build":
                self.build_failed = False
            elif kind == "deploy":
                self.deploy_failed = False
            self.update_build_state()
            self.confirm_space(space)
            return
        if code == DEPLOY_CONFLICT and retry:
            if kind == "build":
                self.build_failed = False
            elif kind == "deploy":
                self.deploy_failed = False
            self.update_build_state()
            self.confirm_replace(*retry)
            return
        if follow and code == 0:
            follow()

    def confirm_space(self, retry):
        """The Xbox's drive looked too small for the deploy; ask before trying anyway."""
        lines = [line.strip() for line in self.output.toPlainText().splitlines()
                 if line.strip().startswith(("space:", "nothing changed:"))]
        answer = QMessageBox.warning(
            self, "Not enough space on the Xbox",
            "\n".join(lines[-2:]) + "\n\nThe deploy may fail partway and leave the game folder "
            "incomplete. Deploy anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            retry()

    def confirm_replace(self, retry, question):
        """A command stopped rather than overwrite something; ask before repeating it."""
        title, lead, ask = question
        lines = [line.split("conflict: ", 1)[1] for line in self.output.toPlainText().splitlines()
                 if "conflict: " in line]
        answer = QMessageBox.warning(
            self, title, lead + "\n\n" + "\n".join(f"• {line}" for line in lines)
            + "\n\n" + ask,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            retry()


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
