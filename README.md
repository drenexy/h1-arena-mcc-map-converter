# H1 Arena MCC Map Converter

Small GUI for **H1 Performance Build 2** (H1 Arena): pick Halo MCC maps you
own, hit **Convert + install**, and the map lands in the game's `maps\` folder,
listed in-game on the next launch. Built to feel like a step-by-step wizard —
you point at your game folder once and double-click maps from there.

## Features

- **Finds your maps automatically** — MCC stock maps and every MCC Workshop
  item you're subscribed to (with real titles, descriptions and thumbnails
  from Steam). Xbox-disc dumps and other wrong-format files are detected and
  labelled, not offered.
- **Converts MCC (cache v13) → Halo PC (cache v7)** — the same pipeline the
  shipped maps went through: `invader-extract` onto a refactored HCEEK tag
  base, `invader-build -g gbx-retail`.
- **Correct HUD scale** — Workshop maps built on Anniversary stock carry the
  CEA HUD, which renders gigantic in this engine. The converter swaps in the
  classic 640×480 PC HUD (and stock weapons/vehicles/globals) taken from the
  game's own shipped maps, automatically, on every conversion that needs it.
- **The game's scripts** — converted maps get H1 Arena's script suite (round
  timer beeps, callouts, player effects) copied out of a shipped map's
  scenario. MCC scripts are stripped first. Scripts that only work on their
  donor map are skipped automatically. *(checkbox, on by default)*
- **Custom weapons → stock** — custom weapon tags that match a stock path are
  swapped for H1 Arena's stock versions; unmappable ones are reported.
  *(checkbox, on by default)*
- **Netgame audit** — netgame flags (teleporter nodes), equipment slots and
  placed-object counts are compared before/after the build; anything dropped
  shows up as a `NOTE:` in the log.
- **Heavy-map & broken-shader warnings** — Halo PC is an old engine: oversized
  maps, single-cluster BSPs and `shader transparent generic` usage (broken by
  Gearbox in the PC port, unfixable) all produce plain-language warnings.
- **Nothing bad can be installed** — every finished map passes the game's own
  header rules before it touches `maps\`.
- **Safe management** — `mapcompanion.json` beside `maps\` records what the
  app installed; **Remove** only deletes those, never the shipped maps.
  Duplicate conversions are refused; name clashes get `_2`, `_3`… Installing
  works even with the game's `maps\` emptied (see stock tag cache).

## Requirements

- Windows, Python 3.9+ (or the standalone `H1ArenaMapConverter.exe`)
- Steam with **Halo: The Master Chief Collection** (it owns the maps you convert)
- Steam with **Halo: CE Mod Tools - MCC** ("HCEEK") installed **and extracted**
  (run `HCEEK (Extract).bat` once in the Mod Tools folder)
- The `invader-*.zip` that ships with the game package's tools
  (or set `"invader_zip"` in `%LOCALAPPDATA%\h1mapcompanion\config.json`)

## Use

```
python companion.py
```

1. **Your game** → point at the Performance Build folder (the one with `maps\`).
2. Tick map sources, find a map (preview panel shows what it is), double-click
   or press **Convert + install selected**.
3. First run does one-time setup (tag base + stock tag cache), a few minutes.
   After that a map takes ~15 s to ~3 min.
4. Launch the game — the map is in the MAPS menu.

Headless:

```
python companion.py --convert "D:\path\to\somemap.map" --game "E:\h1-arena"
```

## The stock tag cache

First time a conversion needs them, the full tag set of one shipped map
(classic PC HUD, stock weapons/vehicles/etc, ~84 MB) is cached to
`%LOCALAPPDATA%\h1mapcompanion\stocktags`. From then on conversions take stock
tags from that cache, not from the game's `maps\` folder — so converting still
works if you empty or move your maps folder. The cache is built from the game's
own files and is never shipped inside the app (they belong to the game).
Delete the `stocktags` folder to rebuild it.

## Build the exe

```
powershell -ExecutionPolicy Bypass -File build_exe.ps1
```

Needs `pip install pyinstaller`. Result: `dist\H1ArenaMapConverter.exe`.

## Known limitations

- Timer scripts that reference objects only their donor map has (the full
  Cortana voice countdown chain) are skipped rather than rewritten.
- Spawn-marker **facing** vs spawn orientation is not verified; the audit
  covers counts (flags, equipment, placed objects), not placement math.
- Exact triangle counts aren't readable from v7 BSPs, so "heavy map" is a
  size/cluster heuristic, not the 12k-triangle rule.
- Random-geo toggle content can't be generated — it's per-map authored work.
- Maps that stream art from MCC's protected resource packs (not self-contained)
  convert with missing textures.
- Anything from the internet can run code on your PC; install maps from people
  you trust.
