from __future__ import annotations

from datetime import UTC, datetime
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from gameforge.models import (
    CompressionMeasurement,
    CompressionProfile,
    CompressionResult,
    FilesystemType,
    Game,
    Launcher,
)
from gameforge.providers import BtrfsCompressionProvider
from gameforge.services import (
    BtrfsAnalysisTaskService,
    PrivilegedMeasurementClient,
    PrivilegedMeasurementError,
    TaskHistoryStore,
    measurement_delta,
)

ROOT = Path(__file__).resolve().parents[1]


def _game(tmp_path: Path) -> Game:
    library = tmp_path / "SteamLibrary"
    game_path = library / "steamapps" / "common" / "Fixture"
    game_path.mkdir(parents=True)
    return Game(
        id="steam-4242",
        name="Fixture",
        launcher=Launcher.STEAM,
        install_path=game_path,
        logical_size_gb=1.0,
        physical_size_gb=1.0,
        filesystem=FilesystemType.BTRFS,
        compression_available=True,
        steam_app_id="4242",
        steam_build_id="100",
        library_path=library,
        filesystem_name="btrfs",
    )


def _payload(game: Game) -> dict[str, object]:
    return {
        "schema_version": 1,
        "ok": True,
        "measurement_source": "polkit_helper",
        "measurement_error": None,
        "authorized_uid": 1000,
        "app_id": "4242",
        "build_id": "100",
        "game_path": str(game.install_path),
        "measured_at": "2026-07-30T10:00:00+00:00",
        "logical_bytes": 1_000_000_000,
        "filesystem_total_bytes": 500_000_000_000,
        "filesystem_used_bytes": 300_000_000_000,
        "filesystem_free_bytes": 200_000_000_000,
        "filesystem_available_bytes": 199_000_000_000,
        "compsize": {
            "disk_usage_bytes": 800_000_000,
            "uncompressed_bytes": 1_000_000_000,
            "referenced_bytes": 1_000_000_000,
            "compression_types": {"zstd": 800_000_000},
        },
        "btrfs_filesystem_du": {
            "total_bytes": 1_100_000_000,
            "exclusive_bytes": 900_000_000,
            "set_shared_bytes": 200_000_000,
            "state": "detected",
        },
        "read_only": True,
    }


def test_polkit_client_uses_fixed_argv_without_shell(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    game = _game(tmp_path)
    helper = tmp_path / "gameforge-linux-measure-helper"
    pkexec = tmp_path / "pkexec"
    helper.write_text("#!/bin/sh\n", encoding="utf-8")
    pkexec.write_text("#!/bin/sh\n", encoding="utf-8")
    helper.chmod(0o755)
    pkexec.chmod(0o755)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, dict(kwargs)))
        return subprocess.CompletedProcess(command, 0, json.dumps(_payload(game)), "")

    client = PrivilegedMeasurementClient(
        helper_path=helper,
        pkexec_path=pkexec,
        command_runner=runner,
    )

    with caplog.at_level("INFO"):
        measured = client.measure(game)

    assert measured.measurement_source == "polkit_helper"
    assert measured.compsize_disk_bytes == 800_000_000
    assert measured.compsize_uncompressed_bytes == 1_000_000_000
    assert measured.exclusive_bytes == 900_000_000
    assert measured.shared_bytes == 200_000_000
    assert measured.filesystem_used_bytes == 300_000_000_000
    command, kwargs = calls[0]
    assert command == [
        str(pkexec),
        str(helper),
        "measure",
        "--library",
        str(game.library_path),
        "--appid",
        "4242",
        "--buildid",
        "100",
    ]
    assert kwargs["shell"] is False
    assert "sudo" not in command
    assert repr(command) in caplog.text


def test_polkit_denial_returns_no_measurement(tmp_path: Path) -> None:
    game = _game(tmp_path)
    helper = tmp_path / "helper"
    pkexec = tmp_path / "pkexec"
    helper.write_text("", encoding="utf-8")
    pkexec.write_text("", encoding="utf-8")
    helper.chmod(0o755)
    pkexec.chmod(0o755)
    client = PrivilegedMeasurementClient(
        helper_path=helper,
        pkexec_path=pkexec,
        command_runner=lambda command, **kwargs: subprocess.CompletedProcess(
            command, 126, "", ""
        ),
    )

    with pytest.raises(
        PrivilegedMeasurementError,
        match="cancelled or denied",
    ):
        client.measure(game)


