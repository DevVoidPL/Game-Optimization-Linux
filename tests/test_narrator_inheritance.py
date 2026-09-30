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


def test_legacy_schema_one_profile_round_trip_preserves_user_values(
    tmp_path: Path,
) -> None:
    repository = NarratorSettingsRepository(tmp_path)
    path = repository.path("123")
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "game_key": "123",
                "enabled": True,
                "voice_id": "legacy-voice",
                "ocr_min_confidence": 0.73,
                "duplicate_cooldown_ms": 6100,
                "subtitle_region": {
                    "x": 0.15,
                    "y": 0.65,
                    "width": 0.7,
                    "height": 0.2,
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = repository.load("123")
    assert loaded.schema_version == 1
    assert loaded.voice_id == "legacy-voice"
    assert loaded.ocr_min_confidence == 0.73
    assert loaded.duplicate_cooldown_ms == 6100
    assert loaded.subtitle_region_source == "saved"

    repository.save(loaded)
    serialized = json.loads(path.read_text(encoding="utf-8"))
    assert serialized["schema_version"] == 1
    assert serialized["ocr_min_confidence"] == 0.73
    assert serialized["duplicate_cooldown_ms"] == 6100
    assert "subtitle_region_source" not in serialized
    assert repository.load("123") == loaded
