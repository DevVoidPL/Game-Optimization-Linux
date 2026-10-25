"""Missing launcher components (MangoHud/Gamescope Flatpak extensions).

No real Flatpak is used: the host bridge is replaced by fakes, and the
HostServiceClient argv checks run with a recording command runner.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import subprocess
from threading import Event
import time

import pytest

from game_optimization_linux.services.host_service import HostServiceClient, HostServiceError
from game_optimization_linux.services.launcher_components import (
    LauncherComponentsService,
    classify_install_failure,
)
from test_heroic_optiscaler import _controller, _flatpak_heroic

HEROIC = "com.heroicgameslauncher.hgl"
MANGO_REF = "runtime/org.freedesktop.Platform.VulkanLayer.MangoHud/x86_64/25.08"


class _Host:
    """Fake host bridge with the same method surface as HostServiceClient."""

    def __init__(self, *, scope="system", runtime="org.freedesktop.Platform/x86_64/25.08",
                 point_version="25.08", remote_has=("MangoHud", "gamescope"),
                 install_rc=0, install_output="Installing… done", installs_tool=True) -> None:
        self.scope, self.runtime, self.point_version = scope, runtime, point_version
        self.remote_has = set(remote_has)
        self.install_rc, self.install_output, self.installs_tool = install_rc, install_output, installs_tool
        self.installed: set[str] = set()
        self.install_calls: list[tuple[str, str, str]] = []
        self.invalidated: list[str] = []
        self.probe_calls = 0
        self.inspect_gate = Event()
        self.inspect_gate.set()

    # sandbox probe
    def flatpak_app_capabilities(self, app_id, commands):
        self.probe_calls += 1
        found = {"gamemoderun"}
        extensions = []
        if "MangoHud" in self.installed:
            extensions.append("MangoHud")
        if "gamescope" in self.installed:
            found.add("gamescope")
        return {"commands": [name for name in commands if name in found], "extensions": extensions}

    def flatpak_runtime(self, app_id):
        return self.runtime

    def invalidate_app_capabilities(self, app_id):
        self.invalidated.append(app_id)

    # components
    def flatpak_app_installation(self, app_id):
        self.inspect_gate.wait(20.0)
        return {"scope": self.scope, "ref": f"app/{app_id}/x86_64/stable",
                "origin": "flathub", "runtime": self.runtime}

    def flatpak_runtime_extension_version(self, runtime, extension):
        return self.point_version

    def flatpak_extension_remote_info(self, scope, remote, ref):
        HostServiceClient.validate_extension_ref(ref)
        name = ref.split("/")[1].rsplit(".", 1)[-1]
        if name in self.remote_has:
            return {"returncode": "0", "stdout": "Ref: " + ref, "stderr": ""}
        return {"returncode": "1", "stdout": "", "stderr": f"error: Nothing matches {ref}"}

    def flatpak_extension_install_argv(self, scope, remote, ref):
        return HostServiceClient().flatpak_extension_install_argv(scope, remote, ref)

    def install_flatpak_extension(self, scope, remote, ref, on_line=None):
        HostServiceClient.validate_extension_ref(ref)
        self.install_calls.append((scope, remote, ref))
        if on_line:
            on_line("Installing 1/1… 50%")
        if self.install_rc == 0 and self.installs_tool:
            self.installed.add(ref.split("/")[1].rsplit(".", 1)[-1])
        return subprocess.CompletedProcess(["flatpak"], self.install_rc, self.install_output, "")


def _wait(function, predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    value = function()
    while time.monotonic() < deadline and not predicate(value):
        time.sleep(0.02)
        value = function()
    return value


def _setup(tmp_path: Path, host: _Host, *, mangohud=True, gamescope=False):
    game = _flatpak_heroic(tmp_path)
    controller = _controller(tmp_path, game)
    controller._host_service = host
    repository = controller._mangohud_repository
    repository.save(replace(repository.load(game.id), enabled=mangohud))
    profiles = controller._optimization_profile_repository
    profiles.save(replace(profiles.load(game.id), gamescope_enabled=gamescope,
                          gamescope_mode="native" if gamescope else "disabled"))
    return game, controller


def _components(controller, game):
    return _wait(lambda: controller.getLauncherComponents(game.id),
                 lambda value: not value["pending"])


# -- host bridge: whitelist and argv ------------------------------------------


@pytest.mark.parametrize("ref", [
    "runtime/org.freedesktop.Platform.VulkanLayer.vkBasalt/x86_64/25.08",
    "runtime/org.freedesktop.Platform.VulkanLayer.OBSVkCapture/x86_64/25.08",
    "app/com.example.Evil/x86_64/stable",
    "runtime/org.freedesktop.Platform.VulkanLayer.MangoHud/x86_64/stable",
    MANGO_REF + " --assumeyes",
    MANGO_REF + ";rm",
    "--user",
])
def test_only_whitelisted_refs_reach_the_host(ref: str) -> None:
    with pytest.raises(HostServiceError):
        HostServiceClient.validate_extension_ref(ref)


def test_install_argv_is_fixed_and_validated() -> None:
    client = HostServiceClient(environment={}, which=lambda name: f"/usr/bin/{name}")
    assert client.flatpak_extension_install_argv("system", "flathub", MANGO_REF) == [
        "flatpak", "install", "--system", "--noninteractive", "flathub", MANGO_REF,
    ]
    for scope, remote in (("root", "flathub"), ("user", "flat hub"), ("user", "-v")):
        with pytest.raises(HostServiceError):
            client.flatpak_extension_install_argv(scope, remote, MANGO_REF)


def test_install_runs_without_shell_and_in_c_locale() -> None:
    seen: dict = {}

    class _Process:
        stdout = iter(["Installing\r50%\r100%\n", "Done.\n"])

        def wait(self):
            return 0

    def popen(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        return _Process()

    client = HostServiceClient(environment={}, which=lambda name: f"/usr/bin/{name}")
    client._popen = popen
    lines: list[str] = []
    result = client.install_flatpak_extension("user", "flathub", MANGO_REF, lines.append)
    assert seen["argv"] == ["/usr/bin/flatpak", "install", "--user", "--noninteractive", "flathub", MANGO_REF]
    assert seen["shell"] is False and seen["env"]["LC_ALL"] == "C"
    assert lines == ["100%", "Done."] and result.returncode == 0


def test_app_installation_reports_the_scope() -> None:
    calls: list[list[str]] = []

    def runner(argv, **_kwargs):
        calls.append(argv)
        scope = argv[2]
        if scope == "--user":
            return subprocess.CompletedProcess(argv, 1, "", "not installed")
        answers = {"--show-ref": "app/x.y.Z/x86_64/stable", "--show-origin": "flathub",
                   "--show-runtime": "org.freedesktop.Platform/x86_64/25.08"}
        return subprocess.CompletedProcess(argv, 0, answers[argv[3]] + "\n", "")

    client = HostServiceClient(environment={}, which=lambda name: f"/usr/bin/{name}", command_runner=runner)
    info = client.flatpak_app_installation("x.y.Z")
    assert info == {"scope": "system", "ref": "app/x.y.Z/x86_64/stable", "origin": "flathub",
                    "runtime": "org.freedesktop.Platform/x86_64/25.08"}
    assert all(call[1] == "info" for call in calls)


def test_install_failures_are_classified() -> None:
    assert classify_install_failure("error: Flatpak system operation Deploy not allowed for user")[0] == "polkit"
    assert classify_install_failure("error: Not authorized")[0] == "polkit"
    assert classify_install_failure("error: Could not resolve hostname dl.flathub.org")[0] == "network"
    assert classify_install_failure("error: Nothing matches org.x in remote flathub")[0] == "missing_ref"


# -- service / controller ------------------------------------------------------


@pytest.mark.parametrize("scope", ["system", "user"])
def test_scope_follows_the_launcher_installation(tmp_path: Path, scope: str) -> None:
    host = _Host(scope=scope)
    game, controller = _setup(tmp_path, host)
    try:
        status = _components(controller, game)
        item = status["components"][0]
        assert item["tool"] == "mangohud" and item["state"] == "missing"
        assert item["scope"] == scope and item["polkit"] is (scope == "system")
        assert item["argv"] == f"flatpak install --{scope} --noninteractive flathub {MANGO_REF}"
        assert status["userFallback"] is False and status["canRepair"] is True
    finally:
        controller.shutdown()


def test_branch_comes_from_the_launcher_runtime(tmp_path: Path) -> None:
    host = _Host(runtime="org.freedesktop.Platform/x86_64/24.08", point_version="")
    game, controller = _setup(tmp_path, host)
    try:
        item = _components(controller, game)["components"][0]
        assert item["ref"] == "runtime/org.freedesktop.Platform.VulkanLayer.MangoHud/x86_64/24.08"
        assert item["branch"] == "24.08"
    finally:
        controller.shutdown()


def test_ref_missing_in_remote_is_not_offered(tmp_path: Path) -> None:
    host = _Host(remote_has=())
    game, controller = _setup(tmp_path, host)
    try:
        status = _components(controller, game)
        assert status["components"][0]["state"] == "unavailable"
        assert status["canRepair"] is False
        assert controller.repairLauncherComponents(game.id)["success"] is False
        assert host.install_calls == []
    finally:
        controller.shutdown()


def test_successful_install_is_confirmed_by_a_fresh_sandbox_probe(tmp_path: Path) -> None:
    host = _Host()
    game, controller = _setup(tmp_path, host)
    try:
        before = _wait(lambda: controller.getGameCapabilities(game.id), lambda c: not c["pending"])
        assert before["mangohud"]["available"] is False
        _components(controller, game)
        probes = host.probe_calls
        result = controller.repairLauncherComponents(game.id)
        assert result["success"] is True and result["started"] == ["mangohud"]
        status = _wait(lambda: controller.getLauncherComponents(game.id),
                       lambda value: value["components"][0]["state"] not in {"installing", "checking"})
        assert status["components"][0]["state"] == "ready"
        assert host.install_calls == [("system", "flathub", MANGO_REF)]
        assert host.invalidated == [HEROIC]  # 60 s cache dropped
        assert host.probe_calls == probes + 1  # fresh probe inside Heroic
        caps = controller.getGameCapabilities(game.id)
        assert caps["mangohud"]["available"] is True
        assert caps["tools"]["mangohud"]["launcher"] is True
        # Profile and launcher config untouched; Apply stays a separate step.
        assert controller.getLauncherIntegration(game.id)["state"] == "not_applied"
    finally:
        controller.shutdown()


def test_process_success_but_tool_missing_in_sandbox_is_an_error(tmp_path: Path) -> None:
    host = _Host(installs_tool=False)
    game, controller = _setup(tmp_path, host)
    try:
        _components(controller, game)
        controller.repairLauncherComponents(game.id)
        item = _wait(lambda: controller.getLauncherComponents(game.id)["components"][0],
                     lambda value: value["state"] != "installing")
        assert item["state"] == "error" and item["kind"] == "not_in_sandbox"
    finally:
        controller.shutdown()


@pytest.mark.parametrize(("output", "kind"), [
    ("error: Could not resolve hostname dl.flathub.org", "network"),
    ("error: Flatpak system operation Deploy not allowed for user", "polkit"),
])
def test_network_and_polkit_failures(tmp_path: Path, output: str, kind: str) -> None:
    host = _Host(install_rc=1, install_output=output)
    game, controller = _setup(tmp_path, host)
    try:
        _components(controller, game)
        controller.repairLauncherComponents(game.id)
        item = _wait(lambda: controller.getLauncherComponents(game.id)["components"][0],
                     lambda value: value["state"] != "installing")
        assert item["state"] == "error" and item["kind"] == kind
        assert controller.getGameCapabilities(game.id)["mangohud"]["available"] is False
    finally:
        controller.shutdown()


def test_restart_required_when_the_launcher_is_running(tmp_path: Path) -> None:
    host = _Host()
    game, controller = _setup(tmp_path, host)
    controller._launcher_integration._process_commands = lambda: ("/app/bin/heroic/heroic",)
    try:
        _components(controller, game)
        controller.repairLauncherComponents(game.id)
        item = _wait(lambda: controller.getLauncherComponents(game.id)["components"][0],
                     lambda value: value["state"] != "installing")
        assert item["state"] == "restart_required"
    finally:
        controller.shutdown()


def test_status_never_blocks_the_gui_thread(tmp_path: Path) -> None:
    host = _Host()
    host.inspect_gate.clear()  # host query hangs (network, flatpak lock)
    game, controller = _setup(tmp_path, host)
    try:
        _wait(lambda: controller.getGameCapabilities(game.id), lambda c: not c["pending"])
        started = time.monotonic()
        status = controller.getLauncherComponents(game.id)
        assert time.monotonic() - started < 1.0
        assert status["pending"] is True and status["canRepair"] is False
        assert status["components"][0]["state"] == "checking"
        host.inspect_gate.set()
        assert _components(controller, game)["components"][0]["state"] == "missing"
    finally:
        host.inspect_gate.set()
        controller.shutdown()


def test_nothing_is_proposed_when_the_profile_does_not_use_the_tool(tmp_path: Path) -> None:
    host = _Host()
    game, controller = _setup(tmp_path, host, mangohud=False, gamescope=False)
    try:
        status = _components(controller, game)
        assert status["components"] == [] and status["canRepair"] is False
        assert controller.repairLauncherComponents(game.id)["success"] is False
        assert host.install_calls == []
    finally:
        controller.shutdown()


def test_gamescope_is_offered_only_when_enabled_and_vkbasalt_never(tmp_path: Path) -> None:
    host = _Host()
    game, controller = _setup(tmp_path, host, mangohud=False, gamescope=True)
    try:
        status = _components(controller, game)
        assert [item["tool"] for item in status["components"]] == ["gamescope"]
        assert [item["tool"] for item in status["future"]] == ["vkbasalt"]
        controller.repairLauncherComponents(game.id)
        _wait(lambda: controller.getLauncherComponents(game.id)["components"][0],
              lambda value: value["state"] != "installing")
        assert all("vkBasalt" not in ref for _scope, _remote, ref in host.install_calls)
        assert host.install_calls[0][2].endswith("VulkanLayer.gamescope/x86_64/25.08")
    finally:
        controller.shutdown()


def test_service_alone_does_not_install_without_inspection() -> None:
    service = LauncherComponentsService(lambda: _Host(), refresh_probe=lambda _app: None)
    try:
        assert service.repair(HEROIC, ["mangohud"], launcher_running=lambda: False) == []
    finally:
        service.shutdown()
