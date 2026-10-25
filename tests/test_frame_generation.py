"""Disposable installations, per-game isolation and real launch contracts."""
import copy
from dataclasses import replace
from hashlib import sha256
import io
import json
from pathlib import Path
import struct
import tarfile
from threading import Event
from types import SimpleNamespace

import pytest

from game_optimization_linux.models import Game, Launcher, FilesystemType, GameOptimizationProfile
from game_optimization_linux.services.frame_generation import (
    DEFAULTS, FrameGenerationService, checked_settings, launch_settings, load_profile, profile_path, render_config,
)
from game_optimization_linux.services.frame_generation_artifacts import FrameGenerationArtifacts, PACKAGES, Cancelled
from game_optimization_linux.services.frame_generation_runner_setup import launch_option_span, configure_steam
from game_optimization_linux.services.fsr_variants import PinnedArchive
from game_optimization_linux.services.optimization_runtime import OptimizationLaunchPlanner, RuntimeToolAvailability
from game_optimization_linux.services.optiscaler import OptiScalerService, OptiScalerProfileRepository
from game_optimization_linux.services.post_processing import PostProcessingService


def archive_bytes(backend="lsfg", payload=b"fixture layer"):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:xz") as archive:
        name = "lib/liblsfg-vk-layer.so" if backend == "lsfg" else "afmf-linux-1.3.1/libafmf-linux.so"
        entries = {name: payload}
        if backend == "afmf": entries["afmf-linux-1.3.1/afmf-linux.json"] = json.dumps({"layer": {"name": "VK_LAYER_AFMF"}}).encode()
        for name, data in entries.items():
            entry = tarfile.TarInfo(name); entry.size = len(data); archive.addfile(entry, io.BytesIO(data))
    return output.getvalue()


@pytest.fixture
def artifacts(tmp_path, monkeypatch):
    data = archive_bytes()
    monkeypatch.setitem(PACKAGES, "lsfg", PinnedArchive("test", "https://example.invalid/test.tar.xz", len(data), sha256(data).hexdigest()))
    return FrameGenerationArtifacts(tmp_path / "components", opener=lambda *a, **k: io.BytesIO(data))


def test_install_remove_is_private_and_preserves_profiles(artifacts):
    root = artifacts.root
    (root / "user-preset").parent.mkdir(parents=True); (root / "user-preset").write_text("keep")
    phases = []
    state = artifacts.install("lsfg", progress=lambda p, v: phases.append((p, v)))
    assert state["installed"] and state["managed"]
    assert not (root / "vulkan/implicit_layer.d").exists()
    assert phases[-1] == ("complete", 1.0)
    artifacts.remove("lsfg")
    assert not artifacts.status("lsfg")["installed"]
    assert (root / "user-preset").read_text() == "keep"


def test_modified_or_unknown_files_are_not_overwritten(artifacts):
    artifacts.install("lsfg")
    target = artifacts.directory("lsfg") / "lib/liblsfg-vk-layer.so"
    target.write_bytes(b"independent edit")
    for action in (artifacts.install, artifacts.remove):
        with pytest.raises(ValueError, match="modified"): action("lsfg")
    assert target.read_bytes() == b"independent edit"


def test_unknown_component_directory_is_preserved(artifacts):
    root = artifacts.directory("lsfg"); root.mkdir(parents=True)
    (root / "mod").write_text("owner")
    with pytest.raises(ValueError, match="unowned"): artifacts.install("lsfg")
    assert (root / "mod").read_text() == "owner"


def test_unknown_files_survive_update_and_interrupted_update_recovers(artifacts):
    artifacts.install("lsfg")
    directory = artifacts.directory("lsfg")
    custom = directory / "user-preset.ini"; custom.write_text("keep")
    with pytest.raises(ValueError, match="Unknown files"): artifacts.install("lsfg")
    assert custom.read_text() == "keep"
    custom.unlink()
    backup = artifacts.root / ".previous-lsfg-fixture"
    staging = artifacts.root / ".stage-lsfg-fixture"
    import shutil
    shutil.copytree(directory, staging); directory.rename(backup)
    journal = artifacts.root / ".transaction-lsfg.json"
    journal.write_text(json.dumps({"staging": staging.name, "backup": backup.name}))
    assert "Interrupted" in artifacts.status("lsfg")["error"]
    assert not directory.exists(), "a status query must not repair/reconfigure"
    assert artifacts.install("lsfg")["installed"]
    assert not backup.exists() and not staging.exists() and not journal.exists()


