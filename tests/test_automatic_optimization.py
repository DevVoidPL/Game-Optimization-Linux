from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from game_optimization_linux.controllers.optimization_controller import (
    OptimizationController,
)
from game_optimization_linux.models import (
    BottleneckAnalysis,
    DetectedValue,
    FilesystemType,
    FrameRateAnalysis,
    Game,
    GameFingerprint,
    GameOptimizationProfile,
    GameSettingsAnalysis,
    Launcher,
    OptimizationAnalysis,
    PerformanceMeasurement,
    SystemSnapshot,
)
from game_optimization_linux.services import (
    AutomaticOptimizationEvaluator,
    AutomaticOptimizationRepository,
    BaselineSessionRepository,
    GameOptimizationProfileRepository,
    OptimizationChangeService,
    RuntimeCandidateRegistry,
    RuntimeToolAvailability,
    diagnose_problem,
    verify_runtime_activation,
)


class Signal:
    def __init__(self) -> None:
        self.values: list[str] = []

    def emit(self, value: str) -> None:
        self.values.append(value)


def _game(tmp_path: Path) -> Game:
    root = tmp_path / "game"
    root.mkdir(parents=True, exist_ok=True)
    return Game(
        id="steam-10",
        name="Runtime Test",
        launcher=Launcher.STEAM,
        install_path=root,
        logical_size_gb=1,
        physical_size_gb=1,
        filesystem=FilesystemType.BTRFS,
        compression_available=True,
        steam_app_id="10",
        data_source="Steam",
    )


def _measurement(
    *,
    fps: float = 90,
    low: float = 55,
    p95: float = 15,
    p99: float = 22,
    cpu: float = 45,
    gpu: float = 65,
    representative: bool = True,
) -> PerformanceMeasurement:
    return PerformanceMeasurement(
        "baseline.csv",
        900,
        90,
        fps,
        low,
        low,
        1000 / fps,
        p95,
        p99,
        cpu,
        gpu,
        4096,
        4096,
        65,
        quality="high" if representative else "low",
        total_samples=900,
        selected_duration_seconds=90,
        representative=representative,
        median_fps=fps,
        p10_fps=fps * 0.95,
        p90_fps=fps * 1.03,
        p95_fps=fps * 1.04,
        p99_fps=fps * 1.05,
        median_frametime_ms=1000 / fps,
    )


def _analysis(
    tmp_path: Path,
    conclusion: str,
    *,
    confidence: float = 0.82,
    measurement: PerformanceMeasurement | None = None,
    frame_rate: FrameRateAnalysis | None = None,
) -> OptimizationAnalysis:
    game = _game(tmp_path)
    unknown = DetectedValue("Unknown", 0.0, "not detected")
    fingerprint = GameFingerprint(
        game.id,
        "10",
        game.name,
        "Steam",
        str(game.install_path),
        "",
        "",
        DetectedValue("Proton", 0.9, "runner"),
        unknown,
        unknown,
        "",
        unknown,
        (),
        unknown,
        False,
        (),
        "",
        SystemSnapshot("CPU", "GPU", 8, 16, "Display", 1920, 1080, 120),
    )
    return OptimizationAnalysis(
        fingerprint,
        measurement if measurement is not None else _measurement(),
        BottleneckAnalysis(conclusion, confidence, ("Measured evidence",), ()),
        (),
        frame_rate or FrameRateAnalysis("not_detected", None, 0.6, (), ()),
        GameSettingsAnalysis.unavailable(),
    )


def _plan(
    tmp_path: Path,
    conclusion: str,
    *,
    profile: GameOptimizationProfile | None = None,
    measurement: PerformanceMeasurement | None = None,
    frame_rate: FrameRateAnalysis | None = None,
    gamemode: bool = True,
    gamescope: bool = True,
    history=(),
):
    analysis = _analysis(
        tmp_path,
        conclusion,
        measurement=measurement,
        frame_rate=frame_rate,
    )
    selected_profile = profile or GameOptimizationProfile.default("10")
    return RuntimeCandidateRegistry().plan(
        analysis,
        selected_profile,
        gamemode_available=gamemode,
        gamescope_available=gamescope,
        history=tuple(history),
    )


def test_balanced_representative_baseline_is_a_no_op(tmp_path: Path) -> None:
    plan = _plan(tmp_path, "balanced")
    assert plan.problem.kind == "balanced"
    assert plan.available == ()
    assert plan.no_op_outcome == "target_already_met"
    assert "already meets" in plan.message


