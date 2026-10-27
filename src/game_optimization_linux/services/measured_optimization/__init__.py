"""GameOpti measured optimization: goal -> clean baseline -> one candidate ->
comparable session -> transparent Keep/Revert/Inconclusive -> confirmation ->
local, scoped evidence for this game and system."""

from .candidates import BY_ID, GOALS, REGISTRY, CandidateContext, evaluate, proposal, verify_activation
from .capabilities import MATRIX, launcher_kind, support
from .comparison import check_comparability, final_level, metric_table, score
from .fingerprint import RunFingerprint, config_snapshot, pe_imports
from .metrics import SessionMetrics, find_session_log, session_metrics
from .store import (
    ACTIVE, STEPS, TERMINAL, MeasuredOptimizationStore, RestoreConflict, accepted_results,
    scope_match, step_index, tested_candidates,
)

__all__ = [
    "ACTIVE", "BY_ID", "CandidateContext", "GOALS", "MATRIX", "MeasuredOptimizationStore", "REGISTRY",
    "RestoreConflict", "RunFingerprint", "STEPS", "SessionMetrics", "TERMINAL", "accepted_results",
    "check_comparability", "config_snapshot", "evaluate", "final_level", "find_session_log",
    "launcher_kind", "metric_table", "pe_imports", "proposal", "scope_match", "score", "session_metrics",
    "step_index", "support", "tested_candidates", "verify_activation",
]
