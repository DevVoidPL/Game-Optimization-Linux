from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping
from uuid import uuid4

from game_optimization_linux.config import STATE_DIR
from game_optimization_linux.models import (
    BottleneckAnalysis,
    FrameRateAnalysis,
    GameFingerprint,
    GameOptimizationProfile,
    OptimizationAnalysis,
    OptimizationCandidate,
    PerformanceComparison,
    PerformanceMeasurement,
    validate_game_key,
)


AUTOMATIC_OPTIMIZATION_SCHEMA_VERSION = 1
AUTOMATIC_OPTIMIZATION_FILE_NAME = "automatic-optimization-v1.json"
_MAX_HISTORY = 20


@dataclass(frozen=True, slots=True)
class OptimizationProblem:
    kind: str
    confidence: float
    target: str
    evidence: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "confidence": min(1.0, max(0.0, self.confidence)),
            "target": self.target,
            "evidence": list(self.evidence),
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True, slots=True)
class CandidateApplicability:
    status: str
    reasons: tuple[str, ...]

    @property
    def available(self) -> bool:
        return self.status == "available"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "available": self.available,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class RuntimeOptimizationCandidate:
    id: str
    name: str
    category: str
    problem_types: tuple[str, ...]
    expected_goal: str
    risk: str
    quality_impact: str
    reversible: bool
    profile_changes: Mapping[str, Any]
    rank: int

    def to_dict(
        self, applicability: CandidateApplicability
    ) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "problemTypes": list(self.problem_types),
            "expectedGoal": self.expected_goal,
            "risk": self.risk,
            "qualityImpact": self.quality_impact,
            "reversible": self.reversible,
            "profileChanges": dict(self.profile_changes),
            "rank": self.rank,
            "applicability": applicability.to_dict(),
        }

    def as_profile_candidate(
        self, profile: GameOptimizationProfile, problem: OptimizationProblem
    ) -> OptimizationCandidate:
        if self.id != "gamemode_runtime":
            raise ValueError("Unsupported runtime optimization candidate")
        return OptimizationCandidate(
            id=self.id,
            target=problem.target,
            mechanism="GameMode runtime experiment",
            source="Representative MangoHud baseline and GameMode service diagnostic",
            evidence=problem.evidence,
            current_value="Enabled" if profile.gamemode_enabled else "Disabled",
            proposed_value="Enabled",
            expected_effect=self.expected_goal,
            quality_impact=self.quality_impact,
            risk=self.risk,
            reversible=self.reversible,
            requires_measurement=True,
            engine_support="Engine independent",
            api_support="Not graphics-API specific",
            env_changes={"wrapper": "gamemoderun"},
            automatically_selected=True,
            performance_impact="unknown until measured",
            confidence_label=(
                "high" if problem.confidence >= 0.8
                else "medium" if problem.confidence >= 0.55 else "low"
            ),
        )


@dataclass(frozen=True, slots=True)
class AutomaticOptimizationPlan:
    problem: OptimizationProblem
    available: tuple[dict[str, Any], ...]
    unavailable: tuple[dict[str, Any], ...]
    context_hash: str
    message: str

    @property
    def no_op_outcome(self) -> str:
        if self.problem.kind in {"balanced", "frame_limited"}:
            return "target_already_met"
        if self.problem.kind == "insufficient_data":
            return "insufficient_data"
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem": self.problem.to_dict(),
            "availableCandidates": [dict(item) for item in self.available],
            "unavailableCandidates": [dict(item) for item in self.unavailable],
            "availableCount": len(self.available),
            "contextHash": self.context_hash,
            "message": self.message,
            "canStart": bool(self.available),
            "noOpOutcome": self.no_op_outcome,
        }


