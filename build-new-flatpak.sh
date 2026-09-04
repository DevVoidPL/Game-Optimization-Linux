#!/usr/bin/env bash
set -e

APP_ID="io.github.DevVoidPL.GameOptimizationLinux"
MANIFEST="flatpak/io.github.DevVoidPL.GameOptimizationLinux.yml"
VERSION_FILE="src/game_optimization_linux/config.py"
BRANCH="stable"
BUILD_DIR=".flatpak-build-dir"
REPO_DIR=".flatpak-repo"

if [[ ! -f "$VERSION_FILE" ]]; then
    echo "Brakuje pliku wersji: $VERSION_FILE"
    exit 1
fi

APP_VERSION="$(sed -nE 's/^APP_VERSION[[:space:]]*=[[:space:]]*"([^"[:space:]]+)"[[:space:]]*$/\1/p' "$VERSION_FILE")"
if [[ -z "$APP_VERSION" || ! "$APP_VERSION" =~ ^[0-9A-Za-z][0-9A-Za-z._+-]*$ ]]; then
    echo "Nie można odczytać bezpiecznej wersji APP_VERSION z: $VERSION_FILE"
    exit 1
fi

OUTPUT="dist/Game-Optimization-Linux-${APP_VERSION}-x86_64.flatpak"

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
    --default-branch="$BRANCH" \
    "$BUILD_DIR" \
    "$MANIFEST"

echo "Tworzenie bundle..."
rm -f "$OUTPUT" "$OUTPUT.sha256"

flatpak build-bundle \
    "$REPO_DIR" \
    "$OUTPUT" \
    "$APP_ID" \
    "$BRANCH"

sha256sum "$OUTPUT" > "$OUTPUT.sha256"
BUNDLE_SHA256="$(sha256sum "$OUTPUT" | awk '{print $1}')"
(
    cd dist
    sha256sum "$(basename "$OUTPUT")" > SHA256SUMS
)

echo
echo "Podsumowanie Flatpaka:"
echo "Version: $APP_VERSION"
echo "Branch: $BRANCH"
echo "Bundle path: $OUTPUT"
echo "SHA256: $BUNDLE_SHA256"
echo "Launch command: flatpak run --branch=$BRANCH $APP_ID"
