# `export/` — PPTX exporter

## `export-deck.py`

A standalone Python script that recreates the 6-slide investor briefing deck (`slides/index.html`) as a **native, fully-editable** `.pptx` file. Every block in PowerPoint is a real shape (rectangle, oval, line, text box) — not a screenshot — so an analyst can swap copy, retype numbers, or restyle a single tile without breaking the layout.

## Install

```bash
pip install python-pptx
```

## Run

```bash
python export-deck.py
# → writes mizanquant-briefing.pptx alongside the script

python export-deck.py --out path/to/output.pptx
```

## What you get

- **6 slides at 16:9, 13.333" × 7.5"** (matches the HTML deck pixel-for-pixel at 144 dpi)
- Title · Architecture (5-stage loop) · Halal Compliance (NVDA specimen) · Backtest Comparison (A·B·C) · Risk Controls (Kelly + 4 metric tiles) · Closing
- All brand tokens mirrored from `colors_and_type.css`: warm near-black canvas, warm gold (`#c8963e`) accent, semantic red/green/amber, DM Sans + JetBrains Mono + Cairo type stacks
- Bilingual headings on the title and closing slides

## Fonts

The script references three font families: **DM Sans**, **JetBrains Mono**, and **Cairo**. PowerPoint substitutes a similar face if any are missing. For distribution to a client desk where these aren't installed, embed them via:

> File → Options → Save → ✅ Embed fonts in the file

## Customizing

All visual tokens live in the `BRAND` block at the top of `export-deck.py`. Change a color or font there and the entire deck restyles on the next run. To add a slide, write a `build_*_slide(prs)` function following the patterns in the existing builders, then add a call in `build_deck()`.
