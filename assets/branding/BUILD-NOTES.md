# Branding pack rebuild - what changed and how to check it

## Defects the rebuild fixes

| # | Was | Now |
|---|-----|-----|
| 1 | `omnicrawler.ico` contained a single 16x16 frame while MANIFEST claimed 7 sizes | 7 real frames (16x16, 24x24, 32x32, 48x48, 64x64, 128x128, 256x256), each drawn for its size |
| 2 | Wordmark used `<text font-family="DejaVu Sans, Arial">`. The shipped PNG proves the drift: its letterforms match DejaVu Sans Bold metrics (mean-diff 35 against 92-104 for every other candidate), so the preview only looked right because the producer happened to have DejaVu installed | Lettering is outlined from **Outfit-Bold.ttf** (20 contours). No `font-family`, no `<text>` and no external reference anywhere in the pack |
| 3 | Light-only lockup: dark green lettering is invisible on a dark README | `-dark` variants of every lockup, plus a `prefers-color-scheme` snippet |
| 4 | `readme/` duplicated `wordmark/` byte for byte, and the PNG did not even share the SVG's frame (PNG 1600x340 = 4.706 aspect, SVG viewBox = 4.667: it was a re-export on a wider margin) | single source of truth, and every PNG is rendered from its own SVG in its own frame |

## The frame invariant

Defect 4 is subtler than it looks, so it is now a checked property rather than a habit.
Saying "the PNG is generated from the SVG" is not enough on its own: a renderer can be handed
a different artboard (a wider margin, a crop, a squashed aspect) and still emit something that
looks fine alone. What matters is that the raster and the vector agree about *where the frame
is*.

So every PNG line in `MANIFEST.txt` names the SVG it was rendered from, and
`verify_branding.py` cross-multiplies the two aspect ratios as integers -- no float, no
tolerance -- and additionally re-derives the relationship from any SVG that physically sits
next to a raster, so a pack that documents nothing is still caught. It refuses a numbered
filename that lies about its own pixel size, and it refuses a MANIFEST line that states a
frame the bytes do not have.

## Icon tiers

A 220-unit master scaled to 16 px turns a 2.6-unit stroke into 0.19 px, so the small sizes get
purpose-drawn artwork. Radii 76/46 are reused, so the small mark still reads as the same family.

| px | source | what it draws |
|----|--------|---------------|
| 16 / 24 / 32 | `omnicrawler-icon-small.svg` | tile + hexagon ring (wall 25.98 units = 1.89 px at 16 px) |
| 48 / 64 | `omnicrawler-icon-medium.svg` | tile + ring, hexagon and nodes re-weighted to >= 1.3 px strokes |
| 128 and up | `omnicrawler-icon.svg` | the original design, geometry unchanged |

## What each platform file is for

| File | Consumer | Note |
|------|----------|------|
| `icon/omnicrawler.ico` | Windows exe, taskbar, PyInstaller | 7 frames, PNG payloads (Vista and later) |
| `icon/omnicrawler.icns` | macOS bundle | 11 chunks, retina pairs included |
| `icon/omnicrawler-icon-*.png` | Linux .desktop, Electron, docs | ladder 1024 -> 16 |
| `icon/omnicrawler-icon-square.svg` | apple-touch, Android, maskable | full bleed, fully opaque, mark inside the 80% safe circle |
| `icon/omnicrawler-mark*.svg` | UI chrome, tray, watermarks | no tile, so the ink flips with the background |
| `web/*` | a docs site | self-contained: manifest and head snippet included |
| `wordmark/*` | README, covers, slides | horizontal, stacked and lettering-only, light and dark |

## Lettering change (the one judgement call here)

The original stack (`DejaVu Sans, Arial, sans-serif`) was never a brand decision; it is whatever
the producer's renderer resolved to. The rebuild outlines **Outfit Bold**, already vendored in
this repository (`docs/archive/omnicrawler-evaluation-report/_shared/fonts/`, SIL OFL 1.1,
licence already vetted), so no new dependency is introduced, and its geometric skeleton matches
the hexagon and radar geometry. At size 76 the lettering spans about
491 units versus 526 for the old fallback, so the lockup reads slightly
narrower; the pipeline, icon position and viewBox are unchanged. Nothing here needs the font
installed, because every lockup ships as outlines.

## How to check this pack

```
python work/verify_branding.py                # all checks against this pack
python work/verify_branding.py --selftest     # the same checks against the OLD pack
python work/det_check.py                      # three fresh builds, compared byte for byte
```

Several checks are required to FAIL on the previous pack, so they are known to detect the
defects they guard instead of merely reporting green. `--selftest` refuses to run against this
pack, because verifying a good pack and calling it a teeth test proves nothing. The icon-shape
check carries its own calibration: it also measures what a 4 px shift of a 512 px icon would
score (IoU 0.9824, below the 0.99 floor), so the threshold is not slack.

Reproducibility is asserted the same way -- by building three times and comparing, not by
arguing about which browser barrier ought to be enough. That is how the intermittent
`og-image.png` difference was found: it only appeared when the viewport changed between jobs,
never when it stayed put, so the renderer is now asked what size it is really using instead of
being given a fixed number of frames to catch up.

```
QT_QPA_PLATFORM=offscreen OmniCrawler/.venv/Scripts/python.exe work/verify_ico_consumers.py
```

Consumer-level check with the two parsers available locally: Qt, which the application actually
uses for its window and taskbar icons, and Pillow. Qt reports 7 sizes for this
pack and 1 size for the previous one.

## Known limitations

- ICO frames are PNG-compressed, which Windows Vista and later, Qt and PyInstaller all read. No
  BMP/DIB frame is shipped; if pre-Vista tooling ever matters, add one in `build_ico`.
- The ICNS structure follows the format and was checked against a real-world sample, but no
  macOS host was available to load it in Finder or `iconutil`.
- Rendering needs a Chromium binary. The build reuses the one cached in the repository
  (`OmniCrawler/build_cache/browsers`), then falls back to the machine's Edge. Nothing is
  downloaded and no new package is required.