def test_exit_zero_without_complete_compsize_is_not_success(tmp_path: Path) -> None:
    game = _game(tmp_path)
    helper = tmp_path / "helper"
    pkexec = tmp_path / "pkexec"
    helper.write_text("", encoding="utf-8")
    pkexec.write_text("", encoding="utf-8")
    helper.chmod(0o755)
    pkexec.chmod(0o755)
    payload = _payload(game)
    payload["ok"] = False
    payload["measurement_error"] = "compsize exited with status 1: No files."
    payload["compsize"] = None
    client = PrivilegedMeasurementClient(
        helper_path=helper,
        pkexec_path=pkexec,
        command_runner=lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, json.dumps(payload), ""
        ),
    )

    with pytest.raises(PrivilegedMeasurementError, match="No files") as caught:
        client.measure(game)

    assert caught.value.exit_code == 0
    assert "No files" in caught.value.stdout
    assert caught.value.stderr == ""


def test_nonzero_helper_result_preserves_full_diagnostics(tmp_path: Path) -> None:
    game = _game(tmp_path)
    helper = tmp_path / "helper"
    pkexec = tmp_path / "pkexec"
    helper.write_text("", encoding="utf-8")
    pkexec.write_text("", encoding="utf-8")
    helper.chmod(0o755)
    pkexec.chmod(0o755)
    stdout = json.dumps(
        {
            "ok": False,
            "measurement_error": "compsize exited with status 1: No files.",
        }
    )
    stderr = "helper argv validated\ncompsize stderr: No files.\n"
    client = PrivilegedMeasurementClient(
        helper_path=helper,
        pkexec_path=pkexec,
        command_runner=lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            1,
            stdout,
            stderr,
        ),
    )

    with pytest.raises(PrivilegedMeasurementError, match="No files") as caught:
        client.measure(game)

    assert caught.value.exit_code == 1
    assert caught.value.stdout == stdout
    assert caught.value.stderr == stderr


def test_helper_failure_diagnostics_are_saved_with_new_verification_task(
    tmp_path: Path,
) -> None:
    game = _game(tmp_path)
    history_path = tmp_path / "tasks.json"

    class FailedMeasurementService:
        def verify_measurement(self, requested: Game) -> CompressionMeasurement:
            assert requested.id == game.id
            raise PrivilegedMeasurementError(
                "compsize exited with status 1: No files.",
                exit_code=1,
                stdout='{"ok": false, "measurement_error": "No files."}\n',
                stderr="compsize: No files.\n",
            )

        def cancel_all(self) -> None:
            return None

    tasks = BtrfsAnalysisTaskService(
        compression_service=FailedMeasurementService(),  # type: ignore[arg-type]
        history_store=TaskHistoryStore(history_path),
        max_workers=1,
    )

    queued = tasks.enqueue_verification(game)
    failed = tasks.wait_for(queued.id, timeout=1.0)

    assert failed.id == queued.id
    assert failed.status.value == "failed"
    assert failed.error == "compsize exited with status 1: No files."
    assert failed.metadata["helper_exit_code"] == 1
    assert failed.metadata["helper_stdout"].startswith('{"ok": false')
    assert failed.metadata["helper_stderr"] == "compsize: No files.\n"
    assert tasks.shutdown(wait=True, timeout=1.0)

    restored = TaskHistoryStore(history_path).load()
    assert len(restored) == 1
    assert restored[0].id == queued.id
    assert restored[0].metadata["helper_exit_code"] == 1
    assert restored[0].metadata["helper_stdout"].startswith('{"ok": false')
    assert restored[0].metadata["helper_stderr"] == "compsize: No files.\n"


def test_gotham_knights_manual_values_only_test_signed_delta() -> None:
    before_used = 274_493_444_096
    after_used = 274_199_433_216

    reclaimed = measurement_delta(before_used, after_used)

    assert reclaimed == 294_010_880
    assert reclaimed / (1024 * 1024) == pytest.approx(280.390625)


def test_savings_claim_requires_two_privileged_compsize_measurements() -> None:
    before = CompressionMeasurement(
        logical_bytes=1_000,
        physical_bytes=800,
        exclusive_bytes=800,
        shared_bytes=0,
        compsize_disk_bytes=800,
        compsize_uncompressed_bytes=1_000,
        compsize_referenced_bytes=1_000,
        scan_complete=True,
        shared_extent_state="not_detected",
        measurement_source="polkit_helper",
    )
    after = CompressionMeasurement(
        logical_bytes=1_000,
        physical_bytes=600,
        exclusive_bytes=600,
        shared_bytes=0,
        compsize_disk_bytes=600,
        compsize_uncompressed_bytes=1_000,
        compsize_referenced_bytes=1_000,
        scan_complete=True,
        shared_extent_state="not_detected",
        measurement_source="polkit_helper",
    )

    assert BtrfsCompressionProvider._actual_savings(before, after) == 200
    assert (
        BtrfsCompressionProvider._actual_savings(
            before,
            CompressionMeasurement.from_dict(
                {
                    **after.to_dict(),
                    "measurement_source": "unprivileged",
                }
            ),
        )
        is None
    )

    now = datetime.now(UTC)
    result = CompressionResult(
        plan_id="plan",
        game_id="game",
        profile=CompressionProfile.BALANCED,
        status="completed",
        started_at=now,
        completed_at=now,
        processed_files=1,
        processed_bytes=1_000,
        before=before,
        after=after,
        actual_saved_bytes=200,
        verification_state="verified",
        full_compression=True,
        after_update=False,
        build_id="100",
    )
    assert result.measurement_authoritative is True
    assert result.active_files_compression_effect_bytes == 400