def test_cancellation_and_failure_leave_previous_installation(artifacts):
    artifacts.install("lsfg")
    original = (artifacts.directory("lsfg") / "gameopti-install.json").read_bytes()
    cancel = Event(); cancel.set()
    with pytest.raises(Cancelled): artifacts.install("lsfg", cancel)
    assert (artifacts.directory("lsfg") / "gameopti-install.json").read_bytes() == original
    assert not list(artifacts.root.glob(".stage-*"))


def test_download_failure_checksum_and_mid_download_cancellation(artifacts):
    artifacts.opener = lambda *a, **k: io.BytesIO(b"bad data")
    with pytest.raises(ValueError, match="verification"): artifacts.install("lsfg")
    assert not artifacts.directory("lsfg").exists()
    assert not list((artifacts.root / "downloads").glob(".download-*"))
    artifacts.opener = lambda *a, **k: (_ for _ in ()).throw(OSError("offline"))
    with pytest.raises(OSError, match="offline"): artifacts.install("lsfg")
    data = archive_bytes(); artifacts.opener = lambda *a, **k: io.BytesIO(data)
    cancel = Event()
    with pytest.raises(Cancelled): artifacts.install("lsfg", cancel, lambda *a: cancel.set())
    assert not artifacts.directory("lsfg").exists()


def test_archive_path_escape_is_rejected(artifacts, monkeypatch):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:xz") as archive:
        entry = tarfile.TarInfo("../../escape"); entry.size = 1; archive.addfile(entry, io.BytesIO(b"x"))
    data = buffer.getvalue()
    monkeypatch.setitem(PACKAGES, "lsfg", PinnedArchive("test", "https://example.invalid/x.tar.xz", len(data), sha256(data).hexdigest()))
    artifacts.opener = lambda *a, **k: io.BytesIO(data)
    with pytest.raises(ValueError, match="Unsafe"): artifacts.install("lsfg")
    assert not (artifacts.root.parent / "escape").exists()


@pytest.fixture
def environment(tmp_path):
    root = tmp_path / "game"; root.mkdir()
    data = bytearray(512); data[:2] = b"MZ"; struct.pack_into("<I", data, 60, 128)
    data[128:132] = b"PE\0\0"; struct.pack_into("<H", data, 132, 0x8664)
    (root / "Game.exe").write_bytes(data); (root / "dxgi.dll").write_bytes(b"working upscaler")
    game = Game(id="steam-123", steam_app_id="123", name="Test", launcher=Launcher.STEAM, install_path=root,
                executable_path="Game.exe", filesystem=FilesystemType.EXT4, logical_size_gb=1,
                physical_size_gb=1, compression_available=False)
    host = SimpleNamespace(game_process_modules=lambda _: (), frame_generation_capabilities=lambda: {"available": True, "compute": True, "storageImages": True},
        frame_generation_environment=lambda *a: {"available": True}, host_process_commands=lambda: ())
    opti = OptiScalerService(profile_repository=OptiScalerProfileRepository(tmp_path / "profiles"),
                            data_root=tmp_path / "data", process_detector=lambda _: [])
    service = FrameGenerationService(PostProcessingService(opti, host), FrameGenerationArtifacts(tmp_path / "components"))
    return service, game


def test_off_and_profile_isolation_preserve_upscaler(environment, monkeypatch):
    service, game = environment
    monkeypatch.setattr(service, "status", lambda game: load_profile(game.profile_key, service.root))
    state = service.apply(game, {"backend": "off", "settings": {"mako": {"multiplier": 4}}})
    assert state["settings"]["mako"]["multiplier"] == 4
    assert load_profile("124", service.root)["settings"]["mako"]["multiplier"] == 2
    env, prefix = launch_settings("123", service.root, inherited={"VK_INSTANCE_LAYERS": "VK_LAYER_AFMF:VK_LAYER_other"})
    assert env["DISABLE_LSFGVK"] == env["DISABLE_MAKO"] == env["DISABLE_AFMF"] == "1"
    assert env["VK_INSTANCE_LAYERS"] == "VK_LAYER_other" and not prefix
    assert (game.install_path / "dxgi.dll").read_bytes() == b"working upscaler"
    assert launch_settings("124", service.root) == ({}, [])


