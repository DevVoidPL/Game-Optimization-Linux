# Narrator components

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

- Runtime: OHF-Voice Piper 1.7.0 (`piper1-gpl`, GPL-3.0-or-later) and ONNX
  Runtime CPU 1.28.0 (MIT).
- Voice: `pl_PL-gosia-medium` from `rhasspy/piper-voices`, revision
  `058271fb41b630e96989367e15b4514992a25b42`.
- Voice repository metadata: MIT. The voice model card identifies the source
  dataset as CC0.
- Model size: 63,201,294 bytes; SHA-256
  `38f66464240ed74f186e6b7dc13c6e3b22e023426299f25c2b3cc9dfa9373fbc`.
- Configuration size: 6,920 bytes; SHA-256
  `956cd5b2a08dca5e780ad584a6d2e971ba3bd7fcd06297dfa6cd85c9fbcd3d42`.

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

Hardware acceleration runtimes are not required or downloaded. CUDA and ROCm
are not part of the base narrator path.
