# UI icons: the "Facet" set

GameOpti's functional icons are solid silhouettes split by one slanted light
cut. The gap divides each symbol into a **body plane** and a **mint accent
plane**, echoing the faceted hexagon of the application symbol. There are no
strokes, gradients, translucency, filters, text or `currentColor`: every file
is a 24 × 24 `viewBox` with plain `<path>` elements in one or two concrete
colours, which Qt's SVG renderer rasterises exactly from 16 px to 4K tiles.

Source geometry lives as Python polygons in `scripts/build-facet-icons.py`,
which writes every packaged SVG and the Desktop `manifest.json`. The script
uses only the standard library. Brand artwork (Steam, Heroic, Lutris,
MangoHud, OptiScaler wordmark) is never generated; see `assets/ui-icons/brands/`.

## Visual contract

- Grid 24 × 24, safe margin 1.5 units on every side (the generator refuses
  geometry that crosses it; `test_ui_icon_presentation.py` rasterises and
  checks the same margin).
- Fill only. `stroke="none"` on the root, no `fill-opacity`, no groups.
- Exactly one body colour and one accent colour per icon:
  - Desktop dark: body `#CFDDE6`, accent `#4FE3B8`; light (`-on-light`):
    body `#24313F`, accent `#128A66`.
  - Couch tiles/actions (dark surfaces only, tinted monochrome by `CouchIcon`
    on light surfaces): body `#E3ECF2`, accent `#4FE3B8`.
  - Couch glyphs: dark as above; light variants use the Desktop light palette.
  - Status icons keep their semantic tone in both themes (success, warning,
    error, info); the manifest `default_color` is always the accent colour.
- The cut is a 1.5-unit gap at −58° through a per-icon anchor. Symbols whose
  meaning sits in a cut-out (checks, arrows, text rows) move the anchor so the
  cut never crosses the symbol; status badges use a corner highlight.
- Minimum detail: 1.6-unit holes, 2-unit bars. Nothing thinner than that.

## Semantic mappings

- Games / library / controller: gamepad with D-pad and two face buttons cut
  out. Library offline: the same gamepad opened by a wide 45° gap.
- Narrator (Lektor): speech bubble with two caption rows, tail toward the
  screen. Polish subtitles: caption bar plus sparkle. Subtitle area: screen
  with the caption region highlighted. Recognition source: scanning beam.
- Tasks: clipboard; Updates: badge with an upward arrow; Check updates: the
  badge with a magnifier handle; System: processor die; Settings: gear;
  Advanced / Tune: three sliders; Donate: heart.
- Analyze: magnifier with a bar chart; Verify: shield with a check; Measure:
  slanted notched ruler; Exact measurement: reticle; Scan: viewfinder with beam.
- Compression: two arrows squeezing a core bar. Verify compression /
  compression profile: a block notched on both sides (check or level bar).
- Overview: logical size = hollow box, physical size = platter with hub,
  saved space = pie with the freed slice set apart, optimization profile /
  apply optimization = rising arrow block. Optimization tile: gauge dial.
- Graphics: Classic = frame with diamond; Light AI = one sparkle; Quality AI =
  large and small sparkle. The DLSS Enabler / Frame generation / Post-processing
  tabs deliberately have **no** decorative header icon: DLSS Enabler, lsfg-vk,
  MAKO, AFMF, ReShade and vkBasalt publish no reusable official mark and
  GameOpti neither invents project logos nor borrows vendor (NVIDIA) branding.
- Install OptiScaler (action): a frame receiving a download arrow with a base
  bar - the act of installing, distinct from the OptiScaler wordmark
  (`brands/optiscaler*.png`) and from generic Install (package with plus).
- Compression profiles (Storage tab): one shared "compressed block" chassis
  with the level cut out - Fast = bolt, Balanced = scale beam with two pans,
  Maximum = full three-bar meter, Automatic = sparkle.
- Gamescope: a display frame holding a smaller game window with an accent
  arrow pushing it to the output corner (compositor scaling). GameMode: bolt.
  FPS: gauge; FPS limit: gauge under a cap bar; Frametime: frame bars with
  one spike; VRR: wave of varying wavelength on a screen; HDR: sun.
- Window modes: fullscreen = corner brackets, windowed = framed pane,
  borderless = pane only. Resolution: game = inner pane, output = full pane.

## Rebuild and verify

```bash
python3 scripts/build-facet-icons.py            # regenerate assets
python3 scripts/build-facet-icons.py --check    # fail if packaged files drift
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_ui_icons.py \
  tests/test_ui_icon_presentation.py tests/test_couch_glyphs.py \
  tests/test_couch_design_system.py
```

`UiIcons.qml` picks Desktop light variants and exposes `couchGlyph*OnLight`
URLs; `UiIcon.qml` keeps MultiEffect tinting for coloured buttons. Historical
stroke-based sets are archived outside the repository and are not a source for
this set.
