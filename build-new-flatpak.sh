#!/usr/bin/env bash
set -e

APP_ID="io.github.DevVoidPL.GameOptimizationLinux"
MANIFEST="flatpak/io.github.DevVoidPL.GameOptimizationLinux.yml"
BUILD_DIR=".flatpak-build-dir"
REPO_DIR=".flatpak-repo"
OUTPUT="dist/Game-Optimization-Linux-0.1.2-alpha-x86_64.flatpak"

if [[ ! -f "$MANIFEST" ]]; then
    echo "Brakuje manifestu: $MANIFEST"
    exit 1
fi

if ! command -v flatpak-builder >/dev/null 2>&1; then
    echo "Brakuje flatpak-builder."
    echo "Zainstaluj go poleceniem:"
    echo "sudo pacman -S flatpak-builder"
    exit 1
fi

echo "Czyszczenie poprzedniego buildu..."
rm -rf "$BUILD_DIR" "$REPO_DIR"
mkdir -p dist

echo "Budowanie aplikacji..."
flatpak-builder \
    --force-clean \
    --install-deps-from=flathub \
    --repo="$REPO_DIR" \
    --default-branch=stable \
    "$BUILD_DIR" \
    "$MANIFEST"

echo "Tworzenie bundle..."
rm -f "$OUTPUT" "$OUTPUT.sha256"

flatpak build-bundle \
    "$REPO_DIR" \
    "$OUTPUT" \
    "$APP_ID" \
    stable

sha256sum "$OUTPUT" > "$OUTPUT.sha256"
(
    cd dist
    sha256sum "$(basename "$OUTPUT")" > SHA256SUMS
)

echo
echo "Gotowy Flatpak:"
ls -lh "$OUTPUT"

echo
echo "SHA256:"
cat "$OUTPUT.sha256"