def test_backend_configuration_matches_versioned_toml_contract():
    import tomllib
    lsfg = tomllib.loads(render_config("lsfg", "123", "Game.exe", "/user/lsfg-vk.dll", DEFAULTS["lsfg"], Path("/log")))
    mako = tomllib.loads(render_config("mako", "123", "Game.exe", "/user/Lossless.dll", DEFAULTS["mako"], Path("/log")))
    assert lsfg["version"] == mako["version"] == 2
    assert lsfg["profile"][0]["pacing"] == "vsync"
    assert mako["profile"][0]["pacing"] == "none"
    assert mako["profile"][0]["frame_generation_enabled"] is True
    assert not mako["profile"][0]["scaling_enabled"]
    assert "adaptive" not in lsfg["profile"][0]


@pytest.mark.parametrize("values", [{"multiplier": 1}, {"multiplier": 2.5}, {"adaptive": True}, {"flow_scale": float("nan")}, {"performance_mode": "true"}])
def test_invalid_or_invented_settings_rejected(values):
    with pytest.raises(ValueError): checked_settings("lsfg", values)


def test_unknown_api_requires_explicit_selection(environment):
    service, game = environment
    with pytest.raises(ValueError, match="inconclusive"): service.apply(game, {"backend": "afmf"})
    assert not profile_path("123", service.root).exists()


def test_plan_preserves_postprocessing_and_isolates_gamescope(environment):
    service, _ = environment
    profile = GameOptimizationProfile(schema_version=1, app_id="123", gamescope_enabled=True, gamescope_mode="native")
    tool = RuntimeToolAvailability(name="Gamescope", executable="/usr/bin/gamescope", available=True, supported_options=("-W", "-H", "-r", "-f", "-b"))
    plan = OptimizationLaunchPlanner().build(profile, ["proton", "run", "Game.exe", "argument with spaces"],
        gamemode=replace(tool, available=False), gamescope=tool,
        frame_generation_environment={"DISABLE_MAKO": "0", "MAKO_CONFIG": "/tmp/game config.toml", "VK_INSTANCE_LAYERS": "VK_LAYER_other"},
        frame_generation_prefix=["env", "-u", "MAKO_ENV", "/private/mako-launch"],
        post_processing_environment={"VK_INSTANCE_LAYERS": "VK_LAYER_VKBASALT_post_processing"})
    assert list(plan.command[-4:]) == ["proton", "run", "Game.exe", "argument with spaces"]
    assert "/private/mako-launch" in plan.command
    assert "VK_LAYER_VKBASALT_post_processing" in plan.environment["VK_INSTANCE_LAYERS"]
    assert plan.wrapper_environment_overrides["DISABLE_MAKO"] == "1"
    assert "MAKO_CONFIG" in plan.wrapper_environment_removed


def test_vdf_patch_identifies_only_selected_game_and_preserves_raw_arguments():
    text = '"UserLocalConfigStore" { "Software" { "Valve" { "Steam" { "apps" { "123" { "LaunchOptions" "FOO=1 %command% -a" } "124" {"LaunchOptions" "keep"} } } } } }'
    start, end, value, existed = launch_option_span(text, "123")
    assert json.loads(text[start:end]) == value == "FOO=1 %command% -a" and existed
    assert launch_option_span(text, "124")[2] == "keep"
    with pytest.raises(ValueError): launch_option_span(text, "125")


def test_runner_setup_refuses_running_steam(environment):
    service, game = environment
    service.host.host_process_commands = lambda: ("/usr/bin/steam -silent",)
    with pytest.raises(ValueError, match="Close Steam"): configure_steam(game, service.host, service.root)


