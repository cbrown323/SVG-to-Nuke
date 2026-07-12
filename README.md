# SVG-to-Nuke

Import animated **SVG**, **Lottie JSON**, or **HTML** assets into Foundry Nuke as PNG frame sequences. Optional **UV** and **Object ID** passes are included for STMap retexturing and per-shape mattes.

Rasterization runs in headless Chromium via Playwright. Nuke stays interactive while frames render — a progress panel shows per-pass status.

## Supported inputs

| Extension | Description |
|-----------|-------------|
| `.svg` | Animated SVG (SMIL, CSS, or JS) |
| `.json` | Lottie / Bodymovin export |
| `.html` | Custom host page (optionally exposes `window.lottieAnim`) |

Output is written next to the source file as `{name}_frames/{name}.0001.png`, …

## Requirements

- **Nuke** 13+ (tested on 14.x; uses PySide2 or PySide6)
- **Python 3** separate from Nuke’s built-in interpreter
- **Playwright** + Chromium (installed in that external Python)

Nuke’s bundled Python cannot run Playwright. The menu command shells out to an external interpreter you configure.

---

## Installation

### 1. Copy the scripts into your `.nuke` folder

Place both files in the same directory:

```
~/.nuke/                          # macOS / Linux
  nuke_svg_import.py
  svg_to_frames.py

C:\Users\<you>\.nuke\             # Windows
  nuke_svg_import.py
  svg_to_frames.py
```

That folder is on Nuke’s default plugin path. If you use a custom `NUKE_PATH`, put both files in a directory listed there instead.

### 2. Register the menu command in `menu.py`

Create or edit `menu.py` in the same `.nuke` directory:

**macOS / Linux:** `~/.nuke/menu.py`  
**Windows:** `C:\Users\<you>\.nuke\menu.py`

Add these lines at **column 0** (no leading indent):

```python
import nuke_svg_import
nuke_svg_import.install()
```

Restart Nuke. You should see **File → Import Animated SVG...**.

> **Common mistake:** Copying indented lines from a docstring into `menu.py` causes `IndentationError: unexpected indent`.

### 3. Install Playwright in an external Python

Use any Python 3 environment **outside** Nuke (system Python, venv, or conda):

```bash
pip install playwright
playwright install chromium
```

On Windows (Command Prompt), to persist the interpreter path for Nuke:

```bat
setx SVG_RASTER_PYTHON "C:\path\to\python.exe"
```

On macOS / Linux, add to your shell profile or Nuke launch script:

```bash
export SVG_RASTER_PYTHON=/path/to/python3
```

Restart Nuke after setting the variable.

Alternatively, edit `EXTERNAL_PYTHON` at the top of `nuke_svg_import.py` (less portable across machines).

### Environment variables

| Variable | Purpose |
|----------|---------|
| `SVG_RASTER_PYTHON` | Python executable with Playwright installed |
| `SVG_RASTER_SCRIPT` | Optional override path to `svg_to_frames.py` (default: beside `nuke_svg_import.py`) |

---

## Usage in Nuke

1. **File → Import Animated SVG...**
2. Choose a `.svg`, `.json`, or `.html` file.
3. Set width, height, frame count, and FPS (defaults to **30** fps for non-Lottie assets).
4. Enable **Auto** to use native loop length (Lottie frame count + fps from the JSON).
5. Optionally enable:
   - **UV Pass** — per-shape ST coordinates for **STMap** retexturing
   - **Object ID Pass** — unique RGB per fill/stroke for puzzle mattes
6. Click OK. A progress panel opens; Nuke remains usable while frames render.

Read nodes are created automatically for each pass.

### UV pass in comp

Feed the UV Read into an **STMap** node’s `uv` input. UVs are per-shape bounding boxes, not a single atlas — each body part gets its own 0–1 tile.

### Object ID pass in comp

Each rendered fill and stroke gets a distinct saturated RGB. Use **Keyer**, **IDistort**, or similar to isolate shapes. Strokes only receive an ID when the element actually has a visible stroke.

---

## Command-line usage

You can also run the rasterizer directly:

```bash
python svg_to_frames.py animation.json \
  --out renders/anim.####.png \
  --auto-frames \
  --width 1920 --height 1080 \
  --uv-pass \
  --id-pass
```

Useful flags:

| Flag | Description |
|------|-------------|
| `--fps` | Frame rate for time-based scrubbing (default: 30) |
| `--frames` | Frame count when `--auto-frames` is off |
| `--auto-frames` | Detect length from Lottie or one animation loop |
| `--uv-pass` | Render UV/ST pass (`anim.uv.0001.png`, …) |
| `--id-pass` | Render Object ID pass (`anim.id.0001.png`, …) |

---

## Output layout

For source `path/to/rocket.svg`:

```
path/to/rocket_frames/
  rocket.0001.png
  rocket.0002.png
  rocket.uv.0001.png      # if UV pass enabled
  rocket.id.0001.png      # if Object ID pass enabled
  _debug_first_load.png   # debug snapshot (every run)
```

Frames are **1-based, 4-digit** (`0001`, `0002`, …).

---

## Limitations

- Output is **raster** at the chosen resolution — not resolution-independent vector.
- Lottie playback loads lottie-web from a CDN (`unpkg.com`) — requires network on first render.
- UV coordinates follow each shape’s bounding box and may “swim” on heavy path morphing.
- Non-Lottie animations driven only by `requestAnimationFrame` (no Web Animations API or SMIL) may not scrub reliably.

---

## Repository layout

```
SVG-to-Nuke/
├── nuke_svg_import.py    # Nuke menu, import panel, async rasterize, Read nodes
├── svg_to_frames.py      # Playwright rasterizer CLI
├── test_assets/          # Sample SVG and regression helpers
├── README.md
├── PROJECT_STATE.md      # Internal project status / backlog
└── DEV_LOG.md            # Development history
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Menu item missing | Check `menu.py` syntax and restart Nuke |
| `Couldn't find the external Python interpreter` | Set `SVG_RASTER_PYTHON` to a Python with Playwright |
| `ModuleNotFoundError: playwright` | Run `pip install playwright` in that Python |
| Chromium errors | Run `playwright install chromium` in that Python |
| UV / color out of sync | Re-render with the current `svg_to_frames.py` (old PNGs won’t match) |

For internal backlog and design notes, see `PROJECT_STATE.md`.
