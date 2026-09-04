from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QGuiApplication

from game_optimization_linux.config import APP_ID, APP_NAME, APP_VERSION
from game_optimization_linux.controllers.system_controller import SystemController
from game_optimization_linux.services.system_diagnostics import (
    build_public_system_diagnostics,
    format_public_system_diagnostics,
    read_flatpak_app_commit,
)


def test_public_diagnostics_reuses_authoritative_application_metadata(
    tmp_path: Path,
) -> None:
    diagnostics = build_public_system_diagnostics(
        {},
        environment={},
        flatpak_info_path=tmp_path / "missing-flatpak-info",
        architecture="x86_64",
    )

    assert diagnostics["appName"] == APP_NAME
    assert diagnostics["appVersion"] == APP_VERSION
    assert diagnostics["appId"] == APP_ID
    assert diagnostics["flatpak"] is False
    assert "appCommit" not in diagnostics


def test_flatpak_metadata_reads_only_a_valid_application_commit(tmp_path: Path) -> None:
    commit = "a1" * 32
    metadata = tmp_path / ".flatpak-info"
    metadata.write_text(
        "[Application]\n"
        "name=io.github.Example.Application\n"
        f"app-commit={commit}\n"
        "branch=unstable\n",
        encoding="utf-8",
    )

    diagnostics = build_public_system_diagnostics(
        {},
        environment={"FLATPAK_ID": "io.github.Example.Application"},
        flatpak_info_path=metadata,
        architecture="x86_64",
    )

    assert read_flatpak_app_commit(metadata) == commit
    assert diagnostics["flatpak"] is True
    assert diagnostics["appCommit"] == commit
    assert "branch" not in diagnostics


def test_malformed_flatpak_metadata_is_omitted_without_hiding_flatpak(
    tmp_path: Path,
) -> None:
    metadata = tmp_path / ".flatpak-info"
    metadata.write_text(
        "[Application\napp-commit=not-a-commit\n",
        encoding="utf-8",
    )

    diagnostics = build_public_system_diagnostics(
        {},
        environment={},
        flatpak_info_path=metadata,
        architecture="aarch64",
    )

    assert diagnostics["flatpak"] is True
    assert "appCommit" not in diagnostics
    assert read_flatpak_app_commit(metadata) == ""


def test_public_copy_is_allowlisted_and_excludes_private_machine_identity(
    tmp_path: Path,
) -> None:
    diagnostics = build_public_system_diagnostics(
        {
            "distribution": "Example Linux 42",
            "kernel": "6.12.1",
            "desktopEnvironment": "KDE Plasma",
            "sessionType": "Wayland",
            "gpu": "Example Radeon GPU",
            "gpuDriver": "Mesa 25.1",
            "vulkanDevice": "RADV Example Radeon",
            "steamExecutableDetected": True,
            "capabilities": {
                "GameMode": "Available",
                "Gamescope": "Not detected",
                "MangoHud": "Available",
            },
            "username": "private-user",
            "hostname": "private-host",
            "home": "/home/private-user",
            "machineId": "private-machine-id",
            "steamAccountId": "76561198000000000",
        },
        environment={
            "USER": "private-user",
            "HOME": "/home/private-user",
            "HOSTNAME": "private-host",
        },
        flatpak_info_path=tmp_path / "missing-flatpak-info",
        architecture="x86_64",
    )

    copied = format_public_system_diagnostics(diagnostics)

    assert f"{APP_NAME} {APP_VERSION}" in copied
    assert f"App ID: {APP_ID}" in copied
    assert "Flatpak: no" in copied
    assert "OS: Example Linux 42" in copied
    assert "Session: Wayland" in copied
    assert "Steam: detected" in copied
    assert "Gamescope: not detected" in copied
    for private_value in (
        "private-user",
        "private-host",
        "/home/",
        "private-machine-id",
        "76561198000000000",
    ):
        assert private_value not in copied


