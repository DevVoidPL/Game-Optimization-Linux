"""MangoHud: preserved user options in GameOpti-owned per-game configs are written once and idempotently."""
from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path

import pytest

from game_optimization_linux.services import mangohud as mangohud_module
from game_optimization_linux.services.mangohud import (
    KNOWN_CONFIG_KEYS,
    MANAGED_HEADER,
    MangoHudConfigWriter,
    MangoHudProfileRepository,
    preserved_options,
)
from game_optimization_linux.services.launcher_mangohud import launcher_mangohud_path, read_launcher_mangohud
from test_launcher_mangohud_sync import _save, _wait_status
from test_heroic_tool_plans import heroic  # noqa: F401 - pytest fixture


@pytest.fixture
def setup(tmp_path: Path):
    profile = replace(MangoHudProfileRepository(tmp_path / "profiles").load("42"), enabled=True)
    return MangoHudConfigWriter(tuple(KNOWN_CONFIG_KEYS)), profile, tmp_path / "MangoHud.conf"


def _block(text: str) -> list[str]:
    lines = text.splitlines()
    return lines[lines.index("# Preserved user options") + 1:] if "# Preserved user options" in lines else []


def test_one_unknown_key_survives_repeated_writes_exactly_once(setup) -> None:
    writer, profile, path = setup
    writer.write(profile, path)
    path.write_text(path.read_text() + "autostart_log=1\n")
    for _ in range(4):
        writer.write(profile, path)
    first = path.read_bytes()
    writer.write(profile, path)
    assert path.read_bytes() == first  # idempotent
    assert path.read_text().count("autostart_log=1") == 1
    assert path.read_text().count("# Preserved user options") == 1
    # The confirmed Jedi case: the same option appended again by a measurement step.
    path.write_text(path.read_text() + "autostart_log=1\n")
    writer.write(profile, path)
    assert path.read_text().count("autostart_log=1") == 1


def test_already_duplicated_block_is_collapsed_keeping_the_effective_last_value(setup) -> None:
    writer, profile, path = setup
    writer.write(profile, path)
    path.write_text(path.read_text() + "\n# Preserved user options\nautostart_log=1\ncustom_a=1\n"
                    "\n# Preserved user options\nautostart_log=1\ncustom_a=2\nautostart_log=1\n")
    writer.write(profile, path)
    assert _block(path.read_text()) == ["autostart_log=1", "custom_a=2"]  # first position, last value


def test_managed_key_is_never_also_preserved(setup) -> None:
    writer, profile, path = setup
    rendered = writer.render(profile)
    managed = next(line.split("=", 1)[0] for line in rendered.splitlines() if "=" in line and not line.startswith("#"))
    assert preserved_options(rendered, rendered + f"\n{managed}=999\nflag_x\n") == ["flag_x"]
    writer.write(profile, path)
    path.write_text(path.read_text() + f"\n# Preserved user options\n{managed}=999\nfps_limit=30\n")
    writer.write(profile, path)
    text = path.read_text()
    assert f"{managed}=999" not in text and "fps_limit=30" not in text and _block(text) == []


def test_values_with_spaces_and_special_characters_are_kept_verbatim(setup) -> None:
    writer, profile, path = setup
    writer.write(profile, path)
    odd = ["output_file=/tmp/my logs/ż ółć #1.csv", 'custom_text_center=Hello, "world" & $HOME', "media_player_format={title};{artist}"]
    path.write_text(path.read_text() + "\n".join(odd) + "\n")
    for _ in range(3):
        writer.write(profile, path)
    assert _block(path.read_text()) == odd


def test_failed_atomic_replacement_leaves_the_previous_file_unchanged(setup, monkeypatch) -> None:
    writer, profile, path = setup
    writer.write(profile, path)
    path.write_text(path.read_text() + "autostart_log=1\n")
    before = path.read_bytes()

    def failing_replace(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(mangohud_module.os, "replace", failing_replace)
    with pytest.raises(OSError):
        writer.write(replace(profile, font_size=30), path)
    assert path.read_bytes() == before
    assert not [p.name for p in path.parent.iterdir() if p.name.endswith(".tmp")]  # no temp file left


def test_user_global_configuration_is_never_rewritten(setup, tmp_path) -> None:
    writer, profile, _path = setup
    user_global = tmp_path / "global" / "MangoHud.conf"
    user_global.parent.mkdir()
    user_global.write_text("fps\nautostart_log=1\nautostart_log=1\n")
    digest = hashlib.sha256(user_global.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="not managed by GameOpti"):
        writer.write(profile, user_global)
    assert hashlib.sha256(user_global.read_bytes()).hexdigest() == digest
    assert not user_global.read_text().startswith(MANAGED_HEADER)


def test_launcher_sync_and_status_refresh_never_accumulate(heroic) -> None:
    """Launcher configs are rewritten from the profile alone: nothing can accumulate."""
    game, controller, _plan = heroic(mangohud=True, gamescope=False)
    controller.applyLauncherIntegration(game.id)
    host = launcher_mangohud_path(game.profile_key)
    host.write_text(host.read_text() + "autostart_log=1\nautostart_log=1\n")
    texts = []
    for _ in range(3):
        _save(controller, game, textColor="FF0000")
        assert _wait_status(controller, game)["configCurrent"] is True
        controller.getLauncherIntegration(game.id)  # status refresh writes nothing
        texts.append(read_launcher_mangohud(host))
    assert texts[0] == texts[1] == texts[2]
    assert "autostart_log" not in texts[0] and "# Preserved user options" not in texts[0]
