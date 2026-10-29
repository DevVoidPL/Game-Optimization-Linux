# GameOpti 2.1

This update adds RapidOCR as an optional Narrator OCR engine. Tesseract remains
the default. Both engines use the existing subtitle region, translator, voices
and shared subtitle consensus and deduplication.

## Narrator

- Select Tesseract or RapidOCR where the old Window/Monitor control was shown.
- Explicitly download the verified RapidOCR English/Polish models (27 MB).
- Save the engine per game, or inherit the global Narrator default.
- Couch includes the same engine choice and managed component installation.
- Existing capture settings and portal permissions are retained. Removing the
  Window/Monitor control does not disable screen capture.

RapidOCR runs locally in an isolated CPU worker. It does not use the community
prototype's HTTP server, hardcoded inner crop or one-line-only filter.

## Editions

- **Couch Edition:** Desktop Mode plus full-screen Couch Mode and controller UI.
- **Desktop Edition:** Desktop Mode only, with Couch QML and Couch-only icons removed.

The bundles share the existing application ID and data location. Install one
edition; installing the other switches edition.

## Verification and packaging

Real RapidOCR inference read synthetic English and Polish subtitle images,
including Polish diacritics, using the packaged dependencies on Python 3.13.
Worker cancellation and the engine-selection save/load path are covered by tests.
Real Wolfenstein/GFN accuracy and installed sandbox execution remain unverified.

The bundles reuse the SHA-256-verified Beta 2.0 native dependencies and rebuild
the Python application and new runtime wheels. They are not a clean build of all
native modules: the build environment forbids nested sandbox namespaces. Build
provenance is included at `share/doc/game-optimization-linux/rapidocr-build.json`.

## Credits

Thanks to [Shadow775711](https://github.com/Shadow775711/Game-Optimization-Linux/tree/feature/rapidocr-narrator)
for the experiment that motivated this integration. RapidOCR and the PaddleOCR
models retain their upstream attribution and Apache-2.0 license notices.
