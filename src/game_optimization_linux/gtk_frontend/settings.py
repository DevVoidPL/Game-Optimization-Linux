"""Preferences UI sharing the existing settings controller API."""

from gi.repository import Adw

from .widgets import choice


class SettingsWindow(Adw.PreferencesWindow):
    def __init__(self, parent, controller):
        super().__init__(transient_for=parent, modal=True, title=parent.tr("SettingsPage", "Settings"))
        self.set_default_size(700, 600)
        page = Adw.PreferencesPage(name="desktop")
        self.page = page
        self.add(page)
        appearance = Adw.PreferencesGroup(title=parent.tr("SettingsPage", "Appearance"))
        page.add(appearance)
        choice(appearance, parent.tr("SettingsPage", "Theme"), {"system": parent.tr("SettingsPage", "System"), "light": parent.tr("SettingsPage", "Light"), "dark": parent.tr("SettingsPage", "Dark")}, controller.themeMode, lambda value: controller.saveSetting("theme", value))
        choice(appearance, parent.tr("SettingsPage", "Language"), {"en": "English", "pl": "Polski", "es": "Español"}, controller.settings.get("language", "en"), lambda value: controller.saveSetting("language", value))
        # TODO: migrate the remaining Desktop settings, including path pickers.