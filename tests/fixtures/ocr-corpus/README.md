# OCR regression fixtures

Small, sanitized Tesseract TSV fixtures exercise subtitle selection, low-confidence
background rejection and malformed quote handling. They contain no screenshots,
account information or runtime logs.

| Fixture | Purpose |
| --- | --- |
| `observation-03.tsv` | A clean Polish subtitle line |
| `observation-01.tsv` | The same subtitle among low-confidence background lines |
| `observation-12.tsv` | Background recognition without a subtitle |

The original recognition used a 1114 × 270 region, a 2× resize, PSM 6, OEM 1 and
the Polish language model. The unmatched quote in two fixtures is intentional:
it checks that TSV parsing does not combine subsequent rows.

Keep larger capture datasets and session material outside the repository. These
fixtures test text-processing regressions; they do not benchmark live capture or OCR.