def test_steam_setup_and_restore_preserve_other_games_and_custom_arguments(environment, monkeypatch, tmp_path):
    service, game = environment
    import game_optimization_linux.services.frame_generation_runner_setup as setup
    monkeypatch.setattr(setup, "host_home_directory", lambda: tmp_path)
    path = tmp_path / ".local/share/Steam/userdata/1/config/localconfig.vdf"
    path.parent.mkdir(parents=True)
    original = '// retained\n"apps" {"123" {"LaunchOptions" "FOO=1 %command% -arg"} "124" {"LaunchOptions" "keep exactly"}}'
    path.write_text(original)
    configure_steam(game, service.host, service.root)
    changed = path.read_text()
    assert '"124" {"LaunchOptions" "keep exactly"}' in changed
    options = launch_option_span(changed, "123")[2]
    assert options.startswith("FOO=1 ") and options.endswith("%command% -arg")
    assert options.count("--appid 123") == 1
    configure_steam(game, service.host, service.root, restore=True)
    assert path.read_text() == original


def test_real_generation_evidence_rejects_repetition_stale_profile_and_plan_only(tmp_path):
    from game_optimization_linux.services.frame_generation import session_evidence
    logs = tmp_path / "session.log"; reports = tmp_path / "launch-reports"; reports.mkdir()
    state = {"backend": "afmf", "configuredAt": "fixture-time"}
    report = {"frameGenerationBackend": "afmf", "frameGenerationConfiguredAt": "fixture-time",
              "frameGenerationLog": str(logs), "status": "completed", "timestamp": "fixture-session"}
    path = reports / "123.json"; path.write_text(json.dumps(report))
    logs.write_text("[AFMF info] interpolation ready: 640x480\n[AFMF info] swapchain 0x1 destroyed after 120 presents: 110 generated\n")
    assert session_evidence("123", state, tmp_path)["generationVerified"]
    logs.write_text(logs.read_text() + "[AFMF warn] interpolation unavailable; repeating frames\n")
    assert not session_evidence("123", state, tmp_path)["generationVerified"]
    assert session_evidence("123", state, tmp_path)["errors"]
    report["frameGenerationConfiguredAt"] = "stale"; path.write_text(json.dumps(report))
    assert not session_evidence("123", state, tmp_path)["errors"]
    report["frameGenerationConfiguredAt"] = "fixture-time"; report["status"] = "plan_verified"; path.write_text(json.dumps(report))
    assert not session_evidence("123", state, tmp_path)["generationVerified"]


def test_backend_switching_unsets_selected_disable_gate_and_checks_mutable_conflicts(artifacts, tmp_path):
    from game_optimization_linux.services.mangohud import _atomic_write
    artifacts.install("lsfg")
    root = tmp_path / "profiles"; path = profile_path("123", root)
    dll = tmp_path / "lsfg-vk.dll"; dll.write_bytes(b"fixture validated dll")
    config = path.parent / "frame-generation/lsfg.toml"; config.parent.mkdir(parents=True); config.write_text("fixture")
    state = {"backend": "lsfg", "componentRoot": str(artifacts.root), "settings": {}, "dlls": {"lsfg": str(dll)},
             "validation": {"lsfg": {"sha256": sha256(dll.read_bytes()).hexdigest(), "componentVersion": "2.0.0"}}}
    _atomic_write(path, json.dumps(state))
    env, prefix = launch_settings("123", root, inherited={"DISABLE_LSFGVK": "1", "VK_INSTANCE_LAYERS": "VK_LAYER_AFMF:VK_LAYER_other"})
    assert "DISABLE_LSFGVK" not in env and prefix[:3] == ["env", "-u", "DISABLE_LSFGVK"]
    assert env["DISABLE_MAKO"] == env["DISABLE_AFMF"] == "1"
    assert env["VK_INSTANCE_LAYERS"] == "VK_LAYER_other"
    with pytest.raises(ValueError, match="VK_LOADER_LAYERS_DISABLE"):
        launch_settings("123", root, inherited={"VK_LOADER_LAYERS_DISABLE": "~implicit~"})
    with pytest.raises(ValueError, match="OpenGL"):
        launch_settings("123", root, inherited={"PROTON_USE_WINED3D": "1"})
    state["backend"] = "off"; _atomic_write(path, json.dumps(state))
    env, prefix = launch_settings("123", root)
    assert env["DISABLE_LSFGVK"] == "1" and not prefix
    state["backend"] = "afmf"; _atomic_write(path, json.dumps(state))
    (path.parent / "optiscaler.json").write_text(json.dumps({"installation_state": "installed"}))
    with pytest.raises(ValueError, match="OptiScaler"): launch_settings("123", root)


