"""Desktop MangoHud editor: control mapping, presets, layout preview, live preview."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import re
import signal
import threading
import time
from typing import Any

import pytest

from game_optimization_linux.models import (
    PRESET_METRICS,
    MangoHudProfile,
    preset_for_metrics,
)
from game_optimization_linux.services import (
    KNOWN_CONFIG_KEYS,
    MangoHudConfigWriter,
    MangoHudProfileRepository,
)
from game_optimization_linux.services import mangohud_preview as preview_module
from game_optimization_linux.services.mangohud import (
    display_config_text,
    hud_layout,
    hud_metrics,
)
from game_optimization_linux.services.mangohud_preview import MangoHudLivePreview
from tests.test_mangohud import _controller, _Detector, _game

QML = Path("src/game_optimization_linux/qml/pages/details/MangoHudTab.qml")
APP_ID = "242550"

# payload key, value, profile attribute, stored value, expected config line
# (None = drives activation, not MangoHud.conf).  Logging cases enable logging.
FIELD_CASES: list[tuple[str, Any, str, Any, str | None]] = [
    ("position", "bottom-right", "position", "bottom-right", "position=bottom-right"),
    ("offsetX", -33, "offset_x", -33, "offset_x=-33"),
    ("offsetY", 17, "offset_y", 17, "offset_y=17"),
    ("cellPaddingY", 0.15, "cellpadding_y", 0.15, "cellpadding_y=0.15"),
    ("graphs", ["gpu_load", "ram"], "graphs", ("gpu_load", "ram"), "graphs=gpu_load,ram"),
    ("fontSize", 30, "font_size", 30, "font_size=30"),
    ("backgroundAlpha", 0.25, "background_alpha", 0.25, "background_alpha=0.25"),
    ("roundCorners", 12, "round_corners", 12, "round_corners=12"),
    ("compact", True, "compact", True, "hud_compact"),
    ("horizontal", True, "horizontal", True, "horizontal"),
    ("tableColumns", 4, "table_columns", 4, "table_columns=4"),
    ("frameTimingGraph", True, "frame_timing_graph", True, "frame_timing"),
    ("fpsLimit", 90, "fps_limit", 90, "fps_limit=90"),
    ("fpsLimitMethod", "late", "fps_limit_method", "late", "fps_limit_method=late"),
    ("vulkanPresentMode", "mailbox", "vulkan_present_mode", "mailbox", "vulkan_present_mode=mailbox"),
    ("vsync", 3, "vsync", 3, "vsync=3"),
    ("loggingEnabled", True, "logging_enabled", True, "log_duration=60"),
    ("logDuration", 120, "log_duration", 120, "log_duration=120"),
    ("logInterval", 0.25, "log_interval", 0.25, "log_interval=250"),
    ("toggleLoggingKey", "Shift_L+F3", "toggle_logging_key", "Shift_L+F3", "toggle_logging=Shift_L+F3"),
    ("outputFolder", "__LOGS__", "output_folder", "__LOGS__", "output_folder=__LOGS__"),
    ("executablePath", "RaymanLegends.exe", "executable_path", "RaymanLegends.exe", None),
    ("textColor", "112233", "text_color", "112233", "text_color=112233"),
    ("gpuColor", "AA0000", "gpu_color", "AA0000", "gpu_color=AA0000"),
    ("cpuColor", "00AA00", "cpu_color", "00AA00", "cpu_color=00AA00"),
    ("vramColor", "0000AA", "vram_color", "0000AA", "vram_color=0000AA"),
    ("ramColor", "ABCDEF", "ram_color", "ABCDEF", "ram_color=ABCDEF"),
    ("frametimeColor", "FEDCBA", "frametime_color", "FEDCBA", "frametime_color=FEDCBA"),
    ("backgroundColor", "101010", "background_color", "101010", "background_color=101010"),
    ("metrics", ["fps", "ram"], "metrics", ("fps", "ram"), "gpu_stats=0"),
]
_LOGGING_KEYS = {"logDuration", "logInterval", "toggleLoggingKey", "outputFolder"}
# Editor state that is not a MangoHud setting.
_UI_ONLY = {"categoryIndex", "configExpanded"}


def _qml_draft_map() -> dict[str, str]:
    source = QML.read_text(encoding="utf-8")
    block = source[source.index("readonly property var draftMap"):]
    block = block[: block.index("})")]
    return {
        prop: key
        for key, prop in re.findall(r'"(\w+)":\s*(\w+)', block)
    }


def _qml_edited_properties() -> set[str]:
    source = QML.read_text(encoding="utf-8")
    edited = set(re.findall(r"tab\.(\w+) = ", source))
    colors = source[source.index("readonly property var colorModel"):]
    colors = colors[: colors.index("]")]
    edited.update(re.findall(r'"id": "(\w+)"', colors))
    # choosePreset/setMetric edit the metric set and the preset.
    edited.update({"metrics", "preset", "profileEnabled"})
    return edited - _UI_ONLY - {"livePreviewRunning", "livePreviewMessage"}


def test_a_every_existing_control_maps_to_payload_model_and_generator() -> None:
    draft = _qml_draft_map()
    edited = _qml_edited_properties()
    unmapped = sorted(prop for prop in edited if prop not in draft)
    assert unmapped == []
    covered = {case[0] for case in FIELD_CASES} | {"preset", "enabled"}
    assert sorted({draft[prop] for prop in edited} - covered) == []
    # Emitted keys are known to MangoHud 0.8.4's example file.
    for _key, _value, _attr, _stored, line in FIELD_CASES:
        if line is not None:
            assert line.split("=", 1)[0] in KNOWN_CONFIG_KEYS


@pytest.fixture()
def editor(tmp_path: Path):
    install = tmp_path / "SteamLibrary" / "steamapps" / "common" / "Rayman Legends"
    game = _game(install)
    (install / "RaymanLegends.exe").write_bytes(b"MZ")
    repository = MangoHudProfileRepository(tmp_path / "games", log_root=tmp_path / "logs")
    controller = _controller(tmp_path, game, repository, _Detector())
    base = controller.getMangoHudProfile(game.id)
    assert base["success"] is True
    yield controller, game, repository, base
    controller.shutdown()


@pytest.mark.parametrize("case", FIELD_CASES, ids=[case[0] for case in FIELD_CASES])
def test_b_control_change_saves_reloads_and_writes_mangohud_key(
    editor, tmp_path: Path, case: tuple[str, Any, str, Any, str | None]
) -> None:
    controller, game, repository, base = editor
    key, value, attribute, stored, line = case
    logs = str(tmp_path / "custom-logs")
    value = logs if value == "__LOGS__" else value
    stored = logs if stored == "__LOGS__" else stored
    line = line.replace("__LOGS__", logs) if line else line
    payload = {**base, "preset": "basic", "enabled": True, key: value}
    if key in _LOGGING_KEYS:
        payload["loggingEnabled"] = True
    if key == "metrics":
        payload["preset"] = "custom"

    preview = controller.previewMangoHudProfile(game.id, payload)
    assert preview["success"] is True
    saved = controller.saveMangoHudProfile(game.id, payload)
    assert saved["success"] is True, saved.get("error")

    assert getattr(repository.load(APP_ID), attribute) == stored
    reloaded = controller.getMangoHudProfile(game.id)
    expected = list(stored) if isinstance(stored, tuple) else stored
    assert reloaded[key] == expected
    if line is not None:
        lines = repository.config_path(APP_ID).read_text(encoding="utf-8").splitlines()
        assert line in lines
        assert line in preview["configPreview"].splitlines()


def test_c_presets_have_exact_stable_sets_and_manual_change_is_custom() -> None:
    profile = MangoHudProfile.default(APP_ID)
    for preset in ("fps_only", "basic", "extended"):
        applied = profile.apply_preset(preset)
        assert set(applied.metrics) == set(PRESET_METRICS[preset])
        assert applied.apply_preset(preset).metrics == applied.metrics
        assert preset_for_metrics(list(reversed(PRESET_METRICS[preset]))) == preset
    assert profile.apply_preset("disabled").metrics == ()
    assert preset_for_metrics([*PRESET_METRICS["basic"], "gpu_power"]) == "custom"
    assert preset_for_metrics(PRESET_METRICS["basic"][:-1]) == "custom"
    assert preset_for_metrics([]) == "custom"

    fps_only = MangoHudConfigWriter().render(profile.apply_preset("fps_only")).splitlines()
    assert {"fps", "frametime", "gpu_stats=0", "cpu_stats=0"} <= set(fps_only)
    assert not {"gpu_temp", "cpu_temp", "vram", "ram"} & set(fps_only)

    # QML takes preset sets from the model instead of a second copy.
    source = QML.read_text(encoding="utf-8")
    assert '"gpu_temperature", "cpu_temperature", "vram", "ram"]' not in source
    assert "presetForMetrics(next)" in source


def test_c_controller_exposes_model_preset_sets(editor) -> None:
    _controller_, _game_, _repository, base = editor
    assert base["presetMetrics"] == {
        preset: list(metrics) for preset, metrics in PRESET_METRICS.items()
    }


def test_d_layout_shows_only_selected_metrics_in_hud_order() -> None:
    profile = replace(
        MangoHudProfile.default(APP_ID).apply_preset("custom"),
        enabled=True,
        metrics=("ram", "fps", "cpu_usage", "gpu_usage", "gpu_temperature"),
    )
    layout = hud_layout(profile)
    assert layout["metrics"] == ["gpu_usage", "gpu_temperature", "cpu_usage", "ram", "fps"]
    assert [row["id"] for row in layout["rows"]] == ["gpu", "cpu", "ram", "fps"]
    texts = [cell["text"] for cell in layout["inlineCells"]]
    assert "VRAM" not in texts and "12.4 GiB" in texts
    # Generator and preview share one metric list.
    config = MangoHudConfigWriter().render(profile).splitlines()
    assert hud_metrics(profile) == tuple(layout["metrics"])
    assert {"gpu_stats", "gpu_temp", "cpu_stats", "ram", "fps", "frametime=0"} <= set(config)

    unsupported = KNOWN_CONFIG_KEYS - {"gpu_temp"}
    assert "gpu_temperature" not in hud_layout(profile, unsupported)["metrics"]
    assert "gpu_temp" not in MangoHudConfigWriter(unsupported).render(profile).splitlines()


def test_d_layout_reacts_to_position_layout_colors_and_transparency() -> None:
    base = MangoHudProfile.default(APP_ID).apply_preset("basic")
    changed = replace(
        base,
        position="bottom-right",
        horizontal=True,
        compact=True,
        table_columns=4,
        background_alpha=0.2,
        background_color="112233",
        gpu_color="FF0000",
        text_color="00FF00",
        frametime_color="0000FF",
        frame_timing_graph=True,
    )
    before, after = hud_layout(base), hud_layout(changed)
    assert (before["position"], after["position"]) == ("top-left", "bottom-right")
    assert (before["horizontal"], after["horizontal"]) == (False, True)
    assert after["compact"] is True and after["tableColumns"] == 4
    assert after["backgroundAlpha"] == 0.2 and after["backgroundColor"] == "#112233"
    gpu_row = after["rows"][0]
    assert gpu_row["label"] == "GPU" and gpu_row["color"] == "#FF0000"
    assert {value["color"] for value in gpu_row["values"]} == {"#00FF00"}
    assert after["frameTimingGraph"] is True and after["frametimeColor"] == "#0000FF"
    assert before["frameTimingGraph"] is False
    # Vertical cells are padded to whole table lines of ``table_columns``.
    assert len(hud_layout(replace(base, table_columns=4))["cells"]) % 4 == 0


def test_d_hidden_values_without_their_row_anchor_are_reported() -> None:
    profile = replace(
        MangoHudProfile.default(APP_ID).apply_preset("custom"),
        enabled=True,
        metrics=("gpu_temperature", "frametime"),
    )
    layout = hud_layout(profile)
    assert layout["rows"] == [] and layout["empty"] is True
    assert {item["requires"] for item in layout["hiddenMetrics"]} == {"gpu_usage", "fps"}


def test_d_preview_label_is_honest() -> None:
    source = QML.read_text(encoding="utf-8")
    source += QML.with_name("MangoHudPreview.qml").read_text(encoding="utf-8")
    assert 'qsTr("Illustrative preview")' in source
    assert 'qsTr("Real Vulkan preview")' in source
    assert "not measurements" in source
    assert "Visual preview" not in source
    assert "live measurement" not in source.casefold()


class _Process:
    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.pid = 4242
        self.returncode: int | None = None
        self._result = returncode
        self._stderr = stderr
        self._done = threading.Event()

    def finish(self) -> None:
        self._done.set()

    def communicate(self) -> tuple[str, str]:
        self._done.wait(5)
        self.returncode = self._result
        return "", self._stderr

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self._done.wait(timeout)
        self.returncode = self._result
        return self._result

    def send_signal(self, _sig: int) -> None:
        self.finish()


class _Popen:
    def __init__(self, process: _Process | None = None) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.process = process or _Process()

    def __call__(self, argv: list[str], **kwargs: Any) -> _Process:
        config = Path(kwargs["env"]["MANGOHUD_CONFIGFILE"])
        kwargs["config_text"] = config.read_text(encoding="utf-8")
        self.calls.append((list(argv), kwargs))
        return self.process


def _wait_idle(preview: MangoHudLivePreview) -> None:
    deadline = time.monotonic() + 5
    while preview.running and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not preview.running


def _native(tmp_path: Path, popen: _Popen, *, missing: str = "") -> MangoHudLivePreview:
    tools = {"mangohud": "/usr/bin/mangohud", "vkcube": "/usr/bin/vkcube"}
    return MangoHudLivePreview(
        preview_root=tmp_path / "cache" / "mangohud-preview",
        environment={"HOME": str(tmp_path), "MANGOHUD_CONFIG": "fps_limit=1"},
        which=lambda name: None if name == missing else tools.get(name),
        popen=popen,
    )


def test_e_native_live_preview_uses_argv_and_controlled_environment(tmp_path: Path) -> None:
    popen = _Popen()
    finished: list[Any] = []
    preview = _native(tmp_path, popen)
    preview.on_finished = finished.append

    result = preview.start("fps\n")

    assert result.success is True
    argv, kwargs = popen.calls[0]
    assert argv == ["/usr/bin/mangohud", "/usr/bin/vkcube"]
    assert kwargs["shell"] is False and kwargs["start_new_session"] is True
    config = Path(kwargs["env"]["MANGOHUD_CONFIGFILE"])
    assert config.parent == tmp_path / "cache" / "mangohud-preview"
    assert kwargs["config_text"] == "fps\n"
    assert "MANGOHUD_CONFIG" not in kwargs["env"]
    assert config.is_file()

    assert preview.start("fps\n").code == "already_running"
    assert len(popen.calls) == 1

    popen.process.finish()
    _wait_idle(preview)
    assert not config.exists()
    assert [item.code for item in finished] == ["finished"]


def test_e_flatpak_live_preview_goes_through_flatpak_spawn(tmp_path: Path) -> None:
    popen = _Popen()

    class _Host:
        def host_command_available(self, name: str) -> bool:
            return name in {"mangohud", "vkcube"}

    preview = MangoHudLivePreview(
        preview_root=tmp_path / "cache",
        environment={"FLATPAK_ID": "io.example.App", "LD_LIBRARY_PATH": "/app/lib"},
        which=lambda name: "/usr/bin/flatpak-spawn" if name == "flatpak-spawn" else None,
        popen=popen,
        host_service=_Host(),
    )
    assert preview.start("fps\n").success is True
    argv, kwargs = popen.calls[0]
    config = kwargs["env"]["MANGOHUD_CONFIGFILE"]
    assert argv[:2] == ["/usr/bin/flatpak-spawn", "--host"]
    assert f"--env=MANGOHUD_CONFIGFILE={config}" in argv
    assert "--unset-env=LD_LIBRARY_PATH" in argv
    assert "--unset-env=MANGOHUD_CONFIG" in argv
    assert argv[-2:] == ["mangohud", "vkcube"]
    popen.process.finish()
    _wait_idle(preview)
    assert not Path(config).exists()


@pytest.mark.parametrize(("missing", "code"), [("vkcube", "vkcube_missing"), ("mangohud", "mangohud_missing")])
def test_e_missing_tools_are_reported_without_starting(
    tmp_path: Path, missing: str, code: str
) -> None:
    popen = _Popen()
    preview = _native(tmp_path, popen, missing=missing)
    result = preview.start("fps\n")
    assert (result.success, result.code) == (False, code)
    assert popen.calls == []
    root = tmp_path / "cache" / "mangohud-preview"
    assert not root.exists() or not any(root.iterdir())


def test_e_stop_terminates_only_own_process_group_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    popen = _Popen()
    preview = _native(tmp_path, popen)
    killed: list[tuple[int, int]] = []

    def killpg(pid: int, sig: int) -> None:
        killed.append((pid, sig))
        popen.process.finish()

    monkeypatch.setattr(preview_module.os, "killpg", killpg)
    finished: list[Any] = []
    preview.on_finished = finished.append
    assert preview.start("fps\n").success is True
    config = Path(popen.calls[0][1]["env"]["MANGOHUD_CONFIGFILE"])

    assert preview.stop() is True
    _wait_idle(preview)
    assert killed == [(4242, signal.SIGTERM)]
    assert not config.exists()
    assert finished[0].success is True
    assert preview.stop() is False


def test_e_process_error_is_readable(tmp_path: Path) -> None:
    popen = _Popen(_Process(returncode=1, stderr="vkcube: Cannot find a compatible Vulkan ICD\n"))
    finished: list[Any] = []
    preview = _native(tmp_path, popen)
    preview.on_finished = finished.append
    preview.start("fps\n")
    popen.process.finish()
    _wait_idle(preview)
    assert finished[0].code == "process_failed"
    assert "status 1" in finished[0].message and "Vulkan ICD" in finished[0].message


def test_e_controller_preview_uses_unsaved_draft_and_saves_nothing(
    tmp_path: Path,
) -> None:
    install = tmp_path / "Library" / "steamapps" / "common" / "Rayman Legends"
    game = _game(install)
    repository = MangoHudProfileRepository(tmp_path / "games", log_root=tmp_path / "logs")
    global_config = tmp_path / "home" / ".config" / "MangoHud" / "MangoHud.conf"
    global_config.parent.mkdir(parents=True)
    global_config.write_text("user global\n", encoding="utf-8")
    controller = _controller(tmp_path, game, repository, _Detector())
    popen = _Popen()
    controller._mangohud_live_preview = _native(tmp_path, popen)
    signals: list[dict[str, Any]] = []
    controller.mangoHudLivePreviewChanged.connect(signals.append)
    controller._mangohud_live_preview.on_finished = lambda result: signals.append(
        result.to_dict()
    )
    try:
        draft = {**controller.getMangoHudProfile(game.id), "preset": "basic",
                 "enabled": True, "gpuColor": "FF0000"}
        result = controller.startMangoHudLivePreview(game.id, draft)
        assert result["success"] is True and result["running"] is True
        assert "gpu_color=FF0000" in popen.calls[0][1]["config_text"].splitlines()
        assert not repository.profile_path(APP_ID).exists()
        assert not repository.config_path(APP_ID).exists()
        again = controller.startMangoHudLivePreview(game.id, draft)
        assert again["code"] == "already_running"
        popen.process.finish()
        _wait_idle(controller._mangohud_live_preview)
        disabled = controller.startMangoHudLivePreview(
            game.id, {**draft, "preset": "disabled"}
        )
        assert disabled["code"] == "disabled"
    finally:
        controller.shutdown()
    assert global_config.read_text(encoding="utf-8") == "user global\n"
    assert not repository.profile_path(APP_ID).exists()


def test_e_controller_reports_missing_mangohud_with_install_message(tmp_path: Path) -> None:
    game = _game(tmp_path / "Library" / "steamapps" / "common" / "Rayman Legends")
    repository = MangoHudProfileRepository(tmp_path / "games", log_root=tmp_path / "logs")
    detector = _Detector(available=False)
    controller = _controller(tmp_path, game, repository, detector)
    controller._mangohud_live_preview = _native(tmp_path, _Popen(), missing="mangohud")
    try:
        draft = {**controller.getMangoHudProfile(game.id), "preset": "basic", "enabled": True}
        result = controller.startMangoHudLivePreview(game.id, draft)
    finally:
        controller.shutdown()
    assert result["code"] == "mangohud_missing"
    assert result["message"] == detector.detect("native").message


def test_f_unknown_profile_fields_survive_load_and_save(editor) -> None:
    controller, game, repository, base = editor
    path = repository.profile_path(APP_ID)
    data = MangoHudProfile.default(APP_ID).apply_preset("basic").to_dict()
    data["future_option"] = {"level": 2}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")

    loaded = controller.getMangoHudProfile(game.id)
    assert loaded["success"] is True
    assert controller.saveMangoHudProfile(game.id, {**loaded, "fontSize": 31})["success"]

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["future_option"] == {"level": 2}
    assert stored["font_size"] == 31
    assert repository.load(APP_ID).extra_fields == {"future_option": {"level": 2}}


def test_f_old_profile_without_new_fields_keeps_mangohud_defaults(tmp_path: Path) -> None:
    repository = MangoHudProfileRepository(tmp_path / "games", log_root=tmp_path / "logs")
    data = MangoHudProfile.default(APP_ID).apply_preset("basic").to_dict()
    for key in ("frame_timing_graph", "text_color", "gpu_color", "background_color"):
        data.pop(key)
    path = repository.profile_path(APP_ID)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    profile = repository.load(APP_ID)
    content = MangoHudConfigWriter().render(profile).splitlines()
    assert profile.gpu_color == "2E9762" and profile.frame_timing_graph is False
    assert "frame_timing=0" in content
    assert not any(line.endswith("_color=2E9762") for line in content)


def test_f_displayed_config_hides_the_home_directory() -> None:
    text = "output_folder=/home/user/.local/state/logs\nfps\n"
    assert display_config_text(text, "/home/user") == (
        "output_folder=~/.local/state/logs\nfps\n"
    )
    assert display_config_text("output_folder=/home/username2/x\n", "/home/user") == (
        "output_folder=/home/username2/x\n"
    )


def test_e_host_probe_accepts_only_preview_commands_via_flatpak_spawn() -> None:
    import subprocess

    from game_optimization_linux.services.host_service import (
        HostServiceClient,
        HostServiceError,
    )

    calls: list[list[str]] = []

    def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        assert kwargs["shell"] is False
        missing = argv[2] == "vkcube"
        return subprocess.CompletedProcess(
            argv, 127 if missing else 0, "", "command not found" if missing else ""
        )

    client = HostServiceClient(
        flatpak_spawn="/usr/bin/flatpak-spawn",
        environment={"FLATPAK_ID": "io.example.App"},
        command_runner=runner,
        which=lambda _name: None,
    )
    assert client.host_command_available("mangohud") is True
    assert client.host_command_available("vkcube") is False
    assert calls == [
        ["/usr/bin/flatpak-spawn", "--host", "mangohud", "--version"],
        ["/usr/bin/flatpak-spawn", "--host", "vkcube", "--help"],
    ]
    with pytest.raises(HostServiceError):
        client.host_command_available("sh")