@dataclass(frozen=True, slots=True)
class AutomaticOptimizationResult:
    outcome: str
    evidence: tuple[str, ...]
    recommend_keep: bool
    recommend_revert: bool
    activation_verified: bool
    activation_reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "evidence": list(self.evidence),
            "recommendKeep": self.recommend_keep,
            "recommendRevert": self.recommend_revert,
            "activationVerified": self.activation_verified,
            "activationReason": self.activation_reason,
        }


class RuntimeCandidateRegistry:
    def __init__(self) -> None:
        self._candidates = (
            RuntimeOptimizationCandidate(
                "gamemode_runtime",
                "GameMode",
                "runtime",
                ("cpu_bound", "frame_pacing"),
                "Test whether GameMode improves CPU-side consistency or frame pacing",
                "Low - the per-game profile can be restored exactly",
                "None",
                True,
                {"gamemode_enabled": True},
                10,
            ),
            RuntimeOptimizationCandidate(
                "gamescope_pacing",
                "Gamescope pacing experiment",
                "runtime",
                ("frame_pacing",),
                "Test a pacing-only Gamescope launch without changing image quality",
                "Moderate - compositor compatibility is game and session dependent",
                "None",
                True,
                {},
                20,
            ),
        )

    def plan(
        self,
        analysis: OptimizationAnalysis,
        profile: GameOptimizationProfile,
        *,
        gamemode_available: bool,
        gamescope_available: bool,
        history: tuple[Mapping[str, Any], ...] = (),
    ) -> AutomaticOptimizationPlan:
        problem = diagnose_problem(analysis, profile)
        context_hash = optimization_context_hash(analysis, profile, problem)
        available: list[dict[str, Any]] = []
        unavailable: list[dict[str, Any]] = []
        for candidate in self._candidates:
            applicability = self._applicability(
                candidate,
                problem,
                profile,
                gamemode_available=gamemode_available,
                gamescope_available=gamescope_available,
                history=history,
                context_hash=context_hash,
            )
            payload = candidate.to_dict(applicability)
            (available if applicability.available else unavailable).append(payload)
        available.sort(key=lambda item: int(item["rank"]))
        message = self._message(problem, bool(available))
        return AutomaticOptimizationPlan(
            problem,
            tuple(available),
            tuple(unavailable),
            context_hash,
            message,
        )

    def get(self, candidate_id: str) -> RuntimeOptimizationCandidate:
        for candidate in self._candidates:
            if candidate.id == candidate_id:
                return candidate
        raise ValueError("Automatic optimization candidate is unavailable")

    @staticmethod
    def _applicability(
        candidate: RuntimeOptimizationCandidate,
        problem: OptimizationProblem,
        profile: GameOptimizationProfile,
        *,
        gamemode_available: bool,
        gamescope_available: bool,
        history: tuple[Mapping[str, Any], ...],
        context_hash: str,
    ) -> CandidateApplicability:
        if problem.kind == "insufficient_data":
            return CandidateApplicability(
                "requires_representative_baseline",
                ("A representative baseline is required",),
            )
        if problem.kind not in candidate.problem_types:
            return CandidateApplicability(
                "not_relevant",
                (f"The measured problem is {problem.kind}",),
            )
        for record in history:
            if (
                str(record.get("candidateId") or "") == candidate.id
                and str(record.get("contextHash") or "") == context_hash
                and str(record.get("outcome") or "") in {
                    "degraded", "marginal", "failed"
                }
            ):
                return CandidateApplicability(
                    "previously_tested",
                    ("The candidate did not help under the same measured conditions",),
                )
        if candidate.id == "gamemode_runtime":
            if not gamemode_available:
                return CandidateApplicability(
                    "not_supported", ("GameMode is unavailable",)
                )
            if profile.gamemode_enabled:
                return CandidateApplicability(
                    "already_active", ("GameMode is already enabled for this game",)
                )
            if profile.manual_overrides.get("gamemode"):
                return CandidateApplicability(
                    "conflict",
                    ("The current GameMode state is a manual user override",),
                )
            minimum = 0.55 if problem.kind == "cpu_bound" else 0.65
            if problem.confidence < minimum:
                return CandidateApplicability(
                    "not_relevant",
                    ("The measured problem confidence is too low",),
                )
            return CandidateApplicability(
                "available",
                ("GameMode is available and is not active in the current profile",),
            )
        if candidate.id == "gamescope_pacing":
            if not gamescope_available:
                return CandidateApplicability(
                    "not_supported", ("Gamescope is unavailable",)
                )
            if profile.gamescope_enabled and profile.gamescope_mode != "disabled":
                return CandidateApplicability(
                    "already_active",
                    ("Gamescope is already configured by the user",),
                )
            return CandidateApplicability(
                "not_supported",
                (
                    "No verified pacing-only Gamescope profile is currently available "
                    "without adding a frame limit or changing image output",
                ),
            )
        return CandidateApplicability("not_supported", ("Candidate is unsupported",))

    @staticmethod
    def _message(problem: OptimizationProblem, has_candidate: bool) -> str:
        if problem.kind == "insufficient_data":
            return "Record a representative baseline before starting Automatic Optimization."
        if problem.kind == "frame_limited":
            return "No runtime optimization is necessary for the current measured target."
        if problem.kind == "balanced":
            return "The measured workload already meets the current target and has healthy frame pacing."
        if problem.kind == "memory_pressure":
            return "Memory pressure was detected, but Automatic v1 has no safe runtime memory candidate."
        if has_candidate:
            return "A reversible runtime experiment is available. Its result must be measured before it can be kept."
        return "No safe runtime optimization candidate is currently available."