def test_verification_task_is_read_only_and_reaches_history(tmp_path: Path) -> None:
    game = _game(tmp_path)
    measured = PrivilegedMeasurementClient.measurement_from_payload(_payload(game))

    class MeasurementService:
        def verify_measurement(self, requested: Game) -> CompressionMeasurement:
            assert requested.id == game.id
            return measured

        def cancel_all(self) -> None:
            raise AssertionError("read-only verification must not cancel writers")

    tasks = BtrfsAnalysisTaskService(
        compression_service=MeasurementService(),  # type: ignore[arg-type]
        max_workers=1,
    )

    queued = tasks.enqueue_verification(game)
    completed = tasks.wait_for(queued.id, timeout=1.0)

    assert completed.status.value == "completed"
    assert completed.task_type.value == "Verification"
    assert completed.metadata["read_only"] is True
    assert completed.result is not None
    assert completed.result["compsize_disk_bytes"] == 800_000_000
    assert tasks.shutdown(wait=True, timeout=1.0)


def test_installed_helper_parses_only_read_only_measurement_outputs() -> None:
    path = ROOT / "libexec" / "gameforge-linux-measure-helper"
    loader = importlib.machinery.SourceFileLoader("gameforge_measure_helper", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)

    compsize = module._parse_compsize(
        "Type Perc Disk Usage Uncompressed Referenced\n"
        "TOTAL 80% 800 1000 1000\n"
        "zstd 80% 800 1000 1000\n"
    )
    btrfs_du = module._parse_du(
        "Total Exclusive Set shared Filename\n"
        "1100 900 200 /proc/self/fd/7\n"
    )

    assert compsize["disk_usage_bytes"] == 800
    assert compsize["uncompressed_bytes"] == 1000
    assert btrfs_du == {
        "total_bytes": 1100,
        "exclusive_bytes": 900,
        "set_shared_bytes": 200,
        "state": "detected",
    }
    assert Path(module.COMPSIZE).name == "compsize"
    assert Path(module.BTRFS).name == "btrfs"


def test_helper_passes_game_path_with_space_as_one_compsize_argument(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = ROOT / "libexec" / "gameforge-linux-measure-helper"
    loader = importlib.machinery.SourceFileLoader(
        "gameforge_measure_helper_space",
        str(path),
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)

    library = tmp_path / "SteamLibrary"
    game_path = library / "steamapps" / "common" / "House Flipper"
    game_path.mkdir(parents=True)
    (game_path / "fixture.bin").write_bytes(b"fixture")
    manifest = library / "steamapps" / "appmanifest_613100.acf"
    manifest.write_text(
        '"AppState"\n{\n'
        '  "appid" "613100"\n'
        '  "installdir" "House Flipper"\n'
        '  "buildid" "123456"\n'
        '}\n',
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_run(
        argv: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        assert kwargs["shell"] is False
        if argv[0] == module.COMPSIZE:
            output = (
                "Type Perc Disk Usage Uncompressed Referenced\n"
                "TOTAL 80% 800 1000 1000\n"
                "zstd 80% 800 1000 1000\n"
            )
        else:
            output = (
                "Total Exclusive Set shared Filename\n"
                f"1100 900 200 {game_path}\n"
            )
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(module, "_calling_uid", os.getuid)
    monkeypatch.setattr(module, "_require_btrfs", lambda descriptor: None)
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    with caplog.at_level("INFO"):
        result = module.measure(
            SimpleNamespace(
                library=str(library),
                appid="613100",
                buildid="123456",
            )
        )

    expected = str(game_path.resolve())
    assert result["ok"] is True
    assert result["game_path"] == expected
    assert calls[0] == [
        module.COMPSIZE,
        "--bytes",
        "--one-file-system",
        expected,
    ]
    assert calls[0][-1] == expected
    assert calls[0].count(expected) == 1
    assert repr(calls[0]) in caplog.text


def test_polkit_policy_targets_only_the_fixed_measurement_helper() -> None:
    policy = ET.parse(
        ROOT
        / "data"
        / "io.github.gameforge_linux.GameForge.measure.policy"
    ).getroot()
    action = policy.find("./action")
    assert action is not None
    assert action.get("id") == "io.github.gameforge_linux.GameForge.measure"
    assert action.findtext("./defaults/allow_active") == "auth_admin_keep"
    annotations = {
        item.get("key"): item.text for item in action.findall("./annotate")
    }
    assert annotations["org.freedesktop.policykit.exec.path"] == (
        "/usr/libexec/gameforge-linux-measure-helper"
    )
