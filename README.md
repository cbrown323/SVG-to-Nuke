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

Place both Python files and the vendored Lottie player in the same directory:

```
~/.nuke/                          # macOS / Linux
  nuke_svg_import.py
  svg_to_frames.py
  requirements.txt
  vendor/
    lottie-web/
      lottie.min.js

C:\Users\<you>\.nuke\             # Windows
  nuke_svg_import.py
  svg_to_frames.py
  requirements.txt
  vendor\
    lottie-web\
      lottie.min.js
```

`svg_to_frames.py` resolves `vendor/lottie-web/lottie.min.js` relative to itself — keep that folder beside the script (or run from a full repo clone).

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

Use any Python 3 environment **outside** Nuke (system Python, venv, or conda). A dedicated venv next to your Nuke scripts is usually the least painful option on Windows.

**Create and install (example — adjust the folder name):**

```bat
cd C:\Users\AlexStudio\.nuke
python -m venv svg-raster-venv
svg-raster-venv\Scripts\python.exe -m pip install -r requirements.txt
svg-raster-venv\Scripts\python.exe -m playwright install chromium
```

If you cloned this repo, copy `requirements.txt` beside the scripts or install from the repo root:

```bash
pip install -r /path/to/SVG-to-Nuke/requirements.txt
playwright install chromium
```

**Verify the interpreter before touching Nuke** (swap in your actual path):

```bat
"C:\Users\AlexStudio\.nuke\svg-raster-venv\Scripts\python.exe" -c "import playwright; print('playwright ok')"
```

If that prints `playwright ok`, you have the right `python.exe`. If you get `ModuleNotFoundError`, you installed Playwright into a *different* Python than the one you are testing.

#### Point Nuke at that Python

**Option A — environment variable (recommended)**

Windows Command Prompt — persists across reboots:

```bat
setx SVG_RASTER_PYTHON "C:\Users\AlexStudio\.nuke\svg-raster-venv\Scripts\python.exe"
```

macOS / Linux — add to `~/.zshrc` or `~/.bashrc`:

```bash
export SVG_RASTER_PYTHON="/Users/alex/.nuke/svg-raster-venv/bin/python3"
```

Close and reopen Nuke after setting the variable. Shortcuts launched from the desktop will not see a variable you only set in an already-open terminal unless you used `setx` (Windows) or logged out/in.

**Option B — edit `EXTERNAL_PYTHON` in `nuke_svg_import.py`**

Use a **raw string** (`r"..."`) or forward slashes. Backslashes alone will break Python:

```python
# Good — raw string
EXTERNAL_PYTHON = os.environ.get(
    "SVG_RASTER_PYTHON",
    r"C:\Users\AlexStudio\.nuke\svg-raster-venv\Scripts\python.exe",
)

# Also good — forward slashes
EXTERNAL_PYTHON = os.environ.get(
    "SVG_RASTER_PYTHON",
    "C:/Users/AlexStudio/.nuke/svg-raster-venv/Scripts/python.exe",
)
```

```python
# Bad — SyntaxError: (unicode error) 'unicodeescape'
EXTERNAL_PYTHON = "C:\Users\AlexStudio\.nuke\svg-raster-venv\Scripts\python.exe"
```

#### Example paths that work vs. paths that do not

| Path | Works? | Notes |
|------|--------|-------|
| `C:\Users\AlexStudio\.nuke\svg-raster-venv\Scripts\python.exe` | Yes | Venv you created and installed Playwright into |
| `C:\Users\AlexStudio\miniconda3\envs\nuke-tools\python.exe` | Yes | Conda env where you ran `pip install playwright` |
| `C:\Users\AlexStudio\AppData\Local\Programs\Python\Python312\python.exe` | Yes | python.org install — use the full path, not the Start-menu shortcut |
| `C:\Users\AlexStudio\AppData\Local\Microsoft\WindowsApps\python.exe` | Usually no | Windows Store stub — often redirects or lacks your packages |
| `python` or `python3` (no full path) | Risky | Nuke may resolve a different interpreter than your terminal |

