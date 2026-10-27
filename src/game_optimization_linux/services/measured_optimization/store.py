"""Per-game workflow state, byte-exact restore points and local evidence.

Everything is stored locally under the GameOpti state directory. Nothing is
uploaded. Evidence is scoped to the game build, hardware, driver, runtime and
relevant configuration; a result from one game never applies to another.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Mapping
from uuid import uuid4

from game_optimization_linux.config import STATE_DIR
from game_optimization_linux.models.mangohud import validate_game_key

SCHEMA_VERSION = 1
FILE_NAME = "measured-optimization-v1.json"
MAX_HISTORY = 50
ACTIVE = ("goal_selected", "baseline_recording", "baseline_ready", "candidate_applied",
          "candidate_recording", "awaiting_confirmation", "result_ready")
TERMINAL = ("kept", "reverted", "cancelled", "failed", "no_candidate")
TRANSITIONS = {
    "goal_selected": {"baseline_recording", "cancelled"},
    "baseline_recording": {"baseline_ready", "goal_selected", "cancelled"},
    "baseline_ready": {"candidate_applied", "baseline_recording", "no_candidate", "cancelled"},
    "candidate_applied": {"candidate_recording", "cancelled", "failed"},
    "candidate_recording": {"awaiting_confirmation", "candidate_applied", "cancelled", "failed"},
    "awaiting_confirmation": {"result_ready", "candidate_applied", "cancelled", "failed"},
    "result_ready": {"kept", "reverted", "awaiting_confirmation", "cancelled", "failed"},
}
STEPS = ("goal", "fingerprint", "baseline", "candidate", "apply", "record", "confirm", "review", "decide", "save")
_STEP_OF_STATE = {"goal_selected": 2, "baseline_recording": 2, "baseline_ready": 3, "candidate_applied": 5,
                  "candidate_recording": 5, "awaiting_confirmation": 6, "result_ready": 8, "kept": 10,
                  "reverted": 10, "cancelled": 10, "failed": 10, "no_candidate": 10}
UNKNOWN = "Unknown"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes, mode: int = 0o600, *, replace: Callable[[Any, Any], None] = os.replace) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class RestoreConflict(RuntimeError):
    """A restore point no longer matches the file it would overwrite."""


class MeasuredOptimizationStore:
    def __init__(self, root: Path = STATE_DIR / "games") -> None:
        self.root = Path(root)

    def path(self, key: object) -> Path:
        return self.root / validate_game_key(key) / FILE_NAME

    def _restore_directory(self, key: str, workflow_id: str) -> Path:
        if not workflow_id.isalnum():
            raise ValueError("invalid workflow id")
        return self.root / validate_game_key(key) / "measured-optimization" / workflow_id

    # Documents -------------------------------------------------------------
    def load(self, key: object) -> dict[str, Any]:
        key = validate_game_key(key)
        try:
            value = json.loads(self.path(key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            value = None
        if (not isinstance(value, dict) or value.get("schemaVersion") != SCHEMA_VERSION
                or value.get("appId") != key):
            return {"schemaVersion": SCHEMA_VERSION, "appId": key, "workflow": {}, "history": []}
        value["workflow"] = value.get("workflow") if isinstance(value.get("workflow"), dict) else {}
        value["history"] = [item for item in value.get("history") or [] if isinstance(item, dict)]
        return value

    def _save(self, document: Mapping[str, Any]) -> None:
        data = (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        atomic_write(self.path(document["appId"]), data)

    def workflow(self, key: object) -> dict[str, Any]:
        return dict(self.load(key)["workflow"])

    def start(self, key: object, game_id: str, goal: Mapping[str, Any]) -> dict[str, Any]:
        document = self.load(key)
        current = document["workflow"]
        if current.get("state") in ACTIVE:
            raise RuntimeError("A measured optimization is already in progress for this game")
        now = _now()
        workflow = {"id": uuid4().hex, "gameId": str(game_id), "state": "goal_selected", "goal": dict(goal),
                    "createdAt": now, "updatedAt": now, "events": [{"at": now, "state": "goal_selected"}],
                    "baseline": {}, "candidate": {}, "restorePoint": {}, "comparison": {},
                    "comparability": {}, "result": {}, "error": "", "skipped": []}
        document["workflow"] = workflow
        self._save(document)
        return dict(workflow)

    def update(self, key: object, workflow_id: str, state: str | None = None, **values: Any) -> dict[str, Any]:
        document = self.load(key)
        workflow = document["workflow"]
        if workflow.get("id") != workflow_id:
            raise RuntimeError("The measured optimization changed in the meantime")
        if state is not None and state != workflow.get("state"):
            allowed = TRANSITIONS.get(str(workflow.get("state")), set())
            if state not in allowed:
                raise RuntimeError(f"Cannot go from {workflow.get('state')} to {state}")
            workflow["state"] = state
            workflow.setdefault("events", []).append({"at": _now(), "state": state})
        workflow.update(values)
        workflow["updatedAt"] = _now()
        self._save(document)
        return dict(workflow)

    def annotate(self, key: object, record_id: str, **values: Any) -> dict[str, Any]:
        """Add an explicit owner decision to one saved result (never its measurements)."""
        document = self.load(key)
        for record in document["history"]:
            if record.get("id") == record_id:
                record.update(values)
                self._save(document)
                return dict(record)
        raise RuntimeError("The saved result was not found")

    def add_evidence(self, key: object, record: Mapping[str, Any]) -> dict[str, Any]:
        document = self.load(key)
        entry = {"id": uuid4().hex, "savedAt": _now(), **dict(record)}
        document["history"] = [*document["history"], entry][-MAX_HISTORY:]
        self._save(document)
        return entry

    # Restore points ---------------------------------------------------------
    def create_restore_point(self, key: str, workflow_id: str, files: Mapping[str, Path]) -> list[dict[str, Any]]:
        """Copy the current bytes (or their absence) of each file."""

        directory = self._restore_directory(key, workflow_id)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        entries = []
        for name, path in sorted(files.items()):
            path = Path(path)
            if path.is_symlink():
                raise ValueError(f"{path.name} is a symbolic link; GameOpti does not modify it")
            existed = path.is_file()
            data = path.read_bytes() if existed else b""
            if existed:
                atomic_write(directory / f"{name}.before", data)
            entries.append({"name": name, "path": str(path), "existed": existed,
                            "beforeSha256": _sha(data) if existed else None, "afterSha256": None})
        return entries

    @staticmethod
    def seal(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Record the bytes written by the candidate (the state to undo)."""

        sealed = []
        for entry in entries:
            path = Path(entry["path"])
            sealed.append({**entry, "afterSha256": _sha(path.read_bytes()) if path.is_file() else None})
        return sealed

    def restore(self, key: str, workflow_id: str, entries: list[dict[str, Any]], *, force: bool = False) -> list[str]:
        """Write the saved bytes back. Refuses when a file changed after the
        candidate was applied (``force`` is used only to undo a failed apply)."""

        directory = self._restore_directory(key, workflow_id)
        if not force:
            for entry in entries:
                path = Path(entry["path"])
                current = _sha(path.read_bytes()) if path.is_file() else None
                if current not in (entry.get("afterSha256"), entry.get("beforeSha256")):
                    raise RestoreConflict(f"{path.name} changed after the candidate was applied")
        restored = []
        for entry in entries:
            path = Path(entry["path"])
            if entry.get("existed"):
                data = (directory / f"{entry['name']}.before").read_bytes()
                if _sha(data) != entry.get("beforeSha256"):
                    raise RestoreConflict(f"The restore copy of {path.name} is damaged")
                atomic_write(path, data, (path.stat().st_mode & 0o777) if path.exists() else 0o600)
                if _sha(path.read_bytes()) != entry["beforeSha256"]:
                    raise RestoreConflict(f"{path.name} could not be restored")
            else:
                path.unlink(missing_ok=True)
            restored.append(path.name)
        return restored


