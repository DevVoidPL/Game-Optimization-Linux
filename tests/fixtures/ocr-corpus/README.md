# OCR regression corpus

Real Tesseract TSV output captured from a live narrator session (Batman, Polish),
sanitised to the TSV only. Previously these artefacts lived in runtime state and
were destroyed with every session, so every measurement restarted from zero.

Source session ROI: **1114x270**, processed **2228x540** (2x upscale), PSM 6,
OEM 1, `pol` model, declared DPI 96.

| file | content | expected text | why it is here |
| --- | --- | --- | --- |
| `observation-03.tsv` | one clean subtitle line | `„W obliczu śmierci nie ma się już czego bać. Twoja zemsta nadejdzie”.` | the good case: single line, phrase confidence 0.963 |
| `observation-01.tsv` | real subtitle plus five background lines | same sentence as above, on TSV line 6 | the subtitle is read at 0.967 while the raw phrase scores 0.347 |
| `observation-12.tsv` | background texture only, no subtitle | *(none)* | every line is weak, so the weak-line rule cannot fire |

`observation-01` and `observation-12` also cover the historical TSV parsing bug:
their text column contains an unmatched ASCII double quote, which once caused
following TSV rows to be absorbed into the parsed token. Re-parsing them with the
current parser is a direct regression check for that fix.

## Not included

ROI/processed PNGs are deliberately excluded: three frames were 940 KB, which is
too heavy to commit. The A/B preprocessing experiment needs those images, so it
still requires a fresh capture. Prefer capturing that corpus from the **GTA V**
session (ROI 960x76), because this session's geometry is much taller and does not
reproduce the reported 480 ms recognition time.
