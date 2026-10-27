# Narrator components

This inventory describes the development checkout. It is not a promise that
the announced 2.0 Beta is available or that narration is fully reliable in
every game. See [development status](../ROADMAP.md) and
[verification guidance](../TESTING.md#narrator-verification-in-games).

The narrator uses CPU inference by default. Model data is not included in the
application package; the user installs each model explicitly from the Lektor
page. Downloads are pinned by URL, byte size, and SHA-256, then installed into
the shared narrator component directory.

## English to Polish translation

- Runtime: CTranslate2 4.8.1 (MIT), Sacremoses 0.1.1 (MIT), and subword-nmt
  0.3.8 (MIT).
- Model: Argos English to Polish package 1.9, derived from OPUS-MT.
- Model license: CC BY 4.0.
- Authors: Jörg Tiedemann and Santhosh Thottingal.
- Source: `https://data.argosopentech.com/argospm/v1/translate-en_pl-1_9.argosmodel`
- Size: 67,294,886 bytes.
- SHA-256: `85d865369326b6d8220876fbd7bc552fa5ec8b99e81161fab4a26f78187cedbc`

The provider loads the package's CTranslate2 model and `bpe.model` locally.
Its versioned provider identifier is part of the translation cache key.

## Polish speech

The catalog offers one Polish voice, **Voidek**, created by the GameOpti
author. It is the default voice and is included in the official GameOpti
packages, so it works without a download. Users can also import their own
Piper voice (`.onnx` with the matching `.onnx.json`). Pinned artifacts are
defined in
[`narrator_components.py`](../src/game_optimization_linux/services/narrator_components.py).

- Runtime: OHF-Voice Piper 1.7.0 (`piper1-gpl`, GPL-3.0-or-later) and ONNX
  Runtime CPU 1.28.0 (MIT).
- Voice: Voidek (epoch 991), files `voidek-pl-e991.onnx` and
  `voidek-pl-e991.onnx.json`.
- Model size: 63,516,050 bytes; SHA-256
  `ca2ad0a22273e3850ce3def4d098f1993c2393c71584f474636afd08ab6af53b`.
- Configuration size: 5,033 bytes; SHA-256
  `4b14423d0575b35bc4ace4dc83d639e9f8d0fa8eab3f238bd9597bfab50af5db`.
- Build source: immutable assets of the `v2.0.0-beta` GitHub release, pinned by
  URL and SHA-256 in the Flatpak manifest. The application never downloads it.
- Model license: separate from the GameOpti MIT license; see
  [`VOIDEK-LICENSE.md`](../flatpak/bundled/VOIDEK-LICENSE.md) and the
  [model card](../flatpak/bundled/VOIDEK-MODEL-CARD.md).

Earlier builds offered the `rhasspy/piper-voices` models Gosia, Bass and
Darkman. They are no longer offered for download. Files a user already
downloaded keep working and stay listed under components until the user
removes them; an update never deletes them.

Piper runs in a narrow local worker process with `use_cuda=False`. PCM is
returned to the application without per-line temporary audio files. Anyone
redistributing a package that includes Piper must also satisfy Piper's GPL
license requirements, including the corresponding-source obligations.

## English OCR

- Runtime: Tesseract and Leptonica (Apache-2.0 and BSD-2-Clause).
- Model: `tessdata_fast` English data at revision
  `87416418657359cb625c412a48b6e1d6d41c29bd` (Apache-2.0).
- Size: 4,113,088 bytes.
- SHA-256: `7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2`.

## Polish OCR

Polish subtitles use the separately installed `ocr.polish-local` component
and `tessdata/pol.traineddata`. In Polish subtitle mode, recognized text goes
to the speech pipeline without English-to-Polish translation. The component
catalog pins the Polish model's source, size, and SHA-256 just as it does for
English OCR; neither model is bundled with the application.

Hardware acceleration runtimes are not required or downloaded. CUDA and ROCm
are not part of the base narrator path.
