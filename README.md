# E7 Spine Asset Converter

Convert Epic Seven Spine assets (`.sct`, `.scsp`, `.atlas`) into standard formats (`.png`, `.json`, `.atlas`).

## Supported Versions

| SCSP Version | Spine Version | Internal ID |
|--------------|---------------|-------------|
| V2           | 2.1.27        | 1           |
| V3           | 3.8.99        | 30001       |

## Dependencies

```
pip install -r requirements.txt
```

Required packages: `lz4`, `Pillow`, `texture2ddecoder`.

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

## Conversion Details

| Input    | Output   | Description                                           |
|----------|----------|-------------------------------------------------------|
| `.sct`   | `.png`   | Decode SCT texture (LZ4 + ASTC/ETC2/RGBA) to PNG     |
| `.scsp`  | `.json`  | Parse Spine binary skeleton to Spine JSON              |
| `.atlas` | `.atlas` | Replace `.sct` texture reference with `.png` on line 2 |

## Special Thanks

* [CeciliaBot/EpicSevenAssetRipper](https://github.com/CeciliaBot/EpicSevenAssetRipper)
* [ww-rm/SpineViewer](https://github.com/ww-rm/SpineViewer)
* [violet-wdream/.Scripts](https://github.com/violet-wdream/.Scripts)
* [juno181/spine-runtimes-2.1.27](https://github.com/juno181/spine-runtimes-2.1.27)
* e7vault/epic7_scsp2json
* Twistzz
