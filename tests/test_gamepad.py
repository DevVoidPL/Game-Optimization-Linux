from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication

from game_optimization_linux.controllers import AppController, CouchNavigationController
from game_optimization_linux.models import (
    AppSettings,
    ControllerMode,
    GamepadAction,
    GamepadDevice,
    GamepadEvent,
    GamepadType,
    PostLaunchBehavior,
    button_hints,
)
from game_optimization_linux.providers import DemoGameProvider, FakeGamepadProvider, UnavailableGamepadProvider
from game_optimization_linux.services import GamepadInputMapper, GamepadService, SettingsStore, UiSoundService


_APPLICATION = QCoreApplication.instance() or QCoreApplication([])


def test_gamepad_mapper_deadzone_repeat_debounce_and_swap() -> None:
    mapper = GamepadInputMapper(deadzone=0.25, repeat_delay_ms=300, repeat_rate_ms=100, swap_accept_back=True)
    assert mapper.process(GamepadEvent("axis", 1, "left_x", True, 0.20), now=1.0) == ()
    assert mapper.process(GamepadEvent("axis", 1, "left_x", True, 0.80), now=1.1) == (GamepadAction.NAVIGATE_RIGHT,)
    assert mapper.poll_repeats(now=1.39) == ()
    assert mapper.poll_repeats(now=1.41) == (GamepadAction.NAVIGATE_RIGHT,)
    assert mapper.process(GamepadEvent("axis", 1, "left_x", False, 0.0), now=1.5) == ()
    assert mapper.poll_repeats(now=2.0) == ()

    south = GamepadEvent("button", 1, "south", True, 1.0)
    assert mapper.process(south, now=3.0) == (GamepadAction.BACK,)
    assert mapper.process(south, now=3.02) == ()
    assert mapper.process(GamepadEvent("button", 1, "east", True, 1.0), now=3.2) == (GamepadAction.CONFIRM,)


def test_gamepad_service_hotplug_active_device_and_safe_disconnect() -> None:
    first = GamepadDevice(
        7,
        "DualSense",
        GamepadType.PLAYSTATION,
        "Mapped",
        82,
        guid="030000004c050000e60c000000016800",
        vendor_id=0x054C,
        product_id=0x0CE6,
    )
    provider = FakeGamepadProvider((first,))
    service = GamepadService(provider)
    actions: list[str] = []
    connected: list[object] = []
    disconnected: list[str] = []
    service.actionTriggered.connect(actions.append)
    service.controllerConnected.connect(connected.append)
    service.controllerDisconnected.connect(disconnected.append)
    try:
        service.start()
        assert service.available is True
        assert service.controllerCount == 1
        assert service.controllers[0]["batteryPercent"] == 82
        assert service.controllers[0]["guid"].startswith("03000000")
        assert service.controllers[0]["vendorId"] == 0x054C

        provider.emit(GamepadEvent("button", 7, "south", True, 1.0))
        service.pollNow()
        assert actions == ["Confirm"]
        assert service.activeController["name"] == "DualSense"
        assert service.buttonHints["accept"] == "Cross"

        second = GamepadDevice(8, "Xbox Wireless Controller", GamepadType.XBOX)
        provider.emit(GamepadEvent("connected", 8), second)
        service.pollNow()
        assert service.controllerCount == 2
        assert connected[-1]["name"] == second.name

        provider.emit(GamepadEvent("button", 8, "north", True, 1.0))
        service.pollNow()
        assert service.activeController["name"] == second.name
        assert actions[-1] == "ContextMenu"

        remapped = GamepadDevice(8, second.name, GamepadType.XBOX, "Updated mapping")
        provider.emit(GamepadEvent("remapped", 8), remapped)
        service.pollNow()
        assert service.controllers[-1]["mappingStatus"] == "Updated mapping"

        provider.emit(GamepadEvent("disconnected", 8))
        service.pollNow()
        assert service.controllerCount == 1
        assert service.activeController == {}
        assert disconnected == ["Xbox Wireless Controller"]
    finally:
        service.stop()
    assert provider.closed is True


def test_missing_sdl_provider_keeps_service_operational() -> None:
    service = GamepadService(UnavailableGamepadProvider("fixture"))
    try:
        service.start()
        service.pollNow()
        assert service.available is False
        assert service.status == "Missing"
        assert service.controllers == []
        assert service.diagnostics["sdl3LibraryAvailable"] is False
        assert service.diagnostics["gamepadCount"] == 0
    finally:
        service.stop()


def test_controller_diagnostics_do_not_treat_loaded_sdl_as_a_gamepad() -> None:
    service = GamepadService(FakeGamepadProvider())
    try:
        service.start()
        assert service.available is True
        assert service.controllerCount == 0
        assert service.diagnostics == {
            "sdl3LibraryAvailable": True,
            "inputDeviceAccessAvailable": True,
            "joystickCount": 0,
            "gamepadCount": 0,
            "reason": "No joystick or gamepad is connected",
        }
    finally:
        service.stop()