def test_healthy_frame_limit_is_not_removed_or_used_for_graphics_reduction(
    tmp_path: Path,
) -> None:
    frame_rate = FrameRateAnalysis(
        "likely_capped", 60, 0.95, ("Stable ceiling",), ()
    )
    plan = _plan(tmp_path, "balanced", frame_rate=frame_rate)
    assert plan.problem.kind == "frame_limited"
    assert plan.available == ()
    assert plan.no_op_outcome == "target_already_met"
    assert all("graphics" not in str(item).casefold() for item in plan.to_dict().values())


def test_unrepresentative_baseline_blocks_automatic(tmp_path: Path) -> None:
    plan = _plan(
        tmp_path,
        "frame_pacing_problem",
        measurement=_measurement(representative=False),
    )
    assert plan.problem.kind == "insufficient_data"
    assert plan.available == ()


def test_frame_pacing_can_select_gamemode_but_not_invent_gamescope_profile(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path, "frame_pacing_problem")
    assert [item["id"] for item in plan.available] == ["gamemode_runtime"]
    rejected = {item["id"]: item for item in plan.unavailable}
    assert rejected["gamescope_pacing"]["applicability"]["status"] == "not_supported"
    assert "frame limit" in rejected["gamescope_pacing"]["applicability"]["reasons"][0]


def test_gpu_bound_does_not_silently_reduce_resolution_or_graphics(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path, "gpu_bottleneck")
    assert plan.problem.kind == "gpu_bound"
    assert plan.available == ()
    assert "No safe runtime" in plan.message


def test_cpu_candidate_requires_available_inactive_gamemode(tmp_path: Path) -> None:
    unavailable = _plan(tmp_path, "cpu_bottleneck", gamemode=False)
    assert unavailable.available == ()
    active_profile = replace(
        GameOptimizationProfile.default("10"), gamemode_enabled=True
    )
    active = _plan(tmp_path, "cpu_bottleneck", profile=active_profile)
    statuses = {
        item["id"]: item["applicability"]["status"]
        for item in active.unavailable
    }
    assert statuses["gamemode_runtime"] == "already_active"


def test_manual_gamemode_override_is_a_conflict(tmp_path: Path) -> None:
    profile = replace(
        GameOptimizationProfile.default("10"),
        manual_overrides={"gamemode": True},
    )
    plan = _plan(tmp_path, "cpu_bottleneck", profile=profile)
    status = next(
        item["applicability"]["status"]
        for item in plan.unavailable if item["id"] == "gamemode_runtime"
    )
    assert status == "conflict"


@pytest.mark.parametrize("outcome", ("degraded", "marginal", "failed"))
def test_failed_candidate_is_not_retested_until_material_context_changes(
    tmp_path: Path, outcome: str,
) -> None:
    first = _plan(tmp_path, "cpu_bottleneck")
    history = ({
        "candidateId": "gamemode_runtime",
        "contextHash": first.context_hash,
        "outcome": outcome,
    },)
    repeated = _plan(tmp_path, "cpu_bottleneck", history=history)
    assert repeated.available == ()
    changed_profile = replace(
        GameOptimizationProfile.default("10"), target_fps=144
    )
    changed = _plan(
        tmp_path, "cpu_bottleneck", profile=changed_profile, history=history
    )
    assert [item["id"] for item in changed.available] == ["gamemode_runtime"]


def test_runtime_candidate_contains_only_reversible_per_game_profile_change(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path, "cpu_bottleneck")
    candidate = plan.available[0]
    assert candidate["profileChanges"] == {"gamemode_enabled": True}
    assert candidate["reversible"] is True
    assert "sysctl" not in json.dumps(candidate).casefold()


def test_activation_verification_requires_matching_completed_runner_session() -> None:
    report = {
        "baselineSessionId": "comparison-1",
        "baselineCompletionReceived": True,
        "wrappers": ["gamemode"],
        "gameModeWrapper": ["/usr/bin/gamemoderun"],
    }
    assert verify_runtime_activation(
        "gamemode_runtime", report, "comparison-1"
    )[0] is True
    assert verify_runtime_activation(
        "gamemode_runtime", report, "comparison-2"
    )[0] is False
    assert verify_runtime_activation(
        "gamemode_runtime", {**report, "gameModeWrapper": []}, "comparison-1"
    )[0] is False


