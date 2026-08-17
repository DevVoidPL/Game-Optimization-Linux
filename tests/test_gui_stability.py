from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "tests" / "qml_runtime_probe.py"


def _run_probe(
    tmp_path: Path,
    mode: str,
    *arguments: str,
    scale: str = "1.0",
    style: str | None = None,
) -> dict[str, object]:
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": str(ROOT / "src"),
            "QT_QPA_PLATFORM": "offscreen",
            "QT_SCALE_FACTOR": scale,
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "GAME_OPTIMIZATION_DEBUG_ARTWORK": (
                "1"
                if mode in {"artwork", "artwork_refresh", "incremental_games"}
                else "0"
            ),
        }
    )
    if style is not None:
        environment["QT_QUICK_CONTROLS_STYLE"] = style
        system_qml = Path("/usr/lib/qt6/qml")
        if system_qml.is_dir():
            existing_imports = environment.get("QML2_IMPORT_PATH", "")
            environment["QML2_IMPORT_PATH"] = os.pathsep.join(
                part
                for part in (str(system_qml), existing_imports)
                if part
            )
    completed = subprocess.run(
        [sys.executable, str(PROBE), mode, *arguments],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result_line = next(
        (line for line in completed.stdout.splitlines() if line.startswith("RESULT:")),
        "",
    )
    assert result_line, completed.stdout + completed.stderr
    return json.loads(result_line.removeprefix("RESULT:"))


def test_storage_tab_uses_booleans_for_every_analysis_state(tmp_path: Path) -> None:
    payload = _run_probe(tmp_path, "storage")
    result = payload["result"]
    assert isinstance(result, dict)
    assert result["completed"]["profilesEnabled"] is True
    for state in (
        "no_report",
        "queued",
        "running",
        "cancelled",
        "failed",
        "ext4",
        "missing_path",
        "unavailable",
    ):
        assert result[state]["profilesEnabled"] is False
    assert result["header_card_consistency"] == {
        "headerPhysical": "166 MiB",
        "cardPhysical": "166 MiB",
        "headerSaving": "20.0 MiB",
        "cardSaving": "20.0 MiB",
    }
    assert result["low_benefit_confirmation"]["manualEnabled"] is True
    assert (
        result["low_benefit_confirmation"]["extraConfirmationVisible"] is True
    )
    assert (
        result["low_benefit_confirmation"]["finalConfirmationVisible"] is False
    )
    assert result["benchmark_estimate"]["classification"] == "Moderately compressed"
    assert result["benchmark_estimate"]["lastOperationReclaimed"] == "256 MiB"
    assert result["benchmark_estimate"]["profitability"] == "Low benefit"
    assert result["benchmark_estimate"]["rewrite"] == "1000 B"
    assert not any("StorageTab" in message for message in payload["messages"])


@pytest.mark.parametrize("width,height", ((1920, 1080), (1600, 900), (1366, 768), (1280, 720)))
@pytest.mark.parametrize("scale", ("1.0", "1.25", "1.5", "1.75"))
def test_grid_and_list_geometry_at_supported_resolutions_and_dpi(
    tmp_path: Path,
    width: int,
    height: int,
    scale: str,
) -> None:
    payload = _run_probe(
        tmp_path,
        "cards",
        "--width",
        str(width),
        "--height",
        str(height),
        "--theme",
        "dark",
        scale=scale,
    )
    result = payload["result"]
    assert result["cards"] > 0
    assert result["rows"] > 0
    assert result["columns"] > 0
    assert result["contentHeight"] > result["viewportHeight"]
    assert result["scrollBarVisible"] is True
    assert result["lastCardVisibleAtEnd"] is True
    assert result["bottomMargin"] >= 8
    assert all(delta > 0 for delta in result["wheelDeltas"].values())
    assert result["returnOffsetValid"] is True
    assert result["returnWheelOffset"] > 0
    assert all(
        height > result["viewportHeight"]
        for height in result["languageContentHeights"].values()
    )
    assert result["libraryCompressionSummary"] == {
        "logical": "362.40 GiB",
        "physical": "350.58 GiB",
        "saving": "11.82 GiB",
        "effect": "3.26%",
        "measuredGames": "7 of 14",
    }
    assert result["compactLibrarySummary"]["defaultCollapsed"] is True
    assert (
        result["compactLibrarySummary"]["expandedHeight"]
        > result["compactLibrarySummary"]["collapsedHeight"]
    )
    assert result["summaryScopes"] == {
        "full": "Whole library",
        "partial": "Measured games total",
    }
    assert result["summaryStatuses"] == {
        "full": "Full measurement",
        "partial": "Partial measurement",
    }
    assert result["fullDateVisible"] is True
    assert result["partialDateVisible"] is False
    assert re.fullmatch(r"\d{2}\.\d{2}\.\d{4}, \d{2}:\d{2}", result["fullDateValue"])
    assert result["counters"] == {
        "visible": "Visible games: 24",
        "available": "Games in available libraries: 23",
        "cached": "Cached from disconnected libraries: 1",
    }
    assert "ntfs3" not in result["filesystemOptions"]
    assert "Btrfs" in result["filesystemOptions"]


@pytest.mark.parametrize("theme", ("light", "dark"))
def test_game_cards_in_light_and_dark_theme(tmp_path: Path, theme: str) -> None:
    payload = _run_probe(
        tmp_path,
        "cards",
        "--width",
        "1366",
        "--height",
        "768",
        "--theme",
        theme,
    )
    assert payload["result"]["cards"] > 0


def test_artwork_lifecycle_logging_is_disabled_by_default(tmp_path: Path) -> None:
    payload = _run_probe(
        tmp_path,
        "cards",
        "--width",
        "1280",
        "--height",
        "720",
    )
    assert not any(
        "Game Optimization GameArtwork lifecycle" in message
        for message in payload["messages"]
    )


def test_other_pages_keep_their_existing_scroll_owners() -> None:
    qml_pages = ROOT / "src" / "game_optimization_linux" / "qml" / "pages"
    for relative in (
        "SettingsPage.qml",
        "SystemPage.qml",
        "details/OverviewTab.qml",
        "details/StorageTab.qml",
        "details/GraphicsTab.qml",
        "details/OptimizationTab.qml",
    ):
        source = (qml_pages / relative).read_text(encoding="utf-8")
        assert "ScrollView {" in source, relative

    updates = (qml_pages / "UpdatesPage.qml").read_text(encoding="utf-8")
    assert "ListView {" in updates
    assert "id: updatesList" in updates


def test_settings_and_sidebar_hide_nonfunctional_controls() -> None:
    qml_root = ROOT / "src" / "game_optimization_linux" / "qml"
    settings = (qml_root / "pages" / "SettingsPage.qml").read_text(encoding="utf-8")
    couch_settings = (qml_root / "couch" / "CouchSettings.qml").read_text(encoding="utf-8")
    sidebar = (qml_root / "components" / "Sidebar.qml").read_text(encoding="utf-8")

    assert 'title: qsTr("CPU usage limit")' not in settings
    assert 'title: qsTr("GPU usage limit")' not in settings
    assert 'title: qsTr("Interface sounds")' not in settings
    assert '"id": "sounds"' not in couch_settings
    assert '"id": "vibration"' not in couch_settings
    assert "Local Steam manifests" not in sidebar
    assert "updateColumn" not in sidebar
    for name in ("games", "narrator", "updates", "tasks", "system", "settings"):
        assert f"sidebar-{name}.svg" in sidebar
        assert (qml_root / "resources" / f"sidebar-{name}.svg").is_file()


def test_kde_breeze_read_only_preview_has_no_textarea_warning(tmp_path: Path) -> None:
    payload = _run_probe(
        tmp_path,
        "breeze",
        style="org.kde.breeze",
    )
    messages = payload["messages"]
    assert not any("TextArea" in message for message in messages)
    source = (ROOT / "src/game_optimization_linux/qml/pages/details/OptimizationTab.qml").read_text(
        encoding="utf-8"
    )
    assert "TextArea {" not in source


def test_sigint_cancels_analysis_and_stops_controller(tmp_path: Path) -> None:
    payload = _run_probe(tmp_path, "signal")
    result = payload["result"]
    assert result["requested_signal"] == signal_number("SIGINT")
    assert result["cancelled"] is True
    assert result["task_status"] == "cancelled"
    assert result["timer_active"] is False
    assert result["elapsed"] < 3.0


def test_window_warns_and_cancels_before_closing_active_compression(
    tmp_path: Path,
) -> None:
    payload = _run_probe(tmp_path, "close")
    result = payload["result"]
    assert result["warning_visible"] is True
    assert result["cancel_calls"] == 1
    assert result["active_after_confirmation"] is False
    assert result["window_visible_after_confirmation"] is False
    assert result["loaders_active_after_shutdown"]
    assert not any(result["loaders_active_after_shutdown"].values())


def test_tasks_page_survives_active_updates_and_rapid_navigation(tmp_path: Path) -> None:
    payload = _run_probe(tmp_path, "tasks")
    result = payload["result"]
    assert result["tasks"] > 0
    assert result["task_list_visible"] is True
    assert result["task_covers_inside"] > 0
    assert result["page"] == "tasks"
    assert not any("Cannot create delegate" in message for message in payload["messages"])
    assert not any("destroyed during incubation" in message for message in payload["messages"])


def test_mangohud_desktop_editor_renders_and_saves_appid_profile(tmp_path: Path) -> None:
    payload = _run_probe(tmp_path, "mangohud")
    result = payload["result"]
    assert result["app_id"] == "239140"
    assert result["preset"] == "basic"
    assert result["enabled"] is True
    assert result["profile_loaded"] is True
    assert result["available"] is True
    assert result["config_has_fps"] is True
    assert result["selected_executable"] == "Spelunky.exe"
    assert result["activation_strategy"] == "per_application_config"
    assert result["application_config"].endswith("/MangoHud/wine-Spelunky.conf")
    assert result["application_config_managed"] is True
    assert result["save_button_inside"] is True


def test_optimization_desktop_editor_saves_real_appid_profile(tmp_path: Path) -> None:
    payload = _run_probe(tmp_path, "optimization")
    result = payload["result"]
    assert result["app_id"] == "224760"
    assert result["preset"] == "quiet"
    assert result["target_fps"] == 45
    assert result["display_count"] >= 1
    assert result["steam_command"].endswith("--appid 224760 -- %command%")
    assert result["fps_limit_owner"] == "gamescope"
    assert "Gamescope" in result["fps_owner_label"]
    assert result["gamescope_r_count"] == 1
    assert result["has_legacy_framerate_limit"] is False
    assert result["optiscaler_plan_ready"] is True
    assert result["optiscaler_archive_format"] == "7Z"
    assert result["optiscaler_picker_filters"] == [
        "OptiScaler archives (*.7z *.zip)"
    ]
    assert result["optiscaler_install_directory"].endswith("/Binaries/Win64")
    assert result["optiscaler_proxy"] == "dxgi.dll"
    assert result["manual_setting_preview"] is True
    assert result["repeat_baseline_enabled"] is True
    assert result["terminal_baseline_retries"] == {
        "failed": True,
        "recorded_unrepresentative": True,
        "cancelled": True,
    }
    assert result["active_baseline_blocks_duplicate"] is True
    assert result["pending_test_blocks_baseline"] is True
    assert result["pending_automatic_blocks_baseline"] is True
    assert result["terminal_comparison_allows_baseline"] is True
    assert "Runner" in result["record_baseline_rejection"]
    assert result["automatic_card_visible"] is True
    assert result["automatic_candidate_visible"] is True
    assert result["save_button_inside"] is True
    assert not any("Unable to assign" in message for message in payload["messages"])
    assert result["screenshot_size"] > 1000


def test_backups_tab_is_removed_and_couch_exposes_optiscaler_status() -> None:
    details = (ROOT / "src/game_optimization_linux/qml/pages/GameDetailsPage.qml").read_text(
        encoding="utf-8"
    )
    couch = (ROOT / "src/game_optimization_linux/qml/couch/CouchGameDetails.qml").read_text(
        encoding="utf-8"
    )
    assert 'qsTr("Backups")' not in details
    assert "BackupsTab" not in details
    assert '{ "id": "backups"' not in couch
    assert '"id": "optiscaler-launch"' in couch
    assert '"id": "optiscaler-remove"' in couch


@pytest.mark.parametrize(
    "width,height",
    ((1600, 900), (1920, 1080), (2560, 1440), (3840, 2160)),
)
@pytest.mark.parametrize("scale", ("1.0", "1.25", "1.5", "1.75", "2.0"))
def test_couch_mode_geometry_at_target_resolutions(
    tmp_path: Path, width: int, height: int, scale: str
) -> None:
    payload = _run_probe(
        tmp_path,
        "couch",
        "--width",
        str(width),
        "--height",
        str(height),
        scale=scale,
    )
    result = payload["result"]
    assert result["games"] == 4
    assert result["scale"] > 0
    assert result["settings_visible"] is True
    assert result["details_tabs"] is True
    assert result["optimization_overlay"] is True
    assert result["optimization_back_cancelled"] is True
    assert result["optimization_saved_preset"] == "maximum_performance"
    assert result["mangohud_overlay"] is True
    assert result["mangohud_back_focus"] is True
    assert result["mangohud_saved_preset"] == "fps_only"
    assert result["system_menu_safe_default"] is True
    assert result["system_menu_entries"] == 6
    assert result["quit_confirmation"] is True
    assert result["details_return_game"] == "dying-light"
    assert result["settings_back_target"] == "tasks"
    assert result["home_back_opens_menu"] is True
    assert result["game_context_menu"] is True
    assert result["modal_layers_exclusive"] is True
    assert result["focus_after_menu"] != "couchSystemMenu"
    assert result["focus_after_menu_visible"] is True
    assert result["library_grid_valid"] is True
    metrics = result["tv_metrics"]
    couch_scale = result["scale"]
    assert metrics["homeTitlePx"] >= 36 * couch_scale
    assert metrics["homeActionMinHeight"] >= 56 * couch_scale
    assert metrics["homeCardMaxWidth"] >= 220 * couch_scale
    assert metrics["homeCardMaxHeight"] >= 330 * couch_scale
    assert metrics["homeNavigationHeight"] >= 64 * couch_scale
    assert 6 <= metrics["libraryColumns"] <= 8
    assert metrics["libraryCellWidth"] >= 210 * couch_scale
    assert metrics["detailsCoverWidth"] >= 260 * couch_scale
    assert metrics["detailsCoverHeight"] / metrics["detailsCoverWidth"] >= 1.45
    assert metrics["detailsTitlePx"] >= 36 * couch_scale
    assert metrics["detailsContentHeight"] >= 250 * couch_scale


@pytest.mark.parametrize("theme", ("light", "dark"))
@pytest.mark.parametrize("scenario", ("one", "many", "disconnected", "long"))
def test_couch_mode_library_states_and_themes(
    tmp_path: Path, theme: str, scenario: str
) -> None:
    payload = _run_probe(
        tmp_path,
        "couch",
        "--width",
        "1920",
        "--height",
        "1080",
        "--theme",
        theme,
        "--scenario",
        scenario,
    )
    result = payload["result"]
    assert result["scenario"] == scenario
    assert result["theme"] == theme
    if scenario == "many":
        assert result["retained_game"]


@pytest.mark.parametrize(
    "width,height",
    ((1920, 1080), (2560, 1440), (3840, 2160)),
)
@pytest.mark.parametrize(
    "scenario",
    ("empty", "one", "many", "long", "disconnected", "active", "error"),
)
def test_updates_desktop_and_couch_runtime_states(
    tmp_path: Path,
    width: int,
    height: int,
    scenario: str,
) -> None:
    payload = _run_probe(
        tmp_path,
        "updates",
        "--width",
        str(width),
        "--height",
        str(height),
        "--scenario",
        scenario,
    )
    result = payload["result"]
    assert result["resolution"] == [width, height]
    assert result["scenario"] == scenario

    desktop = result["desktop"]
    couch = result["couch"]
    if scenario == "empty":
        assert desktop["cards"] == 0
        assert couch["cards"] == 0
        assert desktop["emptyVisible"] is True
        assert couch["emptyVisible"] is True
    else:
        assert desktop["cards"] > 0
        assert couch["cards"] > 0

    if scenario == "many":
        assert desktop["reversedCards"] > 0
        assert couch["retainedGame"]
        assert couch["confirmationStarted"] is True
        assert couch["prepareCalls"] == 1
        assert couch["startCalls"] == 1

    messages = payload["messages"]
    assert not any("Cannot create delegate" in message for message in messages)
    assert not any("destroyed during incubation" in message for message in messages)
    assert not any("Unable to assign" in message for message in messages)


@pytest.mark.parametrize("scale", ("1.0", "1.25", "1.5", "1.75"))
def test_games_filter_popups_use_one_overlay_and_close_cleanly(
    tmp_path: Path, scale: str
) -> None:
    payload = _run_probe(tmp_path, "popups", scale=scale)
    result = payload["result"]
    for key in (
        "firstOpened",
        "firstClosedBySecond",
        "secondOpened",
        "escapeClosed",
        "outsideClosePolicy",
        "focusReturned",
        "pageChangeClosed",
        "pageStayedStill",
        "overlayParent",
        "positionedBelow",
        "positionAfterResize",
        "positionAfterScroll",
    ):
        assert result[key] is True
    assert result["popupAtOrigin"] is False
    assert result["popupZ"] == 10000.0


def test_game_artwork_stays_bound_to_game_id_when_grid_reuses_items(
    tmp_path: Path,
) -> None:
    payload = _run_probe(tmp_path, "artwork")
    result = payload["result"]
    assert result["reuseItems"] is True
    assert result["scrollCycles"] == 3
    assert result["firstSourceStable"] is True
    assert result["filterRestoreStable"] is True
    assert result["visibleAfterRestore"] > 0
    assert result["onPooledLogged"] is True
    assert result["onReusedLogged"] is True


def test_game_artwork_remains_ready_through_refresh_scroll_and_filter(
    tmp_path: Path,
) -> None:
    payload = _run_probe(tmp_path, "artwork_refresh")
    result = payload["result"]
    assert set(result["stages"]) == {
        "loaded",
        "modelRefreshed",
        "scrolledDown",
        "returnedTop",
        "filterChanged",
        "filterRestored",
        "listMode",
        "gridModeRestored",
            "gamesUpdatesGames",
            "transientSourceGap",
            "failedReplacementRecovered",
            "fullRefresh",
        "restart",
    }
    for stage in result["stages"].values():
        for game_id in ("steam-242550", "steam-204360"):
            assert stage[game_id]["effectiveArtworkUrl"].startswith("file:///")
            assert stage[game_id]["qmlImageStatus"] == "Ready"
    rayman = result["steam242550"]
    assert rayman["pythonQUrl"].startswith("file:///")
    assert rayman["pythonQUrl"] == rayman["qmlImageSource"]
    assert rayman["qmlImageStatus"] == "Ready"
    diagnostics = result["lifecycleDiagnostics"]
    assert diagnostics["events"] > 0
    assert diagnostics["hasVisible"] is True
    assert diagnostics["hasGridCurrent"] is True
    assert diagnostics["hasImageSource"] is True
    assert diagnostics["hasImageStatus"] is True
    assert diagnostics["sawHidden"] is True
    assert diagnostics["sawGrid"] is True
    assert diagnostics["sawList"] is True
    assert diagnostics["committedBeforeTransientGap"] is True
    assert diagnostics["transientGapProtected"] is True
    assert diagnostics["failedReplacementPreserved"] is True
    assert diagnostics["staleCallbackIgnored"] is True
    assert diagnostics["artworkPixelsVisible"] is True


def test_incremental_games_model_keeps_existing_delegates_and_images(
    tmp_path: Path,
) -> None:
    payload = _run_probe(tmp_path, "incremental_games")
    result = payload["result"]
    assert result["initial"]["inserted"] == 10
    assert result["identicalChanged"] == [False] * 10
    assert result["serialsStableAfterIdentical"] is True
    assert result["imageRequestsAfterIdentical"] == result["imageRequestsInitial"]
    assert result["singleChange"]["updated"] == 1
    assert result["dataChangedRows"] == [[4, 4]]
    assert result["serialsStableAfterChange"] is True
    assert result["singleAdd"]["inserted"] == 1
    assert result["existingSerialsStableAfterAdd"] is True
    assert result["destroyedBeforeClose"] == 0
    assert result["modelResetCount"] == 0
    assert result["modelResetSignals"] == 0


def signal_number(name: str) -> int:
    import signal

    return int(getattr(signal, name))
