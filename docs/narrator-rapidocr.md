# Optional RapidOCR engine

The Narrator can use Tesseract (the default) or RapidOCR for the same saved
subtitle region. The selection is saved per game and can inherit the global
Narrator defaults. It does not change the translator, voice, capture permission,
subtitle region, consensus gate or deduplication rules.

## Selecting an engine

In Desktop mode, open Narrator, select the game and find **OCR engine** in place of the old Window/Monitor selector. Choose **RapidOCR (optional)**, explicitly download its models,
and save the settings. Stop the current Narrator session before changing engines.

In Couch mode, use the game's **Narrator > OCR engine** action. Install or remove
the optional models in **Settings > Narrator**, using the existing component
manager. Selecting RapidOCR does not silently download models. Missing models or
a worker error are reported instead of silently switching to Tesseract.

Switch back to Tesseract to keep using the existing outline masks and automatic
bright-background handling. RapidOCR receives the original colour ROI: applying
Tesseract's binary mask to a different model is deliberately avoided.

## Runtime and models

- RapidOCR 3.10.0, ONNX Runtime 1.28.0, CPU execution.
- PP-OCRv6 small detector and multilingual recognizer, including English and Polish.
- Model download: 27,288,134 bytes. The pinned RapidOCR wheel is used as a
  hash-verified model archive; downloaded Python files are not imported.
- The optional component is `ocr.rapidocr-local`. It uses about 32 MB of extracted
  model files, plus the archive's small accompanying files.
- Both Flatpak manifests include the Python runtime dependencies but remove the
  wheel's bundled ONNX models. Models are installed only on explicit request.
- Source installations: `python -m pip install -e '.[narrator-rapidocr]'`.
  The application's managed model installation is still required; models found
  in site-packages are not used as an implicit fallback.

The worker verifies all three model hashes before initialization and passes
explicit local model paths. There is no HTTP listener, fixed port or automatic
model download inside inference. Two ONNX intra-op threads, one inter-op thread
and one OpenCV thread bound CPU concurrency. The detector caps its longest side
at 1280 pixels instead of enlarging a narrow subtitle strip to a 736-pixel short
side. No additional hardcoded subtitle crop is applied.

All detected text boxes are retained, with boxes on the same visual row joined
in reading order. Scores remain line confidences; no per-word confidences are
fabricated. Short phrases, Polish characters and numbers are passed to the same
shared subtitle gate as Tesseract. This engine does not bypass that gate.

## Verification and remaining game test

The integration has unit/regression coverage for missing models, engine switching,
inheritance, complete ROI transport, fragmented and multiline text, short and
numeric results, malformed worker responses, and worker shutdown. A real managed
model download and isolated worker were exercised on synthetic English/Polish
subtitle images, including process cancellation. These are not gameplay accuracy
or speed benchmarks.

For a useful comparison, keep the same saved ROI, capture source and subtitle
language. Record a short problematic scene (especially compressed browser/GFN
video), then compare both engines on those identical frames. Check missed lines,
incorrect text and latency separately. One engine working on one scene does not
prove that it will outperform the other in every game.

The release bundles rebuild the Python application and its added dependencies
on top of the hash-verified Beta 2.0 native libraries. This is recorded in
`rapidocr-build.json` inside each bundle. The build container cannot create
sandbox namespaces, so a clean Flatpak Builder build, an installed sandbox check
and Wolfenstein/GFN gameplay remain to be performed on the owner's computer.

## Attribution

The integration was motivated by [Shadow775711's RapidOCR experiment](https://github.com/Shadow775711/Game-Optimization-Linux/tree/feature/rapidocr-narrator).
It does not merge that prototype's fixed-port server, hardcoded inner crop,
minimum three-word filter or global deduplication changes.

[RapidOCR](https://github.com/RapidAI/RapidOCR) and the
[PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) models are Apache-2.0.
The downloaded wheel retains its upstream license information. Runtime wheel
sources and SHA-256 digests are pinned in the Flatpak manifests; the component
archive and individual model pins are in the Narrator services.