def test_frame_pacing_evaluation_prioritizes_lows_and_tail_latency() -> None:
    before = _measurement(fps=201, low=99, p95=8.5, p99=10.1)
    after = _measurement(fps=198, low=126, p95=7.2, p99=7.8)
    problem = OptimizationProblemForTest("frame_pacing")
    result = AutomaticOptimizationEvaluator().evaluate(
        problem,
        before,
        after,
        activation_verified=True,
        activation_reason="Verified",
    )
    assert result.outcome == "improved"
    assert result.recommend_keep is True


def test_tail_regression_prevents_false_improvement() -> None:
    before = _measurement(fps=100, low=80, p95=12, p99=15)
    after = _measurement(fps=108, low=85, p95=12, p99=20)
    result = AutomaticOptimizationEvaluator().evaluate(
        OptimizationProblemForTest("gpu_bound"),
        before,
        after,
        activation_verified=True,
        activation_reason="Verified",
    )
    assert result.outcome == "degraded"
    assert result.recommend_revert is True


def test_gpu_problem_uses_measured_fps_and_frametime_improvement() -> None:
    before = _measurement(fps=55, low=42, p95=22, p99=28, gpu=98)
    after = _measurement(fps=61, low=48, p95=18, p99=23, gpu=94)
    result = AutomaticOptimizationEvaluator().evaluate(
        OptimizationProblemForTest("gpu_bound"),
        before,
        after,
        activation_verified=True,
        activation_reason="Verified",
    )

    assert result.outcome == "improved"
    assert result.recommend_keep is True


def test_same_capped_fps_with_lower_gpu_load_is_headroom_improved() -> None:
    before = _measurement(fps=60, low=57, p95=17.2, p99=18.1, gpu=88)
    after = _measurement(fps=60.2, low=57.5, p95=17.0, p99=18.0, gpu=68)
    capped = FrameRateAnalysis("likely_capped", 60, 0.94, (), ())
    result = AutomaticOptimizationEvaluator().evaluate(
        OptimizationProblemForTest("gpu_bound"),
        before,
        after,
        activation_verified=True,
        activation_reason="Verified",
        before_frame_rate=capped,
        after_frame_rate=capped,
    )

    assert result.outcome == "headroom_improved"
    assert result.recommend_keep is True


def test_noise_level_result_is_marginal() -> None:
    before = _measurement(fps=81.2, low=70, p95=14, p99=17)
    after = _measurement(fps=81.5, low=70.5, p95=13.9, p99=16.9)
    result = AutomaticOptimizationEvaluator().evaluate(
        OptimizationProblemForTest("cpu_bound"),
        before,
        after,
        activation_verified=True,
        activation_reason="Verified",
    )
    assert result.outcome == "marginal"
    assert result.recommend_keep is False


def test_unrepresentative_after_has_no_verdict() -> None:
    result = AutomaticOptimizationEvaluator().evaluate(
        OptimizationProblemForTest("frame_pacing"),
        _measurement(),
        _measurement(representative=False),
        activation_verified=True,
        activation_reason="Verified",
    )
    assert result.outcome == "insufficient_data"
    assert result.recommend_keep is False
    assert result.recommend_revert is False


def test_unverified_candidate_has_no_performance_verdict() -> None:
    result = AutomaticOptimizationEvaluator().evaluate(
        OptimizationProblemForTest("cpu_bound"),
        _measurement(),
        _measurement(fps=120),
        activation_verified=False,
        activation_reason="Wrapper missing",
    )
    assert result.outcome == "insufficient_data"
    assert result.activation_verified is False


def OptimizationProblemForTest(kind: str):
    from game_optimization_linux.services import OptimizationProblem

    return OptimizationProblem(kind, 0.85, "Measured target", ("Evidence",), ())


def test_repository_preserves_immutable_baseline_and_pending_state_across_restart(
    tmp_path: Path,
) -> None:
    repository = AutomaticOptimizationRepository(tmp_path / "state")
    plan = _plan(tmp_path, "cpu_bottleneck")
    baseline = _measurement(fps=90)
    session = repository.create("10", "steam-10", plan, baseline, plan.available[0])
    repository.mark_applied("10", session["id"], "change-1")

    restored = AutomaticOptimizationRepository(tmp_path / "state").load("10")

    assert restored["session"]["state"] == "candidate_applied"
    assert restored["session"]["candidateChangeId"] == "change-1"
    assert restored["session"]["originalBaseline"]["averageFps"] == 90


