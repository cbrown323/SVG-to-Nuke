# Nuke installation layout

Copy the **`svg_to_nuke/`** package folder into your Nuke user directory. Do not copy loose `.py` files into `.nuke` root anymore.

## Target layout

```
~/.nuke/                          # macOS / Linux
  menu.py
  svg_to_nuke/
    __init__.py
    nuke_svg_import.py
    svg_to_frames.py
    vendor/
      lottie-web/
        lottie.min.js

C:\Users\<you>\.nuke\             # Windows
  menu.py
  svg_to_nuke\
    ...
```

Nuke adds `~/.nuke` to Python’s path by default, so `import svg_to_nuke...` works without extra `NUKE_PATH` entries.

## menu.py

Add to `~/.nuke/menu.py` (both lines at column 0):

```python
import svg_to_nuke.nuke_svg_import as nuke_svg_import
nuke_svg_import.install()
```

See `menu.py.example` in this folder.

## External Python (Playwright)

Create a venv anywhere convenient (sibling to `svg_to_nuke/` is fine):

```bash
cd ~/.nuke
python3 -m venv svg-raster-venv
svg-raster-venv/bin/pip install -r /path/to/SVG-to-Nuke/requirements.txt
svg-raster-venv/bin/playwright install chromium
export SVG_RASTER_PYTHON="$HOME/.nuke/svg-raster-venv/bin/python3"
```

From a repo clone:

```bash
pip install -r requirements.txt
playwright install chromium
```

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SVG_RASTER_PYTHON` | (see `nuke_svg_import.py`) | Python with Playwright |
| `SVG_RASTER_SCRIPT` | `svg_to_nuke/svg_to_frames.py` beside `nuke_svg_import.py` | Override raster script path |

If you override `SVG_RASTER_SCRIPT`, point at the file inside the package:

```
~/.nuke/svg_to_nuke/svg_to_frames.py
```

## Migrating from loose files in `.nuke`

If you previously had scripts scattered in `.nuke` root:

1. Create `~/.nuke/svg_to_nuke/`.
2. Move `nuke_svg_import.py`, `svg_to_frames.py`, and `vendor/` into it.
3. Update `menu.py` to the import lines above (replace `import nuke_svg_import`).
4. Remove the old loose copies from `.nuke` root.
5. Update `SVG_RASTER_SCRIPT` if you set it manually.
6. Restart Nuke.

Internal path resolution (`svg_to_frames.py` next to `vendor/`) is unchanged — only the parent folder moved.

## Repo clone vs copy

| Method | Notes |
|--------|-------|
| **Copy** `svg_to_nuke/` only | Minimal install for artists |
| **Clone** full repo to `~/.nuke/SVG-to-Nuke` | Adds `tests/`, docs; run smoke tests from repo root |

If you clone the full repo under `.nuke`, either:

- Symlink or copy `svg_to_nuke/` to `~/.nuke/svg_to_nuke/`, **or**
- Add the clone root to `NUKE_PATH` and use `import svg_to_nuke.nuke_svg_import` (package must be importable from that path).

Recommended: keep **`~/.nuke/svg_to_nuke/`** as the canonical plugin location.
