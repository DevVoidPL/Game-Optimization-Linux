from __future__ import annotations

import json
from pathlib import Path

from game_optimization_linux.models.narrator import NarratorGameSettings
from game_optimization_linux.services.narrator_persistence import (
    NarratorSettingsRepository,
)


def test_missing_game_values_inherit_global_defaults(tmp_path) -> None:
    repository = NarratorSettingsRepository(tmp_path)
    defaults = NarratorGameSettings.default("local-000000000000000000000000")
    repository.save_defaults(defaults)

    loaded = repository.load("123")
    assert loaded.voice_id == defaults.voice_id
    assert repository.load_overrides("123") == {}


def test_global_changes_propagate_but_explicit_override_is_independent(tmp_path) -> None:
    repository = NarratorSettingsRepository(tmp_path)
    defaults = NarratorGameSettings.default("local-000000000000000000000000")
    repository.save_defaults(defaults)

    repository.save_overrides("123", {"voice_id": "global-voice"})
    assert repository.load("123").voice_id == "global-voice"

    repository.save_defaults(
        NarratorGameSettings.from_dict(
            {**defaults.to_dict(), "voice_id": "new-global"},
            expected_game_key=defaults.game_key,
        )
    )
    assert repository.load("123").voice_id == "global-voice"

    repository.clear_overrides("123", ("voice_id",))
    assert repository.load("123").voice_id == "new-global"


def test_subtitle_region_is_never_inherited(tmp_path) -> None:
    repository = NarratorSettingsRepository(tmp_path)
    defaults = NarratorGameSettings.default("local-000000000000000000000000")
    repository.save_defaults(
        NarratorGameSettings.from_dict(
            {
                **defaults.to_dict(),
                "subtitle_region": {"x": 0.2, "y": 0.2, "width": 0.5, "height": 0.3},
            },
            expected_game_key=defaults.game_key,
        )
    )

    loaded = repository.load("123")
    assert loaded.subtitle_region == NarratorGameSettings.default("123").subtitle_region

    raw = json.loads(repository.path("123").read_text()) if repository.path("123").exists() else {}
    assert "subtitle_region" not in raw


def test_game_overrides_are_isolated(tmp_path) -> None:
    repository = NarratorSettingsRepository(tmp_path)
    repository.save_overrides("123", {"voice_id": "voice-a"})
    repository.save_overrides("456", {"voice_id": "voice-b"})

    assert repository.load("123").voice_id == "voice-a"
    assert repository.load("456").voice_id == "voice-b"


def test_global_page_owns_narrator_configuration_and_picker(tmp_path) -> None:
    root = Path("src/game_optimization_linux/qml")
    narrator_page = (root / "pages" / "NarratorPage.qml").read_text(encoding="utf-8")
    details_page = (root / "pages" / "GameDetailsPage.qml").read_text(encoding="utf-8")
    couch_details = (root / "couch" / "CouchGameDetails.qml").read_text(encoding="utf-8")
    couch_home = (root / "couch" / "CouchHome.qml").read_text(encoding="utf-8")
    couch_main = (root / "couch" / "CouchMain.qml").read_text(encoding="utf-8")

    assert "narratorGameSelector" in narrator_page
    assert "getNarratorGameSettings(selectedGameId)" in narrator_page
    assert "saveNarratorGameSettings" in narrator_page
    assert "selectNarratorSubtitleRegion" in narrator_page
    assert 'qsTr("Narrator")' not in details_page
    assert "NarratorTab" not in details_page
    home_tiles = couch_home[couch_home.index("homeTiles"):couch_home.index("displayGames")]
    assert '"title": qsTr("Lektor")' not in home_tiles
    assert '"id": "narrator"' in couch_details
    assert 'qsTr("Lektor")' in couch_main
    assert 'qsTr("Lektor")' in couch_details
    assert "narratorActions" in couch_details
    assert not (root / "pages" / "details" / "NarratorTab.qml").exists()
    assert not (root / "pages" / "details" / "NarratorSection.qml").exists()