def test_repository_result_history_keep_and_revert_actions(tmp_path: Path) -> None:
    repository = AutomaticOptimizationRepository(tmp_path / "state")
    plan = _plan(tmp_path, "cpu_bottleneck")
    baseline = _measurement()
    session = repository.create("10", "steam-10", plan, baseline, plan.available[0])
    repository.mark_applied("10", session["id"], "change-1")
    repository.mark_comparison_recording("10", session["id"], "measurement-1")
    result = AutomaticOptimizationEvaluator().evaluate(
        plan.problem,
        baseline,
        _measurement(fps=100, low=65, p95=13, p99=18),
        activation_verified=True,
        activation_reason="Verified",
    )
    repository.mark_result("10", session["id"], result, _measurement(fps=100))
    repository.finish("10", session["id"], action="kept")

    saved = repository.load("10")
    assert saved["session"]["state"] == "kept"
    assert saved["history"][-1]["action"] == "kept"
    assert saved["history"][-1]["outcome"] == result.outcome
    assert saved["history"][-1]["before"]["averageFps"] == baseline.average_fps
    assert saved["history"][-1]["after"]["averageFps"] == 100
    assert saved["history"][-1]["activation"]["verified"] is True

    restarted = AutomaticOptimizationRepository(tmp_path / "state").load("10")
    assert restarted["session"]["comparison"]["after"]["averageFps"] == 100
    assert restarted["session"]["result"]["outcome"] == result.outcome


def test_repository_blocks_second_simultaneous_experiment(tmp_path: Path) -> None:
    repository = AutomaticOptimizationRepository(tmp_path / "state")
    plan = _plan(tmp_path, "cpu_bottleneck")
    repository.create("10", "steam-10", plan, _measurement(), plan.available[0])
    with pytest.raises(RuntimeError, match="already active"):
        repository.create(
            "10", "steam-10", plan, _measurement(), plan.available[0]
        )


def test_inconclusive_comparison_can_be_retried_with_a_new_session_id(
    tmp_path: Path,
) -> None:
    repository = AutomaticOptimizationRepository(tmp_path / "state")
    plan = _plan(tmp_path, "cpu_bottleneck")
    baseline = _measurement()
    session = repository.create("10", "steam-10", plan, baseline, plan.available[0])
    repository.mark_applied("10", session["id"], "change-1")
    repository.mark_comparison_recording("10", session["id"], "comparison-1")
    repository.mark_result(
        "10",
        session["id"],
        AutomaticOptimizationEvaluator().evaluate(
            plan.problem,
            baseline,
            _measurement(representative=False),
            activation_verified=True,
            activation_reason="Verified",
        ),
        _measurement(representative=False),
    )
    controller = OptimizationController(
        SimpleNamespace(_automatic_optimization_repository=repository)
    )

    controller._mark_automatic_comparison_started("10", "comparison-2")

    restored = repository.load("10")["session"]
    assert restored["state"] == "comparison_recording"
    assert restored["comparisonSessionId"] == "comparison-2"


def test_controller_completes_verified_measured_runtime_experiment(
    tmp_path: Path,
) -> None:
    repository = AutomaticOptimizationRepository(tmp_path / "state")
    plan = _plan(tmp_path, "cpu_bottleneck")
    before = _measurement(fps=80, low=58, p95=16, p99=21)
    after = _measurement(fps=86, low=66, p95=14, p99=18)
    session = repository.create("10", "steam-10", plan, before, plan.available[0])
    repository.mark_applied("10", session["id"], "change-1")
    repository.mark_comparison_recording("10", session["id"], "comparison-1")
    report = tmp_path / "runner-report.json"
    report.write_text(
        json.dumps({
            "baselineSessionId": "comparison-1",
            "baselineCompletionReceived": True,
            "wrappers": ["gamemode"],
            "gameModeWrapper": ["gamemoderun"],
        }),
        encoding="utf-8",
    )
    analysis = _analysis(tmp_path, "cpu_bottleneck", measurement=after)
    app = SimpleNamespace(
        _automatic_optimization_repository=repository,
        _automatic_optimization_evaluator=AutomaticOptimizationEvaluator(),
        _runner_report_path=lambda _app_id: report,
        _frame_rate_analyzer=SimpleNamespace(
            analyze=lambda _measurement, _system: FrameRateAnalysis(
                "not_detected", None, 0.7, (), ()
            )
        ),
    )

    OptimizationController(app)._complete_automatic_experiment(
        "steam-10", "10", "comparison-1", analysis
    )

    saved = repository.load("10")
    assert saved["session"]["state"] == "result_ready"
    assert saved["session"]["result"]["outcome"] == "improved"
    assert saved["session"]["activation"]["verified"] is True
    assert saved["history"][-1]["after"]["averageFps"] == 86


