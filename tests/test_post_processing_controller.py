from concurrent.futures import Future
from types import SimpleNamespace

from game_optimization_linux.controllers.post_processing_controller import PostProcessingController


class Executor:
    def __init__(self): self.calls = []
    def submit(self, action, *args):
        future = Future()
        self.calls.append((future, action, args))
        return future
    def shutdown(self, **kwargs): pass


def controller():
    service = SimpleNamespace(status=lambda game: None, apply=lambda *args: None)
    app = SimpleNamespace(_post_processing_service=service, _resolve_game=lambda *args, **kwargs: object(), _optiscaler_jobs={})
    result = PostProcessingController(app)
    result.executor.shutdown()
    result.executor = Executor()
    return result


def test_polling_is_not_busy_and_queries_are_deduplicated():
    c = controller()
    assert not c.status("game")["busy"]
    assert c.status("game")["checking"]
    assert len(c.executor.calls) == 1
    c.executor.calls[0][0].set_result({"success": True, "installed": True, "statusState": "installed"})
    assert c.status("game")["statusState"] == "installed"
    assert not c.status("game")["busy"]
    assert len(c.executor.calls) == 1


def test_apply_is_not_blocked_by_a_status_query_and_has_own_completion():
    c = controller()
    c.status("game")
    assert c.apply("game", {"parameters": {"Exposure": -.5}})
    assert c.status("game")["busy"]
    assert not c.apply("game", {})
    c.executor.calls[0][0].set_result({"success": True, "statusState": "installed"})
    c.executor.calls[1][0].set_result({"success": True, "statusState": "installed", "parameters": {"Exposure": -.5}})
    state = c.status("game")
    assert not state["busy"] and state["completedOperationId"] == 1
    assert state["parameters"]["Exposure"] == -.5


def test_runtime_evidence_survives_helper_log_truncation_but_not_a_new_session():
    c = controller()
    evidence = dict(configuredAt="config-1", sessionAt="session-1", processId=123)
    c._cache_status("game", dict(installed=True, runtimeLoaded=True, runtimeEvidence=evidence))
    base = dict(installed=True, runtimeLoaded=False, statusState="installed", diagnostics=dict(configuredAt="config-1", sessionAt="session-1"))
    c._cache_status("game", base)
    assert c.cache["game"]["runtimeLoaded"]
    c._cache_status("game", {**base, "diagnostics": dict(configuredAt="config-1", sessionAt="session-2")})
    assert not c.cache["game"]["runtimeLoaded"]


def test_operation_failure_stays_visible_without_turning_queries_into_operations():
    c = controller()
    assert c.apply("game", {})
    c.executor.calls[0][0].set_exception(ValueError("game is running"))
    state = c.status("game")
    assert not state["busy"] and state["operationError"] == "game is running"
    assert state["completedOperationId"] == 1
