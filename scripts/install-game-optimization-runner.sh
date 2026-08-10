#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
app_id="io.github.DevVoidPL.GameOptimizationLinux"
if [ "${FLATPAK_ID:-}" = "$app_id" ]; then
    # Flatpak redirects XDG_DATA_HOME to ~/.var/app/<APP_ID>/data. Steam Launch
    # Options run on the host, so the stable wrapper must live in the regular
    # per-user data directory instead.
    install_root="$HOME/.local/share/game-optimization-linux"
else
    install_root="${XDG_DATA_HOME:-$HOME/.local/share}/game-optimization-linux"
fi
python_root="$install_root/python"
runner_root="$install_root/bin"
mkdir -p "$runner_root"

temporary_runner="$(mktemp "$runner_root/.game-optimization-run.XXXXXX")"
trap 'rm -f "$temporary_runner"' EXIT

if [ "${FLATPAK_ID:-}" = "$app_id" ] \
        || { command -v flatpak >/dev/null 2>&1 && flatpak info "$app_id" >/dev/null 2>&1; }; then
    cp "$project_root/libexec/game-optimization-run-host" "$temporary_runner"
else
    mkdir -p "$python_root"
    python3 -m pip install --no-deps --no-build-isolation --upgrade --target "$python_root" "$project_root"
    {
        printf '%s\n' '#!/usr/bin/env sh'
        printf 'PYTHONPATH=%s exec python3 -m game_optimization_linux.runner "$@"\n' "$(printf '%q' "$python_root")"
    } > "$temporary_runner"
fi
chmod 0755 "$temporary_runner"
mv -f "$temporary_runner" "$runner_root/game-optimization-run"
trap - EXIT

printf 'Installed Game Optimization Runner: %s\n' "$runner_root/game-optimization-run"
