"""Six native per-game pages using the unmodified controller facade.

Advanced write flows remain explicitly unavailable until their original
confirmation, conflict and rollback UX has been migrated and tested.
"""

import json

from gi.repository import Adw, GLib, Gtk

from .widgets import action, choice, confirm, report, spin, switch


class GameDetails(Gtk.Box):
    def __init__(self, window, controller):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.window = window
        self.controller = controller
        self.stack = Adw.ViewStack(vexpand=True)
        self.switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        self.append(self.switcher)
        self.append(self.stack)
        self.game_id = ""
        self.profile = {}
        self.mango = {}

    def group(self, key, title):
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title=GLib.markup_escape_text(title))
        page.add(group)
        self.stack.add_titled(page, key, title)
        return group

    def t(self, context, text):
        return self.window.tr(context, text)

    def call(self, method, *args):
        result = getattr(self.controller, method)(self.game_id, *args)
        if isinstance(result, dict) and result.get("success") is False:
            self.window.toast(str(result.get("error", "Operation failed")))
        return result

    def refresh(self):
        selected = self.stack.get_visible_child_name()
        while self.stack.get_first_child() is not None:
            self.stack.remove(self.stack.get_first_child())
        game = self.controller.selectedGame
        self.game_id = str(game.get("id", ""))
        if not self.game_id:
            return
        capabilities = self.controller.getGameCapabilities(self.game_id)
        compression = self.group("compression", self.t("GameDetailsPage", "Compression"))
        report(compression, self.t("GameDetailsPage", "Overview"), game)
        action(compression, self.t("StorageTab", "Analyze"), lambda: self.call("analyzeGame"))
        action(compression, self.t("StorageTab", "Verify"), lambda: self.call("verifyCompression"))
        self.compression_mode = "Auto"
        choice(compression, self.t("StorageTab", "Profile"), {key: key for key in ("Auto", "Fast", "Balanced", "Maximum")}, "Auto", lambda value: setattr(self, "compression_mode", value))
        action(compression, self.t("StorageTab", "Compress"), self.prepare_compression)

        optimization = self.group("optimization", self.t("GameDetailsPage", "Optimization"))
        self.profile = self.call("getOptimizationProfile")
        self.optimization_changes = {}
        report(optimization, self.t("GameDetailsPage", "Overview"), self.profile)
        if self.profile.get("success") is not False:
            switch(optimization, "GameMode", self.profile.get("gamemodeEnabled", False), lambda value: self.optimization_changes.update(gamemodeEnabled=value))
            spin(optimization, self.t("OptimizationTab", "Target FPS"), self.profile.get("targetFps", 60), 1, 1000, lambda value: self.optimization_changes.update(targetFps=value, targetFpsMode="manual"))
            action(optimization, self.t("MangoHudTab", "Apply"), self.save_optimization)
        report(optimization, "Capabilities", capabilities)
        report(optimization, "Components", self.call("getLauncherComponents"))
        action(optimization, self.t("OptimizationTab", "Install components"), lambda: confirm(self.window, self.t("OptimizationTab", "Install components"), self.t("OptimizationTab", "Install missing components for this launcher?"), lambda: self.call("repairLauncherComponents")))
        self.todo(optimization, "Advanced Gamescope, Proton tweaks, custom launch and automatic optimization")

        scaling = self.group("upscaling", self.t("GameDetailsPage", "Upscaling & effects"))
        report(scaling, "OptiScaler", self.call("getOptiScalerStatus"))
        report(scaling, "Frame generation", self.call("frameGenerationStatus"))
        report(scaling, "Post-processing", self.call("postProcessingStatus"))
        self.todo(scaling, "OptiScaler install/remove, executable selection, anti-cheat/conflict confirmation, effects and LUT/preset imports")

        mangohud = self.group("mangohud", "MangoHud")
        self.mango = self.call("getMangoHudProfile")
        self.mango_changes = {}
        report(mangohud, self.t("GameDetailsPage", "Overview"), self.mango)
        if self.mango.get("success"):
            switch(mangohud, "MangoHud", self.mango.get("enabled", False), lambda value: self.mango_changes.update(enabled=value, preset="custom"))
            spin(mangohud, self.t("MangoHudTab", "Font size"), self.mango.get("fontSize", 24), 6, 100, lambda value: self.mango_changes.update(fontSize=value))
            action(mangohud, self.t("MangoHudTab", "Apply"), self.save_mangohud)
        self.todo(mangohud, "HUD preview, metrics, colors, graphs, presets and FPS ownership editor")

        measurements = self.group("measurements", self.t("GameDetailsPage", "Performance analysis"))
        report(measurements, self.t("GameDetailsPage", "Overview"), self.call("getMeasuredOptimization"))
        action(measurements, self.t("StorageTab", "Measure exact disk usage"), lambda: self.call("exactCompressionMeasurement"))
        self.todo(measurements, "Baseline/comparison recording, log import, candidate evaluation and deep cleanup/recompression")

        backups = self.group("backups", self.t("SettingsPage", "Backups"))
        for backup in self.controller.backups:
            if str(backup.get("gameId", "")) != self.game_id:
                continue
            row = Adw.ActionRow(title=str(backup.get("operation", "Backup")), subtitle=str(backup.get("date", "")))
            from .widgets import button
            row.add_suffix(button(self.t("GameDetailsPage", "Restore"), lambda identifier=str(backup["id"]), description=str(backup): confirm(self.window, self.t("GameDetailsPage", "Restore"), description, lambda: self.controller.restoreBackup(identifier))))
            backups.add(row)
        self.todo(backups, "Backup creation/export and OptiScaler/deep-quarantine restore workflows; list may be empty when backend backup service is unavailable")
        if selected:
            self.stack.set_visible_child_name(selected)

    def todo(self, group, text):
        banner = Adw.Banner(title=f"TODO: {text}", revealed=True)
        group.add(banner)

    def prepare_compression(self):
        plan = self.call("prepareCompression", self.compression_mode, False)
        if not plan.get("eligible"):
            self.window.toast(str(plan.get("blockers") or plan.get("error") or "Compression blocked"))
            return
        # Display the entire backend plan; never bypass its eligibility checks.
        identifier = str(plan.get("id", ""))
        if not identifier:
            self.window.toast("TODO: compression plan has no validated identifier")
            return
        confirm(self.window, self.t("StorageTab", "Compress"), json.dumps(plan, ensure_ascii=False, indent=2, default=str), lambda: self.controller.startCompression(identifier))

    def save_optimization(self):
        payload = dict(self.optimization_changes)
        result = self.call("previewOptimizationProfile", payload)
        if result.get("success") is not False:
            identifier = self.game_id
            confirm(self.window, self.t("MangoHudTab", "Apply"), json.dumps(result, ensure_ascii=False, indent=2, default=str), lambda: self.controller.saveOptimizationProfile(identifier, payload))

    def save_mangohud(self):
        payload = dict(self.mango_changes)
        result = self.call("previewMangoHudProfile", payload)
        if result.get("success"):
            identifier = self.game_id
            confirm(self.window, self.t("MangoHudTab", "Apply"), json.dumps(result, ensure_ascii=False, indent=2, default=str), lambda: self.controller.saveMangoHudProfile(identifier, payload))