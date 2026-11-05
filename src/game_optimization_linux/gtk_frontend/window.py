"""Desktop window, game list, navigation and Qt signal connections."""

from gi.repository import Adw, Gdk, Gio, GObject, Gtk

from .details import GameDetails
from .settings import SettingsWindow
from .widgets import Translator, button, confirm


class GameItem(GObject.Object):
    def __init__(self, data):
        super().__init__()
        self.data = data


class DesktopWindow(Adw.ApplicationWindow):
    def __init__(self, application, controller):
        super().__init__(application=application, title=controller.appName, default_width=1360, default_height=850)
        self.controller = controller
        self.tr = Translator(controller.settings.get("language", "en"))
        self.connections = []
        self.preferences = None
        self.task_window = None
        self.closing_confirmed = False
        self.overlay = Adw.ToastOverlay()
        self.set_content(self.overlay)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.overlay.set_child(root)
        header = Adw.HeaderBar()
        root.append(header)
        self.flap = Adw.Flap()
        self.flap.set_fold_policy(Adw.FlapFoldPolicy.AUTO)
        self.flap.set_modal(False)
        header.pack_start(button("☰", lambda: self.flap.set_reveal_flap(not self.flap.get_reveal_flap())))
        header.pack_end(button(self.tr("SettingsPage", "Settings"), self.show_settings))
        header.pack_end(button(self.tr("TasksPage", "Tasks"), self.show_tasks))
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, width_request=280)
        self.search = Gtk.SearchEntry()
        self.search.connect("search-changed", lambda _widget: self.refresh_games())
        sidebar.append(self.search)
        sidebar.append(button(self.tr("GamesPage", "Refresh"), controller.refreshGames))
        self.game_store = Gio.ListStore.new(GameItem)
        self.selection = Gtk.SingleSelection(model=self.game_store, autoselect=False)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self.setup_row)
        factory.connect("bind", self.bind_row)
        self.list_view = Gtk.ListView(model=self.selection, factory=factory, single_click_activate=True)
        self.list_view.connect("activate", lambda _view, index: self.open_game(str(self.game_store.get_item(index).data["id"])))
        scroll = Gtk.ScrolledWindow(vexpand=True, child=self.list_view)
        sidebar.append(scroll)
        self.flap.set_flap(sidebar)
        self.flap.set_separator(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        self.details = GameDetails(self, controller)
        self.flap.set_content(self.details)
        self.flap.set_vexpand(True)
        root.append(Adw.Banner(title="GTK Desktop preview - TODO controls remain in QML; no QML files are scheduled for deletion.", revealed=True))
        root.append(self.flap)
        for signal, callback in ((controller.gamesChanged, self.refresh_games), (controller.tasksChanged, self.refresh_tasks), (controller.selectedGameChanged, self.details.refresh), (controller.settingsChanged, self.settings_changed), (controller.toastRequested, self.toast), (controller.windowActionRequested, self.window_action)):
            signal.connect(callback)
            self.connections.append((signal, callback))
        self.connect("close-request", self.close_requested)
        self.connect("destroy", self.disconnect_backend)
        self.settings_changed()
        self.refresh_games()

    def setup_row(self, _factory, item):
        label = Gtk.Label(xalign=0, wrap=True, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        item.set_child(label)
        gesture = Gtk.GestureClick(button=3)
        gesture.connect("pressed", lambda _gesture, _count, x, y: self.context_menu(item, x, y))
        label.add_controller(gesture)

    def bind_row(self, _factory, item):
        data = item.get_item().data
        item.get_child().set_text(str(data.get("name") or data.get("title") or data["id"]))

    def context_menu(self, item, x, y):
        if item.get_item() is None:
            return
        data = item.get_item().data
        actions = Gio.SimpleActionGroup()
        menu = Gio.Menu()
        for key, label, callback in (("details", self.tr("GameDetailsPage", "Overview"), lambda: self.open_game(str(data["id"]))), ("launch", self.tr("GameDetailsPage", "Launch"), lambda: self.controller.launchGame(str(data["id"])) )):
            action = Gio.SimpleAction.new(key, None)
            action.connect("activate", lambda _action, _value, cb=callback: cb())
            actions.add_action(action)
            menu.append(label, f"game.{key}")
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.insert_action_group("game", actions)
        popover.set_parent(item.get_child())
        popover.set_pointing_to(Gdk.Rectangle(x=int(x), y=int(y), width=1, height=1))
        popover.connect("closed", lambda widget: widget.unparent())
        popover.popup()

    def refresh_games(self):
        query = self.search.get_text().casefold()
        games = [GameItem(game) for game in self.controller.games if query in str(game.get("name") or game.get("title") or "").casefold()]
        self.game_store.splice(0, self.game_store.get_n_items(), games)

    def open_game(self, identifier):
        self.controller.openGame(identifier)

    def settings_changed(self):
        self.tr = Translator(self.controller.settings.get("language", "en"))
        modes = {"light": Adw.ColorScheme.FORCE_LIGHT, "dark": Adw.ColorScheme.FORCE_DARK}
        Adw.StyleManager.get_default().set_color_scheme(modes.get(self.controller.themeMode, Adw.ColorScheme.DEFAULT))
        # Rebuild per-game labels with the newly selected catalog.
        if self.details.game_id:
            self.details.refresh()
        # TODO: retranslate the open preferences window and window header live.

    def show_settings(self):
        if self.preferences is None:
            self.preferences = SettingsWindow(self, self.controller)
            self.preferences.connect("close-request", self.preferences_closed)
        self.preferences.present()

    def preferences_closed(self, _window):
        self.preferences = None
        return False

    def show_tasks(self):
        if self.task_window is None:
            self.task_window = Adw.PreferencesWindow(transient_for=self, title=self.tr("TasksPage", "Tasks"), default_width=700, default_height=600)
            self.task_page = Adw.PreferencesPage()
            self.task_group = Adw.PreferencesGroup()
            self.task_page.add(self.task_group)
            self.task_window.add(self.task_page)
            self.task_window.connect("destroy", self.tasks_closed)
        self.refresh_tasks()
        self.task_window.present()
        return self.task_window

    def refresh_tasks(self):
        if self.task_window is None:
            return
        self.task_page.remove(self.task_group)
        self.task_group = Adw.PreferencesGroup()
        self.task_page.add(self.task_group)
        for task in self.controller.tasks:
            row = Adw.ActionRow(title=str(task.get("name", "Task")), subtitle=f"{task.get('status', '')} · {task.get('progress', 0)}%")
            if str(task.get("status", "")).casefold() not in {"completed", "failed", "cancelled", "interrupted"}:
                row.add_suffix(button(self.tr("ConfirmDialog", "Cancel"), lambda identifier=str(task["id"]): self.controller.cancelTask(identifier)))
            self.task_group.add(row)

    def tasks_closed(self, *_args):
        self.task_window = None

    def toast(self, message, _tone="info"):
        self.overlay.add_toast(Adw.Toast.new(str(message)))

    def window_action(self, action):
        if action == "close":
            self.close()
        elif action == "minimize":
            self.minimize()

    def close_requested(self, _window):
        if self.controller.hasActiveCompressionTasks and not self.closing_confirmed:
            def accepted():
                self.closing_confirmed = True
                self.close()
            confirm(self, self.tr("Main", "Compression is still running"), self.tr("Main", "Closing now will cancel the active compression and verify its state on the next start. You can return to the application or cancel the task and close."), accepted)
            return True
        self.disconnect_backend()
        if self.preferences is not None:
            self.preferences.destroy()
            self.preferences = None
        if self.task_window is not None:
            self.task_window.destroy()
        return False

    def disconnect_backend(self, *_args):
        for signal, callback in self.connections:
            signal.disconnect(callback)
        self.connections.clear()