def test_flatpak_manifest_exposes_only_dedicated_input_devices() -> None:
    manifest = (
        Path(__file__).resolve().parents[1]
        / "flatpak/io.github.DevVoidPL.GameOptimizationLinux.yml"
    ).read_text(encoding="utf-8")

    assert "--device=input" in manifest
    assert "--device=all" not in manifest


def test_contextual_hints_cover_controller_families_and_unknown_devices() -> None:
    assert button_hints(GamepadType.XBOX)["search"] == "Y"
    assert button_hints(GamepadType.PLAYSTATION)["accept"] == "Cross"
    assert button_hints(GamepadType.PLAYSTATION)["search"] == "Triangle"
    assert button_hints(GamepadType.NINTENDO)["search"] == "X"
    assert button_hints(GamepadType.UNKNOWN)["accept"] == "South"


def test_interface_sounds_are_opt_in_and_use_no_files() -> None:
    played: list[str] = []
    service = UiSoundService(player=played.append)
    assert service.play("navigate") is False
    assert played == []
    service.set_enabled(True)
    assert service.play("accept") is True
    assert played == ["accept"]


class _FakeAudioSink:
    def __init__(self) -> None:
        self.stopped = False
        self.deleted = False

    def stop(self) -> None:
        self.stopped = True

    def deleteLater(self) -> None:
        self.deleted = True


class _FakeAudioBuffer:
    def __init__(self) -> None:
        self.closed = False
        self.deleted = False

    def close(self) -> None:
        self.closed = True

    def deleteLater(self) -> None:
        self.deleted = True


def _attach_fake_audio(service: UiSoundService) -> tuple[_FakeAudioSink, _FakeAudioBuffer]:
    sink = _FakeAudioSink()
    buffer = _FakeAudioBuffer()
    service._active.append((sink, buffer))
    return sink, buffer


def test_disabling_interface_sounds_stops_and_releases_active_audio() -> None:
    service = UiSoundService()
    service.set_enabled(True)
    sink, buffer = _attach_fake_audio(service)

    service.set_enabled(False)

    assert service._active == []
    assert sink.stopped is True
    assert sink.deleted is True
    assert buffer.closed is True
    assert buffer.deleted is True
    assert service.play("navigate") is False


def test_saved_interface_sounds_true_cannot_play_in_couch_mode(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "couch-audio.json")
    store.save(
        AppSettings(
            controller_mode=ControllerMode.COUCH_ONLY,
            interface_sounds=True,
        )
    )
    controller = AppController(
        game_provider=DemoGameProvider(),
        settings_store=store,
        gamepad_service=GamepadService(FakeGamepadProvider()),
        auto_refresh=False,
    )
    played: list[str] = []
    try:
        controller._ui_sound_service._player = played.append
        assert controller.interfaceMode == "couch"
        assert controller.settings["interfaceSounds"] is True

        assert controller.playUiSound("navigate") is False
        assert played == []
    finally:
        controller.shutdown()


def test_entering_couch_mode_stops_active_interface_audio(tmp_path: Path) -> None:
    controller = AppController(
        game_provider=DemoGameProvider(),
        settings_store=SettingsStore(tmp_path / "desktop-audio.json"),
        gamepad_service=GamepadService(FakeGamepadProvider()),
        initial_interface_mode="desktop",
        auto_refresh=False,
    )
    try:
        controller._ui_sound_service.set_enabled(True)
        sink, buffer = _attach_fake_audio(controller._ui_sound_service)

        assert controller.setInterfaceMode("couch") is True

        assert controller.interfaceMode == "couch"
        assert controller._ui_sound_service._active == []
        assert sink.stopped is True
        assert sink.deleted is True
        assert buffer.closed is True
        assert buffer.deleted is True
    finally:
        controller.shutdown()


def _controller_with_mode(tmp_path: Path, mode: ControllerMode) -> tuple[AppController, FakeGamepadProvider]:
    store = SettingsStore(tmp_path / f"{mode.name}.json")
    store.save(AppSettings(controller_mode=mode))
    device = GamepadDevice(4, "Test Controller", GamepadType.XBOX)
    provider = FakeGamepadProvider((device,))
    controller = AppController(
        game_provider=DemoGameProvider(),
        settings_store=store,
        gamepad_service=GamepadService(provider),
        auto_refresh=False,
    )
    return controller, provider