def test_launcher_access_is_narrow_shared_and_preserves_independent_permissions(environment, monkeypatch, tmp_path):
    import game_optimization_linux.services.frame_generation_access as access
    service, game = environment
    monkeypatch.setattr(access, "host_home_directory", lambda: tmp_path)
    game = replace(game, launcher_variant="flatpak")
    override = tmp_path / ".local/share/flatpak/overrides/com.valvesoftware.Steam"
    override.parent.mkdir(parents=True)
    override.write_text('[Context]\nfilesystems=/user/original:ro;\n[Session Bus Policy]\norg.example.User=talk\n')
    def grant(app_id, paths, bridge=False):
        text = override.read_text()
        text = text.replace('filesystems=', 'filesystems=' + ';'.join(paths) + ';', 1)
        if bridge: text += 'org.freedesktop.Flatpak=talk\n'
        override.write_text(text)
        return SimpleNamespace(returncode=0)
    service.host.frame_generation_grant = grant
    access.prepare(game, service, "lsfg")
    first = override.read_text()
    assert str(service.artifacts.directory("lsfg")) + ':ro' in first
    assert 'org.freedesktop.Flatpak=talk' in first
    other = replace(game, id='steam-124', steam_app_id='124')
    access.prepare(other, service, "lsfg")
    # Simulate an unrelated new permission after setup.
    override.write_text(override.read_text() + 'org.example.Later=talk\n')
    access.prepare(game, service, restore=True)
    assert str(service.artifacts.directory("lsfg")) + ':ro' in override.read_text(), 'second game still needs it'
    access.prepare(other, service, restore=True)
    text = override.read_text()
    assert str(service.artifacts.directory("lsfg")) not in text
    assert '/user/original:ro;' in text and 'org.example.User=talk' in text and 'org.example.Later=talk' in text
    assert 'org.freedesktop.Flatpak=talk' not in text


def test_off_does_not_require_installed_component_or_existing_executable(environment, monkeypatch):
    service, game = environment
    (game.install_path / "Game.exe").unlink()
    monkeypatch.setattr(service, "status", lambda game: load_profile(game.profile_key, service.root))
    assert service.apply(game, {"backend": "off"})["backend"] == "off"
    assert launch_settings("123", service.root)[0]["DISABLE_AFMF"] == "1"


def test_frame_generation_stderr_transport_preserves_unrelated_vkbasalt_settings(tmp_path):
    from game_optimization_linux.runner import _host_launcher_payload
    plan = SimpleNamespace(command=["/bin/true"], steam_command=("/bin/true",),
                           wrapper_environment_removed=(), wrapper_environment_overrides={})
    payload = _host_launcher_payload(plan, {}, tmp_path / "fg.log")
    assert b"VKBASALT_LOG_FILE" not in payload
    payload = _host_launcher_payload(plan, {"ENABLE_VKBASALT": "1"}, tmp_path / "shared.log")
    assert b"VKBASALT_LOG_FILE=stderr" in payload


def test_removed_optiscaler_with_retained_version_is_not_a_conflict(environment):
    service, game = environment
    repository = service.post.optiscaler.profile_repository
    original = repository.load(game.profile_key)
    repository.save(replace(original, installed_version="10.0.0-pre1", installation_state="installed"))
    assert any("OptiScaler" in error for error in service.compatibility(game, "afmf", game.install_path / "Game.exe", {"bits": 64}))
    repository.save(replace(repository.load(game.profile_key), enabled=False, installation_state="removed"))
    assert repository.load(game.profile_key).installed_version == "10.0.0-pre1"
    assert not any("OptiScaler" in error for error in service.compatibility(game, "afmf", game.install_path / "Game.exe", {"bits": 64}))
