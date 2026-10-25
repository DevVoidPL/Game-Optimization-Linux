"""Regression: GTA 0x204 paused shader update is not an active content write."""
from dataclasses import replace
import pytest
from game_optimization_linux.providers.steam import steam_update_activity, SteamGameProvider
from test_frame_generation import environment


def manifest(game, flags, app_id=None):
    path = game.install_path.parent / f"appmanifest_{game.steam_app_id}.acf"
    path.write_text('"AppState" { "appid" "' + str(app_id or game.steam_app_id) + '" "StateFlags" "' + str(flags) + '" "BytesToDownload" "0" }')
    return replace(game, steam_manifest_path=path)


@pytest.mark.parametrize("flags", [4, 12, 516, 8196, 16388])
def test_nonwriting_states_are_not_updates(environment, flags):
    service, game = environment
    game = manifest(replace(game, update_in_progress=True), flags)
    assert not SteamGameProvider._steam_update_in_progress(flags)
    observed = steam_update_activity(game)
    assert observed["fresh"] and not observed["blocked"] and observed["appId"] == "123"
    service.post._safe(game, service.post.executable(game))
    service.post.optiscaler._assert_mutation_allowed(game)


@pytest.mark.parametrize("flags", [260, 1028, 1048580, 2097156, 4194308, 8388612, (1 << 25) | 4, 514])
def test_active_or_incomplete_writes_remain_blocked(environment, flags):
    service, game = environment
    game = manifest(game, flags)
    assert SteamGameProvider._steam_update_in_progress(flags)
    assert steam_update_activity(game)["blocked"]
    with pytest.raises(ValueError, match="Steam update"): service.post._safe(game, service.post.executable(game))
    with pytest.raises(RuntimeError, match="Steam update"): service.post.optiscaler._assert_mutation_allowed(game)


def test_recheck_uses_this_game_not_stale_scan_or_other_game(environment):
    service, game = environment
    game = manifest(replace(game, update_in_progress=True), 516)
    other = game.install_path.parent / "appmanifest_124.acf"
    other.write_text('"AppState" {"appid" "124" "StateFlags" "2097156"}')
    assert steam_update_activity(game)["state"] == "paused"
    manifest(game, 260)
    assert steam_update_activity(game)["blocked"]
    manifest(game, 4)
    assert not steam_update_activity(game)["blocked"]


def test_unknown_manifest_is_actionable_and_fail_closed(environment):
    _, game = environment
    game = manifest(game, 4, app_id="124")
    assert steam_update_activity(game)["state"] == "unknown"
    assert steam_update_activity(game)["blocked"]
    game.steam_manifest_path.unlink()
    assert steam_update_activity(game)["blocked"]


def test_game_running_remains_a_separate_guard(environment):
    service, game = environment
    game = manifest(game, 8196)
    service.post.optiscaler._process_detector = lambda _: ["Game.exe"]
    assert not steam_update_activity(game)["blocked"]
    with pytest.raises(ValueError, match="game is currently running"):
        service.post._safe(game, service.post.executable(game))