def test_controller_applies_one_runtime_candidate_and_can_revert_exact_profile(
    tmp_path: Path,
) -> None:
    game = _game(tmp_path)
    profiles = GameOptimizationProfileRepository(tmp_path / "profiles")
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    automatic = AutomaticOptimizationRepository(tmp_path / "automatic")
    changes = OptimizationChangeService(
        tmp_path / "changes", process_checker=lambda _game: False
    )
    analysis = _analysis(tmp_path, "cpu_bottleneck")
    sessions.save_measurement("10", analysis.measurement, slot="before")
    signal = Signal()
    app = SimpleNamespace(
        _resolve_game=lambda game_id, show_error=False: game if game_id == game.id else None,
        _optimization_analyses={game.id: analysis},
        _optimization_profile_repository=profiles,
        _runtime_tool_detector=SimpleNamespace(
            detect=lambda: (
                RuntimeToolAvailability("GameMode", True, "/usr/bin/gamemoderun"),
                RuntimeToolAvailability("Gamescope", False),
            )
        ),
        _automatic_optimization_repository=automatic,
        _runtime_candidate_registry=RuntimeCandidateRegistry(),
        _baseline_sessions=sessions,
        _optimization_change_service=changes,
        _optimization_applied_changes={},
        _optimization_comparisons={},
        optimizationAnalysisChanged=signal,
    )
    controller = OptimizationController(app)
    controller.analyzeGameOptimization = lambda _game_id: {"success": True}  # type: ignore[method-assign]

    started = controller.startAutomaticOptimization(game.id)
    changed_profile = profiles.load("10")
    change_id = started["automaticSession"]["candidateChangeId"]
    reverted = controller.revertOptimizationChange(game.id, change_id)

    assert started["success"] is True
    assert changed_profile.gamemode_enabled is True
    assert reverted["success"] is True
    restored_profile = profiles.load("10")
    assert restored_profile.gamemode_enabled is False
    assert restored_profile.preset == "automatic"
    assert automatic.load("10")["session"]["state"] == "reverted"


def test_apply_failure_restores_original_profile(tmp_path: Path) -> None:
    game = _game(tmp_path)
    profiles = GameOptimizationProfileRepository(tmp_path / "profiles")
    sessions = BaselineSessionRepository(tmp_path / "sessions")
    automatic = AutomaticOptimizationRepository(tmp_path / "automatic")
    analysis = _analysis(tmp_path, "cpu_bottleneck")
    sessions.save_measurement("10", analysis.measurement, slot="before")
    app = SimpleNamespace(
        _resolve_game=lambda *_args, **_kwargs: game,
        _optimization_analyses={game.id: analysis},
        _optimization_profile_repository=profiles,
        _runtime_tool_detector=SimpleNamespace(
            detect=lambda: (
                RuntimeToolAvailability("GameMode", True, "/usr/bin/gamemoderun"),
                RuntimeToolAvailability("Gamescope", False),
            )
        ),
        _automatic_optimization_repository=automatic,
        _runtime_candidate_registry=RuntimeCandidateRegistry(),
        _baseline_sessions=sessions,
        _optimization_change_service=OptimizationChangeService(
            tmp_path / "changes", process_checker=lambda _game: True
        ),
        _optimization_applied_changes={},
        _optimization_comparisons={},
        optimizationAnalysisChanged=Signal(),
    )

    result = OptimizationController(app).startAutomaticOptimization(game.id)

    assert result["success"] is False
    restored_profile = profiles.load("10")
    assert restored_profile.gamemode_enabled is False
    assert restored_profile.preset == "automatic"
    assert automatic.load("10")["session"]["state"] == "failed"


def test_diagnosis_does_not_use_total_cpu_as_a_game_specific_shortcut(
    tmp_path: Path,
) -> None:
    analysis = _analysis(
        tmp_path,
        "balanced",
        measurement=_measurement(cpu=99, gpu=20),
    )
    problem = diagnose_problem(analysis, GameOptimizationProfile.default("10"))
    assert problem.kind == "balanced"