def scope_match(record: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    """How well a saved result's scope matches the current game and system."""

    if record.get("key") != current.get("key"):
        return {"state": "other_game", "usable": False, "differences": ["key"]}

    def known(value: Any) -> bool:
        return value not in (None, "", UNKNOWN)

    differences, unknown, unproven = [], [], []
    for name in ("gpu", "cpu", "vramGb", "build", "driver", "runtime", "resolution", "api"):
        old, new = record.get(name), current.get(name)
        if not known(old) and not known(new):
            unproven.append(name)  # unknown then and now: a stated limitation
        elif not known(old) or not known(new):
            unknown.append(name)
        elif old != new:
            differences.append(name)
    if {"gpu", "cpu", "vramGb"} & set(differences):
        state = "hardware_changed"
    elif "build" in differences:
        state = "build_changed"
    elif {"driver", "runtime"} & set(differences):
        state = "software_changed"
    elif differences:
        state = "configuration_changed"
    elif unknown:
        state = "unverified_scope"
    else:
        state = "current"
    return {"state": state, "usable": state == "current", "differences": differences, "unknown": unknown,
            "unprovenFields": unproven}


def record_status(record: Mapping[str, Any]) -> dict[str, Any]:
    """Confidence parts of a saved result, derived at read time for records
    saved before they were stored; the measured values are never changed."""
    from .candidates import BY_ID
    from .comparison import confidence_parts
    candidate = BY_ID.get(str(record.get("candidateId") or ""))
    image_quality = str(record.get("imageQualityImpact") or (candidate.image_quality if candidate else "none"))
    observations = dict((record.get("comparability") or {}).get("observations") or {})
    visual = str(record.get("qualityDecision") or observations.get("visual") or "not_checked")
    level = str((record.get("comparability") or {}).get("final") or ("controlled" if record.get("verified") else "pending"))
    parts = confidence_parts(str(record.get("decision") or "inconclusive"), level, image_quality, visual)
    return {**parts, "legacy": "overall" not in record, "imageQualityImpact": image_quality}


def tested_candidates(history: list[Mapping[str, Any]], scope: Mapping[str, Any], goal: str) -> dict[str, str]:
    """Candidates with a verified result in the current scope and goal."""

    result: dict[str, str] = {}
    for record in history:
        if (record.get("verified") and record.get("goal") == goal
                and scope_match(record.get("scope") or {}, scope)["usable"]):
            result[str(record.get("candidateId"))] = str(record.get("decision"))
    return result


def accepted_results(history: list[Mapping[str, Any]], scope: Mapping[str, Any], goal: str) -> dict[str, Any]:
    """Optimization-profile values kept after verified results (newest wins)."""

    values: dict[str, Any] = {}
    latest: dict[str, Mapping[str, Any]] = {}
    for record in history:
        if record.get("goal") == goal and record_status(record)["fullyVerified"] \
                and record.get("userAction") in ("kept", "reverted") \
                and record.get("qualityDecision") != "worse" \
                and scope_match(record.get("scope") or {}, scope)["usable"]:
            latest[str(record.get("candidateId"))] = record
    for record in latest.values():
        if record.get("userAction") != "kept" or record.get("decision") != "keep":
            continue
        for change in record.get("changes") or ():
            if isinstance(change, Mapping) and change.get("target") == "optimization":
                values[str(change.get("setting"))] = change.get("after")
    return values


def step_index(state: str) -> int:
    return _STEP_OF_STATE.get(state, 0)


__all__ = [
    "ACTIVE", "FILE_NAME", "MeasuredOptimizationStore", "RestoreConflict", "STEPS", "TERMINAL",
    "TRANSITIONS", "accepted_results", "record_status", "atomic_write", "scope_match", "step_index", "tested_candidates",
]
