# Upstream artwork

## MangoHud

Artwork: **FlightlessMango Icon**, creator **FlightlessMango**.
License declared in the original SVG metadata: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

[Original SVG in the MangoHud repository](https://github.com/flightlessmango/MangoHud/blob/0292bf169e0e89b474494b1f31ac1a783edebc9b/data/io.github.flightlessmango.mangohud.svg).
Verified on 2026-10-18; SHA-256:
`5ff38b7e77f7501c35c6a23b5b10140c483ab080b49ceea1076ef9fe5fd19a43`.
The untouched source, including its metadata, is retained in
`scripts/icon-sources/mangohud-original.svg` at the repository root.

`mangohud.svg` is the Qt-compatible copy: off-canvas variants and problematic
nested clipping were removed. Visible path coordinates, their group transforms,
original orange/green colours and cream background are preserved.
The icon generator fits this artwork into a 19 × 19 area of a 24 × 24 canvas;
it does not redraw, recolour, round or stretch the logo.

The same brand colours are used in both themes. This artwork is an explicit
exception to GameOpti's functional stroke/duotone icon system, and is not
represented as original GameOpti artwork. Other downloads are recorded in
`download-sources.json`.

## Steam

`steam.png` (256 × 256) is the official Steam application icon as shipped by
Valve's Steam client package (`/usr/share/icons/hicolor/256x256/apps/steam.png`,
Arch package `steam 1.0.0.87-3`, SHA-256
`b7087da76b8315024887b9a9313be5b9e6ba87d25eb624eac2d6542a823bb87d`). Copied
byte-for-byte on 2026-10-19; no recolouring, cropping or theming. Steam and the
Steam logo are trademarks of Valve Corporation and identify only the launcher
a game comes from (Valve "Steam Brand Guidelines and Logos", Steamworks docs).

The full-colour icon is used on both themes. It replaces the previous
monochrome `couch/launchers/steam*.png` silhouettes, which rendered as a washed
out light-grey disc on dark covers and plates.

## PayPal

`paypal.png` (128 × 128) is the PayPal monogram from the official PayPal
Newsroom media kit (`PayPal-Monogram-Logo-2024.zip`,
<https://newsroom.paypal-corp.com/media-resources>, file
`Monogram/PayPal-Monogram-FullColor-RGB.png`, SHA-256
`defb3ea3cd1f5ac2548c92ed55d4b6a250691f580eb6583fd6300440d6afeb9e`). The
source was trimmed of transparent padding and downscaled proportionally into a
128 px canvas; colours are unchanged. It is shown untinted beside the PayPal
donation button only. PayPal and the PayPal logo are trademarks of PayPal, Inc.
