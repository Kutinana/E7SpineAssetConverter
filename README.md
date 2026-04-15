# E7 Spine Asset Converter

Convert Epic Seven Spine assets (`.sct`, `.scsp`, `.atlas`) into standard formats (`.png`, `.json`, `.atlas`).

<div align="center">
<img width="557" height="586" alt="screenshot" src="https://github.com/user-attachments/assets/29d38e0d-3436-4e9f-b9f6-d5beead75c54" />
</div>

## Supported Versions

| SCSP Version | Spine Version | Internal ID |
|--------------|---------------|-------------|
| V2           | 2.1.27        | 1           |
| V3           | 3.8.99        | 30001       |

## Dependencies

```
pip install -r requirements.txt
```

Required packages: `lz4`, `Pillow`, `texture2ddecoder`, `numpy`.

## Usage

### GUI (Recommended)

```bash
python gui.py
```

The GUI provides two modes:

- **Single File** — select individual `.sct` / `.scsp` / `.atlas` files and convert.
- **Batch** — select a folder; the tool automatically groups files by name and converts them all.

Language can be switched between **中文** and **English** from the bottom-right corner.

### Pre-built Executable

A standalone `.exe` (Windows) can be built with:

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "E7SpineConverter" --hidden-import=texture2ddecoder gui.py
```

The executable will be in `dist/E7SpineConverter.exe`.

You can find pre-built executable in [Releases](https://github.com/Kutinana/E7SpineAssetConverter/releases).

### Command Line

**Convert .scsp to .json:**

```bash
python scsp2json.py input.scsp                  # outputs input.json
python scsp2json.py input.scsp output.json      # specify output path
python scsp2json.py input_dir/                   # batch: outputs to input_dir_json/
python scsp2json.py input_dir/ output_dir/       # batch: specify output directory
```

**Convert .sct to .png:**

```bash
python sct2png.py input.sct                      # outputs input.png
python sct2png.py input.sct output.png           # specify output path
```

**Fix Atlas (V2 compatibility):**

Some atlas files contain a Spine 3.x `pma` field that causes errors in V2.1 viewers. Use `fix_atlas.py` to remove it:

```bash
python fix_atlas.py input.atlas                  # fix in place
python fix_atlas.py input.atlas output.atlas     # output to new file
```

## Options

| Option | Default | Description |
|--------|---------|-------------|
| **Fix Atlas pma** | ✅ On | Remove the Spine 3.x `pma` field from atlas files, which causes errors in Spine 2.1 viewers. |
| **Fix 180° rotation offset** | ✅ On | Correct V2 skeletal animations where some bone rotation keyframes are stored with a ~180° offset from their intended values. Without this fix, affected bones (e.g. limbs) may appear flipped or spin a full 360° during playback. Only applies to V2 (Spine 2.1.27) files. |

Both options are available as checkboxes in the GUI and are enabled by default.

## Conversion Details

| Input    | Output   | Description                                           |
|----------|----------|-------------------------------------------------------|
| `.sct`   | `.png`   | Decode SCT texture (LZ4 + ASTC/ETC2/RGBA) to PNG     |
| `.scsp`  | `.json`  | Parse Spine binary skeleton to Spine JSON              |
| `.atlas` | `.atlas` | Replace `.sct` texture reference with `.png` on line 2 |

## Known Limitations

- Some model's `.scsp` file can be parsed as a readable JSON, but might not be rendered properly.

## Special Thanks

- [CeciliaBot/EpicSevenAssetRipper](https://github.com/CeciliaBot/EpicSevenAssetRipper)
- [ww-rm/SpineViewer](https://github.com/ww-rm/SpineViewer)
- [violet-wdream/.Scripts](https://github.com/violet-wdream/.Scripts)
- [juno181/spine-runtimes-2.1.27](https://github.com/juno181/spine-runtimes-2.1.27)
- e7vault/epic7_scsp2json
- Twistzz
