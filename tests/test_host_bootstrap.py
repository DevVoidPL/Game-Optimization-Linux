from __future__ import annotations

from pathlib import Path
import inspect

import pytest

from game_optimization_linux.services.host_bootstrap import (
    HostBootstrapError,
    bootstrap_flatpak_host_components,
)
import game_optimization_linux.services.host_service as host_service_module


APP_ID = "io.github.DevVoidPL.GameOptimizationLinux"


def _bundled_components(root: Path, *, runner: bytes = b"runner-v1") -> None:
    libexec = root / "libexec"
    libexec.mkdir(parents=True)
    (libexec / "game-optimization-run-host").write_bytes(runner)


def test_clean_flatpak_bootstrap_installs_only_python_free_runner(tmp_path: Path) -> None:
    app = tmp_path / "app"
    home = tmp_path / "home"
    _bundled_components(app)
    result = bootstrap_flatpak_host_components(
        {"FLATPAK_ID": APP_ID}, app_prefix=app, target_home=home
    )
    assert result.changed == ("game-optimization-run",)
    target = home / ".local/share/game-optimization-linux/bin"
    assert (target / "game-optimization-run").read_bytes() == b"runner-v1"
    assert (target / "game-optimization-run").stat().st_mode & 0o111
    assert not (target / "game-optimization-host").exists()


def test_bootstrap_is_idempotent_and_updates_only_changed_component(tmp_path: Path) -> None:
    app = tmp_path / "app"
    home = tmp_path / "home"
    _bundled_components(app)
    first = bootstrap_flatpak_host_components(
        {"FLATPAK_ID": APP_ID}, app_prefix=app, target_home=home
    )
    assert len(first.changed) == 1
    second = bootstrap_flatpak_host_components(
        {"FLATPAK_ID": APP_ID}, app_prefix=app, target_home=home
    )
    assert second.changed == ()
    (app / "libexec/game-optimization-run-host").write_bytes(b"runner-v2")
    third = bootstrap_flatpak_host_components(
        {"FLATPAK_ID": APP_ID}, app_prefix=app, target_home=home
    )
    assert third.changed == ("game-optimization-run",)
    target = home / ".local/share/game-optimization-linux/bin"
    assert (target / "game-optimization-run").read_bytes() == b"runner-v2"


def test_non_flatpak_does_not_install_components(tmp_path: Path) -> None:
    result = bootstrap_flatpak_host_components(
        {}, app_prefix=tmp_path / "missing", target_home=tmp_path / "home"
    )
    assert result.attempted is False
    assert result.changed == ()


def test_missing_bundled_component_is_a_clear_error(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / "libexec").mkdir(parents=True)
    # The required runner source is intentionally absent.
    with pytest.raises(HostBootstrapError, match="Bundled host component is unavailable"):
        bootstrap_flatpak_host_components(
            {"FLATPAK_ID": APP_ID}, app_prefix=app, target_home=tmp_path / "home"
        )


def test_normal_flatpak_host_path_has_no_python_or_legacy_host_helper() -> None:
    root = Path(__file__).resolve().parents[1]
    runner = (root / "libexec/game-optimization-run-host").read_text(encoding="utf-8")
    manifest = (
        root / "flatpak/io.github.DevVoidPL.GameOptimizationLinux.yml"
    ).read_text(encoding="utf-8")
    bootstrap_source = inspect.getsource(bootstrap_flatpak_host_components)
    diagnostics_source = inspect.getsource(host_service_module.HostServiceClient)
    assert "python" not in runner.casefold()
    assert "game-optimization-host" not in manifest
    assert "game-optimization-host" not in bootstrap_source
    assert "host_component" not in diagnostics_source
