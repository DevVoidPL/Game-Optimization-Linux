#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
helper_source="$project_dir/libexec/gameforge-linux-measure-helper"
policy_source="$project_dir/data/io.github.gameforge_linux.GameForge.measure.policy"
helper_target="/usr/libexec/gameforge-linux-measure-helper"
policy_target="/usr/share/polkit-1/actions/io.github.gameforge_linux.GameForge.measure.policy"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this installer through Polkit:" >&2
    echo "  pkexec $0" >&2
    exit 1
fi

if [ ! -f "$helper_source" ] || [ ! -f "$policy_source" ]; then
    echo "GameForge measurement helper sources were not found." >&2
    exit 1
fi

install -o root -g root -m 0755 "$helper_source" "$helper_target"
install -o root -g root -m 0644 "$policy_source" "$policy_target"

echo "Installed read-only measurement helper: $helper_target"
echo "Installed Polkit policy: $policy_target"
