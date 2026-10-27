"""Custom/Manual entries in the measured optimization engine: identity,
runner classification, game-process matching and candidate capabilities.
Disposable files only."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from game_optimization_linux.models import Launcher, ManualGameConfig
from game_optimization_linux.models.mangohud import validate_game_key
from game_optimization_linux.services import measured_optimization as mo
from game_optimization_linux.services.measured_optimization import manual
from game_optimization_linux.services.measured_optimization.capabilities import REASONS
from tests.test_measured_optimization_candidates import ctx

UUID_A = "manual-11111111-1111-4111-8111-111111111111"
UUID_B = "manual-22222222-2222-4222-8222-222222222222"


def config(tmp_path: Path, *, identifier=UUID_A, name="game", body=b"\x7fELF", runner=(), **values) -> ManualGameConfig:
    root = tmp_path / "Game Dir"
    root.mkdir(exist_ok=True)
    exe = root / name
    exe.write_bytes(body)
    exe.chmod(0o755)
    return ManualGameConfig(id=identifier, name="Custom", executable=exe, install_directory=root,
                            runner_command=tuple(runner), **values)


def game(identifier=UUID_A, **values):
    base = dict(id=identifier, launcher=Launcher.MANUAL, data_source="Manual", profile_key="", steam_app_id=None)
    base.update(values)
    return SimpleNamespace(**base)


def test_stable_identity_survives_edits_and_stays_separate(tmp_path: Path) -> None:
    key = manual.optimization_key(game())
    assert validate_game_key(key) == key and key.startswith("local-")
    first = config(tmp_path)
    edited = replace(first, name="Renamed", executable=first.install_directory / "other",
                     portrait_artwork=tmp_path / "cover.png")
    assert edited.id == first.id
    assert manual.optimization_key(game(edited.id)) == key  # name, path and artwork do not matter
    assert manual.optimization_key(game(UUID_B)) != key
    steam = game("123", launcher=Launcher.STEAM, data_source="Steam", profile_key="123", steam_app_id="123")
    assert manual.optimization_key(steam) == "123" != key  # the Steam entry of the same executable
    heroic = game("heroic-legendary-x", launcher=Launcher.HEROIC, data_source="Heroic", profile_key="heroic-legendary-x")
    assert manual.optimization_key(heroic) == "heroic-legendary-x"
    # Other integrations keep their own namespaced keys for the same UUID.
    from game_optimization_linux.services.custom_launch import launch_key
    from game_optimization_linux.services.gpu_memory import game_key
    assert len({key, launch_key(game()), game_key(game())}) == 3
    assert manual.optimization_key(game("manual-demo-1")) == ""
    assert manual.optimization_key(None) == ""


def test_runner_classification_never_guesses(tmp_path: Path) -> None:
    assert manual.runner_info(config(tmp_path))["kind"] == "manual_native"
    windows = config(tmp_path, name="Game.exe", body=b"MZ\0\0")
    assert manual.runner_info(windows)["code"] == "manual_windows_no_runner"
    disguised = config(tmp_path, name="game.bin", body=b"MZ\0\0")
    assert manual.runner_info(disguised)["code"] == "manual_windows_no_runner"
    wine = tmp_path / "bin" / "wine"
    wine.parent.mkdir()
    wine.write_text("#!/bin/sh\n")
    assert manual.runner_info(replace(windows, runner_command=(str(wine),)))["kind"] == "manual_wine"
    proton = tmp_path / "GE-Proton-Test" / "proton"
    proton.parent.mkdir()
    proton.write_text("#!/bin/sh\nPROTON_NO_NTSYNC\n")
    (proton.parent / "version").write_text("1 GE-Proton-Test\n")
    info = manual.runner_info(replace(windows, runner_command=("python3", str(proton), "run")))
    assert info["kind"] == "manual_proton" and info["runnerVersion"] == "GE-Proton-Test"
    assert manual.runner_info(replace(windows, runner_command=("heroic",)))["code"] == "manual_external_launcher"
    assert manual.runner_info(config(tmp_path, name="steam"))["code"] == "manual_external_launcher"
    assert manual.runner_info(replace(windows, runner_command=("mytool",)))["code"] == "manual_unknown_runner"
    for code in ("manual_windows_no_runner", "manual_external_launcher", "manual_unknown_runner", "manual_tree_unverified"):
        assert REASONS[code]


def test_process_matcher_excludes_helpers_and_runners(tmp_path: Path) -> None:
    native = manual.ManualProcessMatcher(config(tmp_path), "manual_native")
    exe = str(tmp_path / "Game Dir" / "game")
    assert native.matches(exe) and native.matches(f"{exe} --fullscreen")
    for helper in (f"/bin/sh {tmp_path}/Game Dir/pre.sh", f"/usr/bin/mywrapper {exe}", f"systemd-run --user --scope -- {exe}",
                   f"{exe}-helper", f"/usr/bin/gamemoderun {exe}"):
        assert not native.matches(helper), helper
    windows = manual.ManualProcessMatcher(config(tmp_path, name="Game.exe", body=b"MZ"), "manual_wine")
    win = "Z:" + str(tmp_path / "Game Dir" / "Game.exe").replace("/", "\\")
    assert windows.matches(win) and windows.matches(f"{win} -dx12") and windows.matches("C:\\Games\\Game.exe")
    for helper in (f"python3 /opt/proton run {tmp_path}/Game Dir/Game.exe", "/usr/bin/wine Game.exe",
                   "Z:\\x\\NotGame.exe", "Z:\\x\\Game.exe.bak", "wineserver"):
        assert not windows.matches(helper), helper
    assert windows.running(None) is None and windows.running([win]) is True


def test_manual_launch_hash_ignores_name_and_artwork(tmp_path: Path) -> None:
    first = config(tmp_path, pre_launch=("/bin/true",))
    assert manual.launch_profile_hash(first) == manual.launch_profile_hash(replace(first, name="Other",
                                                                                   header_artwork=tmp_path / "h.png"))
    assert manual.launch_profile_hash(first) != manual.launch_profile_hash(replace(first, pre_launch=("/bin/false",)))
    assert manual.launch_profile_hash(first) != manual.launch_profile_hash(replace(first, arguments=("-x",)))


def test_manual_capabilities_and_custom_launch_conflicts() -> None:
    native = {row["id"]: row for row in mo.evaluate(ctx(launcher="manual_native", explicit_experiments=True))}
    assert native["gamemode"]["applicability"]["available"]
    assert native["proton_no_ntsync"]["applicability"]["reason"] == "PROTON_NO_NTSYNC is read only by a Proton runner"
    assert "Steam runner" in native["vkd3d_no_upload_hvv"]["applicability"]["reason"]
    assert native["gpu_memory_priority"]["applicability"]["code"] == "gpu_memory_not_ready"
    proton = {row["id"]: row for row in mo.evaluate(ctx(launcher="manual_proton", explicit_experiments=True, proton=True,
                                                        proton_support={"no_ntsync": "present"}))}
    assert proton["proton_no_ntsync"]["applicability"]["available"]
    conflicted = {row["id"]: row for row in mo.evaluate(ctx(
        launcher="manual_proton", explicit_experiments=True, proton=True, proton_support={"no_ntsync": "present"},
        user_environment=frozenset({"PROTON_NO_NTSYNC"}), user_programs=("gamemoderun",)))}
    assert conflicted["proton_no_ntsync"]["applicability"]["code"] == "custom_launch_conflict"
    assert conflicted["gamemode"]["applicability"]["code"] == "custom_launch_conflict"
    assert "launch configuration" in conflicted["proton_no_ntsync"]["applicability"]["reason"]
    for kind in ("manual_native", "manual_wine", "manual_proton"):
        assert mo.support(kind, "")["supported"] and mo.support(kind, "")["recording"]
    assert mo.support("manual", "manual_windows_no_runner")["supported"] is False
