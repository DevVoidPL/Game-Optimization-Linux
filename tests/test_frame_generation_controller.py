from threading import Event
import time
from types import SimpleNamespace
from game_optimization_linux.controllers.frame_generation_controller import FrameGenerationController


def test_status_coalesces_queries_never_installs_and_discards_pre_operation_snapshot(tmp_path):
    entered, release = Event(), Event()
    calls = {"status": 0, "install": 0}
    def status(game):
        calls["status"] += 1
        if calls["status"] == 1:
            entered.set(); release.wait(2)
            return {"success": True, "backend": "off", "installedFixture": False}
        return {"success": True, "backend": "off", "installedFixture": True}
    def install(*args): calls["install"] += 1
    game = SimpleNamespace(profile_key="123", launcher=SimpleNamespace(value="Steam"))
    service = SimpleNamespace(root=tmp_path, status=status, artifacts=SimpleNamespace(install=install))
    app = SimpleNamespace(_resolve_game=lambda *a, **k: game, _optiscaler_jobs={}, _domain_games={})
    controller = FrameGenerationController(app, service)
    try:
        assert not controller.status("game")["busy"]
        assert entered.wait(1)
        for _ in range(8): assert controller.status("game")["checking"]
        assert calls == {"status": 1, "install": 0}
        assert controller.submit("game", "install", {"backend": "afmf"})
        assert controller.job.result(timeout=1)["installedFixture"]
        result = controller.status("game")
        assert result["installedFixture"] and result["completedOperationId"] == 1
        release.set()
        time.sleep(.02)
        assert controller.status("game")["installedFixture"], "stale query must not overwrite operation completion"
        assert calls["install"] == 1
        controller.cancel_operation(); assert controller.cancel.is_set()
    finally:
        release.set(); controller.shutdown()