def test_automatic_mode_switches_on_first_input_and_stays_safe_after_disconnect(tmp_path: Path) -> None:
    controller, provider = _controller_with_mode(tmp_path, ControllerMode.AUTOMATIC)
    forwarded: list[str] = []
    controller.gamepadAction.connect(forwarded.append)
    try:
        assert controller.interfaceMode == "desktop"
        provider.emit(GamepadEvent("button", 4, "dpad_right", True, 1.0))
        controller._gamepad_service.pollNow()
        assert controller.interfaceMode == "couch"
        assert forwarded == []

        provider.emit(GamepadEvent("button", 4, "south", True, 1.0))
        controller._gamepad_service.pollNow()
        assert forwarded == ["Confirm"]

        provider.emit(GamepadEvent("disconnected", 4))
        controller._gamepad_service.pollNow()
        assert controller.interfaceMode == "couch"
        assert controller.controllerCount == 0
    finally:
        controller.shutdown()


def test_fixed_controller_modes_and_settings_persistence(tmp_path: Path) -> None:
    desktop, desktop_provider = _controller_with_mode(tmp_path, ControllerMode.DESKTOP_ONLY)
    try:
        desktop_provider.emit(GamepadEvent("button", 4, "south", True, 1.0))
        desktop._gamepad_service.pollNow()
        assert desktop.interfaceMode == "desktop"
        assert desktop.toggleInterfaceMode() is True
        assert desktop.interfaceMode == "couch"
    finally:
        desktop.shutdown()

    couch, _ = _controller_with_mode(tmp_path, ControllerMode.COUCH_ONLY)
    try:
        assert couch.interfaceMode == "couch"
        # This is the same controller slot used by the global F11 shortcut.
        assert couch.toggleInterfaceMode() is True
        assert couch.interfaceMode == "desktop"
        # Couch-only defines the preferred startup mode, not an escape trap.
        couch._on_gamepad_action("Accept")
        assert couch.interfaceMode == "desktop"
    finally:
        couch.shutdown()

    settings_path = tmp_path / "persisted.json"
    store = SettingsStore(settings_path)
    controller = AppController(
        game_provider=DemoGameProvider(),
        settings_store=store,
        gamepad_service=GamepadService(FakeGamepadProvider()),
        auto_refresh=False,
    )
    try:
        assert controller.saveSetting("controllerMode", "Couch only")
        assert controller.saveSetting("swapAcceptBack", True)
        assert controller.saveSetting("analogDeadzone", 0.31)
        assert controller.saveSetting("navigationRepeatDelayMs", 450)
        assert controller.saveSetting("navigationRepeatRateMs", 120)
        assert controller.saveSetting("hideCursorInCouchMode", False)
        assert controller.saveSetting("startCouchModeFullscreen", False)
        assert controller.saveSetting("postLaunchBehavior", "Stay open")
        assert controller.saveSetting("interfaceSounds", True)
        assert controller.interfaceMode == "couch"
        mapper = controller._gamepad_service._mapper
        assert mapper.swap_accept_back is True
        assert mapper.deadzone == 0.31
        assert mapper.repeat_delay_seconds == 0.45
        assert mapper.repeat_rate_seconds == 0.12
    finally:
        controller.shutdown()

    restored = store.load()
    assert restored.controller_mode is ControllerMode.COUCH_ONLY
    assert restored.post_launch_behavior is PostLaunchBehavior.STAY_OPEN
    assert restored.swap_accept_back is True
    assert restored.analog_deadzone == 0.31
    assert restored.navigation_repeat_delay_ms == 450
    assert restored.navigation_repeat_rate_ms == 120
    assert restored.hide_cursor_in_couch_mode is False
    assert restored.start_couch_mode_fullscreen is False
    assert restored.interface_sounds is True


def test_ui_mode_emergency_override_and_persistent_reset(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "emergency-settings.json")
    store.save(AppSettings(controller_mode=ControllerMode.COUCH_ONLY))
    service = GamepadService(
        FakeGamepadProvider((GamepadDevice(4, "Test Controller", GamepadType.XBOX),))
    )
    controller = AppController(
        game_provider=DemoGameProvider(),
        settings_store=store,
        gamepad_service=service,
        initial_interface_mode="desktop",
        auto_refresh=False,
    )
    try:
        assert controller.interfaceMode == "desktop"
        assert store.load().controller_mode is ControllerMode.COUCH_ONLY
    finally:
        controller.shutdown()

    reset = AppController(
        game_provider=DemoGameProvider(),
        settings_store=store,
        gamepad_service=GamepadService(FakeGamepadProvider()),
        reset_ui_mode=True,
        auto_refresh=False,
    )
    try:
        assert reset.interfaceMode == "desktop"
        assert reset.settings["controllerMode"] == "Automatic"
        assert store.load().controller_mode is ControllerMode.AUTOMATIC
    finally:
        reset.shutdown()


