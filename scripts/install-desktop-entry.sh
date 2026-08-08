#!/bin/sh
set -eu

app_id="io.github.gameforge_linux.GameForge"
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
desktop_source="$project_dir/data/$app_id.desktop"
desktop_target="$data_home/applications/$app_id.desktop"
metainfo_source="$project_dir/data/$app_id.metainfo.xml"
metainfo_target="$data_home/metainfo/$app_id.metainfo.xml"

usage() {
    echo "usage: $0 [--dev]" >&2
}

development=false
if [ "$#" -gt 1 ]; then
    usage
    exit 2
fi
if [ "$#" -eq 1 ]; then
    if [ "$1" != "--dev" ]; then
        usage
        exit 2
    fi
    development=true
fi

if [ ! -f "$desktop_source" ] || [ ! -f "$metainfo_source" ]; then
    echo "GameForge desktop assets were not found in $project_dir" >&2
    exit 1
fi

launcher=""
if [ "$development" = true ] && [ -x "$project_dir/.venv/bin/gameforge-linux" ]; then
    launcher="$project_dir/.venv/bin/gameforge-linux"
elif command -v gameforge-linux >/dev/null 2>&1; then
    launcher=$(command -v gameforge-linux)
fi

if [ -z "$launcher" ]; then
    if [ "$development" = true ]; then
        echo "No launcher found. Create the project virtual environment and install the package:" >&2
        echo "  python -m venv .venv && .venv/bin/pip install -e ." >&2
    else
        echo "The gameforge-linux command is not available in PATH; install the package first." >&2
        echo "For a local checkout, run: $0 --dev" >&2
    fi
    exit 1
fi

temporary_desktop=$(mktemp "${TMPDIR:-/tmp}/gameforge-desktop.XXXXXX.desktop")
trap 'rm -f "$temporary_desktop"' EXIT HUP INT TERM
escaped_launcher=$(printf '%s' "$launcher" | sed 's/[\\"]/\\&/g; s/`/\\`/g; s/\$/\\$/g')
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
        Exec=*) printf 'Exec="%s"\n' "$escaped_launcher" ;;
        *) printf '%s\n' "$line" ;;
    esac
done < "$desktop_source" > "$temporary_desktop"

if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$temporary_desktop"
fi

install -Dm644 "$temporary_desktop" "$desktop_target"
for size in 16 22 24 32 48 64 128 256; do
    icon_source="$project_dir/data/icons/hicolor/${size}x${size}/apps/$app_id.png"
    icon_target="$data_home/icons/hicolor/${size}x${size}/apps/$app_id.png"
    if [ ! -f "$icon_source" ]; then
        echo "GameForge icon asset is missing: $icon_source" >&2
        exit 1
    fi
    install -Dm644 "$icon_source" "$icon_target"
done
install -Dm644 "$metainfo_source" "$metainfo_target"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$data_home/applications"
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    if ! gtk-update-icon-cache -f -t "$data_home/icons/hicolor"; then
        echo "Warning: the hicolor icon cache could not be refreshed" >&2
    fi
fi
if command -v kbuildsycoca6 >/dev/null 2>&1; then
    if ! kbuildsycoca6 --noincremental; then
        echo "Warning: the KDE 6 service cache could not be refreshed" >&2
    fi
elif command -v kbuildsycoca5 >/dev/null 2>&1; then
    if ! kbuildsycoca5 --noincremental; then
        echo "Warning: the KDE 5 service cache could not be refreshed" >&2
    fi
fi

echo "Installed: $desktop_target"
echo "Installed hicolor icons: 16, 22, 24, 32, 48, 64, 128, and 256 px"
echo "Installed: $metainfo_target"
echo "Launcher: $launcher"
