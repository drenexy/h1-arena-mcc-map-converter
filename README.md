# H1 Map Companion

Small GUI for H1 Performance Build 2: pick Halo MCC maps you own, hit
**Convert + install**, the map lands in the game's `maps\` folder and shows up
in-game on the next launch.

## What it does

- Finds your Steam installs of **Halo: The Master Chief Collection** and
  **Halo: CE Mod Tools - MCC** (the free "HCEEK" tool from Steam).
- Lists every Halo map it can find: MCC stock maps, MCC Workshop maps you
  subscribed to, and any extra folder you add.
- Converts MCC (cache v13) maps to the Halo PC retail format (cache v7) the
  Performance Build loads — the same conversion the shipped maps went through.
- Checks every finished map with the game's own loading rules before it touches
  the `maps\` folder, so anything installed is guaranteed to be listed in-game.
- Same map twice = no duplicate. Name clash with a different map = `_2` suffix.
- `mapcompanion.json` beside `maps\` remembers what the companion installed;
  **Remove** only deletes those, never the shipped maps.

## Requirements

- Windows, Python 3.9+ (or the standalone `H1MapCompanion.exe`)
- Steam with **Halo: The Master Chief Collection** (owns the maps you convert)
- Steam with **Halo: CE Mod Tools - MCC** installed **and extracted**
  (run `HCEEK (Extract).bat` once in the Mod Tools folder)
- The `invader-*.zip` that ships in the game package's `tools\` folder
  (or set `"invader_zip"` in `%LOCALAPPDATA%\h1mapcompanion\config.json`)

## Use

```
python companion.py
```

1. Game folder → your Performance Build folder (the one with `maps\`).
2. Pick map(s) in the list → **Convert + install selected**.
3. First run takes ~3 extra minutes (one-time setup of the tag base).
   After that each map takes about 1–3 minutes.
4. Start the game; the map appears in the MAPS menu.

Headless (no GUI):

```
python companion.py --convert "D:\path\to\somemap.map" --game "E:\h1-arena"
```

## How the conversion works (for the curious)

`invader-extract` unpacks the MCC map into Mod-Tools-style tags, merged onto a
refactored copy of the HCEEK tag base; `invader-build -g gbx-retail` writes the
v7 cache. Stock CEA-schema tags that the v7 target rejects (scorpion lights,
shared globals) are repaired automatically by overlaying the PC-schema copy
taken from the game's own shipped maps, then rebuilding. Everything runs under
`%LOCALAPPDATA%\h1mapcompanion\`.

## Build the exe

```
powershell -ExecutionPolicy Bypass -File build_exe.ps1
```

Needs `pip install pyinstaller`. Result: `dist\H1MapCompanion.exe`.

## Notes

- Maps you get from other people (Discord, the internet) can run code on your
  PC — install maps only from people you trust.
- Converted maps that still stream art from external packs play with some
  textures missing; the list marks those.
