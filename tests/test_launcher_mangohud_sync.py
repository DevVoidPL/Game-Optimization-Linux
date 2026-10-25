"""MangoHud profile saves keep the launcher's host config in sync (round 23).

Temporary HOME/XDG and Heroic fixtures only.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from threading import Event
import time

import pytest

import game_optimization_linux.controllers.optimization_controller as optimization_module
from game_optimization_linux.models import FilesystemType, Game, Launcher
from game_optimization_linux.services import launcher_mangohud
from game_optimization_linux.services.launcher_mangohud import (
    LauncherMangoHudError,
    launcher_mangohud_path,
    write_launcher_mangohud,
)
from test_heroic_optiscaler import APP_NAME
from test_heroic_tool_plans import heroic  # noqa: F401 - pytest fixture


def _heroic_json(tmp_path: Path) -> Path:
    return tmp_path / ".var/app/com.heroicgameslauncher.hgl/config/heroic/GamesConfig" / f"{APP_NAME}.json"


def _save(controller, game, **changes) -> dict:
    payload = dict(controller.getMangoHudProfile(game.id))
    payload.update({"enabled": True, "preset": "extended"})
    payload.update(changes)
    result = controller.saveMangoHudProfile(game.id, payload)
    assert result["success"] is True, result
    return result


def _wait_status(controller, game, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    status = controller.getLauncherIntegration(game.id)
    while status["state"] == "syncing" and time.monotonic() < deadline:
        time.sleep(0.02)
        status = controller.getLauncherIntegration(game.id)
    return status


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_color_change_updates_host_config_without_touching_heroic_json(heroic, tmp_path) -> None:
    game, controller, _plan = heroic(mangohud=True, gamescope=False)
    assert controller.applyLauncherIntegration(game.id)["state"] == "applied"
    host = launcher_mangohud_path(game.profile_key)
    heroic_json = _heroic_json(tmp_path)
    before_json = heroic_json.read_bytes()
    assert "text_color" not in host.read_text()

    result = _save(controller, game, textColor="FF0000")
    assert result["launcherSync"]["state"] == "syncing"
    status = _wait_status(controller, game)
    assert status["state"] == "applied" and status["configCurrent"] is True
    assert "text_color=FF0000" in host.read_text()
    assert heroic_json.read_bytes() == before_json


def test_layout_change_keeps_private_and_host_configs_identical(heroic) -> None:
    game, controller, _plan = heroic(mangohud=True, gamescope=False)
    controller.applyLauncherIntegration(game.id)
    _save(controller, game, compact=True, horizontal=True)
    _wait_status(controller, game)
    private = controller._mangohud_repository.config_path(game.profile_key)
    host = launcher_mangohud_path(game.profile_key)
    assert _sha(private) == _sha(host)
    assert "hud_compact" in host.read_text() and "horizontal" in host.read_text()


def test_after_restore_saves_do_not_export_and_state_is_not_applied(heroic) -> None:
    game, controller, _plan = heroic(mangohud=True, gamescope=False)
    controller.applyLauncherIntegration(game.id)
    controller.restoreLauncherIntegration(game.id)
    host = launcher_mangohud_path(game.profile_key)
    before = host.read_bytes()
    result = _save(controller, game, textColor="00FF00")
    assert result["launcherSync"]["state"] == "not_applied"
    time.sleep(0.1)
    assert host.read_bytes() == before  # the harmless old file is left alone
    assert controller.getLauncherIntegration(game.id)["state"] == "not_applied"
    # A new Apply exports the latest profile, not the old host look.
    controller.applyLauncherIntegration(game.id)
    assert "text_color=00FF00" in host.read_text()


def test_first_apply_exports_the_latest_profile(heroic) -> None:
    game, controller, _plan = heroic(mangohud=True, gamescope=False)
    result = _save(controller, game, textColor="123456")
    assert result["launcherSync"]["state"] == "not_applied"
    host = launcher_mangohud_path(game.profile_key)
    assert not host.exists()
    controller.applyLauncherIntegration(game.id)
    assert "text_color=123456" in host.read_text()
    assert controller.getLauncherIntegration(game.id)["configCurrent"] is True


def test_foreign_mangohud_configfile_is_refused(heroic, tmp_path) -> None:
    game, controller, _plan = heroic(mangohud=True, gamescope=False)
    controller.applyLauncherIntegration(game.id)
    foreign = tmp_path / "home/.config/MangoHud/MangoHud.conf"
    foreign.write_text("fps\n", encoding="utf-8")
    # Point both the record and Heroic's entry at another file.
    record_path = tmp_path / "state/launcher-integration" / f"{game.id}.json"
    record = json.loads(record_path.read_text())
    record["env"]["MANGOHUD_CONFIGFILE"]["value"] = str(foreign)
    record_path.write_text(json.dumps(record))
    document = json.loads(_heroic_json(tmp_path).read_text())
    for item in document[APP_NAME]["enviromentOptions"]:
        if item["key"] == "MANGOHUD_CONFIGFILE":
            item["value"] = str(foreign)
    _heroic_json(tmp_path).write_text(json.dumps(document))
    result = _save(controller, game, textColor="ABCDEF")
    assert result["launcherSync"]["state"] == "refused"
    assert foreign.read_text() == "fps\n"


def test_symlink_and_foreign_owner_are_refused(tmp_path, monkeypatch) -> None:
    directory = tmp_path / ".config/MangoHud"
    directory.mkdir(parents=True)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    target = directory / "gameopti-heroic-legendary-abc.conf"
    target.symlink_to(victim)
    with pytest.raises(LauncherMangoHudError, match="not a regular file"):
        write_launcher_mangohud(target, "fps\n")
    assert victim.read_text() == "keep"
    target.unlink()
    target.write_text("old\n", encoding="utf-8")
    real_uid = os.getuid()
    monkeypatch.setattr(launcher_mangohud.os, "getuid", lambda: real_uid + 1)
    with pytest.raises(LauncherMangoHudError, match="another user"):
        write_launcher_mangohud(target, "fps\n")
    assert target.read_text() == "old\n"


def test_only_gameopti_files_can_be_written(tmp_path) -> None:
    directory = tmp_path / ".config/MangoHud"
    directory.mkdir(parents=True)
    for name in ("MangoHud.conf", "gameopti-../x.conf", "gameopti-heroic-a.txt"):
        with pytest.raises((LauncherMangoHudError, ValueError)):
            write_launcher_mangohud(directory / name, "fps\n")


def test_failed_write_keeps_the_previous_complete_file(tmp_path, monkeypatch) -> None:
    directory = tmp_path / ".config/MangoHud"
    directory.mkdir(parents=True)
    target = directory / "gameopti-heroic-legendary-abc.conf"
    target.write_text("old complete\n", encoding="utf-8")

    def broken(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(launcher_mangohud.os, "replace", broken)
    with pytest.raises(OSError):
        write_launcher_mangohud(target, "new\n")
    assert target.read_text() == "old complete\n"
    assert [p.name for p in directory.iterdir()] == [target.name]  # no temp left


def test_sync_runs_off_the_gui_thread(heroic, monkeypatch) -> None:
    game, controller, _plan = heroic(mangohud=True, gamescope=False)
    controller.applyLauncherIntegration(game.id)
    release = Event()
    real = optimization_module.write_launcher_mangohud

    def slow(*args, **kwargs):
        release.wait(20.0)
        return real(*args, **kwargs)

    monkeypatch.setattr(optimization_module, "write_launcher_mangohud", slow)
    started = time.monotonic()
    result = _save(controller, game, textColor="FF00FF")
    status = controller.getLauncherIntegration(game.id)
    assert time.monotonic() - started < 2.0
    assert result["launcherSync"]["state"] == "syncing" and status["state"] == "syncing"
    assert status["canApply"] is False
    release.set()
    assert _wait_status(controller, game)["configCurrent"] is True


def test_sync_error_shows_outdated_with_retry(heroic, monkeypatch) -> None:
    game, controller, _plan = heroic(mangohud=True, gamescope=False)
    controller.applyLauncherIntegration(game.id)

    real = optimization_module.write_launcher_mangohud

    def fail(*_args, **_kwargs):
        raise LauncherMangoHudError("read-only")

    monkeypatch.setattr(optimization_module, "write_launcher_mangohud", fail)
    _save(controller, game, textColor="FFFF00")
    status = _wait_status(controller, game)
    assert status["state"] == "config_outdated" and status["canSyncConfig"] is True
    assert "read-only" in status["message"]
    monkeypatch.setattr(optimization_module, "write_launcher_mangohud", real)
    retried = controller.syncLauncherMangoHudConfig(game.id)
    assert retried["sync"]["state"] == "syncing"
    assert _wait_status(controller, game)["configCurrent"] is True


def test_steam_games_are_not_synchronized_by_this_path(heroic, tmp_path) -> None:
    _game, controller, _plan = heroic(mangohud=True, gamescope=False)
    steam = Game(
        id="steam-1", steam_app_id="1", name="S", launcher=Launcher.STEAM,
        install_path=tmp_path, logical_size_gb=0, physical_size_gb=0,
        filesystem=FilesystemType.EXT4, compression_available=False, data_source="Steam",
    )
    assert controller._optimization_controller.schedule_launcher_mangohud_sync(steam)["state"] == "not_launcher"