class AutomaticOptimizationEvaluator:
    def evaluate(
        self,
        problem: OptimizationProblem,
        before: PerformanceMeasurement,
        after: PerformanceMeasurement,
        *,
        activation_verified: bool,
        activation_reason: str,
        before_frame_rate: FrameRateAnalysis | None = None,
        after_frame_rate: FrameRateAnalysis | None = None,
    ) -> AutomaticOptimizationResult:
        if not activation_verified:
            return AutomaticOptimizationResult(
                "insufficient_data",
                ("The runtime candidate was not verified in the effective launch plan",),
                False,
                False,
                False,
                activation_reason,
            )
        if not before.available or not after.available:
            return AutomaticOptimizationResult(
                "insufficient_data",
                ("Both measurements must be representative before the candidate is judged",),
                False,
                False,
                True,
                activation_reason,
            )
        base = _base_comparison(
            before,
            after,
            before_frame_rate=before_frame_rate,
            after_frame_rate=after_frame_rate,
        )
        if problem.kind == "frame_pacing":
            return self._frame_pacing_result(
                before, after, base, activation_reason
            )
        outcome = {
            "improvement": "improved",
            "regression": "degraded",
            "no_meaningful_change": "marginal",
            "headroom_improved": "headroom_improved",
            "insufficient_data": "insufficient_data",
        }.get(base.outcome, "marginal")
        return AutomaticOptimizationResult(
            outcome,
            base.evidence,
            outcome in {"improved", "headroom_improved"},
            outcome == "degraded",
            True,
            activation_reason,
        )

    @staticmethod
    def _frame_pacing_result(
        before: PerformanceMeasurement,
        after: PerformanceMeasurement,
        base: PerformanceComparison,
        activation_reason: str,
    ) -> AutomaticOptimizationResult:
        average = _relative_change(before.average_fps, after.average_fps)
        low = _relative_change(
            before.one_percent_low_fps, after.one_percent_low_fps
        )
        p95 = _relative_change(
            before.p95_frametime_ms, after.p95_frametime_ms
        )
        p99 = _relative_change(
            before.p99_frametime_ms, after.p99_frametime_ms
        )
        regression = bool(
            base.recommend_revert
            or (p99 is not None and p99 >= 0.10)
            or (low is not None and low <= -0.08)
        )
        improved = bool(
            not regression
            and average is not None
            and average >= -0.03
            and (
                (low is not None and low >= 0.08 and p99 is not None and p99 <= -0.08)
                or (
                    p95 is not None and p95 <= -0.08
                    and p99 is not None and p99 <= -0.05
                )
            )
        )
        outcome = "degraded" if regression else "improved" if improved else "marginal"
        evidence = list(base.evidence)
        if improved:
            evidence.append(
                "Frame consistency improved even though average FPS was not the primary goal"
            )
        return AutomaticOptimizationResult(
            outcome,
            tuple(evidence),
            outcome == "improved",
            outcome == "degraded",
            True,
            activation_reason,
        )