def test_unknown_optional_fields_and_tool_statuses_are_omitted(tmp_path: Path) -> None:
    diagnostics = build_public_system_diagnostics(
        {
            "distribution": "Unknown",
            "kernel": "6.12.1",
            "desktopEnvironment": "Not checked",
            "sessionType": "Unknown",
            "gpu": "Unknown",
            "gpuDriver": "Unavailable",
            "capabilities": {
                "GameMode": "Not checked",
                "Gamescope": "Game-dependent",
            },
        },
        environment={},
        flatpak_info_path=tmp_path / "missing-flatpak-info",
        architecture="x86_64",
    )

    copied = format_public_system_diagnostics(diagnostics)

    assert "OS:" not in copied
    assert "Desktop:" not in copied
    assert "Session:" not in copied
    assert "GPU:" not in copied
    assert "Driver:" not in copied
    assert "GameMode:" not in copied
    assert "Gamescope:" not in copied


def test_system_page_uses_cached_diagnostics_and_copy_action() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (
        root / "src/game_optimization_linux/qml/pages/SystemPage.qml"
    ).read_text(encoding="utf-8")

    assert 'objectName: "runtimeDiagnosticsCard"' in source
    assert 'value(["runtimeDiagnostics"]' in source
    assert 'objectName: "copySystemInfoButton"' in source
    assert "controller.copySystemInfo()" in source
    assert "flatpak-spawn" not in source


def test_system_controller_copies_only_preformatted_public_text(
    monkeypatch,
) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    application = QGuiApplication.instance() or QGuiApplication([])

    class AppStub:
        _system_info = {"runtimeDiagnosticsText": "Public diagnostics only"}

    assert SystemController(AppStub()).copy_public_diagnostics() is True  # type: ignore[arg-type]
    assert application.clipboard().text() == "Public diagnostics only"


def test_copy_public_diagnostics_fails_safely_without_a_gui_application() -> None:
    """A non-GUI application must produce a refusal, never a crash.

    QGuiApplication.instance() returns any QCoreApplication subclass, so the old
    "is not None" check passed for a bare QCoreApplication and the static
    QGuiApplication.clipboard() then segfaulted.  A segfault cannot be caught by
    except Exception, so this has to be refused before the clipboard is touched.
    """

    class NotAGuiApplication:
        """Stands in for a bare QCoreApplication: not a QGuiApplication."""

    class AppStub:
        _system_info = {"runtimeDiagnosticsText": "Public diagnostics only"}

    controller = SystemController(AppStub())  # type: ignore[arg-type]
    original = QGuiApplication.instance

    try:
        QGuiApplication.instance = staticmethod(lambda: NotAGuiApplication())
        assert controller.copy_public_diagnostics() is False
        QGuiApplication.instance = staticmethod(lambda: None)
        assert controller.copy_public_diagnostics() is False
    finally:
        QGuiApplication.instance = original

    # The real GUI application is still intact and still copies.
    assert controller.copy_public_diagnostics() is True


def test_copy_public_diagnostics_refuses_empty_payload() -> None:
    """Negative check: a missing payload must not be reported as copied."""

    class EmptyAppStub:
        _system_info = {"runtimeDiagnosticsText": "   "}

    class MissingAppStub:
        _system_info: dict[str, str] = {}

    assert SystemController(EmptyAppStub()).copy_public_diagnostics() is False  # type: ignore[arg-type]
    assert SystemController(MissingAppStub()).copy_public_diagnostics() is False  # type: ignore[arg-type]


def test_clipboard_assertion_fails_loudly_on_a_wrong_payload() -> None:
    """Prove the clipboard test cannot pass vacuously.

    If the copied text were wrong or absent, the equality assertion used by
    test_system_controller_copies_only_preformatted_public_text must fail.
    """

    class AppStub:
        _system_info = {"runtimeDiagnosticsText": "Public diagnostics only"}

    application = QGuiApplication.instance()
    assert isinstance(application, QGuiApplication)
    assert SystemController(AppStub()).copy_public_diagnostics() is True  # type: ignore[arg-type]
    clipboard = application.clipboard()
    assert clipboard.text() == "Public diagnostics only"

    # A wrong payload must be detected, not silently tolerated.
    clipboard.setText("something else entirely")
    assert clipboard.text() != "Public diagnostics only"

    # An absent payload must also be detected.
    clipboard.clear()
    assert clipboard.text() != "Public diagnostics only"


def test_session_application_is_a_gui_application() -> None:
    """No test may rely on the old bare QCoreApplication singleton."""

    application = QGuiApplication.instance()
    assert isinstance(application, QGuiApplication)
    assert type(application).__name__ == "QApplication"
