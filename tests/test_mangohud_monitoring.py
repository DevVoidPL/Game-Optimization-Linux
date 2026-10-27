"""Versioned monitoring options, launcher capabilities and profile preservation."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json
import subprocess

import pytest

from game_optimization_linux.models import MangoHudProfile
from game_optimization_linux.services.mangohud import (
    MangoHudConfigWriter, MangoHudDetector, MangoHudAvailability,
    MangoHudProfileRepository, KNOWN_CONFIG_KEYS, NEW_CONFIG_KEYS, hud_layout,
)
from game_optimization_linux.services.host_service import HostServiceClient, HostServiceError
from tests.test_mangohud import _controller, _game, _Detector


@pytest.mark.parametrize(("metric", "lines"), [
    ("avg_fps", ["fps_metrics=avg"]),
    ("fps_1_low", ["fps_metrics=0.01"]),
    ("frametime_detailed", ["frame_timing", "frame_timing_detailed"]),
    ("gpu_memory_clock", ["gpu_mem_clock", "vram"]),
    ("throttling", ["throttling_status"]),
    ("disk_io", ["io_read", "io_write"]),
    ("swap", ["swap"]),
    ("graphics_api", ["engine_version"]),
    ("vulkan_driver", ["vulkan_driver"]),
    ("wine_sync", ["winesync"]),
    ("present_mode", ["present_mode"]),
    ("vkbasalt", ["vkbasalt"]),
])
def test_new_metrics_write_documented_keys(metric, lines):
    profile = replace(MangoHudProfile.default("123"), enabled=True, preset="custom", metrics=("vram", metric))
    config = MangoHudConfigWriter().render(profile).splitlines()
    assert all(line in config for line in lines)
    keys = [line.split("=",1)[0] for line in config if line and not line.startswith("#")]
    assert len(keys) == len(set(keys))
    old = MangoHudConfigWriter(KNOWN_CONFIG_KEYS - NEW_CONFIG_KEYS).render(profile).splitlines()
    assert not any(line.split("=",1)[0] in NEW_CONFIG_KEYS for line in old)


def test_fps_statistics_coalesce_and_all_graphs_use_one_option():
    profile = replace(MangoHudProfile.default("123").apply_preset("diagnostic"), metrics=("avg_fps", "fps_1_low"))
    lines = MangoHudConfigWriter().render(profile).splitlines()
    assert lines.count("fps_metrics=avg,0.01") == 1
    assert "graphs=gpu_load,cpu_load,vram,ram" in lines
    assert hud_layout(profile)["graphs"] == ["gpu_load", "cpu_load", "vram", "ram"]
    with pytest.raises(ValueError):
        replace(profile, graphs=("not_a_graph",))


def test_old_profile_migration_and_new_profiles_do_not_leak(tmp_path):
    repo = MangoHudProfileRepository(tmp_path, log_root=tmp_path / "logs")
    first = replace(repo.default("123").apply_preset("gameopti"), offset_x=-12, graphs=("ram",), extra_fields={"future": {"x":42}})
    repo.save(first)
    repo.save(repo.default("456").apply_preset("minimal"))
    restarted = MangoHudProfileRepository(tmp_path, log_root=tmp_path / "logs")
    assert restarted.load("123") == first
    assert restarted.load("456").graphs == ()
    assert restarted.load("456").offset_x == 0
    raw = first.to_dict()
    for key in ("offset_x", "offset_y", "cellpadding_y", "graphs"):
        raw.pop(key)
    migrated = MangoHudProfile.from_dict(raw, expected_app_id="123")
    assert migrated.offset_x == 0 and migrated.graphs == () and migrated.cellpadding_y == -0.085
    assert migrated.extra_fields["future"] == {"x":42}


def test_unknown_config_options_survive_edits_and_foreign_files_are_untouched(tmp_path):
    path = tmp_path / "MangoHud.conf"
    writer = MangoHudConfigWriter()
    profile = MangoHudProfile.default("123").apply_preset("basic")
    writer.write(profile, path)
    with path.open("a") as stream:
        stream.write("custom_text=My HUD\ncustom_unrecognized=42\n")
    writer.write(replace(profile, font_size=30), path)
    writer.write(replace(profile, font_size=32), path)
    text = path.read_text()
    assert text.count("custom_text=My HUD") == 1
    assert "custom_unrecognized=42" in text and "font_size=32" in text
    path.write_text("# User-owned\nfps\n")
    with pytest.raises(ValueError, match="not managed"):
        writer.write(profile,path)
    assert path.read_text() == "# User-owned\nfps\n"


class RuntimeDetector(MangoHudDetector):
    def detect(self, steam_type="native"):
        return MangoHudAvailability(available=True, steam_type=steam_type, version="0.8.4", layer_available=True, supported_keys=tuple(KNOWN_CONFIG_KEYS), message="MangoHud detected")


@pytest.mark.parametrize("version", ["0.8.3", "unknown", ""])
def test_new_controls_require_version_evidence(tmp_path, version):
    host = SimpleNamespace(mangohud_runtime_info=lambda app: {"checked":True,"available":True,"version":version})
    detector = RuntimeDetector(host_service=host)
    availability, runtime = detector.for_game(_game(tmp_path / "game"))
    assert not runtime["versionVerified"]
    assert not set(availability.supported_keys).intersection(NEW_CONFIG_KEYS)


def test_flatpak_launcher_is_probed_not_gameopti_sandbox(tmp_path):
    calls=[]
    def query(app):
        calls.append(app)
        return {"checked":True, "available":False, "version":"", "layer32":False, "layer64":False}
    game=replace(_game(tmp_path / "game"), launcher_variant="flatpak")
    availability,runtime=RuntimeDetector(host_service=SimpleNamespace(mangohud_runtime_info=query)).for_game(game)
    assert calls == ["com.valvesoftware.Steam"]
    assert not availability.available and runtime["environment"] == calls[0]
    assert "extension" in availability.message


def test_actual_32bit_header_blocks_false_readiness_without_mutating_profile(tmp_path):
    class Detector(_Detector):
        def for_game(self, game):
            return self.detect(), {"layer32":False,"layer64":True}
    game=_game(tmp_path / "game")
    # Minimal PE32 optional-header magic, not a filename-based guess.
    data=bytearray(128);data[:2]=b"MZ";data[60:64]=(64).to_bytes(4,"little")
    data[64:68]=b"PE\0\0";data[88:90]=(0x10b).to_bytes(2,"little")
    (game.install_path / "RaymanLegends.exe").write_bytes(data)
    repo=MangoHudProfileRepository(tmp_path / "games",log_root=tmp_path / "logs")
    controller=_controller(tmp_path,game,repo,Detector())
    try:
        result=controller.getMangoHudProfile(game.id)
        assert result["runtimeInfo"]["architecture"] == "32-bit"
        assert not result["available"] and "32-bit" in result["availabilityMessage"]
        saved=controller.saveMangoHudProfile(game.id,{"preset":"minimal","enabled":True})
        assert not saved["success"] and not repo.profile_path(game.profile_key).exists()
    finally:
        controller.shutdown()


def test_mangoapp_keeps_saved_metrics_but_does_not_claim_process_readings(tmp_path):
    game=_game(tmp_path / "game"); (game.install_path / "RaymanLegends.exe").write_bytes(b"MZ")
    repo=MangoHudProfileRepository(tmp_path / "games", log_root=tmp_path / "logs")
    repo.save(repo.default(game.profile_key).apply_preset("diagnostic"))
    controller=_controller(tmp_path,game,repo,_Detector())
    try:
        from game_optimization_linux.services.optimization_runtime import RuntimeToolAvailability
        controller._runtime_tool_detector = SimpleNamespace(detect=lambda: (None, RuntimeToolAvailability("gamescope",True,supported_options=("--mangoapp",))))
        opt=controller._optimization_profile_repository.load(game.profile_key)
        controller._optimization_profile_repository.save(replace(opt,gamescope_enabled=True,gamescope_mode="custom"))
        result=controller.getMangoHudProfile(game.id)
        assert result["mangoapp"]
        assert "disk_io" in result["metrics"] and "disk_io" not in result["supportedMetrics"]
        assert "io_read\n" not in result["configPreview"]
        assert "proc_vram\n" not in result["configPreview"]
        assert "disk_io" in repo.load(game.profile_key).metrics
    finally:
        controller.shutdown()


def test_runtime_probe_is_read_only_bounded_cached_and_validates_launcher():
    calls=[]
    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv,0,"__VERSION__\nMangoHud 0.8.4\n__LAYERS__\n/usr/share/vulkan/implicit_layer.d/MangoHud.x86_64.json\n__EXAMPLE__\n# graphs=gpu_load\n","")
    client=HostServiceClient(environment={},which=lambda name: "/usr/bin/"+name,command_runner=run)
    first=client.mangohud_runtime_info("com.valvesoftware.Steam")
    assert first["layer64"] and not first["layer32"] and first["available"]
    assert client.mangohud_runtime_info("com.valvesoftware.Steam") == first and len(calls)==1
    assert calls[0][:4] == ["/usr/bin/flatpak","run","--command=sh","com.valvesoftware.Steam"]
    with pytest.raises(HostServiceError): client.mangohud_runtime_info("bad;command")


def test_conflicting_config_does_not_save_half_an_apply(tmp_path):
    game=_game(tmp_path / "game")
    (game.install_path / "RaymanLegends.exe").write_bytes(b"MZ")
    repo=MangoHudProfileRepository(tmp_path / "games",log_root=tmp_path / "logs")
    profile=repo.default(game.profile_key).apply_preset("minimal")
    repo.save(profile)
    original=repo.profile_path(game.profile_key).read_bytes()
    repo.config_path(game.profile_key).write_text("# Personal file\ncustom_text=Keep me\n")
    controller=_controller(tmp_path,game,repo,_Detector())
    try:
        result=controller.saveMangoHudProfile(game.id,{"preset":"minimal","enabled":True,"fontSize":36})
        assert not result["success"]
        assert repo.profile_path(game.profile_key).read_bytes()==original
        assert "Keep me" in repo.config_path(game.profile_key).read_text()
    finally:
        controller.shutdown()


def test_new_editor_catalogs_have_no_untranslated_controls():
    import re
    import xml.etree.ElementTree as ET
    qml=Path("src/game_optimization_linux/qml/pages/details")
    contexts={name:set(re.findall(r'qsTr\("([^"\n]+)"', (qml/(name+".qml")).read_text())) for name in ("MangoHudTab","MangoHudPreview","MangoHudNumber")}
    for language in ("en","pl","es"):
        tree=ET.parse(f"src/game_optimization_linux/translations/game_optimization_{language}.ts")
        for context in tree.findall("context"):
            name=context.findtext("name")
            if name not in contexts: continue
            messages={message.findtext("source"):message.find("translation") for message in context.findall("message")}
            for source in contexts[name]:
                translation=messages.get(source)
                assert translation is not None and translation.text and translation.get("type") not in {"unfinished","obsolete","vanished"}, (language,name,source)