#### If `svg_to_frames.py` is not beside `nuke_svg_import.py`

By default the raster script is resolved automatically:

```python
os.path.join(os.path.dirname(__file__), "svg_to_frames.py")
```

If you keep the scripts in separate folders, set the script path explicitly — do **not** paste a Windows path with bare backslashes into `os.path.join()`:

```bat
setx SVG_RASTER_SCRIPT "C:\Users\AlexStudio\.nuke\svg_to_frames.py"
```

Both files should still live on `NUKE_PATH`; only override `SVG_RASTER_SCRIPT` when the auto-detect path is wrong.

#### Quick checklist

1. `pip install -r requirements.txt` and `playwright install chromium` ran in the **same** `python.exe` you give to Nuke.
2. `SVG_RASTER_PYTHON` points at that exact file (copy path from Explorer or `where python` on Windows).
3. Restart Nuke after `setx` or shell-profile changes.
4. `nuke_svg_import.py`, `svg_to_frames.py`, and `vendor/lottie-web/` sit in the same `.nuke` folder (step 1).

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

Quick smoke test with the bundled sample Lottie:

```bash
python svg_to_frames.py test_assets/sample_bounce.json \
  --out /tmp/sample.####.png --auto-frames --width 256 --height 256
```

### Development / smoke test

Full regression suite (Lottie color + SVG/UV alpha parity, no Nuke required):

```bash
pip install -r requirements.txt pillow
playwright install chromium
python tests/run_smoke.py
```

Or use the shell wrapper:

```bash
bash tests/smoke_test.sh
```

Expected runtime is under 2 minutes at 256². A passing run prints `SMOKE OK`.

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
  render.log              # settings, timing, Lottie meta, warnings/errors
  _debug_first_load.png   # debug snapshot (every run)
```

Frames are **1-based, 4-digit** (`0001`, `0002`, …).

---

## Limitations

- Output is **raster** at the chosen resolution — not resolution-independent vector.
- Lottie playback uses **vendored** lottie-web (`vendor/lottie-web/`) — no CDN or network required at render time.
- UV coordinates follow each shape’s bounding box and may “swim” on heavy path morphing.
- Non-Lottie animations driven only by `requestAnimationFrame` (no Web Animations API or SMIL) may not scrub reliably.

---

## Repository layout

```
SVG-to-Nuke/
├── nuke_svg_import.py    # Nuke menu, import panel, async rasterize, Read nodes
├── svg_to_frames.py      # Playwright rasterizer CLI
├── requirements.txt      # Playwright pin for external Python setup
├── vendor/
│   └── lottie-web/       # Offline lottie-web@5.12.2 (no CDN)
├── test_assets/          # Sample SVG, Lottie JSON, regression helpers
├── README.md
├── PROJECT_STATE.md      # Internal project status / backlog
└── DEV_LOG.md            # Development history
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Menu item missing | Check `menu.py` syntax and restart Nuke |
| `Couldn't find the external Python interpreter` | Set `SVG_RASTER_PYTHON` to the full path of a real `python.exe` (see step 3 examples) |
| `ModuleNotFoundError: playwright` | Run `pip install playwright` in **that same** `python.exe`, then verify with `-c "import playwright"` |
| Chromium errors | Run `playwright install chromium` in that Python |
| `SyntaxError: unicodeescape` in `nuke_svg_import.py` | Use `r"..."` or forward slashes for Windows paths — see step 3 |
| Windows Store `python.exe` does nothing | Use a venv or python.org install path instead of `WindowsApps\python.exe` |
| UV / color out of sync | Re-render with the current `svg_to_frames.py` (old PNGs won’t match) |

For internal backlog and design notes, see `PROJECT_STATE.md`.
