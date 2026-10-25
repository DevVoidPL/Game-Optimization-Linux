"""GameMode registration of Steam games inside the Steam Linux Runtime.

Diagnosed 22.10.2026 (gamemode 1.8.2, SteamLinuxRuntime_4): ``gamemoderun``
preloads only ``libgamemodeauto.so.0``; inside the pressure-vessel container
its ``dlopen("libgamemode.so.0")`` fails ("gamemodeauto: dlopen failed"), so
the game process never registers and MangoHud reports GameMode OFF. Preloading
``libgamemode.so.0`` as well imports it into the container and the game
registers (ListGames showed the in-container PID).
"""

from __future__ import annotations

from dataclasses import replace

from game_optimization_linux.models import GameOptimizationProfile
from game_optimization_linux.services.optimization_runtime import (
    GAMEMODE_CLIENT_LIBRARY,
    OptimizationLaunchPlanner,
    RuntimeToolAvailability,
)

GAMEMODE = RuntimeToolAvailability("GameMode", True, "/usr/bin/gamemoderun", "1.8.2")
NO_GAMESCOPE = RuntimeToolAvailability("Gamescope", False)
GAMESCOPE = RuntimeToolAvailability(
    "Gamescope", True, "/usr/bin/gamescope", "3.16.25",
    ("-W", "-H", "-w", "-h", "-r", "-o", "-f", "-b", "-S", "-F", "--mangoapp"),
)
STEAM = {"LD_PRELOAD": "/home/u/.steam/ubuntu12_64/gameoverlayrenderer.so"}


def _profile(**changes):
    return replace(
        GameOptimizationProfile.default("1774580"), **{"gamemode_enabled": True, **changes}
    )


def test_steam_plan_preloads_gamemode_client_and_keeps_steam_preload() -> None:
    plan = OptimizationLaunchPlanner().build(
        _profile(), ["/steam/reaper", "--", "game.exe"], gamemode=GAMEMODE,
        gamescope=NO_GAMESCOPE, existing_environment=STEAM, gamemode_preload_client=True,
    )
    assert plan.command[0] == "/usr/bin/gamemoderun"
    assert plan.environment["LD_PRELOAD"] == (
        f"{GAMEMODE_CLIENT_LIBRARY}:{STEAM['LD_PRELOAD']}"
    )
    assert plan.environment_sources["LD_PRELOAD"] == "gamemode"
    assert any("Steam Linux Runtime registers with GameMode" in r for r in plan.reasons)


def test_gamescope_keeps_preload_away_from_host_gamescope_and_restores_it_for_game() -> None:
    plan = OptimizationLaunchPlanner().build(
        _profile(gamescope_enabled=True, gamescope_mode="native"),
        ["/steam/reaper", "--", "game.exe"], gamemode=GAMEMODE, gamescope=GAMESCOPE,
        existing_environment=STEAM, gamemode_preload_client=True,
    )
    command = plan.command
    assert command[0] == "/usr/bin/gamescope"
    assert "LD_PRELOAD" in plan.wrapper_environment_removed
    env_index = command.index("env")
    gamemode_index = command.index("/usr/bin/gamemoderun")
    assert env_index < gamemode_index
    restored = [item for item in command[env_index:gamemode_index] if item.startswith("LD_PRELOAD=")]
    assert restored == [f"LD_PRELOAD={GAMEMODE_CLIENT_LIBRARY}:{STEAM['LD_PRELOAD']}"]


def test_preload_is_not_duplicated_and_absent_without_flag_or_gamemode() -> None:
    planner = OptimizationLaunchPlanner()
    already = {"LD_PRELOAD": "/usr/lib/libgamemode.so.0"}
    plan = planner.build(_profile(), ["game"], gamemode=GAMEMODE, gamescope=NO_GAMESCOPE,
                         existing_environment=already, gamemode_preload_client=True)
    assert "LD_PRELOAD" not in plan.environment
    plan = planner.build(_profile(), ["game"], gamemode=GAMEMODE, gamescope=NO_GAMESCOPE,
                         existing_environment=STEAM)
    assert "LD_PRELOAD" not in plan.environment
    plan = planner.build(_profile(gamemode_enabled=False), ["game"], gamemode=GAMEMODE,
                         gamescope=NO_GAMESCOPE, existing_environment=STEAM,
                         gamemode_preload_client=True)
    assert "LD_PRELOAD" not in plan.environment