def test_unavailable_controller_backend_falls_back_from_couch(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "unavailable-controller.json")
    store.save(AppSettings(controller_mode=ControllerMode.COUCH_ONLY))
    controller = AppController(
        game_provider=DemoGameProvider(),
        settings_store=store,
        gamepad_service=GamepadService(UnavailableGamepadProvider("fixture")),
        auto_refresh=False,
    )
    try:
        assert controller.interfaceMode == "desktop"
    finally:
        controller.shutdown()


def test_couch_launch_uses_existing_launcher_and_default_minimize_behavior(
    tmp_path: Path,
) -> None:
    launched: list[str] = []

    class Launcher:
        def launch(self, game: object) -> tuple[str, ...]:
            launched.append(str(getattr(game, "id", "")))
            return ("steam", "-applaunch", "fixture")

    controller = AppController(
        game_provider=DemoGameProvider(),
        settings_store=SettingsStore(tmp_path / "launch-settings.json"),
        gamepad_service=GamepadService(FakeGamepadProvider()),
        game_launcher=Launcher(),
        demo_mode=False,
        auto_refresh=False,
    )
    window_actions: list[str] = []
    controller.windowActionRequested.connect(window_actions.append)
    try:
        assert controller.launchGame("batman-arkham-knight") is True
        assert launched == ["batman-arkham-knight"]
        assert window_actions == ["minimize"]
        assert controller.launchGame("batman-arkham-knight") is False
        assert launched == ["batman-arkham-knight"]
    finally:
        controller.shutdown()


def test_semantic_mapping_and_long_view_hold_open_system_menu() -> None:
    mapper = GamepadInputMapper()
    assert mapper.process(GamepadEvent("button", 9, "south", True), now=1.0) == (GamepadAction.CONFIRM,)
    assert mapper.process(GamepadEvent("button", 9, "north", True), now=1.2) == (GamepadAction.CONTEXT_MENU,)
    assert mapper.process(GamepadEvent("button", 9, "left_shoulder", True), now=1.4) == (GamepadAction.PAGE_LEFT,)
    assert mapper.process(GamepadEvent("button", 9, "right_shoulder", True), now=1.6) == (GamepadAction.PAGE_RIGHT,)
    assert mapper.process(GamepadEvent("button", 9, "start", True), now=1.8) == (GamepadAction.OPEN_SYSTEM_MENU,)
    assert mapper.process(GamepadEvent("button", 9, "guide", True), now=2.0) == (GamepadAction.TOGGLE_DESKTOP_COUCH,)

    assert mapper.process(GamepadEvent("button", 9, "back", True), now=3.0) == ()
    assert mapper.poll_repeats(now=4.99) == ()
    assert mapper.poll_repeats(now=5.01) == (GamepadAction.OPEN_SYSTEM_MENU,)
    assert mapper.poll_repeats(now=5.5) == ()
    assert mapper.process(GamepadEvent("button", 9, "back", False), now=5.6) == ()

    assert mapper.process(GamepadEvent("button", 9, "back", True), now=6.0) == ()
    assert mapper.process(GamepadEvent("button", 9, "back", False), now=6.4) == (GamepadAction.CONTEXT_MENU,)


def test_couch_navigation_retains_focus_reconciles_removal_and_restores_modal() -> None:
    navigation = CouchNavigationController()
    navigation.enterScreen("library", "steam-2")
    navigation.rememberFocus("library", "steam-2", 1)
    assert navigation.focusedId == "steam-2"
    assert navigation.reconcileFocus("library", ["steam-1", "steam-2", "steam-3"]) == "steam-2"

    # The selected stable ID disappeared; the nearest surviving index wins.
    assert navigation.reconcileFocus("library", ["steam-1", "steam-3"]) == "steam-3"
    navigation.openModal("confirm", "cancel")
    assert navigation.modalOpen is True
    assert navigation.focusedId == "cancel"
    navigation.closeModal()
    assert navigation.modalOpen is False
    assert navigation.focusedId == "steam-3"

    navigation.enterScreen("details", "tab-overview")
    assert navigation.previousScreen() == "library"
    assert navigation.focusedId == "steam-3"


def test_couch_navigation_disconnect_blocks_pad_and_5000_inputs_keep_focus() -> None:
    navigation = CouchNavigationController()
    received: list[str] = []
    navigation.actionRequested.connect(received.append)
    navigation.enterScreen("library", "steam-17")
    for index in range(5000):
        assert navigation.dispatch(("NavigateLeft", "NavigateRight")[index % 2]) is True
    assert len(received) == 5000
    assert navigation.focusedId == "steam-17"

    navigation.setControllerConnected(False)
    assert navigation.dispatch("Confirm") is False
    assert len(received) == 5000
    navigation.setControllerConnected(True)
    assert navigation.focusedId == "steam-17"
    assert navigation.dispatch("Confirm") is True