class AutomaticOptimizationRepository:
    def __init__(self, root: Path = STATE_DIR / "games") -> None:
        self.root = Path(root)

    def path(self, app_id: object) -> Path:
        return self.root / validate_game_key(app_id) / AUTOMATIC_OPTIMIZATION_FILE_NAME

    def load(self, app_id: object) -> dict[str, Any]:
        path = self.path(app_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self._empty(app_id)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return self._empty(app_id)
        if (
            not isinstance(payload, dict)
            or int(payload.get("schemaVersion") or 0)
            != AUTOMATIC_OPTIMIZATION_SCHEMA_VERSION
            or str(payload.get("appId") or "") != validate_game_key(app_id)
        ):
            return self._empty(app_id)
        history = payload.get("history")
        session = payload.get("session")
        payload["history"] = history if isinstance(history, list) else []
        payload["session"] = session if isinstance(session, dict) else {}
        return payload

    def create(
        self,
        app_id: object,
        game_id: str,
        plan: AutomaticOptimizationPlan,
        baseline: PerformanceMeasurement,
        candidate: Mapping[str, Any],
    ) -> dict[str, Any]:
        document = self.load(app_id)
        current = document.get("session")
        if isinstance(current, Mapping) and str(current.get("state") or "") in {
            "planned", "candidate_applied", "comparison_recording", "result_ready"
        }:
            raise RuntimeError("An Automatic Optimization experiment is already active")
        now = datetime.now(UTC).isoformat()
        session = {
            "id": uuid4().hex,
            "appId": validate_game_key(app_id),
            "gameId": str(game_id),
            "state": "planned",
            "createdAt": now,
            "updatedAt": now,
            "problem": plan.problem.to_dict(),
            "target": plan.problem.target,
            "contextHash": plan.context_hash,
            "candidateQueue": [item["id"] for item in plan.available],
            "candidate": dict(candidate),
            "candidateChangeId": "",
            "activation": {"verified": False, "reason": "Not launched yet"},
            "originalBaseline": baseline.to_dict(),
            "comparison": {},
            "result": {},
            "failureReason": "",
        }
        document["session"] = session
        self._save(document)
        return session

    def mark_applied(
        self, app_id: object, session_id: str, change_id: str
    ) -> dict[str, Any]:
        return self._update(
            app_id,
            session_id,
            state="candidate_applied",
            candidateChangeId=str(change_id),
            activation={"verified": False, "reason": "Waiting for comparison launch"},
        )

    def mark_comparison_recording(
        self,
        app_id: object,
        session_id: str,
        measurement_session_id: str,
    ) -> dict[str, Any]:
        return self._update(
            app_id,
            session_id,
            state="comparison_recording",
            comparisonSessionId=str(measurement_session_id),
            activation={"verified": False, "reason": "Waiting for comparison completion"},
            comparison={},
            result={},
        )

    def mark_result(
        self,
        app_id: object,
        session_id: str,
        result: AutomaticOptimizationResult,
        after: PerformanceMeasurement,
    ) -> dict[str, Any]:
        session = self._update(
            app_id,
            session_id,
            state="result_ready",
            activation={
                "verified": result.activation_verified,
                "reason": result.activation_reason,
            },
            comparison={"after": after.to_dict()},
            result=result.to_dict(),
        )
        document = self.load(app_id)
        history = [
            item for item in document["history"]
            if not (
                isinstance(item, Mapping)
                and str(item.get("sessionId") or "") == session_id
            )
        ]
        history.append({
            "sessionId": session_id,
            "candidateId": str(session.get("candidate", {}).get("id") or ""),
            "candidateName": str(session.get("candidate", {}).get("name") or ""),
            "contextHash": str(session.get("contextHash") or ""),
            "problem": str(session.get("problem", {}).get("kind") or ""),
            "target": str(session.get("target") or ""),
            "outcome": result.outcome,
            "action": "pending",
            "testedAt": datetime.now(UTC).isoformat(),
            "before": dict(session.get("originalBaseline") or {}),
            "after": after.to_dict(),
            "activation": {
                "verified": result.activation_verified,
                "reason": result.activation_reason,
            },
            "result": result.to_dict(),
        })
        document["history"] = history[-_MAX_HISTORY:]
        self._save(document)
        return session

    def finish(
        self, app_id: object, session_id: str, *, action: str
    ) -> dict[str, Any]:
        if action not in {"kept", "reverted"}:
            raise ValueError("Unsupported Automatic Optimization action")
        session = self._update(app_id, session_id, state=action)
        document = self.load(app_id)
        for record in document["history"]:
            if (
                isinstance(record, dict)
                and str(record.get("sessionId") or "") == session_id
            ):
                record["action"] = action
        self._save(document)
        return session

    def fail(
        self, app_id: object, session_id: str, reason: str
    ) -> dict[str, Any]:
        return self._update(
            app_id,
            session_id,
            state="failed",
            failureReason=str(reason),
        )

    def history(self, app_id: object) -> tuple[Mapping[str, Any], ...]:
        return tuple(
            item for item in self.load(app_id)["history"]
            if isinstance(item, Mapping)
        )

    def _update(
        self, app_id: object, session_id: str, **values: Any
    ) -> dict[str, Any]:
        document = self.load(app_id)
        session = document.get("session")
        if not isinstance(session, dict) or str(session.get("id") or "") != session_id:
            raise RuntimeError("Automatic Optimization session is no longer current")
        session.update(values)
        session["updatedAt"] = datetime.now(UTC).isoformat()
        self._save(document)
        return dict(session)

    def _save(self, payload: Mapping[str, Any]) -> None:
        path = self.path(payload.get("appId"))
        path.parent.mkdir(parents=True, exist_ok=True)
        data = (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        descriptor, name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _empty(app_id: object) -> dict[str, Any]:
        return {
            "schemaVersion": AUTOMATIC_OPTIMIZATION_SCHEMA_VERSION,
            "appId": validate_game_key(app_id),
            "session": {},
            "history": [],
        }


def diagnose_problem(
    analysis: OptimizationAnalysis, profile: GameOptimizationProfile
) -> OptimizationProblem:
    measurement = analysis.measurement
    bottleneck = analysis.bottleneck
    if measurement is None or not measurement.available:
        return OptimizationProblem(
            "insufficient_data",
            0.0,
            "Record representative performance data",
            (),
            ("A representative baseline is required",),
        )
    if (
        analysis.frame_rate.state == "likely_capped"
        and bottleneck.conclusion == "balanced"
    ):
        return OptimizationProblem(
            "frame_limited",
            max(analysis.frame_rate.confidence, bottleneck.confidence),
            "Preserve the current healthy frame target",
            (*analysis.frame_rate.evidence, *bottleneck.evidence),
            analysis.frame_rate.limitations,
        )
    mapping = {
        "gpu_bottleneck": ("gpu_bound", "Improve GPU-limited performance"),
        "cpu_bottleneck": ("cpu_bound", "Improve CPU-side consistency and performance"),
        "frame_pacing_problem": ("frame_pacing", "Improve frame consistency"),
        "vram_pressure": ("memory_pressure", "Reduce measured VRAM pressure"),
        "ram_pressure": ("memory_pressure", "Reduce measured RAM pressure"),
        "balanced": ("balanced", "Preserve the current balanced workload"),
    }
    kind, target = mapping.get(
        bottleneck.conclusion,
        ("insufficient_data", "Record representative performance data"),
    )
    return OptimizationProblem(
        kind,
        bottleneck.confidence,
        target,
        bottleneck.evidence,
        bottleneck.limitations,
    )


def optimization_context_hash(
    analysis: OptimizationAnalysis,
    profile: GameOptimizationProfile,
    problem: OptimizationProblem,
) -> str:
    fingerprint = analysis.fingerprint
    payload = {
        "problem": problem.kind,
        "target": profile.target_fps,
        "goal": profile.user_goal,
        "executable": fingerprint.main_executable,
        "runtime": fingerprint.runtime.value,
        "engine": fingerprint.engine.value,
        "cpu": fingerprint.system.cpu,
        "gpu": fingerprint.system.gpu,
        "vram": fingerprint.system.vram_gb,
        "resolution": [
            fingerprint.system.resolution_width,
            fingerprint.system.resolution_height,
            fingerprint.system.refresh_rate,
        ],
        "settings": sorted(
            (item.file, item.config_sha256)
            for item in analysis.settings.detected
            if item.file and item.config_sha256
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def verify_runtime_activation(
    candidate_id: str,
    report: Mapping[str, Any],
    comparison_session_id: str,
) -> tuple[bool, str]:
    if str(report.get("baselineSessionId") or "") != comparison_session_id:
        return False, "The runner report belongs to a different measurement session"
    if not bool(report.get("baselineCompletionReceived", False)):
        return False, "The runner did not confirm comparison completion"
    if candidate_id == "gamemode_runtime":
        wrappers = report.get("wrappers")
        wrapper = report.get("gameModeWrapper")
        if (
            isinstance(wrappers, list)
            and "gamemode" in wrappers
            and isinstance(wrapper, list)
            and bool(wrapper)
        ):
            return True, "The effective runner plan wrapped the measured game command with GameMode"
        return False, "GameMode was requested but was absent from the measured runner plan"
    if candidate_id == "gamescope_pacing":
        wrappers = report.get("wrappers")
        wrapper = report.get("gamescopeWrapper")
        if (
            isinstance(wrappers, list)
            and "gamescope" in wrappers
            and isinstance(wrapper, list)
            and bool(wrapper)
        ):
            return True, "The effective runner plan used Gamescope"
        return False, "Gamescope was absent from the measured runner plan"
    return False, "The runtime candidate has no activation verifier"


def _relative_change(old: float | None, new: float | None) -> float | None:
    if old is None or new is None or old == 0:
        return None
    return (new - old) / old


def _base_comparison(
    before: PerformanceMeasurement,
    after: PerformanceMeasurement,
    *,
    before_frame_rate: FrameRateAnalysis | None,
    after_frame_rate: FrameRateAnalysis | None,
) -> PerformanceComparison:
    from .performance_analysis import compare_measurements

    return compare_measurements(
        before,
        after,
        before_frame_rate=before_frame_rate,
        after_frame_rate=after_frame_rate,
    )


__all__ = [
    "AUTOMATIC_OPTIMIZATION_FILE_NAME",
    "AUTOMATIC_OPTIMIZATION_SCHEMA_VERSION",
    "AutomaticOptimizationEvaluator",
    "AutomaticOptimizationPlan",
    "AutomaticOptimizationRepository",
    "AutomaticOptimizationResult",
    "CandidateApplicability",
    "OptimizationProblem",
    "RuntimeCandidateRegistry",
    "RuntimeOptimizationCandidate",
    "diagnose_problem",
    "optimization_context_hash",
    "verify_runtime_activation",
]
